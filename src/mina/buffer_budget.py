"""実ヘッダーに束縛した共通照会予算。一回probe以外で旧上限を迂回しない。"""
import contextvars,hashlib,json,re,time,os
from contextlib import contextmanager
from .buffer_trial import ProbeError,ORGANIZATIONS

PROBE=contextvars.ContextVar('buffer_quota_probe',default=None)
RESERVATION=contextvars.ContextVar('buffer_budget_reservation',default=None)
REFRESH=contextvars.ContextVar('buffer_budget_refresh',default=None)
WINDOWS=(900,86400,2592000)

def subject(token):return hashlib.sha256(token.encode()).hexdigest()

def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS buffer_quota_probes(grant_id TEXT PRIMARY KEY,subject TEXT NOT NULL,target_date TEXT NOT NULL,deadline REAL NOT NULL,state TEXT NOT NULL,request_id INTEGER,organization_digest TEXT NOT NULL,UNIQUE(subject,target_date))')
    db.execute('CREATE TABLE IF NOT EXISTS buffer_rate_observations(subject TEXT NOT NULL,window INTEGER NOT NULL,quota INTEGER NOT NULL,remaining INTEGER NOT NULL,expires REAL NOT NULL,observed REAL NOT NULL,request_id INTEGER NOT NULL,PRIMARY KEY(subject,window))')
    db.execute('CREATE TABLE IF NOT EXISTS buffer_request_reservations(budget_id TEXT PRIMARY KEY,subject TEXT NOT NULL,target_date TEXT NOT NULL,remaining INTEGER NOT NULL,original INTEGER NOT NULL,deadline REAL NOT NULL,state TEXT NOT NULL,UNIQUE(subject,target_date))')
    db.execute('CREATE TABLE IF NOT EXISTS buffer_retry_deadlines(subject TEXT PRIMARY KEY,until REAL NOT NULL,request_id INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS buffer_continuation_reservations(budget_id TEXT PRIMARY KEY,subject TEXT NOT NULL,target_date TEXT NOT NULL,remaining INTEGER NOT NULL,original INTEGER NOT NULL,deadline REAL NOT NULL,state TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS buffer_continuation_refresh(grant_id TEXT PRIMARY KEY,subject TEXT NOT NULL,deadline REAL NOT NULL,state TEXT NOT NULL,request_id INTEGER)')

