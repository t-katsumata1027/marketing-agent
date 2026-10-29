"""10/29日次二件だけの別継続。元四件権限・停止・承認原本を変更しない。"""
import contextvars,json,uuid,os
from contextlib import contextmanager
from dataclasses import asdict,replace
from datetime import UTC,datetime,timedelta
from pathlib import Path
from .domain import Candidate,JST
from .instagram_domain import InstagramCandidate
from .preparation import fingerprint
from .publication_hold import exists

KEY='daily_late_continuation'
TARGET='2026-10-29'
END=datetime(2026,10,29,22,30,tzinfo=JST)
ACTIVE=contextvars.ContextVar('daily_late_operator',default=None)
TABLES=('candidates','instagram_candidates','buffer_publication','instagram_attempts','publication_holds','post_promotions','post_promotion_phases','notices','instagram_notices','scoped_preparation_locks')

def row(db,target=TARGET):
    return db.execute('SELECT * FROM daily_late_jobs WHERE target_date=?',(target,)).fetchone() if exists(db,'daily_late_jobs') else None

def source(c):return c.approval_source if isinstance(c,Candidate) else c.source

def proposed(binding,target):
    if not isinstance(binding,dict) or KEY not in binding:return None
    m=binding[KEY]
    if set(m)!={'case_uuid','deadline'} or target!=TARGET or str(uuid.UUID(m['case_uuid']))!=m['case_uuid']:raise ValueError('限定日次の型が不正です')
    until=datetime.fromisoformat(m['deadline'])
    if until.tzinfo is None or until>END:raise ValueError('限定日次の期限が不正です')
    return until.astimezone(UTC)

def snapshot(db):
    return {t:[dict(r) for r in db.execute('SELECT * FROM '+t+' WHERE '+('promotion_uuid=(SELECT promotion_uuid FROM post_promotions WHERE target_date=?)' if t=='post_promotion_phases' else 'target_date=?'),(TARGET,))] for t in TABLES}

def valid_proof(proof,old,now):
    from .replacement_bundle import proof_valid
    return proof_valid(proof['source'],old['post_promotions'][0]['source_digest'],now) and proof.get('media_verified') is True and proof.get('external_verified') is True and 0<=(now-datetime.fromisoformat(proof['external']['read_started_at'])).total_seconds()<=120

