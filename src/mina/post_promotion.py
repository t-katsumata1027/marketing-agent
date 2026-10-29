"""10/29夕食・日次の準備成果物を、本人承認後の通常公開へ一度だけ接続する。"""
import hashlib,json,os,sqlite3,uuid
from dataclasses import asdict,replace
from datetime import UTC,datetime,timedelta
from pathlib import Path
from .domain import Candidate,JST,slot_time,date
from .preparation import fingerprint
from .publication_hold import exists
from .replacement_contract import stamp

VERSION='mina-post-promotion-v1'
KEY='publication_promotion'
TARGET='2026-10-29'
SLOTS=('dinner','daily')
MEMBERS={(channel,slot) for channel in ('x','instagram') for slot in SLOTS}

def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS post_promotions(target_date TEXT PRIMARY KEY,promotion_uuid TEXT UNIQUE NOT NULL,source_digest TEXT UNIQUE NOT NULL,document TEXT NOT NULL,state TEXT NOT NULL,proof TEXT,created TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS post_promotion_phases(promotion_uuid TEXT NOT NULL,phase TEXT NOT NULL,state TEXT NOT NULL,receipt TEXT,PRIMARY KEY(promotion_uuid,phase))')

def record(db,target):
    return db.execute('SELECT * FROM post_promotions WHERE target_date=?',(target,)).fetchone() if exists(db,'post_promotions') else None

def capture(db):
    """完成した原本・受領済み四案・公開停止の所有を読取り固定する。"""
    from .current_day_checkpoint import file_record
    cards=[dict(r) for r in db.execute('SELECT * FROM current_day_cards ORDER BY platform,slot')]
    if len(cards)!=8 or {(r['platform'],r['slot']) for r in cards}!={(p,s) for p in ('x','instagram') for s in ('breakfast','lunch','dinner','daily')}:raise ValueError('準備八案が必要です')
    selected=[r for r in cards if (r['platform'],r['slot']) in MEMBERS]
    if any(r['state']!='sent' or not r['message_id'] for r in selected):raise ValueError('先送四案の受領が必要です')
    owners={r['recovery_uuid'] for r in cards}
    if len(owners)!=1:raise ValueError('元成果物の所有が不一致です')
    hold=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone();lock=db.execute('SELECT * FROM scoped_preparation_locks WHERE target_date=?',(TARGET,)).fetchone()
    if not hold or not lock or hold['state']!='held' or hold['owner']!=next(iter(owners)) or (hold['owner'],hold['epoch'])!=(lock['owner'],lock['epoch']):raise ValueError('元公開停止の所有が不一致です')
    receipts=[dict(r) for r in db.execute('SELECT * FROM current_day_discord_delivery_attempts ORDER BY platform,slot')]
    if len(receipts)!=4 or any(r['state']!='sent' or not json.loads(r['receipt']).get('assets_verified') for r in receipts):raise ValueError('先送四案の画像受領証跡が必要です')
    for row in selected:
        match=[r for r in receipts if (r['platform'],r['slot'],r['message_id'])==(row['platform'],row['slot'],row['message_id'])]
        if len(match)!=1:raise ValueError('先送案と受領IDが不一致です')
    names=('meal_review:breakfast:corrected','ogp_review:breakfast','meal_review:lunch:corrected','ogp_review:lunch','meal_review:dinner:corrected','ogp_review:dinner','ogp_review:daily')
    reviews=[dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=? ORDER BY step',(TARGET,)) if r['step'] in names]
    if len(reviews)!=7 or any(r['state']!='complete' or json.loads(r['result'])!={'status':'publishable','reason_codes':[]} for r in reviews):raise ValueError('三食/日次の独立点検原本が不足しています')
    files={}
    for row in cards:
        p=json.loads(row['payload'])
        if fingerprint(p)!=row['digest'] or p['target_date']!=TARGET or p['scheduled_at'] is not None:raise ValueError('準備原本の版が不一致です')
        for a in p['assets']:
            f=file_record(a['path'])
            if f['sha256']!=a['sha256']:raise ValueError('準備画像が不一致です')
            files[f['path']]=f
    return {'target_date':TARGET,'cards':cards,'hold':dict(hold),'lock':dict(lock),'receipts':receipts,'files':list(files.values()),'reviews':reviews}

def guard_source(db,snapshot):
    from .current_day_checkpoint import file_record
    rows=[dict(r) for r in db.execute('SELECT * FROM current_day_cards ORDER BY platform,slot')]
    if rows!=snapshot['cards']:raise ValueError('元準備カードが変わりました')
    if [dict(r) for r in db.execute('SELECT * FROM current_day_discord_delivery_attempts ORDER BY platform,slot')]!=snapshot['receipts']:raise ValueError('元受領証跡が変わりました')
    names={r['step'] for r in snapshot['reviews']}
    if [dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=? ORDER BY step',(TARGET,)) if r['step'] in names]!=snapshot['reviews']:raise ValueError('独立点検の原本が変わりました')
    lock=db.execute('SELECT * FROM scoped_preparation_locks WHERE target_date=?',(TARGET,)).fetchone()
    if not lock or dict(lock)!=snapshot['lock']:raise ValueError('元公開lockが変わりました')
    for f in snapshot['files']:
        if file_record(f['path'])!=f:raise ValueError('元画像が変わりました')

def load(store,target):
    from .current_day_cli import config,protected_json
    with store.transaction() as db:r=record(db,target)
    if not r:raise ValueError('昇格証跡がありません')
    doc=json.loads(r['document']);settings,check=config(doc['config_path'],Path(__file__).resolve().parents[2],version=VERSION,extras=('checkpoint',))
    if protected_json(doc['config_path'],root_owned=True)[1]!=doc['config_sha256'] or settings['approval']['promotion_uuid']!=r['promotion_uuid']:raise ValueError('昇格承認の版が不一致です')
    snapshot,sha=protected_json(settings['checkpoint']['path'],root_owned=True)
    if sha!=settings['checkpoint']['sha256'] or fingerprint(snapshot)!=r['source_digest']:raise ValueError('昇格原本の版が不一致です')
    check()
    from .current_day_checkpoint import file_record
    if 'fresh_history' not in doc or file_record(doc['fresh_history']['path'])!=doc['fresh_history']:raise ValueError('実履歴照合の原本が不一致です')
    with store.transaction() as db:guard_source(db,snapshot)
    return dict(r),doc,settings,snapshot,check

def new_xs(snapshot,commit,key):
    from .scoped_preparation import KEY as OLD
    out={}
    for row in snapshot['cards']:
        if row['platform']!='x':continue
        c=Candidate(**json.loads(row['payload'])['source']['candidate'])
        if row['slot'] in SLOTS:
            source={k:v for k,v in c.approval_source.items() if k!=OLD};source[KEY]=key
            c=replace(c,source_commit=commit,approval_source=source)
        out[row['slot']]=c
    return out

def verify_all(snapshot,xs,meal,host,capture_image,clock):
    from .m1_contract import verify_source,validate_state
    started=clock();versions=set()
    for slot,c in xs.items():
        c.validate_content(host);verify_source(c,meal,host,capture_image,clock=clock);versions.add(c.approval_source['state_version'])
    state=meal.day_state(TARGET);sv=validate_state(state,TARGET,clock())
    if versions!={sv} or state['meal_count']!=3 or not 1800<=state['summary']['totalCalories']<=2000:raise ValueError('三食の現在正本/実合計が不一致です')
    targets=json.loads(snapshot['cards'][0]['payload'])['source']['nutrition_targets']
    if state.get('nutrition_targets')!=targets:raise ValueError('栄養目標が変わりました')
    if not 0<=(clock()-started).total_seconds()<=120:raise ValueError('昇格照合が古くなりました')
    from .replacement_bundle import CHECKS
    return {'verdict':'pass','verification':'current-source-uuid-ogp-v1','checks':sorted(CHECKS),'read_started_at':stamp(started),'state_version':sv,'source_digest':fingerprint(snapshot)}

def register(store,settings,snapshot,config_path,config_sha,clock):
    from .replacement_client import timestamp
    a=settings['approval'];now=clock();MealKey=a['promotion_uuid'];uuid.UUID(MealKey)
    if os.getenv('MINA_POST_PROMOTION_ENABLED','false').lower()!='true' or a['mode']!='approve_then_scheduled' or a['target_date']!=TARGET or fingerprint(snapshot)!=a['source_digest'] or now.astimezone(JST).date().isoformat()!=TARGET or now>=timestamp(a['not_after']):raise ValueError('昇格承認/期限が不正です')
    if a['publication_times']!={s:stamp(slot_time(date.fromisoformat(TARGET),s)) for s in SLOTS} or now>=slot_time(date.fromisoformat(TARGET),'dinner'):raise ValueError('昇格の定時/未来条件が不正です')
    with store.transaction() as db:
        initialize(db);guard_source(db,snapshot)
        if dict(db.execute('SELECT * FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone())!=snapshot['hold']:raise ValueError('旧holdが変わりました')
        for table in ('candidates','instagram_candidates','buffer_publication','instagram_attempts'):
            if db.execute('SELECT 1 FROM '+table+' WHERE target_date=?',(TARGET,)).fetchone():raise ValueError('通常投稿の既存状態があり昇格できません')
        doc={'config_path':str(config_path),'config_sha256':config_sha}
        db.execute('INSERT INTO post_promotions VALUES(?,?,?,?,?,?,?)',(TARGET,MealKey,fingerprint(snapshot),json.dumps(doc),'preparing',None,stamp(now)))
        for phase in ('stage','dinner','daily'):db.execute("INSERT INTO post_promotion_phases VALUES(?,?,'pending',NULL)",(MealKey,phase))

def stage(store,settings,snapshot,xs,igs,proof,clock):
    from .replacement_bundle import initialize as bundle_init,proof_valid
    from .replacement_contract import digest
    now=clock();a=settings['approval']
    if not proof_valid(proof,fingerprint(snapshot),now) or set(xs)!= {'breakfast','lunch','dinner','daily'} or {c.slot for c in igs}!=set(SLOTS):raise ValueError('昇格の検査/構成が不完全です')
    expected=new_xs(snapshot,a['code_commit'],a['promotion_uuid'])
    if {s:asdict(c) for s,c in xs.items()}!={s:asdict(c) for s,c in expected.items()}:raise ValueError('昇格元とX本文/版が不一致です')
    if {c.approval_source['state_version'] for c in xs.values()}!={proof['state_version']}:raise ValueError('昇格証跡の日版が不一致です')
    for s in SLOTS:xs[s].validate(settings['host'],now)
    for c in igs:
        original=json.loads(next(r['payload'] for r in snapshot['cards'] if (r['platform'],r['slot'])==('instagram',c.slot)))
        if c.caption!=original['body'] or c.channel_id!=settings['channels']['instagram'] or c.source!={'bundle_sha256':original['source']['bundle_sha256'],'state_version':proof['state_version'],'x_digests':{s:x.digest for s,x in xs.items()},KEY:a['promotion_uuid']} or len(c.assets)!=len(original['assets']):raise ValueError('Instagramの元本文/正本/宛先が不一致です')
        for asset,old in zip(c.assets,original['assets']):
            if {k:asset[k] for k in old}!=old:raise ValueError('Instagramの元画像が不一致です')
    from .instagram_media import HOSTS
    for c in igs:c.validate(now,HOSTS)
    with store.transaction() as db:
        guard_source(db,snapshot);r=record(db,TARGET)
        if not r or r['state']!='preparing' or r['promotion_uuid']!=a['promotion_uuid']:raise ValueError('昇格の段階が不一致です')
        phase=db.execute("SELECT receipt FROM post_promotion_phases WHERE promotion_uuid=? AND phase='stage' AND state='complete'",(r['promotion_uuid'],)).fetchone()
        if not phase or (now-datetime.fromisoformat(json.loads(phase[0])['read_started_at'])).total_seconds()>120:raise ValueError('外部予約確認が古いか不完全です')
        h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone()
        if dict(h)!=snapshot['hold']:raise ValueError('旧holdの所有が変わりました')
        bundle_init(db)
        if db.execute('SELECT 1 FROM replacement_bundles WHERE target_date=?',(TARGET,)).fetchone():raise ValueError('既存bundleがあります')
        for table in ('candidates','instagram_candidates','buffer_publication','instagram_attempts'):
            if db.execute('SELECT 1 FROM '+table+' WHERE target_date=?',(TARGET,)).fetchone():raise ValueError('昇格中に通常投稿状態が変わりました')
        for s in ('breakfast','lunch'):db.execute('INSERT INTO publication_exclusions VALUES(?,?,?)',(TARGET,s,'本人指定：10/29朝昼は公開しない'))
        members=[]
        for channel,c in [('x',xs[s]) for s in SLOTS]+[('instagram',c) for c in igs]:
            table='candidates' if channel=='x' else 'instagram_candidates'
            db.execute('INSERT INTO '+table+"(target_date,slot,revision,payload,digest,scheduled,state) VALUES(?,?,1,?,?,?,'approval_pending')",(TARGET,c.slot,json.dumps(asdict(c),ensure_ascii=False),c.digest,c.scheduled_at.timestamp()))
            members.append((channel,c.slot,1,c.digest));db.execute('INSERT INTO replacement_bundle_members VALUES(?,?,?,?,?)',(TARGET,channel,c.slot,1,c.digest))
        members.sort();epoch=h['epoch']+1
        db.execute("UPDATE publication_holds SET owner=?,epoch=?,state='saved_pending_review',reason='post_promotion_awaiting_four_approvals' WHERE target_date=?",(a['promotion_uuid'],epoch,TARGET))
        db.execute("INSERT INTO replacement_bundles VALUES(?,?,?,?,?,?,?,'approval_pending')",(TARGET,a['promotion_uuid'],epoch,digest(members),fingerprint(snapshot),json.dumps(members),json.dumps(proof)))
        doc=json.loads(r['document']);doc.update(xs={s:asdict(c) for s,c in xs.items()},igs={c.slot:asdict(c) for c in igs},old_hold=snapshot['hold'],old_lock=snapshot['lock'])
        db.execute("UPDATE post_promotions SET document=?,state='approval_pending',proof=? WHERE target_date=?",(json.dumps(doc),json.dumps(proof),TARGET))

def notice_ready(db,target):
    r=record(db,target)
    return bool(r and r['state']=='approval_pending')

def lock_released(db,target):
    r=record(db,target)
    if not r or r['state']!='released':return False
    d=json.loads(r['document']);h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(target,)).fetchone();lock=db.execute('SELECT * FROM scoped_preparation_locks WHERE target_date=?',(target,)).fetchone()
    return bool(h and lock and h['state']=='released' and h['owner']==r['promotion_uuid'] and h['epoch']==d['old_hold']['epoch']+1 and dict(lock)==d['old_lock'])

def maybe_release(db,target,now):
    """四つの承認と新鮮な証跡が揃うまで旧公開停止を引き継ぐ。"""
    r=record(db,target)
    if not r:return False
    if r['state']!='approval_pending':return True
    from .replacement_bundle import proof_valid
    from .replacement_contract import digest
    b=db.execute('SELECT * FROM replacement_bundles WHERE target_date=?',(target,)).fetchone();d=json.loads(r['document'])
    if not b or not proof_valid(json.loads(b['proof']),r['source_digest'],now):return True
    ms=[dict(m) for m in db.execute('SELECT * FROM replacement_bundle_members WHERE target_date=? ORDER BY channel,slot',(target,))]
    binding=[(m['channel'],m['slot'],m['revision'],m['digest']) for m in ms]
    if {(m['channel'],m['slot']) for m in ms}!=MEMBERS or digest(binding)!=b['digest'] or [list(x) for x in binding]!=json.loads(b['required']):return True
    for m in ms:
        table='candidates' if m['channel']=='x' else 'instagram_candidates';c=db.execute('SELECT * FROM '+table+' WHERE target_date=? AND slot=?',(target,m['slot'])).fetchone()
        if not c or c['state']!='approved' or not c['actor'] or c['approved'] is None or c['scheduled']<=now.timestamp() or (c['revision'],c['digest'])!=(m['revision'],m['digest']):return True
        expected=d['xs'][m['slot']] if m['channel']=='x' else d['igs'][m['slot']]
        if json.loads(c['payload'])!=expected:return True
    lock=db.execute('SELECT * FROM scoped_preparation_locks WHERE target_date=?',(target,)).fetchone();h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(target,)).fetchone()
    if not lock or dict(lock)!=d['old_lock'] or not h or (h['owner'],h['epoch'],h['state'])!=(r['promotion_uuid'],d['old_hold']['epoch']+1,'saved_pending_review'):return True
    db.execute("UPDATE publication_holds SET state='released',reason='post_promotion_four_approvals' WHERE target_date=?",(target,));db.execute("UPDATE replacement_bundles SET state='released' WHERE target_date=?",(target,));db.execute("UPDATE post_promotions SET state='released' WHERE target_date=?",(target,));return True

def _maintain(store,clock):
    with store.transaction() as db:
        r=record(db,TARGET)
        if not r or r['state']!='approval_pending':return
        members=[dict(c) for table in ('candidates','instagram_candidates') for c in db.execute('SELECT state,scheduled FROM '+table+' WHERE target_date=?',(TARGET,))]
        if len(members)!=4 or any(c['state']!='approved' for c in members):return
        if any(c['scheduled']<=clock().timestamp() for c in members):return
    r,d,settings,snapshot,check=load(store,TARGET)
    from .current_day_cli import scoped_meal
    from .ogp import capture_ogp
    proof=verify_all(snapshot,{s:Candidate(**c) for s,c in d['xs'].items()},scoped_meal(),settings['host'],capture_ogp,clock)
    with store.transaction() as db:
        check();guard_source(db,snapshot)
        db.execute("UPDATE replacement_bundles SET proof=? WHERE target_date=? AND owner=? AND state='approval_pending'",(json.dumps(proof),TARGET,r['promotion_uuid']));maybe_release(db,TARGET,clock())

def maintain(store,clock=lambda:datetime.now(UTC)):
    try:return _maintain(store,clock)
    except Exception as error:
        from .audit import safe_failure
        reason,details=safe_failure(error)
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS post_promotion_failures(id INTEGER PRIMARY KEY,target_date TEXT,stage TEXT,error_type TEXT,reason TEXT,details TEXT,created TEXT)')
            db.execute('INSERT INTO post_promotion_failures(target_date,stage,error_type,reason,details,created) VALUES(?,?,?,?,?,?,?)',(TARGET,'approval_source_refresh',type(error).__name__,reason,json.dumps(details),stamp(clock())))
            db.execute("UPDATE post_promotions SET state='held' WHERE target_date=? AND state='approval_pending'",(TARGET,))
        return 'held'


def verify_ig(runtime,candidate):
    r,d,settings,snapshot,check=load(runtime.store,candidate.target_date)
    if r['state']!='released' or candidate.slot not in SLOTS or asdict(candidate)!=d['igs'][candidate.slot]:raise ValueError('Instagram昇格版が不一致です')
    with runtime.store.transaction() as db:
        for s in SLOTS:
            x=db.execute('SELECT * FROM candidates WHERE target_date=? AND slot=?',(TARGET,s)).fetchone()
            if not x or json.loads(x['payload'])!=d['xs'][s]:raise ValueError('InstagramのX対応版が変わりました')
    from .ogp import capture_ogp
    verify_all(snapshot,{s:Candidate(**c) for s,c in d['xs'].items()},runtime.meal or __import__('src.mina.instagram_runtime',fromlist=['meal_client']).meal_client(),settings['host'],capture_ogp,lambda:datetime.now(UTC))
    return True