def reservation(db,key):
    for table in ('buffer_request_reservations','buffer_continuation_reservations'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():
            row=db.execute('SELECT * FROM '+table+' WHERE budget_id=?',(key,)).fetchone()
            if row:return table,row
    return None,None

def observations(db,key,now,raw=False):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='buffer_rate_observations'").fetchone():return None
    values=[dict(r) for r in db.execute('SELECT * FROM buffer_rate_observations WHERE subject=?',(key,))]
    if {r['window'] for r in values}!=set(WINDOWS) or any(r['observed']>now or r['expires']<=now for r in values):return None
    if next(r['quota'] for r in values if r['window']==86400)<600:return None
    if not raw and (not db.execute("SELECT 1 FROM sqlite_master WHERE name='buffer_quota_probes'").fetchone() or not db.execute("SELECT 1 FROM buffer_quota_probes WHERE subject=? AND state='complete'",(key,)).fetchone()):return None
    return values

def limits(db,key,now):
    values=observations(db,key,now) if key else None
    return ((900,90),(86400,540),(2592000,2900)) if values else ((900,90),(86400,240),(2592000,2900))

def outstanding(db,now,exclude=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='buffer_request_reservations'").fetchone():return 0
    total=0
    for table in ('buffer_request_reservations','buffer_continuation_reservations'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():total+=db.execute('SELECT COALESCE(sum(remaining),0) FROM '+table+" WHERE state='active' AND deadline>? AND budget_id<>?",(now,exclude or '')).fetchone()[0]
    return total

def check(db,key,now,needed=1,reserve_normal=0,exclude=None,probe=False):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='buffer_retry_deadlines'").fetchone() and db.execute('SELECT 1 FROM buffer_retry_deadlines WHERE subject=? AND until>?',(key,now)).fetchone():raise ProbeError('buffer_retry_after_active')
    values=observations(db,key,now) if key else None
    if needed>1 and values is None:raise ProbeError('buffer_quota_verification_required')
    held=outstanding(db,now,exclude)
    for window,maximum in limits(db,key,now):
        used=db.execute('SELECT count(*) FROM buffer_api_usage WHERE requested>=?',(now-window,)).fetchone()[0]
        if probe and window==86400:maximum=241
        if values:
            observation=next(r for r in values if r['window']==window);maximum=min(maximum,int(observation['quota']*.9))
            later=db.execute('SELECT count(*) FROM buffer_api_usage WHERE id>?',(observation['request_id'],)).fetchone()[0]
            if observation['remaining']-later<needed+held+max(5,reserve_normal):raise ProbeError('buffer_remote_quota_stop')
        if used+held+needed+reserve_normal>maximum:raise ProbeError('buffer_local_quota_stop')

def observe(store,headers,key,request_id):
    """順序/結合に依存せずq/r/tを抽出。pkやヘッダー原文は保持しない。"""
    def parse(name,fields):
        out={}
        for line in headers.get_all(name,[]):
            for part in re.split(r',\s*(?=")',line):
                label=re.match(r'\s*"([^"]+)"',part)
                if not label:continue
                attrs={k:int(v) for k,v in re.findall(r';\s*([qrtw])\s*=\s*(\d+)',part)}
                if fields<=set(attrs):
                    if label[1] in out and out[label[1]]!=attrs:raise ProbeError('buffer_rate_headers_invalid')
                    out[label[1]]=attrs
        return out
    def invalidate():
        with store.transaction() as db:
            setup(db);db.execute('UPDATE buffer_rate_observations SET expires=0 WHERE subject=?',(key,))
    retry=headers.get('Retry-After')
    if retry is not None:
        if not re.fullmatch(r'\d+',retry.strip()):invalidate();return False
        with store.transaction() as db:
            setup(db);db.execute('INSERT OR REPLACE INTO buffer_retry_deadlines VALUES(?,?,?)',(key,time.time()+int(retry),request_id))
    try:policies=parse('RateLimit-Policy',{'q','w'});remaining=parse('RateLimit',{'r','t'})
    except ProbeError:invalidate();raise
    now=time.time();result=[]
    for label,p in policies.items():
        r=remaining.get(label)
        if p['w'] not in WINDOWS:continue
        if not r or not 0<=r['r']<=p['q'] or p['q']<=0 or not 0<r['t']<=p['w']:invalidate();return False
        result.append((key,p['w'],p['q'],r['r'],now+r['t'],now,request_id))
    if {r[1] for r in result}!=set(WINDOWS) or len(result)!=3:invalidate();return False
    with store.transaction() as db:
        setup(db)
        for row in result:
            prior=db.execute('SELECT request_id FROM buffer_rate_observations WHERE subject=? AND window=?',(key,row[1])).fetchone()
            if not prior or prior['request_id']<=request_id:db.execute('INSERT OR REPLACE INTO buffer_rate_observations VALUES(?,?,?,?,?,?,?)',row)
    return True

def grant_probe(store,grant_id,key,target,deadline,organization_id):
    if os.name!='posix' or os.geteuid()!=0:raise ProbeError('buffer_probe_root_authorization_required')
    from .clients import MealClient
    MealClient.request_key(grant_id)
    if target!='2026-10-29' or not time.time()<deadline<=__import__('datetime').datetime.fromisoformat('2026-10-29T06:00:00+00:00').timestamp():raise ProbeError('buffer_probe_authorization_invalid')
    with store.transaction() as db:
        if not isinstance(organization_id,str) or not organization_id:raise ProbeError('buffer_probe_organization_required')
        setup(db);db.execute('INSERT INTO buffer_quota_probes VALUES(?,?,?,?,?,NULL,?)',(grant_id,key,target,deadline,'reserved',subject(organization_id)))

def run_probe(store,api,grant_id):
    key=subject(api._token)
    with store.transaction() as db:
        row=db.execute('SELECT * FROM buffer_quota_probes WHERE grant_id=?',(grant_id,)).fetchone()
        if not row or row['subject']!=key or row['state']!='reserved' or row['deadline']<=time.time():raise ProbeError('buffer_probe_unavailable')
    token=PROBE.set(grant_id)
    try:
        result=api._request(ORGANIZATIONS)
        orgs=result.get('account',{}).get('organizations',[])
        if not isinstance(orgs,list) or sum(isinstance(o,dict) and isinstance(o.get('id'),str) and subject(o['id'])==row['organization_digest'] for o in orgs)!=1:raise ProbeError('buffer_probe_organization_mismatch')
        with store.transaction() as db:
            values=observations(db,key,time.time(),raw=True)
            request_id=db.execute('SELECT request_id FROM buffer_quota_probes WHERE grant_id=?',(grant_id,)).fetchone()[0]
            if values is None or any(r['request_id']!=request_id for r in values):raise ProbeError('buffer_quota_verification_required')
            db.execute("UPDATE buffer_quota_probes SET state='complete' WHERE grant_id=? AND state='started'",(grant_id,))
        return [{'window':r['window'],'quota':r['quota'],'remaining':r['remaining']} for r in values]
    except BaseException as error:
        state='failed' if isinstance(error,ProbeError) and str(error)!='buffer_response_unknown' else 'unknown'
        with store.transaction() as db:db.execute("UPDATE buffer_quota_probes SET state=? WHERE grant_id=? AND state='started'",(state,grant_id))
        raise
    finally:PROBE.reset(token)

def reserve(store,budget_id,key,target,units,deadline):
    from .clients import MealClient
    MealClient.request_key(budget_id)
    if target!='2026-10-29' or type(units) is not int or not 1<=units<=78 or not time.time()<deadline<=__import__('datetime').datetime.fromisoformat('2026-10-29T06:00:00+00:00').timestamp():raise ProbeError('buffer_budget_invalid')
    with store.transaction() as db:
        setup(db)
        if not db.execute("SELECT 1 FROM buffer_quota_probes WHERE subject=? AND target_date=? AND state='complete' AND deadline>?",(key,target,time.time())).fetchone():raise ProbeError('buffer_probe_unconfirmed')
        check(db,key,time.time(),units,8)
        db.execute('INSERT INTO buffer_request_reservations VALUES(?,?,?,?,?,?,?)',(budget_id,key,target,units,units,deadline,'active'))

@contextmanager
def using(budget_id):
    token=RESERVATION.set(budget_id)
    try:yield
    finally:RESERVATION.reset(token)

def release(store,budget_id):
    # remainingは送信claim前の未使用分だけ。送信済み/unknownのusageは維持する。
    with store.transaction() as db:
        table,row=reservation(db,budget_id)
        if row:db.execute('UPDATE '+table+" SET remaining=0,state='closed' WHERE budget_id=? AND state='active'",(budget_id,))

def continuation_refresh_check(db,key,now):
    """新承認の固定読取一回だけ。長窓の実残量と全ローカル消費を維持する。"""
    if db.execute('SELECT 1 FROM buffer_retry_deadlines WHERE subject=? AND until>?',(key,now)).fetchone():raise ProbeError('buffer_retry_after_active')
    if not db.execute("SELECT 1 FROM buffer_quota_probes WHERE subject=? AND state='complete'",(key,)).fetchone():raise ProbeError('buffer_probe_unconfirmed')
    for window,maximum in ((900,90),(86400,540),(2592000,2900)):
        used=db.execute('SELECT count(*) FROM buffer_api_usage WHERE requested>=?',(now-window,)).fetchone()[0]
        if window!=900:
            row=db.execute('SELECT * FROM buffer_rate_observations WHERE subject=? AND window=?',(key,window)).fetchone()
            if not row or row['expires']<=now or row['observed']>now:raise ProbeError('buffer_quota_verification_required')
            later=db.execute('SELECT count(*) FROM buffer_api_usage WHERE id>?',(row['request_id'],)).fetchone()[0]
            if row['remaining']-later<=8:raise ProbeError('buffer_remote_quota_stop')
            maximum=min(maximum,int(row['quota']*.9))
        if used+outstanding(db,now)+1+8>maximum:raise ProbeError('buffer_local_quota_stop')

def continuation_reserve(store,budget_id,key,units,deadline):
    """旧予約を変更せず、新承認の15:30までの予約だけを追記する。"""
    from .clients import MealClient
    MealClient.request_key(budget_id)
    if type(units) is not int or not 1<=units<=78 or not time.time()<deadline<=__import__('datetime').datetime.fromisoformat('2026-10-29T06:30:00+00:00').timestamp():raise ProbeError('buffer_budget_invalid')
    with store.transaction() as db:
        setup(db);check(db,key,time.time(),units,8)
        if db.execute('SELECT 1 FROM buffer_continuation_reservations').fetchone():raise ProbeError('buffer_continuation_reissue_forbidden')
        db.execute('INSERT INTO buffer_continuation_reservations VALUES(?,?,?,?,?,?,?)',(budget_id,key,'2026-10-29',units,units,deadline,'active'))

def grant_continuation_refresh(store,grant_id,key,deadline):
    from .clients import MealClient
    MealClient.request_key(grant_id)
    if os.name!='posix' or os.geteuid()!=0 or not time.time()<deadline<=__import__('datetime').datetime.fromisoformat('2026-10-29T06:30:00+00:00').timestamp():raise ProbeError('buffer_refresh_authorization_invalid')
    with store.transaction() as db:
        setup(db)
        if db.execute('SELECT 1 FROM buffer_continuation_refresh').fetchone():raise ProbeError('buffer_refresh_reissue_forbidden')
        continuation_refresh_check(db,key,time.time())
        db.execute('INSERT INTO buffer_continuation_refresh VALUES(?,?,?,?,NULL)',(grant_id,key,deadline,'reserved'))

def run_continuation_refresh(store,api,grant_id):
    key=subject(api._token);token=REFRESH.set(grant_id)
    try:
        result=api._request(ORGANIZATIONS)
        with store.transaction() as db:
            expected=db.execute("SELECT organization_digest FROM buffer_quota_probes WHERE subject=? AND state='complete'",(key,)).fetchone()
            orgs=result.get('account',{}).get('organizations',[])
            if not expected or sum(isinstance(o,dict) and isinstance(o.get('id'),str) and subject(o['id'])==expected[0] for o in orgs)!=1:raise ProbeError('buffer_probe_organization_mismatch')
            values=observations(db,key,time.time(),raw=True)
            row=db.execute('SELECT * FROM buffer_continuation_refresh WHERE grant_id=?',(grant_id,)).fetchone()
            if not row or row['state']!='started' or values is None or any(r['request_id']!=row['request_id'] for r in values):raise ProbeError('buffer_quota_verification_required')
            db.execute("UPDATE buffer_continuation_refresh SET state='complete' WHERE grant_id=?",(grant_id,))
        return [{'window':r['window'],'quota':r['quota'],'remaining':r['remaining']} for r in values]
    except BaseException:
        with store.transaction() as db:db.execute("UPDATE buffer_continuation_refresh SET state='unknown' WHERE grant_id=? AND state='started'",(grant_id,))
        raise
    finally:REFRESH.reset(token)