def stage(store,approval,old,proof,clock):
    """旧未送信/固定原本の比較と新二候補の保存を一つのTXで行う。"""
    now=clock();key=approval['case_uuid'];uuid.UUID(key)
    if os.getenv('MINA_DAILY_LATE_ENABLED','false').lower()!='true':raise ValueError('限定継続は既定OFFです')
    if set(approval)!={'case_uuid','commit','old_root','old_commit','config_path','config_sha256','approval_reference','snapshot_digest','not_after'} or not approval['approval_reference'] or approval['snapshot_digest']!=fingerprint(old) or datetime.fromisoformat(approval['not_after'])!=END or now>=END:raise ValueError('日次継続の承認が不正です')
    if not valid_proof(proof,old,now):raise ValueError('日次継続の実証跡が不完全/古いです')
    deadline=min(END,now+timedelta(minutes=15));marker={'case_uuid':key,'deadline':deadline.isoformat()}
    xs={r['slot']:r for r in old['candidates']};igs={r['slot']:r for r in old['instagram_candidates']}
    if set(xs)!= {'dinner','daily'} or set(igs)!=set(xs) or xs['dinner']['state']!='published' or igs['dinner']['state']!='published' or xs['daily']['state']!='changes_requested' or xs['daily']['reason']!='preflight_failed' or igs['daily']['state']!='held':raise ValueError('限定日次の旧状態が不一致です')
    routes=[r for r in old['buffer_publication'] if r['slot']=='daily']
    if len(routes)!=1 or routes[0]['state']!='armed' or any(routes[0][k] is not None for k in ('attempted','post_id','request_json','response_json','proof_json')) or any(r['slot']=='daily' for r in old['instagram_attempts']):raise ValueError('日次の送信履歴があり新送信できません')
    xold=Candidate(**json.loads(xs['daily']['payload']));x=replace(xold,source_commit=approval['commit'],approval_source={**xold.approval_source,KEY:marker})
    igold=InstagramCandidate(**json.loads(igs['daily']['payload']));ig=replace(igold,source={**igold.source,KEY:marker,'x_digests':{**igold.source['x_digests'],'daily':x.digest}})
    x.validate('www.log-eats.com',now)
    from .instagram_media import HOSTS
    ig.validate(now,HOSTS)
    with store.transaction() as db:
        if snapshot(db)!=old:raise ValueError('日次継続の採用が競合しました')
        db.execute('CREATE TABLE IF NOT EXISTS daily_late_jobs(target_date TEXT PRIMARY KEY,case_uuid TEXT UNIQUE,approval TEXT,old_snapshot TEXT,proof TEXT,document TEXT,state TEXT,first_active REAL,deadline REAL,activation_at REAL)')
        if row(db):raise ValueError('日次継続を再発行しません')
        doc={'x':asdict(x),'instagram':asdict(ig),'revisions':{p:r['revision']+1 for p,r in (('x',xs['daily']),('instagram',igs['daily']))}}
        db.execute("INSERT INTO daily_late_jobs VALUES(?,?,?,?,?,?,'approval_pending',?,?,NULL)",(TARGET,key,json.dumps(approval),json.dumps(old),json.dumps(proof),json.dumps(doc),now.timestamp(),deadline.timestamp()))
        for platform,candidate in (('x',x),('instagram',ig)):
            table='candidates' if platform=='x' else 'instagram_candidates'
            db.execute("UPDATE "+table+" SET revision=?,payload=?,digest=?,scheduled=?,state='approval_pending',nonce_hash=NULL,actor=NULL,approved=NULL,dispatch_started=NULL,reason=NULL WHERE target_date=? AND slot='daily'",(doc['revisions'][platform],json.dumps(asdict(candidate),ensure_ascii=False),candidate.digest,deadline.timestamp(),TARGET))
            notices='notices' if platform=='x' else 'instagram_notices'
            db.execute("UPDATE "+notices+" SET state='obsolete' WHERE target_date=? AND slot='daily' AND state IN ('pending','sending')",(TARGET,))
        db.execute("UPDATE buffer_publication SET revision=?,digest=? WHERE target_date=? AND slot='daily' AND state='armed'",(doc['revisions']['x'],x.digest,TARGET))
        db.execute("UPDATE publication_holds SET owner=?,epoch=epoch+1,state='held',reason='daily_late_two_approvals' WHERE target_date=?",(key,TARGET))
    return deadline

def member(db,target,platform,slot,revision=None,digest=None):
    r=row(db,target)
    if not r or slot!='daily':return False
    d=json.loads(r['document']);table='candidates' if platform=='x' else 'instagram_candidates';c=db.execute('SELECT * FROM '+table+' WHERE target_date=? AND slot=?',(target,slot)).fetchone()
    expected=d[platform]
    return bool(c and c['revision']==d['revisions'][platform] and json.loads(c['payload'])==expected and c['digest']==(Candidate if platform=='x' else InstagramCandidate)(**expected).digest and (revision is None or c['revision']==revision) and (digest is None or c['digest']==digest))

def fence(db,r):
    old=json.loads(r['old_snapshot']);h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(r['target_date'],)).fetchone()
    lock=[dict(v) for v in db.execute('SELECT * FROM scoped_preparation_locks WHERE target_date=?',(r['target_date'],))]
    return bool(h and (h['owner'],h['epoch'],h['state'])==(r['case_uuid'],old['publication_holds'][0]['epoch']+1,'held') and lock==old['scoped_preparation_locks'])

def approval_allowed(db,target,platform,slot,revision,digest,now=None):
    r=row(db,target)
    if not r:return False
    now=now or datetime.now(UTC)
    if r['state']!='approval_pending' or now.timestamp()>=r['deadline'] or not fence(db,r) or platform not in ('x','instagram') or (slot is not None and not member(db,target,platform,slot,revision,digest)):raise ValueError('日次二件の承認範囲外です')
    return True

def notice_ready(db,notice,platform):
    r=row(db,notice['target_date'])
    if not r:return None
    if ACTIVE.get()!=r['case_uuid']:return False
    try:return approval_allowed(db,notice['target_date'],platform,notice['slot'],notice['revision'],None)
    except ValueError:return False

