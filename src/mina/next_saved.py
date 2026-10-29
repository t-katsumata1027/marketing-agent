"""10/30の確定保存済み三食からだけ承認八案を完成する別準備権限。"""
import contextvars,hashlib,json,uuid
from contextlib import contextmanager
from datetime import UTC,datetime,timedelta
from pathlib import Path
from .preparation import fingerprint
from .publication_hold import exists
from .domain import JST,slot_time,date
from .tolerance_review import policy_sha256,validate

TARGET='2026-10-30'
END=datetime(2026,10,30,0,30,tzinfo=JST)
STEP='meal_review:lunch:corrected'
PASS={'status':'publishable','reason_codes':[]}
RULE='同じ計画材料が皿へ少量分配される見た目差は警告。重量一致を断定しない。主要欠落/別食品/別人数分/重大栄養矛盾は保留。旧保留の合格変換はしない。'
POLICY=fingerprint({'parent_policy':policy_sha256(),'rule':RULE,'version':'next-saved-review-v1'})
CURRENT=contextvars.ContextVar('next_saved_preparation',default=None)

def read_rows(db,table):
    values=[]
    for r in db.execute('SELECT * FROM '+table+' WHERE target_date=?',(TARGET,)):
        values.append({k:({'blob_sha256':hashlib.sha256(v).hexdigest(),'length':len(v)} if isinstance(v,bytes) else v) for k,v in dict(r).items()})
    return values

def snapshot(db):
    return {t:read_rows(db,t) for t in ('preparation_steps','mina_requests','publication_holds','preparation','candidates','instagram_candidates','buffer_publication','instagram_attempts')}

class Gate:
    def __init__(self,store,a,old,manual,files,clock):self.store,self.a,self.old,self.manual,self.files,self.clock=store,a,old,manual,files,clock;self.key=a['case_uuid']
    def check(self,db=None):
        if db is None:
            with self.store.transaction() as connection:return self.check(connection)
        now=self.clock();r=db.execute('SELECT * FROM next_saved_jobs WHERE target_date=?',(TARGET,)).fetchone()
        if not r or r['case_uuid']!=self.key or r['state']!='running' or now.timestamp()>=r['deadline']:raise ValueError('翌日追加準備の権限/期限が不正です')
        if json.loads(r['approval'])!=self.a or json.loads(r['manual'])!=self.manual:raise ValueError('翌日承認/独立点検の封印が変わりました')
        if fingerprint(json.loads(r['original']))!=self.a['snapshot_digest']:raise ValueError('翌日準備の原本が変わりました')
        h=read_rows(db,'publication_holds')
        if h!=self.old['publication_holds']:raise ValueError('翌日公開停止の所有が変わりました')
        for table in ('preparation_steps','mina_requests'):
            key='step' if table=='preparation_steps' else 'request_key';current={v[key]:v for v in read_rows(db,table)}
            if any(current.get(v[key])!=v for v in self.old[table]):raise ValueError('旧工程/要求の原本が変わりました')
        if any(read_rows(db,t) for t in ('candidates','instagram_candidates','buffer_publication','instagram_attempts')):raise ValueError('翌日の公開処理が競合しました')
        for path,sha in self.files.items():
            p=Path(path)
            if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise ValueError('翌日の画像/計画原本が変わりました')
        return r
    def register(self):
        a=self.a;now=self.clock();uuid.UUID(self.key)
        if set(a)!={'case_uuid','target_date','commit','snapshot_digest','approval_reference','not_after','state_version','review_context_digest'} or a['target_date']!=TARGET or datetime.fromisoformat(a['not_after'])!=END or not a['approval_reference'] or now>=END or self.old['candidates'] or self.old['instagram_candidates'] or self.old['buffer_publication'] or self.old['instagram_attempts']:raise ValueError('翌日追加準備の本人承認が不正です')
        requests=self.old['mina_requests']
        holds=self.old['publication_holds']
        if len(holds)!=1 or holds[0]['target_date']!=TARGET or holds[0]['state']!='held' or not holds[0]['owner'] or type(holds[0]['epoch']) is not int or holds[0]['epoch']<1:raise ValueError('翌日公開停止の所有証明が必要です')
        if len(requests)!=6 or {(r['slot'],r['operation'],r['phase']) for r in requests}!={(s,o,'complete') for s in ('breakfast','lunch','dinner') for o in ('meal_register','meal_correction')}:raise ValueError('確定済み六要求だけを再利用します')
        if any(r['state']!='complete' for r in self.old['preparation_steps']):raise ValueError('不明な旧工程は照会が必要です')
        steps={r['step']:r for r in self.old['preparation_steps']}
        if json.loads(steps[STEP]['result'])!={'status':'hold','reason_codes':['image_analysis_mismatch']}:raise ValueError('対象の旧保留が違います')
        for s in ('breakfast','dinner'):
            if json.loads(steps['meal_review:'+s+':corrected']['result'])!=PASS or json.loads(steps['ogp_review:'+s]['result'])!=PASS:raise ValueError('朝夕の旧合格が不足しています')
        with self.store.transaction() as db:
            if snapshot(db)!=self.old:raise ValueError('翌日準備開始が競合しました')
            db.execute('CREATE TABLE IF NOT EXISTS next_saved_jobs(target_date TEXT PRIMARY KEY,case_uuid TEXT UNIQUE,state TEXT,approval TEXT,original TEXT,manual TEXT,first_started REAL,deadline REAL)')
            db.execute("INSERT INTO next_saved_jobs VALUES(?,?,'running',?,?,?,?,?)",(TARGET,self.key,json.dumps(a),json.dumps(self.old),json.dumps(self.manual),now.timestamp(),min(END,now+timedelta(minutes=45)).timestamp()))
        self.check()
    def review(self,journal,target,step,inputs,operation):
        if target!=TARGET or step!=STEP:return None
        self.check();original=next(r for r in self.old['preparation_steps'] if r['step']==STEP)
        if fingerprint(inputs)!=original['digest']:raise ValueError('新点検の入力が旧対象と異なります')
        expected=fingerprint({'target_date':TARGET,'meal':inputs['meal'],'response':inputs['response']})
        if expected!=self.a['review_context_digest'] or self.manual['context_digest']!=expected or self.manual['policy_sha256']!=POLICY or self.manual['image_sha256']!=inputs['image_sha256']:raise ValueError('新独立点検の原本が不一致です')
        verdict=validate(self.manual['raw'],inputs['meal'],'publishable')
        result=journal.run(TARGET,STEP+':continuation:'+self.key,{'old_input_digest':original['digest'],'old_review_digest':fingerprint(json.loads(original['result'])),'new_policy_sha256':POLICY,'manual_review_digest':fingerprint(self.manual),'inputs':inputs},lambda:verdict)
        if result!=PASS:raise ValueError('新独立点検も保留です')
        return result
    def require_daily(self,db,r):
        self.check(db)
        p=r['payload']
        if r['target_date']!=TARGET or r['slot']!='daily' or r['operation']!='daily_share' or p['date']!=TARGET or p['expected_meal_count']!=3 or p['expected_state_version']!=self.a['state_version'] or p['comment']!=self.manual['daily_comment']:raise ValueError('新準備で食事の書換えを許可しません')
        db.execute('CREATE TABLE IF NOT EXISTS next_saved_daily_claims(target_date TEXT PRIMARY KEY,case_uuid TEXT,request_key TEXT UNIQUE,input_digest TEXT)')
        db.execute('INSERT INTO next_saved_daily_claims VALUES(?,?,?,?)',(TARGET,self.key,r['request_key'],r['input_digest']))
        return True
    def historical_plan(self,draft):
        """保存後の再計画予約を消さず、既存食事版だけを履歴として検査する。"""
        self.check()
        path=Path('/var/lib/mina-agent/daily')/TARGET/'draft.json'
        original=json.loads(path.read_text())
        if draft!={**original,'daily_comment':self.manual['daily_comment']}:raise ValueError('保存済み献立を新計画へ差し替えません')
        return True

