"""本人の明示指示により、時計差で送信前停止した朝食を一度だけ遅延公開へ渡す。"""
import hashlib
import json
import uuid
from dataclasses import asdict,replace
from datetime import datetime,timedelta
from .clients import require_live
from .domain import Candidate,JST,instant


def authorize_breakfast(store,target,expected_digest,authorization,inventory,host,verify,clock):
    """現在の本文と実在庫を確認し、旧承認を保全して今回の本人指示を新承認として固定する。"""
    require_live()
    now=instant(clock())
    if (target!=now.astimezone(JST).date().isoformat() or not isinstance(authorization,str)
            or not authorization.strip() or len(authorization)>1000):
        raise ValueError('当日の本人指示が必要です')
    def checked_inventory():
        age=(instant(clock())-instant(datetime.fromisoformat(inventory['checked_at']))).total_seconds()
        if (inventory.get('date')!=target or inventory.get('complete') is not True
                or inventory.get('fixed_x_matches') is not True or inventory.get('target_or_unknown_posts')!=0
                or not 0<=age<=120):
            raise ValueError('新鮮なX全件照合が必要です')
    checked_inventory()
    row=store.get(target,'breakfast')
    if not row or row['digest']!=expected_digest:raise ValueError('対象朝食の版が変わっています')
    original=Candidate(**json.loads(row['payload']))
    if original.digest!=expected_digest or original.approval_source.get('publish_after_approval_until'):
        raise ValueError('通常承認した朝食だけを扱います')
    if not original.scheduled_at<=now<original.scheduled_at+timedelta(hours=2):
        raise ValueError('朝食予定から2時間以内だけ復旧できます')
    verify(original)
    now=instant(clock());checked_inventory()
    until=min(now+timedelta(minutes=15),original.scheduled_at+timedelta(hours=2))
    recovered=replace(original,approval_source={**original.approval_source,'publish_after_approval_until':until.isoformat()})
    recovered.validate(host,now)
    key=str(uuid.uuid4());receipt=hashlib.sha256(authorization.encode('utf-8')).hexdigest()
    with store.transaction() as db:
        old=db.execute("SELECT * FROM candidates WHERE target_date=? AND slot='breakfast'",(target,)).fetchone()
        route=db.execute("SELECT * FROM buffer_publication WHERE target_date=? AND slot='breakfast'",(target,)).fetchone()
        if (not old or old['state']!='changes_requested' or old['reason']!='preflight_failed'
                or old['revision']!=row['revision'] or old['digest']!=expected_digest or old['x_id'] is not None
                or not route or route['state']!='armed' or route['revision']!=old['revision'] or route['digest']!=old['digest']
                or any(route[k] is not None for k in ('attempted','post_id','request_json','response_json','proof_json'))):
            raise ValueError('朝食の送信前停止を確認できません')
        event=db.execute("SELECT reason,details FROM operation_events WHERE category='publication' AND target_date=? AND slot='breakfast' AND revision=? AND event='stopped' ORDER BY id DESC LIMIT 1",(target,old['revision'])).fetchone()
        if not event or event['reason']!='clock_ahead' or json.loads(event['details']).get('write_started') is not False:
            raise ValueError('時計差による未送信の証跡が必要です')
        if db.execute("SELECT 1 FROM publication_exclusions WHERE target_date=? AND slot='breakfast'",(target,)).fetchone():
            raise ValueError('本人指定の公開除外を維持します')
        if db.execute("SELECT 1 FROM publication_exceptions WHERE target_date=? AND slot='breakfast'",(target,)).fetchone():
            raise ValueError('復旧は一度だけです')
        db.execute('''CREATE TABLE IF NOT EXISTS publication_recoveries (
            round_key TEXT PRIMARY KEY,target_date TEXT NOT NULL,slot TEXT NOT NULL,
            previous_candidate TEXT NOT NULL,previous_route TEXT NOT NULL,created REAL NOT NULL)''')
        db.execute('INSERT INTO publication_recoveries VALUES(?,?,?,?,?,?)',(key,target,'breakfast',json.dumps(dict(old),ensure_ascii=False),json.dumps(dict(route),ensure_ascii=False),now.timestamp()))
        db.execute('''CREATE TABLE IF NOT EXISTS clock_recovery_authorizations (
            round_key TEXT PRIMARY KEY, instruction TEXT NOT NULL, instruction_sha256 TEXT NOT NULL, authorized REAL NOT NULL)''')
        db.execute('INSERT INTO clock_recovery_authorizations VALUES(?,?,?,?)',(key,authorization,receipt,now.timestamp()))
        db.execute("INSERT INTO publication_exceptions VALUES(?,'breakfast',?,'本人明示指示：時計差停止した今朝のX投稿を復旧')",(target,until.isoformat()))
        revision=old['revision']+1
        db.execute("UPDATE candidates SET revision=?,payload=?,digest=?,scheduled=?,state='approved',actor='owner_explicit_chat',approved=?,nonce_hash=NULL,dispatch_started=NULL,x_id=NULL,reason=NULL WHERE target_date=? AND slot='breakfast'",(revision,json.dumps(asdict(recovered),ensure_ascii=False),recovered.digest,now.timestamp(),now.timestamp(),target))
        db.execute("UPDATE buffer_publication SET revision=?,digest=? WHERE target_date=? AND slot='breakfast'",(revision,recovered.digest,target))
        store.audit.emit('publication',target,'breakfast',revision,'recovery','recovered','recovered',{'input_sha256':receipt,'write_started':False},now=now,db=db)
    return {'target_date':target,'slot':'breakfast','revision':revision,'state':'approved','deadline':until.isoformat()}
