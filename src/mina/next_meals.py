"""日次OGP保留を残したまま、10/30朝昼夕の六承認だけを独立させる。"""
import hashlib,json,uuid
from datetime import UTC,datetime
from pathlib import Path
from .preparation import fingerprint
from .publication_hold import exists
TARGET='2026-10-30';SLOTS=('breakfast','lunch','dinner');KEY='meal_only_continuation';VERSION='mina-meal-only-v1'

def marker(key):return {'contract_version':VERSION,'case_uuid':key,'excluded_slots':['daily']}
def valid_source(c):
    m=c.source[KEY];uuid.UUID(m['case_uuid'])
    if m!=marker(m['case_uuid']) or c.target_date!=TARGET or c.slot not in SLOTS or set(c.source['x_digests'])!=set(SLOTS):raise ValueError('三食専用画像の契約が不正です')

def job(db,target):
    return db.execute('SELECT * FROM next_meal_jobs WHERE target_date=?',(target,)).fetchone() if target==TARGET and exists(db,'next_meal_jobs') else None

def intact(db,r):
    old=json.loads(r['origin']);h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone();parent=db.execute('SELECT * FROM next_saved_jobs WHERE target_date=?',(TARGET,)).fetchone()
    if not h or dict(h)!=old['hold'] or not parent or parent['case_uuid']!=old['parent_case'] or parent['state']!='held':return False
    from .next_saved import read_rows
    for table,key in (('mina_requests','requests_digest'),('preparation_steps','steps_digest')):
        if fingerprint(read_rows(db,table))!=old[key]:return False
    for path,sha in old['files'].items():
        p=Path(path)
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:return False
    return True

def members(db,r,decided=False):
    if not intact(db,r):return None
    values=json.loads(r['members']);out=[]
    if {(v['platform'],v['slot']) for v in values}!={(p,s) for p in ('x','instagram') for s in SLOTS} or len(values)!=6:return None
    for v in values:
        table='candidates' if v['platform']=='x' else 'instagram_candidates';c=db.execute('SELECT * FROM '+table+' WHERE target_date=? AND slot=?',(TARGET,v['slot'])).fetchone()
        if not c or c['revision']!=v['revision'] or c['digest']!=v['digest'] or fingerprint(json.loads(c['payload']))!=v['payload_digest']:return None
        from .domain import Candidate
        from .instagram_domain import InstagramCandidate
        candidate=(Candidate if v['platform']=='x' else InstagramCandidate)(**json.loads(c['payload']))
        source=candidate.approval_source if v['platform']=='x' else candidate.source
        if candidate.digest!=c['digest'] or source.get(KEY)!=marker(r['case_uuid']):return None
        allowed=('approved','cancelled','dispatching','accepted','published') if decided else ('approval_pending','approved','cancelled','dispatching','accepted','published')
        if c['state'] not in allowed or (decided and (not c['actor'] or c['approved'] is None)):return None
        attempts='buffer_publication' if v['platform']=='x' else 'instagram_attempts'
        if exists(db,attempts):
            attempt=db.execute('SELECT * FROM '+attempts+' WHERE target_date=? AND slot=?',(TARGET,v['slot'])).fetchone()
            states=('armed','attempting','accepted','sent') if v['platform']=='x' else ('attempting','accepted','published')
            if attempt and (attempt['state'] not in states or attempt['revision']!=v['revision'] or attempt['digest']!=v['digest']):return None
        out.append((v,dict(c)))
    return out

def receipts(db,r):
    if not exists(db,'routine_notice_receipts'):return False
    values=list(db.execute('SELECT * FROM routine_notice_receipts WHERE target_date=? AND policy_id=?',(TARGET,r['case_uuid'])))
    return len(values)==6 and all(v['state']=='sent' and v['message_id'] for v in values)