def maybe_release(db,target,now):
    r=row(db,target)
    if not r:return False
    if r['state']!='approval_pending':return True
    if now.timestamp()>=r['deadline'] or not fence(db,r):return True
    if not exists(db,'daily_late_notices') or db.execute("SELECT count(*) FROM daily_late_notices WHERE case_uuid=? AND state='sent'",(r['case_uuid'],)).fetchone()[0]!=2:return True
    for p in ('x','instagram'):
        if not member(db,target,p,'daily'):return True
        table='candidates' if p=='x' else 'instagram_candidates';c=db.execute("SELECT * FROM "+table+" WHERE target_date=? AND slot='daily'",(target,)).fetchone()
        if c['state']!='approved' or not c['actor'] or c['approved'] is None:return True
    db.execute("UPDATE daily_late_jobs SET state='ready',activation_at=? WHERE target_date=?",(now.timestamp(),target))
    for t in ('candidates','instagram_candidates'):db.execute("UPDATE "+t+" SET scheduled=? WHERE target_date=? AND slot='daily'",(now.timestamp(),target))
    return True

def window(store,candidate,clock=lambda:datetime.now(UTC)):
    if KEY not in (source(candidate) or {}):return None
    with store.transaction() as db:
        r=row(db,candidate.target_date);p='x' if isinstance(candidate,Candidate) else 'instagram'
        if not r or not member(db,candidate.target_date,p,candidate.slot) or source(candidate)[KEY]['case_uuid']!=r['case_uuid']:raise ValueError('日次継続の正本が変わりました')
        due=r['activation_at'];deadline=r['deadline']
    return (datetime.fromtimestamp(due if due is not None else deadline,UTC),datetime.fromtimestamp(deadline,UTC))

@contextmanager
def scope(key):
    token=ACTIVE.set(key)
    try:yield
    finally:ACTIVE.reset(token)

def clear(db,target):
    r=row(db,target)
    if not r or ACTIVE.get()!=r['case_uuid'] or r['state']!='executing' or datetime.now(UTC).timestamp()>=min(r['deadline'],r['activation_at']+120):return False
    if not fence(db,r):return False
    for p in ('x','instagram'):
        if not member(db,target,p,'daily'):return False
        table='candidates' if p=='x' else 'instagram_candidates';c=db.execute("SELECT state FROM "+table+" WHERE target_date=? AND slot='daily'",(target,)).fetchone()
        if c['state'] not in ('approved','dispatching','accepted','published'):return False
        candidate=db.execute('SELECT * FROM '+table+" WHERE target_date=? AND slot='daily'",(target,)).fetchone()
        attempts='buffer_publication' if p=='x' else 'instagram_attempts'
        a=db.execute('SELECT * FROM '+attempts+" WHERE target_date=? AND slot='daily'",(target,)).fetchone()
        allowed=('armed','attempting','accepted','sent') if p=='x' else ('attempting','accepted','published')
        if p=='x' and not a:return False
        if a and (a['state'] not in allowed or (a['revision'],a['digest'])!=(candidate['revision'],candidate['digest'])):return False
        if not a and c['state'] not in ('approved','dispatching'):return False
    return True

def guard(db,runtime_row):
    r=row(db,runtime_row['target_date'])
    if not r:return False
    if not clear(db,runtime_row['target_date']) or runtime_row['slot']!='daily':raise ValueError('日次継続の実行権限がありません')
    proof=json.loads(r['proof'])
    if not valid_proof(proof,json.loads(r['old_snapshot']),datetime.now(UTC)):raise ValueError('日次継続の実証跡が古いです')
    return True

