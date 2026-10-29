"""当日準備の一度だけの例外権限と送信claim。SNS権限は発行しない。"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
import json,os,re
from pathlib import Path
from .current_day_origin import TARGET,VERSION,capture
from .preparation import fingerprint
from .replacement_client import timestamp
from .publication_hold import exists,PublicationHeld
from .replacement_contract import stamp

CURRENT=ContextVar('current_day_preparation',default=None)


class Gate:
    def __init__(self,store,approval,clock,check_code,inventory=None):
        self.store,self.approval,self.clock,self.check_code=store,approval,clock,check_code
        self.inventory,self.latest_inventory=inventory,None

    def issue(self,draft,policy,api_state,inventory):
        """新初回欄だけを作る。再発行・旧期限の更新は認めない。"""
        from .m1_contract import validate_state
        from .domain import JST,ACCOUNT
        from .publication_hold import initialize
        from .clients import MealClient
        self.check_code();now=self.clock();a=self.approval
        if os.getenv('MINA_CURRENT_DAY_RECOVERY_ENABLED','false').lower()!='true':raise ValueError('current_day_recovery_disabled')
        required={'spec','target_date','recovery_uuid','code_commit','not_after','origin_digest','initial_state_version','approval_reference','mode','cost_mode'}
        if set(a)!=required or a['spec']!=VERSION or a['target_date']!=TARGET or a['mode']!='prepare_only' or a['cost_mode']!='waived':raise ValueError('当日権限の範囲が不正です')
        MealClient.request_key(a['recovery_uuid'])
        if (not isinstance(a['approval_reference'],str) or not 1<=len(a['approval_reference'])<=200
                or not re.fullmatch('[0-9a-f]{40}',a['code_commit']) or now.astimezone(JST).date().isoformat()!=TARGET):raise ValueError('当日権限の承認・日付が不正です')
        end=timestamp(a['not_after'])
        if end.astimezone(JST).date().isoformat()!=TARGET or (end-now).total_seconds()<60:raise ValueError('当日権限の残時間不足')
        if validate_state(api_state,TARGET,now)!=a['initial_state_version'] or api_state['meal_count']!=0 or api_state.get('daily_share') is not None:
            raise ValueError('当日の既存保存/共有を照合してください')
        if inventory.get('account')!=ACCOUNT or inventory.get('target_date')!=TARGET or not inventory.get('pages'):
            raise ValueError('全外部予約を確認できません')
        if not 0<=(now-timestamp(inventory['read_started_at'])).total_seconds()<=120 or {r['channel'] for r in inventory['pages']}!={'x','instagram'}:
            raise ValueError('全外部予約の鮮度・全頁証拠不足')
        with self.store.transaction() as db:
            from .replacement_contract import digest,canonical
            if not exists(db,'replacement_inventory_checks') or not db.execute('SELECT 1 FROM replacement_inventory_checks WHERE digest=? AND receipt=?',
                (digest(inventory),canonical(inventory).decode())).fetchone():raise ValueError('外部予約の実取得原本がありません')
            origin=capture(db,draft,policy)
            if fingerprint(origin)!=a['origin_digest']:raise ValueError('別承認と旧原本が一致しません')
            for table in ('mina_requests','meal_replacement_requests','replacement_attempts','candidates','instagram_candidates','publication_mutation_leases','buffer_publication','instagram_attempts'):
                if exists(db,table) and db.execute('SELECT 1 FROM '+table+' WHERE target_date=?',(TARGET,)).fetchone():raise ValueError('既存要求・候補がある日を初期化しません')
            if exists(db,'instagram_drafts') and db.execute("SELECT 1 FROM instagram_drafts WHERE state<>'verified'").fetchone():raise ValueError('外部下書きの照合が必要です')
            initialize(db)
            if db.execute('SELECT 1 FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone():raise ValueError('別の公開holdが存在します')
            db.execute('''CREATE TABLE IF NOT EXISTS current_day_jobs (
                target_date TEXT PRIMARY KEY,recovery_uuid TEXT NOT NULL UNIQUE,approval TEXT NOT NULL,approval_digest TEXT NOT NULL,
                origin TEXT NOT NULL,origin_digest TEXT NOT NULL,activated_at TEXT NOT NULL,deadline TEXT NOT NULL,state TEXT NOT NULL)''')
            if db.execute('SELECT 1 FROM current_day_jobs WHERE target_date=?',(TARGET,)).fetchone():raise ValueError('当日例外は再発行しません')
            deadline=min(end,now+timedelta(minutes=15))
            db.execute('INSERT INTO current_day_jobs VALUES(?,?,?,?,?,?,?,?,?)',(TARGET,a['recovery_uuid'],json.dumps(a),fingerprint(a),json.dumps(origin),fingerprint(origin),stamp(now),stamp(deadline),'issued'))
            self.store.audit.emit('creation',TARGET,None,None,'recovery','started',
                details={'input_sha256':fingerprint(a),'source_commit':a['code_commit']},now=timestamp(stamp(now)),db=db)
            db.execute("INSERT INTO publication_holds VALUES(?,1,?,'held',?,'current_day_prepare_only')",(TARGET,a['recovery_uuid'],now.timestamp()))
            db.execute('''CREATE TABLE IF NOT EXISTS scoped_preparation_locks (
                target_date TEXT PRIMARY KEY,case_uuid TEXT NOT NULL,owner TEXT NOT NULL,epoch INTEGER NOT NULL,
                window_json TEXT NOT NULL,candidates_digest TEXT NOT NULL)''')
            # 旧コードが読むcase_uuidカラムもAgent作業IDであり、本体caseの発行ではない。
            db.execute('INSERT INTO scoped_preparation_locks VALUES(?,?,?,1,?,?)',(TARGET,a['recovery_uuid'],a['recovery_uuid'],json.dumps({'contract':VERSION,'mode':'prepare_only'}),fingerprint(a)))
        self.latest_inventory=inventory
        return deadline

    def refresh_external(self):
        # 長い生成の後でも、停止済み/期限後の権限で新しい外部一覧を取得しない。
        if os.getenv('MINA_CURRENT_DAY_RECOVERY_ENABLED','false').lower()!='true':raise ValueError('current_day_recovery_disabled')
        self.check_code()
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM current_day_jobs WHERE target_date=?',(TARGET,)).fetchone()
            if (not row or row['state'] not in ('issued','running') or row['approval_digest']!=fingerprint(self.approval)
                    or row['origin_digest']!=self.approval['origin_digest'] or json.loads(row['approval'])!=self.approval
                    or self.clock()<timestamp(row['activated_at']) or (timestamp(row['deadline'])-self.clock()).total_seconds()<60):
                raise ValueError('外部予約を再取得できる当日権限がありません')
            from .current_day_origin import stored,verify_stored_rows
            verify_stored_rows(db,stored(db))
        if self.latest_inventory is not None and 0<=(self.clock()-timestamp(self.latest_inventory['read_started_at'])).total_seconds()<90:return
        if self.inventory is None:raise ValueError('外部予約の再取得adapterがありません')
        self.latest_inventory=self.inventory.check(TARGET)

    def check(self,db=None,*,sending=False):
        self.check_code()
        if os.getenv('MINA_CURRENT_DAY_RECOVERY_ENABLED','false').lower()!='true':raise ValueError('current_day_recovery_disabled')
        if db is None:
            with self.store.transaction() as connection:return self.check(connection,sending=sending)
        database=next((r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main'),None)
        if not database or Path(database).resolve()!=Path(self.store.path).resolve():raise ValueError('別のDBへ当日権限を転用しません')
        row=db.execute('SELECT * FROM current_day_jobs WHERE target_date=?',(TARGET,)).fetchone()
        if (not row or row['state']!='running' or row['approval_digest']!=fingerprint(self.approval) or row['origin_digest']!=self.approval['origin_digest']
                or json.loads(row['approval'])!=self.approval):raise ValueError('当日権限の版・実行段階が不正です')
        from .current_day_origin import stored,verify_stored_rows
        verify_stored_rows(db,stored(db))
        now=self.clock()
        first=[r['occurred'] for r in db.execute("SELECT occurred,details FROM operation_events WHERE target_date=? AND stage='recovery' AND event='started'",(TARGET,))
            if json.loads(r['details']).get('input_sha256')==fingerprint(self.approval)]
        if not first or min(first)!=timestamp(row['activated_at']).timestamp() or timestamp(row['deadline'])!=min(timestamp(self.approval['not_after']),timestamp(row['activated_at'])+timedelta(minutes=15)):
            raise ValueError('例外の初回時刻・期限が変更されています')
        if now<timestamp(row['activated_at']) or now>=timestamp(row['deadline']) or (sending and (timestamp(row['deadline'])-now).total_seconds()<60):raise ValueError('当日準備期限を超過しました')
        h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone()
        lock=db.execute('SELECT owner,epoch FROM scoped_preparation_locks WHERE target_date=?',(TARGET,)).fetchone()
        if not h or not lock or (h['owner'],h['epoch'],h['state'])!=(self.approval['recovery_uuid'],1,'held') or tuple(lock)!=(self.approval['recovery_uuid'],1):raise PublicationHeld()
        from .replacement_contract import digest,canonical
        receipt=self.latest_inventory
        if (receipt is None or receipt.get('target_date')!=TARGET or not 0<=(now-timestamp(receipt['read_started_at'])).total_seconds()<=120
                or not db.execute('SELECT 1 FROM replacement_inventory_checks WHERE digest=? AND receipt=?',
                    (digest(receipt),canonical(receipt).decode())).fetchone()):raise ValueError('外部予約の鮮度・原本を再照合してください')

    @contextmanager
    def running(self):
        # 終端jobの再実行では、外部一覧の再取得すら新たに開始しない。
        with self.store.transaction() as db:
            row=db.execute('SELECT state,deadline FROM current_day_jobs WHERE target_date=? AND approval_digest=?',(TARGET,fingerprint(self.approval))).fetchone()
            if not row or row['state']!='issued' or (timestamp(row['deadline'])-self.clock()).total_seconds()<60:raise ValueError('実行可能な未消費の当日権限がありません')
        self.refresh_external()
        with self.store.transaction() as db:
            changed=db.execute("UPDATE current_day_jobs SET state='running' WHERE target_date=? AND approval_digest=? AND state='issued'",(TARGET,fingerprint(self.approval))).rowcount
            if changed!=1:raise ValueError('再起動/二重実行は照会から復旧してください')
        token=CURRENT.set(self)
        try:
            self.check(sending=True)
            yield
        except BaseException:
            with self.store.transaction() as db:db.execute("UPDATE current_day_jobs SET state='held' WHERE target_date=? AND state='running'",(TARGET,))
            raise
        else:
            with self.store.transaction() as db:db.execute("UPDATE current_day_jobs SET state='complete' WHERE target_date=? AND state='running'",(TARGET,))
        finally:CURRENT.reset(token)


def require_m1(db,request):
    gate=CURRENT.get()
    if gate is None:return False
    gate.check(db,sending=True)
    if request['target_date']!=TARGET or request['operation'] not in ('meal_register','meal_correction','daily_share'):raise ValueError('当日M1の限定範囲外です')
    if request['slot'] not in (('daily',) if request['operation']=='daily_share' else ('breakfast','lunch','dinner')):raise ValueError('M1操作と対象枠が一致しません')
    db.execute('''CREATE TABLE IF NOT EXISTS current_day_m1_claims (
        target_date TEXT NOT NULL,slot TEXT NOT NULL,operation TEXT NOT NULL,request_key TEXT NOT NULL UNIQUE,
        input_digest TEXT NOT NULL,PRIMARY KEY(target_date,slot,operation))''')
    db.execute('INSERT INTO current_day_m1_claims VALUES(?,?,?,?,?)',(TARGET,request['slot'],request['operation'],request['request_key'],request['input_digest']))
    return True


def require_stage(store,target,db=None):
    """別承認日の工程は通常CLIから再生成しない。読取検査とは分ける。"""
    from .next_saved import guard_stage
    if db is None:
        with store.transaction() as connection:guard_stage(store,target,connection)
    else:guard_stage(store,target,db)
    if target!=TARGET:return
    if db is None:
        gate=CURRENT.get()
        if gate is not None and gate.store.path==store.path:gate.refresh_external()
        with store.transaction() as connection:return require_stage(store,target,connection)
    if not exists(db,'current_day_jobs') or not db.execute('SELECT 1 FROM current_day_jobs WHERE target_date=?',(TARGET,)).fetchone():return
    gate=CURRENT.get()
    if gate is None or gate.store.path!=store.path:raise ValueError('限定operator以外の当日処理は禁止です')
    gate.check(db,sending=True)


def safe_digest(value):
    """原画像・食品原文・promptを複製せず呼出入力を相関する。"""
    if isinstance(value,bytes):return {'bytes_sha256':__import__('hashlib').sha256(value).hexdigest(),'length':len(value)}
    if isinstance(value,dict):return {k:safe_digest(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [safe_digest(v) for v in value]
    return value


class Operations:
    """実費成否を0へ戻さない論理操作台帳。SDKの自動retryは許可しない。"""
    def __init__(self,gate):
        self.gate=gate
        with gate.store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS current_day_calls (
                id INTEGER PRIMARY KEY,recovery_uuid TEXT NOT NULL,operation TEXT NOT NULL,input_digest TEXT NOT NULL,
                state TEXT NOT NULL,result_digest TEXT,cost_mode TEXT NOT NULL)''')

    def invoke(self,name,inputs,call,lookup=False):
        self.gate.refresh_external()
        self.gate.check(sending=True)
        with self.gate.store.transaction() as db:
            self.gate.check(db,sending=True)
            if not lookup and db.execute("SELECT 1 FROM current_day_calls WHERE recovery_uuid=? AND state<>'complete'",(self.gate.approval['recovery_uuid'],)).fetchone():raise ValueError('外部操作の成否照合が必要です')
            key=db.execute("INSERT INTO current_day_calls(recovery_uuid,operation,input_digest,state,cost_mode) VALUES(?,?,?,'started','waived')",
                (self.gate.approval['recovery_uuid'],name,fingerprint(safe_digest(inputs)))).lastrowid
            record=getattr(self.gate,'record_call',None)
            if record is not None:record(db,key,name)
        from .external_guard import guard,normal_retry
        token=guard.set(lambda:self.gate.check(sending=True));retry=normal_retry.set(False)
        try:
            result=call()
            result_digest=fingerprint(safe_digest(result))
        except BaseException:
            with self.gate.store.transaction() as db:db.execute("UPDATE current_day_calls SET state='unknown' WHERE id=?",(key,))
            raise
        else:
            with self.gate.store.transaction() as db:db.execute("UPDATE current_day_calls SET state='complete',result_digest=? WHERE id=?",(result_digest,key))
            return result
        finally:guard.reset(token);normal_retry.reset(retry)

    def client(self,client,prefix,methods):
        operations=self
        class Checked:
            def __getattr__(self,name):
                value=getattr(client,name)
                if not callable(value):return value
                if name not in methods:raise ValueError('限定外の外部操作です')
                return lambda *args,**kwargs:operations.invoke(prefix+':'+name,{'args':args,'kwargs':kwargs},lambda:value(*args,**kwargs),lookup=name in ('day_state','request_state'))
        return Checked()
