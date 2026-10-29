"""停止済み親jobを更新せず、一度だけの別承認で未開始工程を継続する。"""
from contextlib import contextmanager
from datetime import timedelta
import json,os
from pathlib import Path
from .current_day_gate import Gate,CURRENT,Operations
from .current_day_origin import TARGET
from .current_day_checkpoint import VERSION,verify
from .preparation import fingerprint
from .replacement_client import timestamp
from .replacement_contract import stamp,canonical,digest


class ContinuationGate(Gate):
    def __init__(self,store,approval,snapshot,clock,check_code,inventory=None):
        super().__init__(store,approval,clock,check_code,inventory);self.snapshot=snapshot

    def parent(self,db):
        verify(db,self.snapshot)
        if (fingerprint(self.snapshot)!=self.approval['checkpoint_digest'] or self.snapshot['parent']['recovery_uuid']!=self.approval['recovery_uuid']):raise ValueError('別の停止原本へ継続権限を転用しません')
        h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone()
        lock=db.execute('SELECT owner,epoch FROM scoped_preparation_locks WHERE target_date=?',(TARGET,)).fetchone()
        if not h or not lock or (h['owner'],h['epoch'],h['state'])!=(self.approval['recovery_uuid'],1,'held') or tuple(lock)!=(self.approval['recovery_uuid'],1):raise ValueError('公開hold/所有世代が変わっています')

    def enabled(self):
        if os.getenv('MINA_CURRENT_DAY_CONTINUATION_ENABLED','false').lower()!='true':raise ValueError('current_day_continuation_disabled')
        self.check_code()

    def authority(self,db,states,sending):
        self.enabled();self.parent(db)
        actual=next((r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main'),None)
        if not actual or Path(actual).resolve()!=Path(self.store.path).resolve():raise ValueError('別のDBへ継続しません')
        r=db.execute('SELECT * FROM current_day_continuations WHERE target_date=?',(TARGET,)).fetchone()
        if (not r or r['state'] not in states or r['approval_digest']!=fingerprint(self.approval) or json.loads(r['approval'])!=self.approval):raise ValueError('継続権限の段階・版が不一致です')
        first=[e['occurred'] for e in db.execute("SELECT occurred,details FROM operation_events WHERE target_date=? AND stage='recovery' AND event='started'",(TARGET,)) if json.loads(e['details']).get('input_sha256')==fingerprint(self.approval)]
        if not first or min(first)!=timestamp(r['activated_at']).timestamp() or timestamp(r['deadline'])!=min(timestamp(self.approval['not_after']),timestamp(r['activated_at'])+timedelta(minutes=15)):raise ValueError('継続の初回・期限が変わっています')
        now=self.clock()
        if now<timestamp(r['activated_at']) or now>=timestamp(r['deadline']) or (sending and (timestamp(r['deadline'])-now).total_seconds()<60):raise ValueError('継続期限を超過しました')
        return r

    def receipt(self,db):
        r=self.latest_inventory
        if (not r or r.get('target_date')!=TARGET or not 0<=(self.clock()-timestamp(r['read_started_at'])).total_seconds()<=120
                or not db.execute('SELECT 1 FROM replacement_inventory_checks WHERE digest=? AND receipt=?',(digest(r),canonical(r).decode())).fetchone()):raise ValueError('継続の外部一覧を再照合してください')

    def check(self,db=None,*,sending=False):
        if db is None:
            with self.store.transaction() as connection:return self.check(connection,sending=sending)
        self.authority(db,('running',),sending);self.receipt(db)

    def refresh_external(self):
        with self.store.transaction() as db:self.authority(db,('issued','running'),True)
        if self.latest_inventory and 0<=(self.clock()-timestamp(self.latest_inventory['read_started_at'])).total_seconds()<90:return
        if self.inventory is None:raise ValueError('継続の外部一覧adapterがありません')
        self.latest_inventory=self.inventory.check(TARGET)

    def issue(self,state,receipt):
        from .clients import MealClient
        from .m1_contract import validate_state
        from .domain import JST,ACCOUNT
        self.enabled();a=self.approval;now=self.clock()
        required={'spec','target_date','recovery_uuid','continuation_uuid','code_commit','not_after','checkpoint_digest','initial_state_version','approval_reference','mode','cost_mode'}
        import re
        if (set(a)!=required or a['spec']!=VERSION or a['target_date']!=TARGET or a['mode']!='prepare_only' or a['cost_mode']!='waived'
                or not re.fullmatch('[a-f0-9]{40}',a['code_commit']) or not isinstance(a['approval_reference'],str) or not 1<=len(a['approval_reference'])<=200):raise ValueError('継続権限の範囲が不正です')
        for key in ('recovery_uuid','continuation_uuid'):MealClient.request_key(a[key])
        end=timestamp(a['not_after'])
        if (a['continuation_uuid']==a['recovery_uuid'] or now.astimezone(JST).date().isoformat()!=TARGET or end.astimezone(JST).date().isoformat()!=TARGET or (end-now).total_seconds()<60):raise ValueError('継続の日時・IDが不正です')
        if validate_state(state,TARGET,now)!=a['initial_state_version'] or state['meal_count']!=0 or state.get('daily_share') is not None:raise ValueError('既存正本がある日は継続しません')
        if (receipt.get('account')!=ACCOUNT or receipt.get('target_date')!=TARGET or not receipt.get('pages')
                or {p['channel'] for p in receipt['pages']}!={'x','instagram'}):raise ValueError('継続の全外部一覧不足')
        with self.store.transaction() as db:
            self.parent(db);self.latest_inventory=receipt;self.receipt(db)
            from .current_day_checkpoint import rows
            from .publication_hold import exists
            for t in ('mina_requests','meal_replacement_requests','replacement_attempts','candidates','instagram_candidates','publication_mutation_leases','buffer_publication','instagram_attempts'):
                if exists(db,t) and rows(db,t):raise ValueError('要求・公開・予約がある日は継続しません')
            if exists(db,'current_day_cards') and db.execute('SELECT 1 FROM current_day_cards WHERE recovery_uuid=?',(a['recovery_uuid'],)).fetchone():raise ValueError('既存の準備カード・送付があります')
            calls=[dict(r) for r in db.execute('SELECT * FROM current_day_calls WHERE recovery_uuid=? ORDER BY id',(a['recovery_uuid'],))]
            if calls!=self.snapshot['calls'] or rows(db,'preparation_steps')!=self.snapshot['tables']['preparation_steps']:raise ValueError('停止後に別の操作・工程が追加されています')
            if exists(db,'instagram_drafts') and db.execute("SELECT 1 FROM instagram_drafts WHERE state<>'verified'").fetchone():raise ValueError('未照合draftがあります')
            db.execute('''CREATE TABLE IF NOT EXISTS current_day_continuations (
                target_date TEXT PRIMARY KEY,continuation_uuid TEXT NOT NULL UNIQUE,recovery_uuid TEXT NOT NULL UNIQUE,
                approval TEXT NOT NULL,approval_digest TEXT NOT NULL,activated_at TEXT NOT NULL,deadline TEXT NOT NULL,state TEXT NOT NULL)''')
            if db.execute('SELECT 1 FROM current_day_continuations WHERE target_date=?',(TARGET,)).fetchone():raise ValueError('継続は再発行しません')
            deadline=min(end,now+timedelta(minutes=15))
            db.execute('INSERT INTO current_day_continuations VALUES(?,?,?,?,?,?,?,?)',(TARGET,a['continuation_uuid'],a['recovery_uuid'],json.dumps(a),fingerprint(a),stamp(now),stamp(deadline),'issued'))
            self.store.audit.emit('creation',TARGET,None,None,'recovery','started',details={'input_sha256':fingerprint(a),'source_commit':a['code_commit']},now=timestamp(stamp(now)),db=db)
        return deadline

    @contextmanager
    def running(self):
        with self.store.transaction() as db:self.authority(db,('issued',),True)
        self.refresh_external()
        with self.store.transaction() as db:
            self.authority(db,('issued',),True)
            if db.execute("UPDATE current_day_continuations SET state='running' WHERE target_date=? AND state='issued' AND approval_digest=?",(TARGET,fingerprint(self.approval))).rowcount!=1:raise ValueError('継続の二重実行を拒否します')
        token=CURRENT.set(self)
        try:
            self.check(sending=True);yield
        except BaseException:
            with self.store.transaction() as db:db.execute("UPDATE current_day_continuations SET state='held' WHERE target_date=? AND state='running'",(TARGET,))
            raise
        else:
            with self.store.transaction() as db:db.execute("UPDATE current_day_continuations SET state='complete' WHERE target_date=? AND state='running'",(TARGET,))
        finally:CURRENT.reset(token)


def execute(gate,ai,drive,meal,host,evidence,capture,discord,commit):
    """朝昼を再生成せず、未開始の昼点検→夕画像→既存の登録・カード工程へ進む。"""
    from .preparation import prepare_images,PreparationJournal
    from .current_day_recovery import bounded_drive,finish
    from .preparation_status import QualityHold
    from .daily_pipeline import write_artifact
    out=Path(gate.snapshot['output']);path=out/'draft.json';draft=json.loads(path.read_bytes())
    from .readiness import check_evidence
    check_evidence(evidence,TARGET,gate.clock())
    if commit!=gate.approval['code_commit'] or fingerprint(draft)!=gate.snapshot['draft_digest']:raise ValueError('継続の採用計画・実装版が不一致です')
    with gate.running(),bounded_drive(drive,gate):
        ops=Operations(gate);ai=ops.client(ai,'ai',{'structured','image','review_image','review_meal','review_ogp'})
        drive=ops.client(drive,'drive',{'upload','verify'});meal=ops.client(meal,'m1',{'day_state','request_state','register','json_write'})
        reader=lambda url,host:ops.invoke('ogp:read',{'url':url},lambda:capture(url,host),lookup=True)
        from .m1_contract import validate_state
        state=meal.day_state(TARGET)
        if validate_state(state,TARGET,gate.clock())!=gate.approval['initial_state_version'] or state['meal_count']!=0:raise ValueError('継続発行後の正本変更')
        images={}
        # 旧Journalへ同じ入力/path/roundを渡し、complete画像を再利用する。
        for slot in ('breakfast','lunch'):
            r=gate.snapshot['rounds'][slot]
            value=prepare_images(gate.store,path,out/'images',ai,replacement_round=r,replace_slots=(slot,),only_slots=(slot,))
            images.update(value['images'])
        try:value=prepare_images(gate.store,path,out/'images',ai,only_slots=('dinner',))
        except QualityHold as error:
            if error.step!='image_review:dinner' or set(error.reason_codes)!={'image_analysis_mismatch'}:raise
            round_key=PreparationJournal(gate.store).run(TARGET,'automatic_image_repair:dinner',{'meal':draft['meals']['dinner'],'reason_codes':list(error.reason_codes)},lambda:{'round':__import__('uuid').uuid4().__str__()})['round']
            value=prepare_images(gate.store,path,out/'images',ai,replacement_round=round_key,replace_slots=('dinner',),only_slots=('dinner',))
        images.update(value['images']);reviewed={'target_date':TARGET,'state':'images_reviewed','images':images}
        write_artifact(out/'images-reviewed.json',reviewed)
        return finish(gate,draft,reviewed,out,drive,meal,ai,host,evidence,reader,discord,commit)