def historical(store):
    """旧実コードrootに旧構成を検証し、新コードの名義へ置換しない。"""
    from .current_day_cli import config,protected_json
    from .post_promotion import VERSION,guard_source
    with store.transaction() as db:r=row(db);old=json.loads(r['old_snapshot']);a=json.loads(r['approval'])
    old_root=Path(a['old_root'])
    from .readiness import release_commit
    release_commit(old_root,a['old_commit'])
    settings,check=config(a['config_path'],old_root,version=VERSION,extras=('checkpoint',))
    if protected_json(a['config_path'],root_owned=True)[1]!=a['config_sha256'] or settings['approval']['promotion_uuid']!=old['post_promotions'][0]['promotion_uuid']:raise ValueError('旧構成の原本が変わりました')
    checkpoint,sha=protected_json(settings['checkpoint']['path'],root_owned=True)
    if sha!=settings['checkpoint']['sha256'] or fingerprint(checkpoint)!=old['post_promotions'][0]['source_digest']:raise ValueError('旧検査原本が変わりました')
    check()
    with store.transaction() as db:guard_source(db,checkpoint)
    return r,old,a,settings,checkpoint,check

def fresh(store,meal,api,capture,media,clock,inputs=None):
    """全原本・夕食実ID・全外部ページを読む。送信・生成・食事操作はしない。"""
    if inputs is None:r,old,a,settings,checkpoint,check=historical(store)
    else:old,settings,checkpoint,check=inputs
    from .post_promotion import verify_all
    olddoc=json.loads(old['post_promotions'][0]['document'])
    proof=verify_all(checkpoint,{s:Candidate(**v) for s,v in olddoc['xs'].items()},meal,settings['host'],capture,clock)
    for asset in olddoc['igs']['daily']['assets']:media(asset)
    from .post_promotion_external import own_sent
    from .replacement_inventory import BufferInventoryAdapter,ExternalInventory
    own=own_sent(store,api,TARGET,olddoc);adapter=BufferInventoryAdapter(api,settings['organization_id'])
    class Pages:
        usage_counted=True
        def begin_check(self):adapter.begin_check()
        def __call__(self,channel,binding,target,cursor):
            p=adapter(channel,binding,target,cursor);p['items']=[i for i in p['items'] if not(i['status']=='sent' and i['post_digest'] in own[channel])];return p
    external=ExternalInventory(store,Pages(),clock,settings['channels']).check(TARGET)
    check();return {'source':proof,'external':external,'media_verified':True,'external_verified':True}

def send_cards(store,client):
    """新二カードだけを各一回送信し、同IDの全操作をGET確認する。"""
    from .routine_handoff import matches,visible
    author=client.identity()
    for p in ('x','instagram'):
        table='notices' if p=='x' else 'instagram_notices'
        with store.transaction() as db:
            r=row(db)
            if r['state']!='approval_pending' or not fence(db,r) or datetime.now(UTC).timestamp()>=r['deadline']:raise ValueError('新二カードの権限/期限が不正です')
            n=db.execute('SELECT * FROM '+table+" WHERE target_date=? AND slot='daily' AND revision=? AND kind='approval'",(TARGET,json.loads(r['document'])['revisions'][p])).fetchone()
            if not n or n['state']!='pending':raise ValueError('新承認カードを再送しません')
            wire={**json.loads(n['payload']),'nonce':fingerprint({'case_uuid':r['case_uuid'],'platform':p})[:24],'enforce_nonce':True}
            db.execute('CREATE TABLE IF NOT EXISTS daily_late_notices(case_uuid TEXT,platform TEXT,state TEXT,wire TEXT,message_id TEXT,receipt TEXT,PRIMARY KEY(case_uuid,platform))')
            db.execute("INSERT INTO daily_late_notices VALUES(?,?,'dispatching',?,NULL,NULL)",(r['case_uuid'],p,json.dumps(wire)))
            db.execute("UPDATE "+table+" SET state='sending' WHERE id=?",(n['id'],))
        try:
            result=client.send(wire);key=result.get('id')
            if not isinstance(key,str) or not key.isdigit():raise ValueError('新カードの受領IDが不明です')
            with store.transaction() as db:db.execute('UPDATE daily_late_notices SET message_id=? WHERE case_uuid=? AND platform=?',(key,r['case_uuid'],p))
            actual=client.message(key)
            if actual.get('id')!=key or not matches(actual,wire,author,client.channel):raise ValueError('新カードの全操作/宛先/原本が不一致です')
            with store.transaction() as db:
                db.execute("UPDATE daily_late_notices SET state='sent',receipt=? WHERE case_uuid=? AND platform=?",(json.dumps({'message_id':key,'visible_digest':fingerprint(visible(actual)),'nonce_echo_present':actual.get('nonce') is not None}),r['case_uuid'],p));db.execute("UPDATE "+table+" SET state='sent' WHERE id=?",(n['id'],))
                maybe_release(db,TARGET,datetime.now(UTC))
        except BaseException:
            with store.transaction() as db:
                db.execute("UPDATE daily_late_notices SET state='unknown' WHERE case_uuid=? AND platform=?",(r['case_uuid'],p));db.execute("UPDATE "+table+" SET state='outcome_unknown' WHERE id=?",(n['id'],));db.execute("UPDATE daily_late_jobs SET state='held' WHERE target_date=?",(TARGET,))
            raise