def approval(db,target,platform,slot=None,revision=None,digest=None):
    r=job(db,target)
    if not r or r['state'] not in ('approval_pending','ready'):return False
    values=members(db,r)
    if values is None:return False
    return any(v['platform']==platform and (slot is None or v['slot']==slot and (revision is None or v['revision']==revision) and (digest is None or v['digest']==digest)) for v,c in values)

def notice(db,row,platform):
    r=job(db,row['target_date'])
    return bool(r and datetime.now(UTC).timestamp()<r['deadline'] and approval(db,row['target_date'],platform,row['slot'],row['revision']))

def maybe_ready(db,target):
    r=job(db,target)
    if not r:return False
    if r['state']=='approval_pending' and receipts(db,r) and members(db,r,True) is not None:
        db.execute("UPDATE next_meal_jobs SET state='ready' WHERE target_date=? AND state='approval_pending'",(target,))
    return True

def publication_allowed(db,target,publication):
    r=job(db,target)
    if not r or r['state']!='ready' or not isinstance(publication,tuple) or len(publication)!=2 or publication[0] not in ('x','instagram') or not receipts(db,r):return False
    platform,row=publication
    if not isinstance(row,dict) or row.get('target_date')!=target or row.get('slot') not in SLOTS:return False
    values=members(db,r,True)
    return values is not None and any(v['platform']==platform and v['slot']==row['slot'] and c['state'] in ('approved','dispatching') and c['revision']==row.get('revision') and c['digest']==row.get('digest') and c['actor']==row.get('actor') and c['approved']==row.get('approved') and c['payload']==row.get('payload') for v,c in values)

def handoff_policy(db,target):
    r=job(db,target)
    return {'policy_id':r['case_uuid']} if r and r['state'] in ('approval_pending','ready') and members(db,r) is not None else None

def maintain(store):
    with store.transaction() as db:maybe_ready(db,TARGET)

def verify_ig(runtime,candidate):
    """日次共有OGPを合格扱いせず、同枠の個別OGP・実画像と三食SVへ照合する。"""
    from .domain import Candidate
    from .m1_contract import validate_state,verify_source
    from .current_day_cli import scoped_meal
    from .ogp import capture_ogp
    from .instagram_runtime import caption
    valid_source(candidate)
    with runtime.store.transaction() as db:
        r=job(db,TARGET)
        if not r or r['state']!='ready' or members(db,r,True) is None:raise ValueError('三食専用承認の状態が変わりました')
        material=json.loads(r['material']);row=db.execute('SELECT * FROM instagram_candidates WHERE target_date=? AND slot=?',(TARGET,candidate.slot)).fetchone()
        if not row or row['digest']!=candidate.digest:raise ValueError('三食専用画像版が違います')
    bundle=material['bundle'];manifest=material['manifest'];expected=material['igs'][candidate.slot]
    from dataclasses import asdict
    if asdict(candidate)!=expected or candidate.caption!=caption(bundle,candidate.slot) or [v['sha256'] for v in candidate.assets]!=[v['sha256'] for v in manifest['assets'][candidate.slot]]:raise ValueError('三食専用画像/本文が変わりました')
    meal=scoped_meal();state=meal.day_state(TARGET)
    if validate_state(state,TARGET,datetime.now(UTC))!=bundle['state_version']:raise ValueError('三食の現在版が変わりました')
    actual={v['meal']['meal_type']:v for v in state['meals']}
    if set(actual)!=set(SLOTS):raise ValueError('三食の正本が不足しています')
    for slot in SLOTS:
        if actual[slot]['result_version']!=material['xs'][slot]['approval_source']['result_version'] or actual[slot]['image_sha256']!=bundle['meals'][slot]['image_sha256']:raise ValueError('三食の実解析/原画像が変わりました')
        p=Path(bundle['meals'][slot]['image_path'])
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=bundle['meals'][slot]['image_sha256']:raise ValueError('三食の実画像が変わりました')
    return verify_source(Candidate(**material['xs'][candidate.slot]),meal,'www.log-eats.com',capture_ogp)
