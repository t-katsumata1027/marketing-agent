"""10/30の保存済み三食から日次と通常承認八案だけを一度完成する。"""
import argparse,hashlib,json,os,uuid
from datetime import UTC,datetime
from pathlib import Path
from dataclasses import asdict
from . import next_saved as n
from .preparation import fingerprint

def load(path):
    from .current_day_cli import protected_json
    from .readiness import release_commit
    d,sha=protected_json(path,root_owned=True,max_bytes=524288)
    release_commit(Path(__file__).resolve().parents[2],d['approval']['commit'])
    binding=fingerprint({k:v for k,v in d.items() if k!='references'})
    if set(d['references'])!={'implementation_review','execution_approval'}:raise ValueError('準備承認原本が不足しています')
    for role,ref in d['references'].items():
        value,actual=protected_json(ref['path'],root_owned=True)
        if actual!=ref['sha256'] or value!={'role':role,'config_digest':binding,'verdict':'Go' if role=='implementation_review' else 'approved'}:raise ValueError('準備承認が一致しません')
    def check():
        if protected_json(path,root_owned=True,max_bytes=524288)[1]!=sha:raise ValueError('準備構成が変更されました')
        for ref in d['references'].values():
            if protected_json(ref['path'],root_owned=True)[1]!=ref['sha256']:raise ValueError('承認原本が変更されました')
    return d,check

def reserve(store,api,key,phase,deadline):
    from .buffer_budget import setup,subject,run_continuation_refresh,check
    from .routine_buffer import check_refresh
    grant=str(uuid.uuid5(uuid.UUID(key),'refresh:'+phase));budget=str(uuid.uuid5(uuid.UUID(key),'budget:'+phase));identity=subject(api._token)
    with store.transaction() as db:
        setup(db);check_refresh(db,identity,datetime.now(UTC).timestamp())
        db.execute('CREATE TABLE IF NOT EXISTS buffer_next_refresh(grant_id TEXT PRIMARY KEY,subject TEXT NOT NULL,deadline REAL NOT NULL,state TEXT NOT NULL,request_id INTEGER)')
        if db.execute("SELECT 1 FROM buffer_next_refresh WHERE subject=? AND state IN ('reserved','started','unknown')",(identity,)).fetchone():raise ValueError('Buffer再確認の不明結果を保持します')
        db.execute("INSERT INTO buffer_next_refresh VALUES(?,?,?,'reserved',NULL)",(grant,identity,deadline))
    run_continuation_refresh(store,api,grant,'buffer_next_refresh')
    with store.transaction() as db:
        check(db,identity,datetime.now(UTC).timestamp(),16,16)
        db.execute("INSERT INTO buffer_promotion_reservations VALUES(?,?,?,16,16,?,'active')",(budget,identity,n.TARGET,deadline))
    return budget

def inventory(store,api,d,phase,deadline,clock):
    from .buffer_budget import using,release
    from .replacement_inventory import BufferInventoryAdapter,ExternalInventory
    budget=reserve(store,api,d['approval']['case_uuid'],phase,deadline)
    try:
        with using(budget):
            result=ExternalInventory(store,BufferInventoryAdapter(api,d['organization_id']),clock,d['channels']).check(n.TARGET)
            if phase=='initial':
                from .buffer_trial import GET_POST
                from .buffer_publisher import BufferPublisher
                from .domain import Candidate
                with store.transaction() as db:
                    routes=[dict(r) for r in db.execute("SELECT * FROM buffer_publication WHERE state='sent' AND target_date<? AND attempted>=?",(n.TARGET,datetime.fromisoformat(d['history']['checked_at']).timestamp()))]
                for route in routes:
                    row=dict(store.get(route['target_date'],route['slot']));c=Candidate(**json.loads(row['payload']))
                    post=api._request(GET_POST,{'input':{'id':route['post_id']}})['post'];p=BufferPublisher(store,row,route,api);p.proof=json.loads(route['proof_json'])
                    if not post or post['id']!=route['post_id'] or p.validate(post,c.content)!=row['x_id']:raise ValueError('履歴取得後の自動投稿を照合できません')
        return result
    finally:release(store,budget)

