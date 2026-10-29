"""朝までの別準備権限。旧停止を残し、食事六件と日次二件を別々に承認する。"""
import hashlib,json,uuid
from datetime import UTC,datetime
from pathlib import Path
from .preparation import fingerprint
from .publication_hold import exists
from .next_saved import read_rows

TARGET='2026-10-30';KEY='morning_preparation';VERSION='mina-morning-preparation-v1'
MEALS=('breakfast','lunch','dinner');COHORTS={'meals':MEALS,'daily':('daily',)}

def marker(key,slot):return {'contract_version':VERSION,'case_uuid':key,'target_date':TARGET,'cohort':'daily' if slot=='daily' else 'meals'}

def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS morning_preparations(target_date TEXT PRIMARY KEY,case_uuid TEXT UNIQUE,commit_sha TEXT,config_path TEXT,config_sha256 TEXT,origin TEXT,phase TEXT,first_started REAL,deadline REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS morning_cohorts(case_uuid TEXT,cohort TEXT,state TEXT,material TEXT,PRIMARY KEY(case_uuid,cohort))')
    db.execute('CREATE TABLE IF NOT EXISTS morning_members(case_uuid TEXT,platform TEXT,slot TEXT,cohort TEXT,revision INTEGER,digest TEXT,payload_digest TEXT,PRIMARY KEY(case_uuid,platform,slot))')
    db.execute('CREATE TABLE IF NOT EXISTS morning_events(id INTEGER PRIMARY KEY,case_uuid TEXT,cohort TEXT,stage TEXT,event TEXT,details TEXT,created REAL)')

def job(db,target):return db.execute('SELECT * FROM morning_preparations WHERE target_date=?',(target,)).fetchone() if target==TARGET and exists(db,'morning_preparations') else None

def event(store,key,cohort,stage,kind,details):
    with store.transaction() as db:
        initialize(db);db.execute('INSERT INTO morning_events(case_uuid,cohort,stage,event,details,created) VALUES(?,?,?,?,?,?)',(key,cohort,stage,kind,json.dumps(details,ensure_ascii=False),datetime.now(UTC).timestamp()))

def intact(db,r):
    origin=json.loads(r['origin']);h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone()
    if not h or dict(h)!=origin['hold']:return False
    for table in ('next_saved_jobs','next_meal_jobs'):
        row=db.execute('SELECT * FROM '+table+' WHERE target_date=?',(TARGET,)).fetchone()
        if not row or row['case_uuid']!=origin['parents'][table]['case_uuid'] or row['state']!='held' or row['first_started']!=origin['parents'][table]['first_started'] or row['deadline']!=origin['parents'][table]['deadline']:return False
        if fingerprint(dict(row))!=origin['parents'][table]['record_digest']:return False
    for table in ('mina_requests','preparation_steps'):
        if fingerprint(read_rows(db,table))!=origin[table+'_digest']:return False
    locks=read_rows(db,'scoped_preparation_locks') if exists(db,'scoped_preparation_locks') else None
    if locks!=origin['scoped_preparation_locks']:return False
    for path,sha in origin['files'].items():
        p=Path(path)
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:return False
    return True

def members(db,r,cohort,decided=False):
    if cohort not in COHORTS or not intact(db,r):return None
    rows=list(db.execute('SELECT * FROM morning_members WHERE case_uuid=? AND cohort=?',(r['case_uuid'],cohort)))
    if {(v['platform'],v['slot']) for v in rows}!={(platform,s) for platform in ('x','instagram') for s in COHORTS[cohort]} or len(rows)!=len(COHORTS[cohort])*2:return None
    values=[]
    for v in rows:
        table='candidates' if v['platform']=='x' else 'instagram_candidates';c=db.execute('SELECT * FROM '+table+' WHERE target_date=? AND slot=?',(TARGET,v['slot'])).fetchone()
        if not c or c['revision']!=v['revision'] or c['digest']!=v['digest'] or fingerprint(json.loads(c['payload']))!=v['payload_digest']:return None
        from .domain import Candidate
        from .instagram_domain import InstagramCandidate
        candidate=(Candidate if v['platform']=='x' else InstagramCandidate)(**json.loads(c['payload']));source=candidate.approval_source if v['platform']=='x' else candidate.source
        if candidate.digest!=c['digest'] or source.get(KEY)!=marker(r['case_uuid'],v['slot']):return None
        states=('approved','expired','dispatching','accepted','published') if decided else ('approval_pending','changes_requested','approved','expired','cancelled','dispatching','accepted','published')
        if c['state'] not in states or (decided and (not c['actor'] or c['approved'] is None)):return None
        if c['state']=='expired' and (not c['actor'] or c['approved'] is None or c['approved']>=candidate.scheduled_at.timestamp() or c['scheduled']!=candidate.scheduled_at.timestamp() or c['nonce_hash'] is not None):return None
        attempts='buffer_publication' if v['platform']=='x' else 'instagram_attempts'
        if exists(db,attempts):
            a=db.execute('SELECT * FROM '+attempts+' WHERE target_date=? AND slot=?',(TARGET,v['slot'])).fetchone()
            allowed=('armed','attempting','accepted','sent') if v['platform']=='x' else ('attempting','accepted','published')
            if a and (a['state'] not in allowed or a['revision']!=c['revision'] or a['digest']!=c['digest']):return None
            if c['state']=='expired' and a and (v['platform']!='x' or a['state']!='armed'):return None
        values.append((dict(v),dict(c)))
    return values

def receipts(db,r,cohort):
    if not exists(db,'routine_notice_receipts'):return False
    rows=list(db.execute('SELECT * FROM routine_notice_receipts WHERE target_date=? AND policy_id=?',(TARGET,r['case_uuid'])))
    from .routine_handoff import buttons,visible
    expected={(m['platform'],m['slot']):dict(m) for m in db.execute('SELECT * FROM morning_members WHERE case_uuid=? AND cohort=?',(r['case_uuid'],cohort))}
    if set(expected)!={(platform,s) for platform in ('x','instagram') for s in COHORTS[cohort]}:return False
    if len({v['message_id'] for v in rows if v['message_id']})!=sum(bool(v['message_id']) for v in rows):return False
    table_rows=[];seen=set();origin=json.loads(r['origin'])
    for row in rows:
        table='notices' if row['platform']=='x' else 'instagram_notices';notice=db.execute('SELECT * FROM '+table+' WHERE id=?',(row['notice_id'],)).fetchone()
        if not notice or notice['slot'] not in COHORTS[cohort]:continue
        from .notice_reissue import historical_receipt
        if historical_receipt(db,r,row,notice):continue
        key=(row['platform'],notice['slot']);member=expected.get(key)
        if not member or key in seen or notice['target_date']!=TARGET or notice['revision']!=member['revision'] or notice['kind']!='approval':return False
        seen.add(key);table_rows.append((row,notice))
        try:
            wire=json.loads(row['payload']);actual=json.loads(row['receipt']);original={k:v for k,v in wire.items() if k not in ('nonce','enforce_nonce')}
            if (row['state']!='sent' or notice['state']!='sent' or not row['message_id'] or actual['message_id']!=row['message_id']
                or row['channel']!=origin['discord_channel'] or not row['author'] or original!=json.loads(notice['payload'])
                or fingerprint(wire)!=row['payload_digest'] or fingerprint(buttons(wire))!=row['token_binding']
                or wire['nonce']!=row['nonce'] or actual['token_binding']!=row['token_binding']
                or actual['visible_digest']!=fingerprint(visible(wire)) or actual['verification'] not in ('same_id_get','complete_history_get')):return False
        except (KeyError,TypeError,ValueError):return False
    return len(table_rows)==len(COHORTS[cohort])*2 and set(expected)==seen

def approval(db,target,platform,slot=None,revision=None,digest=None):
    r=job(db,target)
    if not r:return False
    for cohort in COHORTS:
        row=db.execute('SELECT state FROM morning_cohorts WHERE case_uuid=? AND cohort=?',(r['case_uuid'],cohort)).fetchone()
        if not row or row['state'] not in ('staged','notified','ready'):continue
        values=members(db,r,cohort)
        if values is not None and any(v['platform']==platform and (slot is None or v['slot']==slot and (revision is None or v['revision']==revision) and (digest is None or v['digest']==digest)) for v,c in values):return True
    return False

def notice(db,row,platform):
    r=job(db,row['target_date'])
    from .notice_reissue import fresh_notice
    special=fresh_notice(db,r,row,platform) if r else None
    return bool(r and (special if special is not None else datetime.now(UTC).timestamp()<r['deadline']) and approval(db,row['target_date'],platform,row['slot'],row['revision']))

def maybe_ready(db,target):
    r=job(db,target)
    if not r:return False
    for cohort in COHORTS:
        row=db.execute('SELECT state FROM morning_cohorts WHERE case_uuid=? AND cohort=?',(r['case_uuid'],cohort)).fetchone()
        if row and row['state'] in ('staged','notified') and receipts(db,r,cohort) and members(db,r,cohort,True) is not None:
            db.execute("UPDATE morning_cohorts SET state='ready' WHERE case_uuid=? AND cohort=? AND state IN ('staged','notified')",(r['case_uuid'],cohort))
    return True

def publication_allowed(db,target,publication):
    r=job(db,target)
    if not r or not isinstance(publication,tuple) or len(publication)!=2 or publication[0] not in ('x','instagram'):return False
    platform,expected=publication
    if not isinstance(expected,dict) or expected.get('target_date')!=target or expected.get('slot') not in (*MEALS,'daily'):return False
    cohort='daily' if expected['slot']=='daily' else 'meals';c=db.execute('SELECT state FROM morning_cohorts WHERE case_uuid=? AND cohort=?',(r['case_uuid'],cohort)).fetchone()
    if not c or c['state']!='ready' or not receipts(db,r,cohort):return False
    values=members(db,r,cohort,True)
    return values is not None and any(v['platform']==platform and v['slot']==expected['slot'] and row['state'] in ('approved','dispatching') and all(row[k]==expected.get(k) for k in ('revision','digest','payload','actor','approved')) for v,row in values)

def handoff_policy(db,target):
    r=job(db,target)
    return {'policy_id':r['case_uuid']} if r and any(members(db,r,c) is not None for c in COHORTS) else None

def valid_ig_source(c):
    m=c.source[KEY];uuid.UUID(m['case_uuid'])
    if c.target_date!=TARGET or m!=marker(m['case_uuid'],c.slot):raise ValueError('朝まで準備のInstagram識別子が不正です')
    expected=set(MEALS) if c.slot in MEALS else {*MEALS,'daily'}
    if set(c.source['x_digests'])!=expected:raise ValueError('Instagramと同じ投稿正本の対応が不足しています')

def material(db,r,cohort):
    row=db.execute('SELECT material FROM morning_cohorts WHERE case_uuid=? AND cohort=?',(r['case_uuid'],cohort)).fetchone()
    if not row or not row['material']:raise ValueError('朝まで準備の画像正本がありません')
    return json.loads(row['material'])

def verify_ig(runtime,candidate):
    """IG固有の実画像を検査し、共有OGPの不良をIG合格へ読み替えない。"""
    from dataclasses import asdict
    from .morning_sources import read_current
    from .instagram_runtime import caption
    from .instagram_media import verify_public
    from .domain import Candidate
    valid_ig_source(candidate);cohort='daily' if candidate.slot=='daily' else 'meals'
    with runtime.store.transaction() as db:
        r=job(db,TARGET)
        if not r or members(db,r,cohort,True) is None:raise ValueError('Instagramの承認正本が変わりました')
        value=material(db,r,cohort)
        if value['igs'][candidate.slot]!=asdict(candidate):raise ValueError('Instagramの画像版が変わりました')
    bundle=value['bundle'];manifest=value['manifest']
    if candidate.caption!=caption(bundle,candidate.slot) or [a['sha256'] for a in candidate.assets]!=[a['sha256'] for a in manifest['assets'][candidate.slot]]:raise ValueError('Instagramの本文・画像が変わりました')
    from .current_day_cli import scoped_meal
    current=read_current(Candidate(**value['xs'][candidate.slot]),scoped_meal(),'www.log-eats.com',bundle=bundle)
    if candidate.slot=='daily' and current['state']['nutrition_targets']!=bundle['targets']:raise ValueError('目標達成率の表示原本が変わりました')
    for asset in candidate.assets:
        p=Path(asset['path'])
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=asset['sha256']:raise ValueError('Instagram画像の実原本が変わりました')
        verify_public(asset)
    from .m1_contract import validate_state
    final=scoped_meal().day_state(TARGET)
    if validate_state(final,TARGET,datetime.now(UTC))!=bundle['state_version']:raise ValueError('画像照合中に三食正本が変わりました')
    if candidate.slot=='daily' and final['nutrition_targets']!=bundle['targets']:raise ValueError('画像照合中に栄養目標が変わりました')
    return {'verified':'instagram_native_bundle','state_version':bundle['state_version']}

def maintain(store):
    with store.transaction() as db:maybe_ready(db,TARGET)
