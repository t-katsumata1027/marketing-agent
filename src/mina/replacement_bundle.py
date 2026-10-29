"""停止中の新版だけを承認し、最後の判断と日停止解除を同じtxで行う。"""
import json
from .replacement_contract import digest
from .publication_hold import exists,require_clear

TABLES=('candidates','instagram_candidates','buffer_publication','notices','instagram_notices')
CHECKS={'current_state','uuid_results','original_bytes','ogp_bytes','independent_review'}

def proof_valid(proof,source_digest,now):
    from .replacement_client import timestamp
    try:
        return (proof['verdict']=='pass' and proof['source_digest']==source_digest and set(proof['checks'])==CHECKS
                and proof['verification']=='current-source-uuid-ogp-v1' and 0<=(now-timestamp(proof['read_started_at'])).total_seconds()<=120)
    except (KeyError,TypeError,ValueError):return False

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS replacement_archives (
        id INTEGER PRIMARY KEY,target_date TEXT NOT NULL,table_name TEXT NOT NULL,snapshot TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS replacement_bundles (
        target_date TEXT PRIMARY KEY,owner TEXT NOT NULL,epoch INTEGER NOT NULL,digest TEXT NOT NULL,
        source_digest TEXT NOT NULL,required TEXT NOT NULL,proof TEXT NOT NULL,state TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS replacement_bundle_members (
        target_date TEXT NOT NULL,channel TEXT NOT NULL,slot TEXT NOT NULL,revision INTEGER NOT NULL,
        digest TEXT NOT NULL,PRIMARY KEY(target_date,channel,slot))''')

def archive(db,target,table):
    if exists(db,table):
        rows=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE target_date=?',(target,))]
        db.execute('INSERT INTO replacement_archives(target_date,table_name,snapshot) VALUES(?,?,?)',(target,table,json.dumps(rows,ensure_ascii=False)))

def invalidate_saved(db,target):
    initialize(db)
    for table in TABLES:archive(db,target,table)
    for table in ('candidates','instagram_candidates'):
        if exists(db,table):db.execute("UPDATE "+table+" SET state='changes_requested',nonce_hash=NULL,actor=NULL,approved=NULL,reason='replacement_review' WHERE target_date=?",(target,))
    for table in ('notices','instagram_notices'):
        if exists(db,table):db.execute("UPDATE "+table+" SET state='obsolete' WHERE target_date=? AND state='pending'",(target,))

def approval_allowed(db,target,channel,slot=None,revision=None,candidate_digest=None):
    """通常入口は停止を守り、新版の指定メンバーだけに限定許可する。"""
    from .next_saved import blocked_handoff
    if blocked_handoff(db,target):
        from .publication_hold import PublicationHeld
        raise PublicationHeld()
    from .post_late import approval_allowed as late
    if late(db,target,channel,slot,revision,candidate_digest):return
    from .publication_hold import held,PublicationHeld
    if not held(db,target):return
    if not exists(db,'replacement_bundles'):raise PublicationHeld()
    b=db.execute("SELECT * FROM replacement_bundles WHERE target_date=? AND state='approval_pending'",(target,)).fetchone()
    h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(target,)).fetchone()
    if not b or not h or h['state']!='saved_pending_review' or (b['owner'],b['epoch'])!=(h['owner'],h['epoch']):raise PublicationHeld()
    rows=list(db.execute('SELECT * FROM replacement_bundle_members WHERE target_date=? AND channel=?',(target,channel)))
    if not rows:raise PublicationHeld()
    if slot is not None and not any(r['slot']==slot and (revision is None or r['revision']==revision) and (candidate_digest is None or r['digest']==candidate_digest) for r in rows):raise PublicationHeld()

def notice_allowed(db,row,channel,*,scoped_operator=False):
    from .next_saved import blocked_handoff
    if blocked_handoff(db,row['target_date'],notice=True):return False
    from .post_late import notice_ready
    late=notice_ready(db,row,channel)
    if late is not None:return late
    try:
        from .scoped_preparation import locked
        # 準備専用日は、期限を検査する限定operatorだけが承認カードを送る。
        # 常駐daemonに渡すと、operator停止・期限後にもpendingを送信し得る。
        from .post_promotion import notice_ready
        if locked(db,row['target_date']) and not scoped_operator and not notice_ready(db,row['target_date']):return False
        table='candidates' if channel=='x' else 'instagram_candidates'
        candidate=db.execute('SELECT * FROM '+table+' WHERE target_date=? AND slot=?',(row['target_date'],row['slot'])).fetchone()
        if not candidate or candidate['state']!='approval_pending' or candidate['revision']!=row['revision']:return False
        approval_allowed(db,row['target_date'],channel,row['slot'],row['revision'],candidate['digest'])
        return True
    except ValueError:
        return False

def maybe_release(db,target,now):
    from .next_saved import blocked_handoff
    if blocked_handoff(db,target,release=True):return
    from .post_late import maybe_release as late
    if late(db,target,now):return
    from .post_promotion import maybe_release as promoted_release
    if promoted_release(db,target,now):return
    from .scoped_preparation import locked
    if locked(db,target):return
    if not exists(db,'replacement_bundles'):return
    b=db.execute("SELECT * FROM replacement_bundles WHERE target_date=? AND state='approval_pending'",(target,)).fetchone()
    if not b:return
    proof=json.loads(b['proof'])
    if not proof_valid(proof,b['source_digest'],now):return
    members=list(db.execute('SELECT * FROM replacement_bundle_members WHERE target_date=? ORDER BY channel,slot',(target,)))
    binding=[(m['channel'],m['slot'],m['revision'],m['digest']) for m in members]
    if not members or digest(binding)!=b['digest'] or [list(v) for v in binding]!=json.loads(b['required']):return
    for member in members:
        table='candidates' if member['channel']=='x' else 'instagram_candidates'
        r=db.execute('SELECT * FROM '+table+' WHERE target_date=? AND slot=?',(target,member['slot'])).fetchone()
        if not r or (r['revision'],r['digest'])!=(member['revision'],member['digest']) or r['state'] not in ('approved','cancelled') or not r['actor'] or r['scheduled']<=now.timestamp():return
        from .domain import Candidate
        from .instagram_domain import InstagramCandidate
        payload=json.loads(r['payload']);actual=(Candidate if member['channel']=='x' else InstagramCandidate)(**payload).digest
        from .scoped_preparation import blocked
        if blocked(payload):return
        if actual!=r['digest'] or (payload.get('approval_source') if member['channel']=='x' else payload.get('source',{})).get('state_version')!=proof['state_version']:return
    h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(target,)).fetchone()
    if not h or (h['owner'],h['epoch'],h['state'])!=(b['owner'],b['epoch'],'saved_pending_review'):return
    db.execute("UPDATE publication_holds SET state='released',reason='new_bundle_approved' WHERE target_date=?",(target,))
    db.execute("UPDATE replacement_bundles SET state='released' WHERE target_date=?",(target,))

def stage(store,target,owner,epoch,source_digest,x_candidates,ig_candidates,host,hosts,now,proof,required_channels=('x','instagram'),checkpoint=None,preparation_window=None):
    """検証済み候補だけを通常型で保持し、送信履歴のあるrouteを再利用しない。"""
    from dataclasses import asdict
    from .replacement_client import timestamp
    if not proof_valid(proof,source_digest,now):
        raise ValueError('replacement_bundle_source_unverified')
    if {c.slot for c in x_candidates}!={'breakfast','lunch','dinner','daily'}:raise ValueError('replacement_bundle_incomplete')
    if set(required_channels) not in ({'x'},{'x','instagram'}):raise ValueError('replacement_bundle_channels_invalid')
    if ('instagram' in required_channels)!=bool(ig_candidates) or (ig_candidates and {c.slot for c in ig_candidates}!={'breakfast','lunch','dinner','daily'}):raise ValueError('replacement_bundle_incomplete')
    versions={c.approval_source['state_version'] for c in x_candidates if c.approval_source}
    if versions!={proof['state_version']} or any(not c.approval_source for c in x_candidates):raise ValueError('replacement_bundle_wrong_source')
    for c in ig_candidates:
        if c.source['state_version'] not in versions or c.source['x_digests']!={x.slot:x.digest for x in x_candidates}:raise ValueError('replacement_bundle_wrong_source')
    for c in x_candidates:c.validate(host,now)
    if any((c.approval_source or {}).get('publish_after_approval_until') for c in x_candidates):raise ValueError('replacement_late_publication_not_authorized')
    for c in ig_candidates:c.validate(now,hosts)
    all_candidates=[('x',c) for c in x_candidates]+[('instagram',c) for c in ig_candidates]
    if len({(ch,c.slot) for ch,c in all_candidates})!=len(all_candidates):raise ValueError('replacement_bundle_duplicate')
    with store.transaction() as db:
        if checkpoint is not None:checkpoint(db)
        initialize(db);h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(target,)).fetchone()
        if not h or (h['owner'],h['epoch'],h['state'])!=(owner,epoch,'saved_pending_review'):raise ValueError('replacement_hold_changed')
        from .scoped_preparation import blocked,seal
        marked=any(blocked({'approval_source':c.approval_source}) for c in x_candidates) or any(blocked({'source':c.source}) for c in ig_candidates)
        if marked != (preparation_window is not None):raise ValueError('scoped_preparation_marker_invalid')
        if preparation_window is not None:seal(db,preparation_window,owner,epoch,x_candidates,ig_candidates,now)
        if db.execute('SELECT 1 FROM replacement_bundles WHERE target_date=?',(target,)).fetchone():raise ValueError('replacement_bundle_already_staged')
        if exists(db,'instagram_attempts') and db.execute('SELECT 1 FROM instagram_attempts WHERE target_date=?',(target,)).fetchone():raise ValueError('external_dispatch_present')
        if db.execute("SELECT 1 FROM buffer_publication WHERE target_date=? AND (state<>'armed' OR request_json IS NOT NULL OR post_id IS NOT NULL)",(target,)).fetchone():raise ValueError('external_dispatch_present')
        archive(db,target,'buffer_publication');db.execute('DELETE FROM buffer_publication WHERE target_date=?',(target,))
        members=[]
        for ch,c in all_candidates:
            if c.target_date!=target:raise ValueError('replacement_bundle_wrong_day')
            table='candidates' if ch=='x' else 'instagram_candidates'
            archive(db,target,table)
            old=db.execute('SELECT revision FROM '+table+' WHERE target_date=? AND slot=?',(target,c.slot)).fetchone();rev=old['revision']+1 if old else 1
            db.execute('DELETE FROM '+table+' WHERE target_date=? AND slot=?',(target,c.slot))
            db.execute('INSERT INTO '+table+'(target_date,slot,revision,payload,digest,scheduled,state) VALUES(?,?,?,?,?,?,?)',
                       (target,c.slot,rev,json.dumps(asdict(c),ensure_ascii=False),c.digest,c.scheduled_at.timestamp(),'approval_pending'))
            db.execute('INSERT INTO replacement_bundle_members VALUES(?,?,?,?,?)',(target,ch,c.slot,rev,c.digest));members.append((ch,c.slot,rev,c.digest))
        members.sort()
        db.execute('INSERT INTO replacement_bundles VALUES(?,?,?,?,?,?,?,?)',(target,owner,epoch,digest(members),source_digest,json.dumps(members),json.dumps(proof),'approval_pending'))
        if checkpoint is not None:checkpoint(db)

def refresh(store,target,source_digest,proof,now):
    """承認が遅れた場合も、新たな正本照合証跡がないまま停止解除しない。"""
    from .replacement_client import timestamp
    if not proof_valid(proof,source_digest,now):raise ValueError('replacement_bundle_source_unverified')
    with store.transaction() as db:
        if db.execute('UPDATE replacement_bundles SET proof=? WHERE target_date=? AND source_digest=? AND state=?',(json.dumps(proof),target,source_digest,'approval_pending')).rowcount!=1:raise ValueError('replacement_bundle_changed')
        maybe_release(db,target,now)
