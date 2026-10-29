"""差戻し一件だけの再承認を履歴付きで作り、画像を直接添付する。"""
import argparse,hashlib,json,os,uuid
from contextvars import ContextVar
from datetime import UTC,datetime
from pathlib import Path
from . import morning_registry as m
from .preparation import fingerprint
VERSION='mina-ig-dinner-renotice-v1'
END=datetime.fromisoformat('2026-10-30T18:30:00+09:00')
scope=ContextVar('mina_renotice_scope',default=None)

def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS notice_reissues(request_uuid TEXT PRIMARY KEY,case_uuid TEXT,platform TEXT,slot TEXT,old_revision INTEGER,new_revision INTEGER,old_row TEXT,old_notice TEXT,old_receipt TEXT,display_failure TEXT,config_sha TEXT,asset TEXT,deadline REAL,state TEXT,new_notice_id INTEGER,UNIQUE(case_uuid,platform,slot,old_revision))')

def historical_receipt(db,job,receipt,notice):
    if not m.exists(db,'notice_reissues'):return False
    for r in db.execute('SELECT * FROM notice_reissues WHERE case_uuid=?',(job['case_uuid'],)):
        if (r['platform']==receipt['platform'] and json.loads(r['old_notice'])==dict(notice) and json.loads(r['old_receipt'])==dict(receipt)
            and r['new_revision']==r['old_revision']+1 and notice['revision']==r['old_revision']):return True
    return False

def fresh_notice(db,job,notice,platform):
    if not m.exists(db,'notice_reissues'):return None
    row=db.execute('SELECT * FROM notice_reissues WHERE case_uuid=? AND platform=? AND slot=? AND new_revision=?',(job['case_uuid'],platform,notice['slot'],notice['revision'])).fetchone()
    if not row:return None
    return bool(row['request_uuid']==scope.get() and row['new_notice_id']==notice['id'] and row['state']=='registered' and datetime.now(UTC).timestamp()<row['deadline'])

def protected_notice(db,platform,notice_id):
    return bool(m.exists(db,'notice_reissues') and db.execute('SELECT 1 FROM notice_reissues WHERE platform=? AND new_notice_id=?',(platform,notice_id)).fetchone())

