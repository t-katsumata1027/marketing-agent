"""本人が承認した三工程だけで残量を再取得し、外部全ページを排他的に照合する。"""
import json,os,time,uuid,hashlib
from contextlib import contextmanager
from datetime import datetime,UTC
from pathlib import Path
from .post_promotion import load,record,TARGET,SLOTS
from .domain import slot_time,date
from .replacement_contract import digest,stamp

@contextmanager
def phase_lock(store,key,phase):
    if os.name!='posix':
        yield;return
    import fcntl
    p=Path(store.path).parent/('post-promotion-'+key+'-'+phase+'.lock')
    fd=os.open(p,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX);yield
    finally:os.close(fd)

def current(db,document,target,phase):
    if phase=='stage':
        if any(db.execute('SELECT 1 FROM '+t+' WHERE target_date=?',(target,)).fetchone() for t in ('candidates','instagram_candidates','buffer_publication','instagram_attempts')):raise ValueError('通常投稿状態が変わりました')
        return
    for table in ('candidates','instagram_candidates'):
        expected=document['xs'] if table=='candidates' else document['igs']
        rows=list(db.execute('SELECT * FROM '+table+' WHERE target_date=?',(target,)))
        if {r['slot'] for r in rows}!=set(SLOTS):raise ValueError('公開候補の構成が変わりました')
        for c in rows:
            if c['state'] not in ('approved','dispatching','accepted','published') or not c['actor'] or c['approved'] is None or json.loads(c['payload'])!=expected[c['slot']]:raise ValueError('公開候補の版/成否が変わりました')
    for table,states in (('buffer_publication',('armed','attempting','accepted','sent')),('instagram_attempts',('attempting','accepted','published'))):
        candidates='candidates' if table=='buffer_publication' else 'instagram_candidates'
        for attempt in db.execute('SELECT * FROM '+table+' WHERE target_date=?',(target,)):
            c=db.execute('SELECT * FROM '+candidates+' WHERE target_date=? AND slot=?',(target,attempt['slot'])).fetchone()
            if not c or attempt['state'] not in states or (attempt['revision'],attempt['digest'])!=(c['revision'],c['digest']):raise ValueError('公開試行の成否/版が不明です')

def own_sent(store,api,target,document):
    from .buffer_trial import GET_POST
    from .buffer_publisher import BufferPublisher
    from .domain import Candidate
    from .instagram_publisher import GET as IG_GET,validate_post
    from .instagram_domain import InstagramCandidate
    own={channel:set() for channel in ('x','instagram')}
    with store.transaction() as db:
        routes=[dict(r) for r in db.execute("SELECT * FROM buffer_publication WHERE target_date=? AND state='sent'",(target,))]
        attempts=[dict(r) for r in db.execute("SELECT * FROM instagram_attempts WHERE target_date=? AND state='published'",(target,))]
        xs={r['slot']:dict(r) for r in db.execute('SELECT * FROM candidates WHERE target_date=?',(target,))}
    for route in routes:
        post=api._request(GET_POST,{'input':{'id':route['post_id']}})['post']
        if not isinstance(post,dict) or post.get('id')!=route['post_id']:raise ValueError('自動X投稿が消失しました')
        publisher=BufferPublisher(store,xs[route['slot']],route,api);publisher.proof=json.loads(route['proof_json'])
        if publisher.validate(post,Candidate(**document['xs'][route['slot']]).content)!=xs[route['slot']]['x_id']:raise ValueError('自動X投稿が変更されました')
        own['x'].add(digest(route['post_id']))
    for attempt in attempts:
        post=api._request(IG_GET,{'input':{'id':attempt['post_id']}})['post']
        if not isinstance(post,dict) or post.get('id')!=attempt['post_id'] or validate_post(InstagramCandidate(**document['igs'][attempt['slot']]),post)!=attempt['external_url']:raise ValueError('自動Instagram投稿が消失/変更されました')
        own['instagram'].add(digest(attempt['post_id']))
    return own

def ensure_external(store,target,phase,clock=lambda:datetime.now(UTC)):
    if target!=TARGET or phase not in ('stage',*SLOTS):raise ValueError('公開照合の範囲が不正です')
    r,d,settings,snapshot,check=load(store,target);key=r['promotion_uuid']
    if (phase=='stage' and r['state']!='preparing') or (phase!='stage' and r['state']!='released'):raise ValueError('公開照合の段階が不一致です')
    with phase_lock(store,key,phase):
        with store.transaction() as db:
            row=db.execute('SELECT * FROM post_promotion_phases WHERE promotion_uuid=? AND phase=?',(key,phase)).fetchone()
            if not row:raise ValueError('承認済み照合工程がありません')
            current(db,d,target,phase)
            if row['state']=='complete':
                receipt=json.loads(row['receipt'])
                if not 0<=(clock()-datetime.fromisoformat(receipt['read_started_at'])).total_seconds()<=120:raise ValueError('外部照合が失効しました')
                return receipt
            if row['state']!='pending':raise ValueError('不明な外部照合は自動再試行しません')
            db.execute("UPDATE post_promotion_phases SET state='started' WHERE promotion_uuid=? AND phase=?",(key,phase))
        budget_id=str(uuid.uuid5(uuid.UUID(key),'budget:'+phase));grant_id=str(uuid.uuid5(uuid.UUID(key),'refresh:'+phase))
        try:
            from .buffer_budget import setup,subject,continuation_refresh_check,run_continuation_refresh,check as quota_check,using,release
            from .buffer_publisher import production_client
            api=production_client(store);identity=subject(api._token)
            deadline=min(clock().timestamp()+120,slot_time(date.fromisoformat(target),'dinner' if phase=='stage' else phase).timestamp()+120)
            with store.transaction() as db:
                setup(db);continuation_refresh_check(db,identity,time.time())
                db.execute("INSERT INTO buffer_promotion_refresh VALUES(?,?,?,'reserved',NULL)",(grant_id,identity,deadline))
            run_continuation_refresh(store,api,grant_id,'buffer_promotion_refresh')
            with store.transaction() as db:
                quota_check(db,identity,time.time(),12,24)
                db.execute("INSERT INTO buffer_promotion_reservations VALUES(?,?,?,12,12,?,'active')",(budget_id,identity,target,deadline))
            from .replacement_inventory import BufferInventoryAdapter,ExternalInventory
            adapter=BufferInventoryAdapter(api,settings['organization_id'])
            class Page:
                usage_counted=True
                def begin_check(self):adapter.begin_check()
                def __call__(self,channel,binding,target,cursor):
                    page=adapter(channel,binding,target,cursor)
                    page['items']=[i for i in page['items'] if not (i['status']=='sent' and i['post_digest'] in own[channel])]
                    return page
            with using(budget_id):
                own=own_sent(store,api,target,d) if phase!='stage' else {'x':set(),'instagram':set()}
                receipt=ExternalInventory(store,Page(),clock,settings['channels']).check(target)
            with store.transaction() as db:
                check();current(db,d,target,phase)
                db.execute("UPDATE post_promotion_phases SET state='complete',receipt=? WHERE promotion_uuid=? AND phase=? AND state='started'",(json.dumps(receipt),key,phase))
            return receipt
        except BaseException:
            with store.transaction() as db:db.execute("UPDATE post_promotion_phases SET state='held' WHERE promotion_uuid=? AND phase=?",(key,phase))
            raise
        finally:
            from .buffer_budget import release
            release(store,budget_id)