@contextmanager
def scope(g):
    token=CURRENT.set(g)
    try:yield
    finally:CURRENT.reset(token)

def guard_stage(store,target,db):
    if target!=TARGET or not exists(db,'next_saved_jobs') or not db.execute('SELECT 1 FROM next_saved_jobs WHERE target_date=?',(TARGET,)).fetchone():return
    g=CURRENT.get()
    if g is None or g.store.path!=store.path:raise ValueError('翌日追加準備は専用operatorだけです')
    g.check(db)

def preserve_request(store,key,phase):
    g=CURRENT.get()
    if g is None:return False
    original=next((r for r in g.old['mina_requests'] if r['request_key']==key),None)
    if original is None:return False
    g.check()
    if phase!='complete' or original['phase']!='complete':raise ValueError('旧要求の状態を変更しません')
    return True

def provider_started(job,receipt):
    g=CURRENT.get()
    if g is None:return
    g.check();p=receipt.get('preparation')
    if not p or p['target_date']!=TARGET or p['step'] not in ('ogp_review:lunch','ogp_review:daily'):raise ValueError('許可した二共有画像点検以外のAIを実行しません')
    with g.store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS next_saved_calls(job TEXT PRIMARY KEY,case_uuid TEXT,step TEXT,receipt_digest TEXT,state TEXT,cost_status TEXT,UNIQUE(case_uuid,step))')
        if db.execute('SELECT count(*) FROM next_saved_calls WHERE case_uuid=?',(g.key,)).fetchone()[0]>=2:raise ValueError('新共有画像点検の上限です')
        db.execute("INSERT INTO next_saved_calls VALUES(?,?,?,?,'started','unmeasured')",(job,g.key,p['step'],fingerprint(receipt)))

def provider_finished(job,state):
    g=CURRENT.get()
    if g is not None:
        with g.store.transaction() as db:
            if exists(db,'next_saved_calls'):db.execute('UPDATE next_saved_calls SET state=? WHERE job=?',(state,job))