def proof(store,meal,xs,igs,clock,source):
    from .m1_contract import verify_source
    from .ogp import capture_ogp
    from .instagram_media import verify_public
    from .replacement_contract import stamp
    from .replacement_bundle import CHECKS
    started=clock()
    for c in xs:verify_source(c,meal,'www.log-eats.com',capture_ogp,clock=clock)
    for c in igs:
        for a in c.assets:verify_public(a)
    return {'verdict':'pass','verification':'current-source-uuid-ogp-v1','checks':sorted(CHECKS),'read_started_at':stamp(started),'state_version':xs[0].approval_source['state_version'],'source_digest':source}

def send_cards(store,discord,check,deadline,clock):
    """対象八件だけを送る。他日や不明通知をこのoperatorで再送しない。"""
    from .routine_handoff import transmit
    from .replacement_bundle import notice_allowed
    sent=0
    for platform,table in (('x','notices'),('instagram','instagram_notices')):
        while True:
            check()
            if clock().timestamp()>=deadline:raise ValueError('翌日準備の送付期限を超過しました')
            with store.transaction() as db:
                row=db.execute("SELECT * FROM "+table+" WHERE target_date=? AND kind='approval' AND state='pending' ORDER BY id LIMIT 1",(n.TARGET,)).fetchone()
                if not row:break
                if not notice_allowed(db,row,platform):raise ValueError('翌日承認カードの版が違います')
                row=dict(row);db.execute("UPDATE "+table+" SET state='sending' WHERE id=? AND state='pending'",(row['id'],))
            try:transmit(store,row,platform,discord)
            except BaseException:
                with store.transaction() as db:db.execute("UPDATE "+table+" SET state='outcome_unknown' WHERE id=?",(row['id'],))
                raise
            with store.transaction() as db:db.execute("UPDATE "+table+" SET state='sent' WHERE id=? AND state='sending'",(row['id'],))
            sent+=1
    with store.transaction() as db:
        rows=list(db.execute('SELECT * FROM routine_notice_receipts WHERE target_date=?',(n.TARGET,)))
        if len(rows)!=8 or any(r['state']!='sent' or not r['message_id'] for r in rows):raise ValueError('八件すべての承認操作付き受領を確認できません')
    return sent

