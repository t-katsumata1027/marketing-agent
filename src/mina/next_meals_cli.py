"""旧日次保留と旧窓を保持し、朝昼夕六件だけを通常承認へ渡す。"""
import argparse,hashlib,json,os,uuid
from datetime import UTC,datetime
from pathlib import Path
from dataclasses import asdict
from . import next_meals as p
from .preparation import fingerprint

def load(path):
    from .current_day_cli import protected_json
    from .readiness import release_commit
    d,sha=protected_json(path,root_owned=True)
    release_commit(Path(__file__).resolve().parents[2],d['commit'])
    binding=fingerprint({k:v for k,v in d.items() if k!='references'})
    if set(d['references'])!={'implementation_review','execution_approval'}:raise ValueError('三食準備の承認が不足しています')
    for role,ref in d['references'].items():
        v,h=protected_json(ref['path'],root_owned=True)
        if h!=ref['sha256'] or v!={'role':role,'config_digest':binding,'verdict':'Go' if role=='implementation_review' else 'approved'}:raise ValueError('三食準備の承認版が不一致です')
    values={}
    for key in ('parent','preview','image_review'):
        v,h=protected_json(d[key]['path'],root_owned=True,max_bytes=524288 if key=='parent' else 131072)
        if h!=d[key]['sha256']:raise ValueError('三食準備の原本が不一致です')
        values[key]=v
    def check():
        if protected_json(path,root_owned=True)[1]!=sha:raise ValueError('三食準備構成が変わりました')
        for key in ('parent','preview','image_review'):
            if protected_json(d[key]['path'],root_owned=True,max_bytes=524288 if key=='parent' else 131072)[1]!=d[key]['sha256']:raise ValueError('三食準備原本が変わりました')
    return d,values,check

def send(store,discord,check,deadline):
    from .routine_handoff import transmit
    for platform,table in (('x','notices'),('instagram','instagram_notices')):
        while True:
            check()
            if datetime.now(UTC).timestamp()>=deadline:raise ValueError('三食準備の期限を超過しました')
            with store.transaction() as db:
                row=db.execute("SELECT * FROM "+table+" WHERE target_date=? AND kind='approval' AND state='pending' ORDER BY id LIMIT 1",(p.TARGET,)).fetchone()
                if not row:break
                if not p.notice(db,row,platform):raise ValueError('三食専用通知の版が変わりました')
                row=dict(row);db.execute("UPDATE "+table+" SET state='sending' WHERE id=? AND state='pending'",(row['id'],))
            try:transmit(store,row,platform,discord)
            except BaseException:
                with store.transaction() as db:db.execute("UPDATE "+table+" SET state='outcome_unknown' WHERE id=?",(row['id'],))
                raise
            with store.transaction() as db:db.execute("UPDATE "+table+" SET state='sent' WHERE id=? AND state='sending'",(row['id'],))
    with store.transaction() as db:
        r=p.job(db,p.TARGET)
        if not p.receipts(db,r):raise ValueError('三食専用六通知の到達を確認できません')
        p.maybe_ready(db,p.TARGET)