def handoff_policy(db,target):
    """新規日次方針へ遡及せず、別承認の八候補だけに受領確認を適用する。"""
    if target!=TARGET or not exists(db,'next_saved_jobs'):return None
    r=db.execute("SELECT * FROM next_saved_jobs WHERE target_date=? AND state IN ('approval_pending','refresh_started','complete')",(target,)).fetchone()
    if not r:return None
    b=db.execute('SELECT owner FROM replacement_bundles WHERE target_date=?',(target,)).fetchone() if exists(db,'replacement_bundles') else None
    if not b or b['owner']!=r['case_uuid']:return None
    return {'policy_id':r['case_uuid']}

def blocked_handoff(db,target,*,release=False,notice=False):
    """停止した別準備を一般bundleの承認・通知・解除で迂回しない。"""
    if target!=TARGET or not exists(db,'next_saved_jobs'):return False
    r=db.execute('SELECT * FROM next_saved_jobs WHERE target_date=?',(target,)).fetchone()
    if not r:return False
    if r['state'] not in ('approval_pending','refresh_started','complete'):return True
    if notice and datetime.now(UTC).timestamp()>=r['deadline']:return True
    if release:
        if not exists(db,'routine_notice_receipts'):return True
        rows=list(db.execute('SELECT * FROM routine_notice_receipts WHERE target_date=?',(target,)))
        if len(rows)!=8 or any(v['state']!='sent' or v['policy_id']!=r['case_uuid'] or not v['message_id'] for v in rows):return True
    return False

def maintain(store,clock=lambda:datetime.now(UTC)):
    """八件の本人判断後に一度だけ実正本を再照合。失敗時は公開停止を維持する。"""
    with store.transaction() as db:
        if not exists(db,'next_saved_jobs'):return
        r=db.execute("SELECT * FROM next_saved_jobs WHERE target_date=? AND state='approval_pending'",(TARGET,)).fetchone()
        if not r:return
        if blocked_handoff(db,TARGET,release=True):return
        bundle=db.execute('SELECT * FROM replacement_bundles WHERE target_date=?',(TARGET,)).fetchone()
        if bundle and bundle['owner']==r['case_uuid'] and bundle['state']=='released':
            db.execute("UPDATE next_saved_jobs SET state='complete' WHERE target_date=? AND state='approval_pending'",(TARGET,));return
        members=[dict(c) for table in ('candidates','instagram_candidates') for c in db.execute('SELECT * FROM '+table+' WHERE target_date=?',(TARGET,))]
        if len(members)!=8 or any(c['state'] not in ('approved','cancelled') or not c['actor'] or c['approved'] is None for c in members):return
        if any(c['scheduled']<=clock().timestamp() for c in members):return
        config=db.execute('SELECT * FROM next_saved_configs WHERE case_uuid=?',(r['case_uuid'],)).fetchone()
        db.execute("UPDATE next_saved_jobs SET state='refresh_started' WHERE target_date=? AND state='approval_pending'",(TARGET,))
        original=json.loads(r['original']);key=r['case_uuid'];expected=json.loads(r['approval'])
    try:
        from .next_saved_cli import load,inventory,proof
        from .current_day_cli import scoped_meal
        from .buffer_publisher import production_client
        from .domain import Candidate
        from .instagram_domain import InstagramCandidate
        from .replacement_bundle import refresh
        d,check=load(config['path'])
        if fingerprint(d)!=config['sha256'] or d['approval']!=expected or d['original']!=original:raise ValueError('翌日追加準備の原本が違います')
        for path,sha in d['files'].items():
            p=Path(path)
            if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise ValueError('原本画像が変わりました')
        xs=[Candidate(**json.loads(c['payload'])) for c in members if 'x_id' in c]
        igs=[InstagramCandidate(**json.loads(c['payload'])) for c in members if 'x_id' not in c]
        api=production_client(store);inventory(store,api,d,'approval',clock().timestamp()+120,clock)
        result=proof(store,scoped_meal(),xs,igs,clock,expected['snapshot_digest']);check()
        refresh(store,TARGET,expected['snapshot_digest'],result,clock())
        with store.transaction() as db:
            b=db.execute('SELECT state FROM replacement_bundles WHERE target_date=?',(TARGET,)).fetchone()
            if not b or b['state']!='released':raise ValueError('八件承認の公開停止解除を確認できません')
            db.execute("UPDATE next_saved_jobs SET state='complete' WHERE target_date=? AND state='refresh_started'",(TARGET,))
    except BaseException as error:
        from .audit import safe_failure
        reason,details=safe_failure(error)
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS next_saved_failures(id INTEGER PRIMARY KEY,target_date TEXT,reason TEXT,details TEXT)')
            db.execute('INSERT INTO next_saved_failures(target_date,reason,details) VALUES(?,?,?)',(TARGET,reason,json.dumps(details)))
            db.execute("UPDATE next_saved_jobs SET state='held' WHERE target_date=? AND state='refresh_started'",(TARGET,))