def verify_candidate(store,candidate):
    with store.transaction() as db:
        if not guard(db,{'target_date':candidate.target_date,'slot':candidate.slot}):raise ValueError('限定日次の点検権限がありません')
    # 実GET/画像点検は同じoperatorのfreshが行い、120秒を超える証跡はguardが拒否する。
    return True

def refresh_budget(store,api,key,phase,deadline):
    """実ヘッダー一読と全ページ用の有限予約。元消費・不明を返却しない。"""
    from .buffer_budget import setup,subject,continuation_refresh_check,run_continuation_refresh,check
    identity=subject(api._token);grant=str(uuid.uuid5(uuid.UUID(key),'refresh:'+phase));budget=str(uuid.uuid5(uuid.UUID(key),'budget:'+phase))
    with store.transaction() as db:
        setup(db);continuation_refresh_check(db,identity,datetime.now(UTC).timestamp())
        db.execute('CREATE TABLE IF NOT EXISTS buffer_late_refresh(grant_id TEXT PRIMARY KEY,subject TEXT NOT NULL,deadline REAL NOT NULL,state TEXT NOT NULL,request_id INTEGER)')
        db.execute("INSERT INTO buffer_late_refresh VALUES(?,?,?,'reserved',NULL)",(grant,identity,deadline))
    run_continuation_refresh(store,api,grant,'buffer_late_refresh')
    with store.transaction() as db:
        check(db,identity,datetime.now(UTC).timestamp(),16,16)
        db.execute("INSERT INTO buffer_promotion_reservations VALUES(?,?,?,16,16,?,'active')",(budget,identity,TARGET,deadline))
    return budget

def execute(store,refresh,publish_x,publish_ig,clock=lambda:datetime.now(UTC)):
    """二件の実承認が揃った一度だけ。第一送信不明なら第二送信へ進まない。"""
    now=clock()
    with store.transaction() as db:
        r=row(db)
        if not r or r['state']!='ready' or not fence(db,r) or not r['activation_at'] or now.timestamp()>=min(r['deadline'],r['activation_at']+120):raise ValueError('二件承認/期限を確認できません')
        db.execute("UPDATE daily_late_jobs SET state='checking' WHERE target_date=?",(TARGET,))
        key=r['case_uuid'];old=json.loads(r['old_snapshot'])
    try:
        proof=refresh()
        if not valid_proof(proof,old,clock()):raise ValueError('日次公開前の実照合が不完全です')
        with store.transaction() as db:
            live=row(db)
            if live['state']!='checking' or clock().timestamp()>=min(live['deadline'],live['activation_at']+120):raise ValueError('限定日次照合が競合/失効しました')
            db.execute("UPDATE daily_late_jobs SET state='executing',proof=? WHERE target_date=?",(json.dumps(proof),TARGET))
        with scope(key):
            publish_x()
            with store.transaction() as db:
                if db.execute("SELECT state FROM candidates WHERE target_date=? AND slot='daily'",(TARGET,)).fetchone()[0]!='published':raise ValueError('Xの公開結果を確認できません。追加送信しません')
            publish_ig()
            with store.transaction() as db:
                if db.execute("SELECT state FROM instagram_candidates WHERE target_date=? AND slot='daily'",(TARGET,)).fetchone()[0]!='published':raise ValueError('Instagramの公開結果を確認できません')
                db.execute("UPDATE daily_late_jobs SET state='complete' WHERE target_date=?",(TARGET,))
        return 'complete'
    except BaseException:
        with store.transaction() as db:db.execute("UPDATE daily_late_jobs SET state='held' WHERE target_date=?",(TARGET,))
        raise