def run(path):
    from .store import Store
    from .instagram_store import InstagramStore
    from .current_day_cli import scoped_meal
    from .domain import Candidate
    from .m1_contract import completed_request,validate_write,verify_source
    from .registration import assert_day_state
    from .ogp import capture_ogp
    from .readiness import check_evidence
    from .next_saved import read_rows
    from .next_saved_cli import inventory,reserve
    from .buffer_budget import using,release
    from .buffer_publisher import production_client
    from .instagram_publisher import instagram_channel
    from .instagram_domain import InstagramCandidate
    from .instagram_runtime import caption
    from .instagram_media import host_asset,HOSTS
    from .drive import DriveClient
    from .clients import DiscordClient
    d,v,check=load(path);parent=v['parent'];preview=v['preview'];key=d['case_uuid'];uuid.UUID(key);store=Store('/var/lib/mina-agent/mina.sqlite3');ig=InstagramStore(store.path);clock=lambda:datetime.now(UTC)
    os.environ['MINA_PUBLISH_ENABLED']='false';os.environ['MINA_INSTAGRAM_PUBLISH_ENABLED']='false'
    with store.transaction() as db:
        old=db.execute('SELECT * FROM next_saved_jobs WHERE target_date=?',(p.TARGET,)).fetchone()
        if not old or old['state']!='held' or old['case_uuid']!=parent['approval']['case_uuid'] or clock().timestamp()>=old['deadline']:raise ValueError('旧窓と旧保留の照合が必要です')
        deadline=old['deadline'];hold=dict(db.execute('SELECT * FROM publication_holds WHERE target_date=?',(p.TARGET,)).fetchone())
        if hold!=parent['original']['publication_holds'][0]:raise ValueError('全日公開停止が変わりました')
        if any(read_rows(db,t) for t in ('candidates','instagram_candidates','buffer_publication','instagram_attempts')):raise ValueError('三食準備の公開処理が競合しています')
        if json.loads(db.execute("SELECT result FROM preparation_steps WHERE target_date=? AND step='ogp_review:daily'",(p.TARGET,)).fetchone()[0])!={'status':'hold','reason_codes':['share_ogp_mismatch']}:raise ValueError('日次の旧保留が違います')
        steps={r['step']:json.loads(r['result']) for r in read_rows(db,'preparation_steps')}
        if steps.get('meal_review:lunch:corrected:continuation:'+old['case_uuid'])!={'status':'publishable','reason_codes':[]}:raise ValueError('昼食の新独立点検が不足しています')
        for slot in p.SLOTS:
            if steps.get('ogp_review:'+slot)!={'status':'publishable','reason_codes':[]}:raise ValueError('三食の個別OGP合格が不足しています')
        origin={'parent_case':old['case_uuid'],'hold':hold,'files':parent['files'],'requests_digest':fingerprint(read_rows(db,'mina_requests')),'steps_digest':fingerprint(read_rows(db,'preparation_steps'))}
        db.execute('CREATE TABLE IF NOT EXISTS next_meal_jobs(target_date TEXT PRIMARY KEY,case_uuid TEXT UNIQUE,commit_sha TEXT,origin TEXT,material TEXT,members TEXT,state TEXT,first_started REAL,deadline REAL)')
        db.execute("INSERT INTO next_meal_jobs VALUES(?,?,?,?,NULL,NULL,'started',?,?)",(p.TARGET,key,d['commit'],json.dumps(origin),old['first_started'],deadline))
    def guard():
        check()
        with store.transaction() as db:
            r=p.job(db,p.TARGET)
            if not r or r['state']!='started' or not p.intact(db,r) or clock().timestamp()>=deadline:raise ValueError('三食準備の権限/期限が変わりました')
    try:
        guard();meal=scoped_meal();api=production_client(store)
        config={**parent,'approval':{**parent['approval'],'case_uuid':key}}
        inventory(store,api,config,'meals_initial',min(deadline,clock().timestamp()+120),clock)
        current=meal.day_state(p.TARGET);completed={s:steps['meal_correction:'+s] for s in p.SLOTS};sv=assert_day_state(current,p.TARGET,completed,clock())
        if sv!=parent['approval']['state_version']:raise ValueError('三食の期待版が変わりました')
        bundle=preview['bundle'];manifest=preview['manifest'];review=v['image_review']
        image_shas={s:manifest['assets'][s][0]['sha256'] for s in p.SLOTS}
        reviews=review['reviews']
        if (review['status']!='publishable' or review['target_date']!=p.TARGET or review['context_digest_scheme']!='sha256_json_utf8_sorted_compact'
            or review['context_digest']!=fingerprint(preview) or review['source_document_sha256']!=d['preview']['sha256'] or len(reviews)!=3
            or {r['slot']:r['image_sha256'] for r in reviews}!=image_shas
            or any(r['status']!='publishable' or r['blocking'] or r['context_digest']!=review['context_digest'] or any(value!='pass' for value in r['checks'].values()) for r in reviews)):
            raise ValueError('三食合成画像の独立点検が不一致です')
        if bundle['state_version']!=sv or bundle['target_date']!=p.TARGET:raise ValueError('合成画像の三食版が違います')
        entries=check_evidence(parent['history'],p.TARGET,clock());comments=[e['comment'] for e in entries];weekly=[e['comment'] for e in entries];menus={e['menu'] for e in entries};xs=[]
        from .tracking import configured_policy
        for slot in p.SLOTS:
            guard();response=completed[slot];request=next(r for r in parent['original']['mina_requests'] if r['operation']=='meal_correction' and r['slot']==slot);lookup=meal.request_state(request['request_key'],'meal_correction');ih=completed_request(lookup,request['request_key'],'meal_correction',response['result'])
            value=validate_write(response,slot,p.TARGET,'www.log-eats.com',operation='meal_correction',logged_at=response['result']['meal']['logged_at'],input_comment=bundle['meals'][slot]['comment']);captured=capture_ogp(value['share_url'],'www.log-eats.com')
            from .ogp import verify_image_reference
            verify_image_reference(captured,value['ogp'],value['share_url'],'www.log-eats.com')
            if bundle['meals'][slot]['nutrition']!=value['nutrition'] or bundle['meals'][slot]['image_sha256']!=response['result']['image_sha256'] or bundle['meals'][slot]['menu'] in menus:raise ValueError('三食画像と現在正本/履歴が不一致です')
            source={'request_key':request['request_key'],'operation':'meal_correction','input_hash':ih,'result_sha256':value['result_sha256'],'result_version':response['result']['result_version'],'stored_image_sha256':response['result']['stored_image_sha256'],'ogp_revision':value['ogp']['revision'],'ogp_source_version':value['source_version'],'state_version':sv,'readiness_sha256':fingerprint(parent['history']),'ogp_image_url':value['ogp']['image_url'],'ogp_image_sha256':value['ogp']['image_sha256'],p.KEY:p.marker(key)}
            if configured_policy() is not None:source['url_tracking']=configured_policy()
            c=Candidate(target_date=p.TARGET,slot=slot,comment=value['comment'],api_text=value['api_text'],share_url=value['share_url'],menu=bundle['meals'][slot]['menu'],nutrition=value['nutrition'],image_sha256=response['result']['image_sha256'],ogp_sha256=captured['digest'],api_quality='pass',secondary_review='publishable',ogp_verified=True,publish_text=value['publish_text'],approval_source=source,source_commit=d['commit'],history_verified=True,legacy_schedule_clear=True)
            c.validate('www.log-eats.com',clock(),comments,weekly);verify_source(c,meal,'www.log-eats.com',capture_ogp);xs.append(c);comments.append(c.comment);weekly.append(c.comment)
        budget=reserve(store,api,key,'meals_channel',min(deadline,clock().timestamp()+120))
        try:
            with using(budget):channel=instagram_channel(api)
        finally:release(store,budget)
        drive=DriveClient.from_env()
        def drive_request(url,data,method,operation):
            if method not in ('GET','HEAD'):guard()
            return operation()
        drive.request_guard=drive_request;drive.before_media_write=guard;igs=[]
        for slot in p.SLOTS:
            guard();assets=[host_asset(a,drive,store,allow_public=True) for a in manifest['assets'][slot]]
            source={'bundle_sha256':bundle['bundle_sha256'],'state_version':sv,'x_digests':{c.slot:c.digest for c in xs},p.KEY:p.marker(key)}
            c=InstagramCandidate(p.TARGET,slot,channel['id'],channel['serviceId'],'story',caption(bundle,slot),assets,source);c.validate(clock(),HOSTS);igs.append(c)
        guard();inventory(store,api,config,'meals_stage',min(deadline,clock().timestamp()+120),clock)
        with store.transaction() as db:
            r=p.job(db,p.TARGET)
            if r['state']!='started' or not p.intact(db,r):raise ValueError('三食承認前の原本が変わりました')
            if any(read_rows(db,t) for t in ('candidates','instagram_candidates','buffer_publication','instagram_attempts')):raise ValueError('三食承認前の処理が競合しました')
            members=[]
            for platform,cs in (('x',xs),('instagram',igs)):
                table='candidates' if platform=='x' else 'instagram_candidates'
                for c in cs:
                    raw=asdict(c);db.execute('INSERT INTO '+table+"(target_date,slot,revision,payload,digest,scheduled,state) VALUES(?,?,1,?,?,?,'approval_pending')",(p.TARGET,c.slot,json.dumps(raw,ensure_ascii=False),c.digest,c.scheduled_at.timestamp()));members.append({'platform':platform,'slot':c.slot,'revision':1,'digest':c.digest,'payload_digest':fingerprint(raw)})
            material={'bundle':bundle,'manifest':manifest,'xs':{c.slot:asdict(c) for c in xs},'igs':{c.slot:asdict(c) for c in igs}}
            db.execute("UPDATE next_meal_jobs SET members=?,material=?,state='approval_pending' WHERE target_date=? AND state='started'",(json.dumps(members),json.dumps(material,ensure_ascii=False),p.TARGET))
        store.issue_cards(p.TARGET,clock())
        for row in ig.rows(p.TARGET):ig.queue_approval(row)
        send(store,DiscordClient(os.environ['DISCORD_BOT_TOKEN'],parent['discord_channel']),check,deadline)
        print(json.dumps({'normal_cards_sent':6,'daily_held':True,'food_writes':0,'ai_calls':0}),flush=True)
    except BaseException:
        with store.transaction() as db:db.execute("UPDATE next_meal_jobs SET state='held' WHERE target_date=?",(p.TARGET,))
        raise

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);run(parser.parse_args().config)