def register(store,config,config_sha,display,ig=None):
    now=datetime.now(UTC)
    if now>=END:raise ValueError('再通知専用期限を超過しました')
    with store.transaction() as db:
        initialize(db);job=m.job(db,m.TARGET)
        if not job or not m.intact(db,job) or job['case_uuid']!=config['case_uuid']:raise ValueError('準備の原本が変わりました')
        old=db.execute("SELECT * FROM instagram_candidates WHERE target_date=? AND slot='dinner'",(m.TARGET,)).fetchone()
        if not old or old['state']!='changes_requested' or old['revision']!=config['old_revision'] or old['digest']!=config['digest'] or not old['actor'] or old['approved'] is None:raise ValueError('差戻し原本が不一致です')
        if db.execute('SELECT 1 FROM instagram_attempts WHERE target_date=? AND slot=?',(m.TARGET,'dinner')).fetchone():raise ValueError('送信開始済みの候補は再発行できません')
        previous=db.execute("SELECT * FROM instagram_notices WHERE target_date=? AND slot='dinner' AND revision=? AND kind='approval'",(m.TARGET,old['revision'])).fetchone()
        receipt=db.execute("SELECT * FROM routine_notice_receipts WHERE platform='instagram' AND notice_id=?",(previous['id'],)).fetchone()
        from .routine_handoff import matches
        origin=json.loads(job['origin'])
        if (not receipt or receipt['channel']!=config['channel'] or config['channel']!=origin['discord_channel']
            or receipt['message_id']!=config['old_message_id'] or not matches(display,json.loads(receipt['payload']),receipt['author'],receipt['channel'])):raise ValueError('差戻し通知の送付先・著者・本文・ボタンが不一致です')
        asset=json.loads(old['payload'])['assets'][0]
        if len(json.loads(old['payload'])['assets'])!=1 or asset!=config['asset'] or hashlib.sha256(Path(asset['path']).read_bytes()).hexdigest()!=asset['sha256']:raise ValueError('夕食画像の原本が変わりました')
        if (not receipt or receipt['state']!='sent' or display['id']!=receipt['message_id'] or display.get('attachments')
            or len(display.get('embeds',[]))!=1 or display['embeds'][0].get('image',{}).get('width')!=0
            or display['embeds'][0].get('image',{}).get('height')!=0):raise ValueError('画像表示失敗の原本が不足しています')
        revision=old['revision']+1
        # 他七件の承認には触れず、新しいnonceだけを通常通知生成で払い出す。
        db.execute('INSERT INTO notice_reissues VALUES(?,?,\'instagram\',\'dinner\',?,?,?,?,?,?,?,?,?,\'registered\',NULL)',(config['request_uuid'],job['case_uuid'],old['revision'],revision,json.dumps(dict(old)),json.dumps(dict(previous)),json.dumps(dict(receipt)),json.dumps(display),config_sha,json.dumps(asset),END.timestamp()))
        db.execute("UPDATE instagram_candidates SET revision=?,state='approval_pending',actor=NULL,approved=NULL,nonce_hash=NULL WHERE target_date=? AND slot='dinner'",(revision,m.TARGET))
        db.execute("UPDATE morning_members SET revision=? WHERE case_uuid=? AND platform='instagram' AND slot='dinner'",(revision,job['case_uuid']))
        db.execute("UPDATE morning_cohorts SET state='staged' WHERE case_uuid=? AND cohort='meals'",(job['case_uuid'],))
        if ig is not None:
            current=dict(db.execute("SELECT * FROM instagram_candidates WHERE target_date=? AND slot='dinner'",(m.TARGET,)).fetchone())
            ig.queue_approval(current,connection=db)
            notice=db.execute("SELECT * FROM instagram_notices WHERE target_date=? AND slot='dinner' AND revision=? AND kind='approval'",(m.TARGET,revision)).fetchone()
            wire=json.loads(notice['payload']);name=Path(asset['path']).name;wire['embeds']=[{'image':{'url':'attachment://'+name}}];wire['attachments']=[{'id':0,'filename':name}]
            db.execute("UPDATE instagram_notices SET payload=?,state='sending' WHERE id=? AND state='pending'",(json.dumps(wire,ensure_ascii=False),notice['id']))
            if db.execute('SELECT changes()').fetchone()[0]!=1:raise ValueError('再通知claimが競合しました')
            db.execute('UPDATE notice_reissues SET new_notice_id=? WHERE request_uuid=?',(notice['id'],config['request_uuid']))
    return asset

def run(path,resume_registered=False,continuation_path=None):
    from .current_day_cli import protected_json
    from .readiness import release_commit
    from .store import Store
    from .instagram_store import InstagramStore
    from .clients import DiscordClient
    from .notice_attachment import AttachedDiscord
    from .routine_handoff import transmit
    config,sha=protected_json(path,root_owned=True);uuid.UUID(config['request_uuid'])
    if config['contract_version']!=VERSION or config['target_date']!=m.TARGET or config['slot']!='dinner' or config['not_after']!=END.isoformat() or not config['approval_reference']:raise ValueError('再通知専用権限が不一致です')
    continuation=None;continuation_sha=None
    if resume_registered:
        if not continuation_path:raise ValueError('送信前継続の別原本が必要です')
        continuation,continuation_sha=protected_json(continuation_path,root_owned=True)
        if (continuation.get('contract_version')!=VERSION or continuation.get('mode')!='finish_registered_before_post'
            or continuation.get('config_sha256')!=sha or continuation.get('request_uuid')!=config['request_uuid']
            or continuation.get('new_revision')!=config['old_revision']+1 or continuation.get('not_after')!=END.isoformat()
            or not continuation.get('approval_reference')):raise ValueError('送信前継続の別原本が不一致です')
        release_commit(Path(__file__).resolve().parents[2],continuation['commit'])
    else:release_commit(Path(__file__).resolve().parents[2],config['commit'])
    os.environ['MINA_PUBLISH_ENABLED']='false';os.environ['MINA_INSTAGRAM_PUBLISH_ENABLED']='false'
    store=Store('/var/lib/mina-agent/mina.sqlite3');ig=InstagramStore(store.path);client=DiscordClient(os.environ['DISCORD_BOT_TOKEN'],config['channel'])
    display=client.message(config['old_message_id'])
    token=scope.set(config['request_uuid'])
    try:
        if resume_registered:
            with store.transaction() as db:
                record=db.execute('SELECT * FROM notice_reissues WHERE request_uuid=?',(config['request_uuid'],)).fetchone()
                if (not record or record['state']!='registered' or record['config_sha']!=sha or record['case_uuid']!=config['case_uuid']
                    or record['platform']!='instagram' or record['slot']!='dinner' or record['new_revision']!=config['old_revision']+1
                    or record['new_notice_id']!=continuation['notice_id']
                    or record['old_revision']!=config['old_revision'] or json.loads(record['asset'])!=config['asset']
                    or db.execute("SELECT 1 FROM routine_notice_receipts WHERE platform='instagram' AND notice_id=?",(record['new_notice_id'],)).fetchone()):raise ValueError('送信前停止の原本を確認できません。元受領照会が必要です')
                old=json.loads(record['old_row']);current=db.execute("SELECT * FROM instagram_candidates WHERE target_date=? AND slot='dinner'",(m.TARGET,)).fetchone()
                if not current or current['state']!='approval_pending' or current['revision']!=record['new_revision'] or any(current[k]!=old[k] for k in ('payload','digest','scheduled')):raise ValueError('送信前停止の候補版が変わりました')
            return send_registered(store,ig,client,config,sha,path,continuation_reference=(continuation_path,continuation_sha))
        return deliver(store,ig,client,config,sha,display,path)
    finally:scope.reset(token)