def run(path):
    from .store import Store
    from .instagram_store import InstagramStore
    from .current_day_cli import scoped_meal
    from .codex_client import generation_client
    from .daily_pipeline import DeadlineClient,write_artifact
    from .registration import register_day
    from .readiness import check_evidence,build_candidates
    from .domain import Candidate,SLOTS
    from .instagram_source import load_bundle
    from .instagram_runtime import save_json,caption
    from .instagram_render import render_bundle
    from .instagram_domain import InstagramCandidate,LAYOUT_VERSION
    from .instagram_media import host_asset,HOSTS
    from .instagram_publisher import instagram_channel
    from .buffer_publisher import production_client
    from .drive import DriveClient
    from .replacement_bundle import stage
    from .clients import DiscordClient
    d,check=load(path);clock=lambda:datetime.now(UTC);store=Store('/var/lib/mina-agent/mina.sqlite3');ig=InstagramStore(store.path)
    os.environ['MINA_PUBLISH_ENABLED']='false';os.environ['MINA_INSTAGRAM_PUBLISH_ENABLED']='false'
    g=n.Gate(store,d['approval'],d['original'],d['manual'],d['files'],clock)
    check();check_evidence(d['history'],n.TARGET,clock());g.register()
    with store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS next_saved_configs(case_uuid TEXT PRIMARY KEY,path TEXT,sha256 TEXT)')
        db.execute('INSERT INTO next_saved_configs VALUES(?,?,?)',(g.key,str(path),fingerprint(d)))
    def guard():check();g.check()
    meal=DeadlineClient(scoped_meal(),guard);api=production_client(store)
    try:
        with n.scope(g):
            with store.transaction() as db:deadline=g.check(db)['deadline']
            inventory(store,api,d,'initial',min(deadline,clock().timestamp()+120),clock)
            base=Path('/var/lib/mina-agent/daily')/n.TARGET
            draft=json.loads((base/'draft.json').read_text());saved=json.loads((base/'drive.json').read_text())
            from .replacement_daily import daily_comment
            if daily_comment(meal.day_state(n.TARGET))!=d['manual']['daily_comment']:raise ValueError('実合計に基づく日次本文が変わりました')
            # 旧draftは保持し、新規の日次要求だけ実合計の短文を使用する。
            draft['daily_comment']=d['manual']['daily_comment']
            ai=DeadlineClient(generation_client(),guard,allow_retry=False)
            reviewed=register_day(store,draft,saved,base/'ogp',meal,ai,'www.log-eats.com',allow_correction=True)
            if reviewed['posts']['daily']['comment']!=d['manual']['daily_comment']:raise ValueError('日次本文が承認原本と違います')
            write_artifact(base/'posts-reviewed.json',reviewed)
            xs=[Candidate(**v) for v in build_candidates(reviewed,d['history'],d['approval']['commit'],'www.log-eats.com',clock())]
            shadow_path=base/('next-saved-'+g.key+'.sqlite3');shadow=Store(shadow_path);shadow.save_many(xs,'www.log-eats.com',clock())
            bundle=load_bundle(shadow_path,base.parent,n.TARGET,meal.day_state(n.TARGET),clock())
            out=Path('/var/lib/mina-agent/instagram')/n.TARGET/LAYOUT_VERSION/bundle['bundle_sha256'];out.mkdir(parents=True,exist_ok=False,mode=0o700)
            save_json(out/'bundle.json',bundle);manifest=render_bundle(bundle,out)
            budget=reserve(store,api,g.key,'channel',min(deadline,clock().timestamp()+120))
            from .buffer_budget import using,release
            try:
                with using(budget):channel=instagram_channel(api)
            finally:release(store,budget)
            drive=DeadlineClient(DriveClient.from_env(),guard);igs=[]
            for slot in SLOTS:
                guard();assets=[host_asset(a,drive,store,allow_public=True) for a in manifest['assets'][slot]]
                c=InstagramCandidate(n.TARGET,slot,channel['id'],channel['serviceId'],'post' if slot=='daily' else 'story',caption(bundle,slot),assets,{k:bundle[k] for k in ('bundle_sha256','state_version','x_digests')})
                c.validate(clock(),HOSTS);igs.append(c)
            save_json(out/'candidates.json',[asdict(c) for c in igs])
            guard();inventory(store,api,d,'stage',min(deadline,clock().timestamp()+120),clock)
            source=d['approval']['snapshot_digest'];verified=proof(store,meal,xs,igs,clock,source)
            with store.transaction() as db:
                guard_row=g.check(db);h=d['original']['publication_holds'][0]
                if db.execute("UPDATE publication_holds SET state='saved_pending_review',owner=?,epoch=epoch+1,reason='next_saved_new_review' WHERE target_date=? AND owner=? AND epoch=? AND state='held'",(g.key,n.TARGET,h['owner'],h['epoch'])).rowcount!=1:raise ValueError('翌日公開停止が競合しました')
                db.execute("UPDATE next_saved_jobs SET state='staging' WHERE target_date=? AND state='running'",(n.TARGET,))
            stage(store,n.TARGET,g.key,h['epoch']+1,source,xs,igs,'www.log-eats.com',HOSTS,clock(),verified)
            with store.transaction() as db:db.execute("UPDATE next_saved_jobs SET state='approval_pending' WHERE target_date=? AND state='staging'",(n.TARGET,))
        store.preparation_done(n.TARGET);store.issue_cards(n.TARGET,clock())
        for row in ig.rows(n.TARGET):ig.queue_approval(row)
        discord=DiscordClient(os.environ['DISCORD_BOT_TOKEN'],d['discord_channel'])
        sent=send_cards(store,discord,check,deadline,clock)
        print(json.dumps({'target_date':n.TARGET,'state':'approval_pending','cards_sent':sent,'food_writes':0,'image_generations':0}),flush=True)
    except BaseException:
        with store.transaction() as db:db.execute("UPDATE next_saved_jobs SET state='held' WHERE target_date=? AND state<>'complete'",(n.TARGET,))
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);run(p.parse_args().config)
