"""通常日次に限る実上限の再確認。古い残量を現在残量へ転用しない。"""
import math,time,uuid
import json
from .buffer_trial import ProbeError


def check_refresh(db,key,now):
    from .buffer_budget import outstanding,WINDOWS
    if db.execute('SELECT 1 FROM buffer_retry_deadlines WHERE subject=? AND until>?',(key,now)).fetchone():raise ProbeError('buffer_retry_after_active')
    if not db.execute("SELECT 1 FROM buffer_quota_probes WHERE subject=? AND state='complete'",(key,)).fetchone():raise ProbeError('buffer_probe_unconfirmed')
    rows={r['window']:r for r in db.execute('SELECT * FROM buffer_rate_observations WHERE subject=?',(key,))}
    if set(rows)!=set(WINDOWS) or rows[86400]['quota']<600:raise ProbeError('buffer_quota_verification_required')
    for window,cap in ((900,90),(86400,540),(2592000,2900)):
        r=rows[window]
        if r['observed']>now:raise ProbeError('buffer_quota_verification_required')
        used=db.execute('SELECT count(*) FROM buffer_api_usage WHERE requested>=?',(now-window,)).fetchone()[0]
        if used+outstanding(db,now)+9>min(cap,int(r['quota']*.9)):raise ProbeError('buffer_local_quota_stop')
        # 未失効の長窓では既知の実残量も守る。失効した残量は補完しない。
        if r['expires']>now:
            later=db.execute('SELECT count(*) FROM buffer_api_usage WHERE id>?',(r['request_id'],)).fetchone()[0]
            if r['remaining']-later<=outstanding(db,now)+8:raise ProbeError('buffer_remote_quota_stop')


def maybe_refresh(api,store,token):
    from .buffer_budget import REFRESH,PROBE,RESERVATION,setup,subject,observations,run_continuation_refresh
    from .routine_recovery import CURRENT,admitted
    if store is None or REFRESH.get() or PROBE.get() or RESERVATION.get():return
    run=CURRENT.get();target=run.target if run else getattr(api,'publication_target',None)
    if not target:return
    now=time.time();key=subject(token)
    if run:run.check()
    with store.transaction() as db:
        policy=admitted(db,target)
        if not policy:return
        if run is None:
            claim=getattr(api,'routine_publication_claim',None)
            if not isinstance(claim,tuple) or len(claim)!=2 or claim[0] not in ('x','instagram'):return
            platform,expected=claim;table='candidates' if platform=='x' else 'instagram_candidates'
            from .publication_hold import require_clear
            from .domain import Candidate
            from .instagram_domain import InstagramCandidate
            require_clear(db,target)
            row=db.execute('SELECT * FROM '+table+' WHERE target_date=? AND slot=?',(target,expected['slot'])).fetchone()
            if not row or row['state']!='dispatching' or not row['actor'] or row['approved'] is None or (row['target_date'],row['revision'],row['digest'])!=(expected['target_date'],expected['revision'],expected['digest']):raise ProbeError('buffer_routine_claim_changed')
            if (Candidate if platform=='x' else InstagramCandidate)(**json.loads(row['payload'])).digest!=row['digest']:raise ProbeError('buffer_routine_claim_changed')
        setup(db)
        if observations(db,key,now) is not None:return
        db.execute('CREATE TABLE IF NOT EXISTS buffer_routine_refresh(grant_id TEXT PRIMARY KEY,subject TEXT NOT NULL,deadline REAL NOT NULL,state TEXT NOT NULL,request_id INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS routine_buffer_refresh_claims(subject TEXT,bucket INTEGER,grant_id TEXT UNIQUE,target_date TEXT,policy_id TEXT,PRIMARY KEY(subject,bucket))')
        check_refresh(db,key,now)
        bucket=math.floor(now/900)
        if db.execute('SELECT 1 FROM routine_buffer_refresh_claims WHERE subject=? AND bucket=?',(key,bucket)).fetchone():raise ProbeError('buffer_refresh_reconciliation_required')
        # 過去の不明再確認を、新バケット/新日付で迂回しない。
        if db.execute("SELECT 1 FROM buffer_routine_refresh WHERE subject=? AND state IN ('reserved','started','unknown')",(key,)).fetchone():raise ProbeError('buffer_refresh_reconciliation_required')
        grant=str(uuid.uuid4());deadline=min(now+60,run.deadline.timestamp() if run else now+60)
        db.execute('INSERT INTO routine_buffer_refresh_claims VALUES(?,?,?,?,?)',(key,bucket,grant,target,policy['policy_id']))
        db.execute("INSERT INTO buffer_routine_refresh VALUES(?,?,?,'reserved',NULL)",(grant,key,deadline))
    run_continuation_refresh(store,api,grant,'buffer_routine_refresh')