def deliver(store,ig,client,config,sha,display,path):
    register(store,config,sha,display,ig=ig)
    return send_registered(store,ig,client,config,sha,path)

def send_registered(store,ig,client,config,sha,path,continuation_reference=None):
    from .notice_attachment import AttachedDiscord
    from .routine_handoff import transmit
    from .current_day_cli import protected_json
    asset=config['asset']
    row=next(r for r in ig.rows(m.TARGET) if r['slot']=='dinner')
    with store.transaction() as db:
        notice=dict(db.execute("SELECT * FROM instagram_notices WHERE target_date=? AND slot='dinner' AND revision=? AND kind='approval'",(m.TARGET,row['revision'])).fetchone())
        if not m.notice(db,notice,'instagram'):raise ValueError('再通知の対象・期限が変わりました')
    def guard():
        if protected_json(path,root_owned=True)[1]!=sha:raise ValueError('再通知権限が変わりました')
        if continuation_reference and protected_json(continuation_reference[0],root_owned=True)[1]!=continuation_reference[1]:raise ValueError('送信前継続の別原本が変わりました')
        with store.transaction() as db:
            current=db.execute('SELECT * FROM instagram_notices WHERE id=?',(notice['id'],)).fetchone()
            if not current or current['state']!='sending' or current['payload']!=notice['payload'] or not m.notice(db,current,'instagram'):raise ValueError('再通知の期限・claim所有が変わりました')
    from .external_guard import guard as external_guard
    guard_token=external_guard.set(guard)
    try:
        guard()
        transmit(store,notice,'instagram',AttachedDiscord(client,asset))
        with store.transaction() as db:
            db.execute("UPDATE instagram_notices SET state='sent' WHERE id=? AND state='sending'",(notice['id'],))
            if not m.receipts(db,m.job(db,m.TARGET),'meals'):raise ValueError('新版の同ID受領を確認できません')
            db.execute("UPDATE notice_reissues SET state='sent' WHERE request_uuid=?",(config['request_uuid'],))
            db.execute("UPDATE morning_cohorts SET state='notified' WHERE case_uuid=? AND cohort='meals'",(config['case_uuid'],));m.maybe_ready(db,m.TARGET)
        print(json.dumps({'state':'renotified','platform':'instagram','slot':'dinner','revision':row['revision'],'attachment_sha256':asset['sha256'],'other_approvals_preserved':7,'sns_writes':0}))
    except BaseException:
        with store.transaction() as db:db.execute("UPDATE instagram_notices SET state='outcome_unknown' WHERE id=?",(notice['id'],));db.execute("UPDATE notice_reissues SET state='unknown' WHERE request_uuid=?",(config['request_uuid'],))
        raise
    finally:external_guard.reset(guard_token)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--finish-registered',action='store_true');parser.add_argument('--continuation');args=parser.parse_args()
    # -mの__main__と通常importでContextVarを二重生成しない。
    from .notice_reissue import run as canonical_run
    canonical_run(args.config,resume_registered=args.finish_registered,continuation_path=args.continuation)
