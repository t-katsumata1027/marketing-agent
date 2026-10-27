"""保存済み専用のP/D/C調停。通常登録へfallbackせず、未解決はGETで照合する。"""
from .external_guard import managed_call
import hashlib
import json
import uuid
from functools import wraps
from .replacement_contract import Contract,canonical,digest,stamp,source_digest as digest_source
from .replacement_journal import ReplacementJournal
from .replacement_client import ImageDescriptor
from .publication_hold import acquire

class PreflightFailure(ValueError):
    """外部原文を持たず、確認段階の固定停止理由を監査へ引き渡す。"""
    audit_stage='reconciliation'
    def __init__(self,reason):
        self.audit_reason=reason
        super().__init__(reason)

def tracked(method):
    """停止した段階と固定理由を追記し、例外の生文・UUIDは監査表示へ出さない。"""
    @wraps(method)
    def invoke(self,target,*args,**kwargs):
        try:return method(self,target,*args,**kwargs)
        except Exception as error:
            reasons=set(self.contract.schema['$defs']['Reason']['enum'])|{
                'replacement_external_lookup_required','replacement_concurrent_operation','replacement_source_changed',
                'replacement_failed_image_reused','replacement_menu_unchanged','replacement_conflict','replacement_lookup_required',
                'replacement_policy_unverified','replacement_original_source_changed','replacement_drive_evidence_invalid',
                'replacement_saved_result_changed','replacement_rebuild_source_changed','replacement_bundle_wrong_source',
                'replacement_original_queue_changed','replacement_original_instagram_review_required',
                'replacement_candidate_input_changed','replacement_candidate_target_changed','replacement_review_invalid',
                'replacement_hold_changed','replacement_budget_policy_changed','replacement_call_lookup_only',
                'external_dispatch_unverified','external_dispatch_present','external_inventory_incomplete','external_inventory_stale',
                'budget_unconfigured','budget_exceeded','replacement_no_request_to_reconcile',
                'replacement_preflight_busy','replacement_preflight_not_pristine','replacement_source_incomplete'}
            reason=str(error) if str(error) in reasons else 'unconfirmed'
            with self.store.transaction() as db:
                e=db.execute('SELECT stage,slot FROM replacement_episodes WHERE target_date=?',(target,)).fetchone()
                db.execute('INSERT INTO replacement_stop_events(target_date,operation,stage,reason,occurred_at) VALUES(?,?,?,?,?)',
                           (target,method.__name__,e['stage'] if e else 'precheck',reason,stamp(self.clock())))
                db.execute('UPDATE replacement_episodes SET reason=? WHERE target_date=?',(reason,target))
            if reason in ('external_dispatch_present','external_dispatch_unverified','external_inventory_incomplete',
                          'external_inventory_stale','replacement_source_incomplete','replacement_preflight_busy'):
                raise PreflightFailure(reason) from None
            raise
    return invoke

class ReplacementFlow:
    def __init__(self,store,client,policy,inventory,ports,clock):
        self.store,self.client,self.policy,self.inventory,self.ports,self.clock=store,client,policy,inventory,ports,clock
        self.contract=Contract(clock);self.journal=ReplacementJournal(store)
        with store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_stop_events (
                id INTEGER PRIMARY KEY,target_date TEXT,operation TEXT,stage TEXT,reason TEXT,occurred_at TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_external_calls (
                call_key TEXT PRIMARY KEY,target_date TEXT,kind TEXT,state TEXT,encoding TEXT,value BLOB,completed_at TEXT)''')
            db.execute('CREATE TABLE IF NOT EXISTS replacement_original_queue (target_date TEXT PRIMARY KEY,snapshot TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_episodes (
                target_date TEXT PRIMARY KEY,slot TEXT NOT NULL,owner TEXT NOT NULL,epoch INTEGER NOT NULL,
                stage TEXT NOT NULL,old_source TEXT NOT NULL,prepare_uuid TEXT,discard_uuid TEXT,commit_uuid TEXT,
                deadline REAL NOT NULL,reason TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_materials (
                prepare_uuid TEXT PRIMARY KEY,plan TEXT NOT NULL,image BLOB NOT NULL,sha256 TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_wire (
                request_key TEXT PRIMARY KEY,metadata TEXT NOT NULL,content_type TEXT NOT NULL)''')

    def episode(self,target):
        with self.store.transaction() as db:
            r=db.execute('SELECT * FROM replacement_episodes WHERE target_date=?',(target,)).fetchone()
            if not r:raise ValueError('replacement_episode_missing')
            return dict(r)

    def progress(self,target,stage,reason=None,**keys):
        if not set(keys)<= {'prepare_uuid','discard_uuid','commit_uuid','deadline'}:raise ValueError('replacement_progress_invalid')
        with self.store.transaction() as db:
            for name,value in keys.items():db.execute('UPDATE replacement_episodes SET '+name+'=? WHERE target_date=?',(value,target))
            db.execute('UPDATE replacement_episodes SET stage=?,reason=? WHERE target_date=?',(stage,reason,target))
        self.store.audit.emit('creation',target,None,None,'recovery','observed',details={'matched':reason is None})

    def checkpoint(self,e,saved=False):
        if self.clock().timestamp()>=e['deadline']:raise ValueError('deadline_exceeded')
        with self.store.transaction() as db:
            h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(e['target_date'],)).fetchone()
            if not h or (h['owner'],h['epoch'],h['state'])!=(e['owner'],e['epoch'],'saved_pending_review' if saved else 'held'):raise ValueError('replacement_hold_changed')

    def external(self,e):
        receipt=self.inventory.check(e['target_date'])
        return {'hold_epoch':e['epoch'],'pause_receipt_digest':digest({'target_date':e['target_date'],'owner':e['owner'],'epoch':e['epoch']}),
            'provider_check_digest':digest(receipt),'provider_checked_at':receipt['checked_at']}

    def call(self,e,kind,operation,saved=False):
        self.checkpoint(e,saved=saved)
        with self.store.transaction() as db:
            ordinal=db.execute('SELECT max(ordinal) FROM replacement_attempts WHERE target_date=? AND slot=?',(e['target_date'],e['slot'])).fetchone()[0]
            key=digest({'owner':e['owner'],'epoch':e['epoch'],'ordinal':ordinal,'kind':kind})
            old=db.execute('SELECT * FROM replacement_external_calls WHERE call_key=?',(key,)).fetchone()
            if old:
                if old['state']!='complete':raise ValueError('replacement_external_lookup_required')
                return bytes(old['value']) if old['encoding']=='bytes' else json.loads(old['value'])
        self.policy.reserve(e['target_date'],key,kind)
        with self.store.transaction() as db:db.execute("INSERT INTO replacement_external_calls VALUES(?,?,?,'started',NULL,NULL,NULL)",(key,e['target_date'],kind))
        try:value=managed_call(operation)
        except BaseException:
            self.policy.called(key,False);raise
        encoding='bytes' if isinstance(value,bytes) else 'json';raw=value if encoding=='bytes' else canonical(value)
        with self.store.transaction() as db:db.execute("UPDATE replacement_external_calls SET state='complete',encoding=?,value=?,completed_at=? WHERE call_key=?",(encoding,raw,stamp(self.clock()),key))
        self.policy.called(key,True);return value

    def called_at(self,e,kind):
        with self.store.transaction() as db:
            row=db.execute('SELECT completed_at FROM replacement_external_calls WHERE target_date=? AND kind=? AND state=? ORDER BY completed_at DESC LIMIT 1',(e['target_date'],kind,'complete')).fetchone()
            if not row:raise ValueError('replacement_call_not_complete')
            return row[0]

    def policy_gate(self,p,*,initial=False,expired_open=False):
        """初回Pの実証跡経路だけを区別し、機能・費用方式・providerの拒否を維持する。"""
        policy=p['policy']
        costs_ok=(policy.get('cost_control')=={'mode':'waived','approval_reference_digest':self.policy.policy.get('approval_reference_digest')}
                  and policy.get('analysis_provider')=='gemini' and policy['budget_configured'] is False) if self.policy.waived else policy['budget_configured'] is True
        initial_ok=(initial and policy['existing_preparation_deadline_verified'] is False
                    and policy.get('analysis_provider')=='gemini'
                    and policy['blocking_reasons']==(['replacement_cooldown','deadline_unverified'] if expired_open else ['deadline_unverified']))
        if (not costs_ok or policy['policy_version']!=self.policy.policy['policy_version']
                or policy['budget_policy_digest']!=self.policy.policy_digest
                or not (initial_ok or policy['existing_preparation_deadline_verified'] is True and not policy['blocking_reasons'])):
            raise ValueError('replacement_policy_unverified')

    def target_policy(self,p,e):
        """既知対象contextの原本照合後も、他枠未開始という表示だけは保持する。"""
        initial=json.loads(e['old_source'])['policy']['existing_preparation_deadline_verified'] is False
        if initial:
            from .replacement_origin import verify_receipt
            with self.store.transaction() as db:
                wire=db.execute('SELECT metadata FROM replacement_wire WHERE request_key=?',(e['prepare_uuid'],)).fetchone()
                if not wire:raise ValueError('migration_unverified')
                metadata=json.loads(wire[0])
                verify_receipt(db,e['target_date'],e['slot'],metadata['migration'],e['owner'],e['epoch'])
        self.policy_gate(p,initial=initial)

    @tracked
    def begin(self,target,slot,*,quality_hold):
        from .preparation_status import QualityHold
        if (not isinstance(quality_hold,QualityHold) or quality_hold.step.split(':')[:2]!=['meal_review',slot]
                or not quality_hold.reason_codes or not set(quality_hold.reason_codes)<={'image_analysis_mismatch','nutrition_semantic_mismatch'}):
            raise ValueError('replacement_quality_hold_required')
        # 再起動・別UUIDによって同じ日の途中状態を上書きしない。
        with self.store.transaction() as db:
            if db.execute('SELECT 1 FROM replacement_episodes WHERE target_date=?',(target,)).fetchone():raise ValueError('replacement_resume_required')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='mina_requests'").fetchone() and db.execute("SELECT 1 FROM mina_requests WHERE target_date=? AND phase<>'complete'",(target,)).fetchone():raise ValueError('replacement_existing_request_unresolved')
        from .domain import approval_time,date
        if self.clock()>=approval_time(date.fromisoformat(target)):raise ValueError('deadline_exceeded')
        owner=str(uuid.uuid4());hold=acquire(self.store,target,owner,self.clock())
        if hold['state']!='held':raise ValueError('external_dispatch_unverified')
        e={'target_date':target,'slot':slot,'owner':owner,'epoch':hold['epoch'],'deadline':approval_time(date.fromisoformat(target)).timestamp()}
        from .instagram_preparation import owner as process_owner
        identity=process_owner()
        with self.store.transaction() as db:
            db.execute('INSERT INTO replacement_episodes VALUES(?,?,?,?,?,?,?,?,?,?,?)',(target,slot,owner,hold['epoch'],'checking','{}',None,None,None,e['deadline'],None))
            db.execute('CREATE TABLE IF NOT EXISTS replacement_preflight_owners (target_date TEXT PRIMARY KEY,identity TEXT)')
            db.execute('INSERT INTO replacement_preflight_owners VALUES(?,?)',(target,identity))
            from .publication_hold import exists
            snapshot={table:[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE target_date=?',(target,))]
                for table in ('candidates','instagram_candidates','buffer_publication','instagram_attempts') if exists(db,table)}
            db.execute('INSERT INTO replacement_original_queue VALUES(?,?)',(target,canonical(snapshot).decode()))
        try:return self._complete_preflight(target,e)
        finally:
            with self.store.transaction() as db:
                db.execute('UPDATE replacement_preflight_owners SET identity=NULL WHERE target_date=? AND identity=?',(target,identity))

    def _complete_preflight(self,target,e):
        """全外部予約の確認を先に行い、正式3食の成立後だけ候補生成へ進む。"""
        self.external(e)
        collect=getattr(self,'collect_missing',None)
        if collect is not None:collect(e)
        inspect=getattr(self,'inspect_composition',None)
        if inspect is not None:inspect(target)
        p=self.contract.preconditions(self.client.preconditions(target),target)
        if collect is not None:collect.verify_preconditions(p)
        if self.ports.previous_image_hashes()!={m['expected']['target_slot']:m['image_sha256'] for m in p['meals']}:
            raise ValueError('replacement_original_source_changed')
        from .replacement_control import observe_circuit,require_available
        observe_circuit(self.store,p,self.clock(),allow_probe=True)
        require_available(self.store,self.clock())
        circuit=p.get('circuit')
        from .replacement_contract import timestamp
        expired_open=bool(circuit and circuit['state']=='open' and not circuit['recovery_required']
                   and circuit['retry_at'] and timestamp(circuit['retry_at'])<=self.clock())
        self.policy_gate(p,initial=True,expired_open=expired_open)
        with self.store.transaction() as db:
            db.execute("UPDATE replacement_episodes SET stage='held',old_source=? WHERE target_date=?",(canonical(p).decode(),target))
        return self.next_candidate(target)

    @tracked
    def resume_checking(self,target):
        """UUID発行前の確認だけを同じholdで再開する。要求不明や生成途中へ流用しない。"""
        from .instagram_preparation import owner,alive
        e=self.episode(target);self.checkpoint(e)
        identity=owner()
        with self.store.transaction() as db:
            current=db.execute('SELECT * FROM replacement_episodes WHERE target_date=?',(target,)).fetchone()
            if not current or dict(current)!=e:raise ValueError('replacement_hold_changed')
            if (e['stage']!='checking' or e['old_source']!='{}' or not e['reason']
                    or any(e[k] is not None for k in ('prepare_uuid','discard_uuid','commit_uuid'))):
                raise ValueError('replacement_preflight_not_pristine')
            for table in ('meal_replacement_requests','replacement_attempts','replacement_external_calls'):
                if db.execute('SELECT 1 FROM '+table+' WHERE target_date=?',(target,)).fetchone():
                    raise ValueError('replacement_preflight_not_pristine')
            for table in ('replacement_cost_reservations','replacement_waived_calls'):
                unresolved=list(db.execute("SELECT * FROM "+table+" WHERE target_date=? AND state IN ('reserved','unknown')",(target,)))
                day=db.execute('SELECT policy_digest FROM replacement_budget_days WHERE target_date=?',(target,)).fetchone()
                legitimate={
                    'server-allocation:'+target:('server_allocation',self.policy.policy.get('server_allocation_units'),'reserved'),
                    'opening-exposure:'+target:('opening_exposure',self.policy.policy.get('opening_exposure_units'),'unknown')}
                for r in unresolved:
                    allowed=(table=='replacement_cost_reservations' and not self.policy.waived and day and day[0]==self.policy.policy_digest
                             and r['call_key'] in legitimate and (r['kind'],r['units'],r['state'])==legitimate[r['call_key']])
                    if not allowed:raise ValueError('replacement_preflight_not_pristine')
            if db.execute('SELECT 1 FROM replacement_materials m WHERE NOT EXISTS (SELECT 1 FROM meal_replacement_requests r WHERE r.request_key=m.prepare_uuid)').fetchone():
                raise ValueError('replacement_preflight_not_pristine')
            if db.execute("SELECT 1 FROM replacement_wire WHERE json_extract(metadata,'$.expected.target_date')=?",(target,)).fetchone():
                raise ValueError('replacement_preflight_not_pristine')
            snapshot=json.loads(db.execute('SELECT snapshot FROM replacement_original_queue WHERE target_date=?',(target,)).fetchone()[0])
            for table,rows in snapshot.items():
                if [dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE target_date=?',(target,))]!=rows:
                    raise ValueError('replacement_original_queue_changed')
            if db.execute("SELECT 1 FROM mina_requests WHERE target_date=? AND phase<>'complete'",(target,)).fetchone():
                raise ValueError('replacement_existing_request_unresolved')
            db.execute('CREATE TABLE IF NOT EXISTS replacement_preflight_owners (target_date TEXT PRIMARY KEY,identity TEXT)')
            previous=db.execute('SELECT identity FROM replacement_preflight_owners WHERE target_date=?',(target,)).fetchone()
            if previous and previous[0] and alive(previous[0]):raise ValueError('replacement_preflight_busy')
            db.execute('INSERT OR REPLACE INTO replacement_preflight_owners VALUES(?,?)',(target,identity))
        try:return self._complete_preflight(target,e)
        finally:
            with self.store.transaction() as db:
                db.execute('UPDATE replacement_preflight_owners SET identity=NULL WHERE target_date=? AND identity=?',(target,identity))

    @tracked
    def next_candidate(self,target):
        e=self.episode(target);self.checkpoint(e)
        if e['stage'] not in ('held','discarded','rejected'):raise ValueError('replacement_lookup_required')
        with self.store.transaction() as db:
            if db.execute("UPDATE replacement_episodes SET stage='generating' WHERE target_date=? AND stage=?",(target,e['stage'])).rowcount!=1:raise ValueError('replacement_concurrent_operation')
        old=json.loads(e['old_source'])
        fresh=self.contract.preconditions(self.client.preconditions(target),target)
        if digest_source(fresh)!=digest_source(old):raise ValueError('replacement_source_changed')
        from .replacement_control import observe_circuit,require_available
        observe_circuit(self.store,fresh,self.clock(),allow_probe=True);require_available(self.store,self.clock())
        self.policy_gate(fresh,initial=old['policy']['existing_preparation_deadline_verified'] is False)
        binding={'owner':e['owner'],'epoch':e['epoch'],'preconditions':fresh} if old['policy']['existing_preparation_deadline_verified'] is False else None
        migration,deadline=self.policy.attempt(target,e['slot'],binding=binding);self.progress(target,'generating',deadline=deadline)
        e=self.episode(target);old=json.loads(e['old_source']);plan=self.call(e,'menu',lambda:self.ports.plan(e['slot'],old,migration['attempt_ordinal']))
        from .generation import MEAL_SCHEMA
        from .m1_contract import normalized_comment
        from jsonschema import Draft202012Validator
        Draft202012Validator(MEAL_SCHEMA).validate(plan)
        plan['comment']=normalized_comment(plan['comment'])
        with self.store.transaction() as db:
            earlier=list(db.execute('SELECT m.plan,m.sha256 FROM replacement_materials m JOIN meal_replacement_requests r ON m.prepare_uuid=r.request_key WHERE r.target_date=? AND r.slot=?',(target,e['slot'])))
        if any(plan['menu']==json.loads(row['plan'])['menu'] or plan['foods']==json.loads(row['plan'])['foods'] for row in earlier):raise ValueError('replacement_menu_unchanged')
        image=self.call(e,'image',lambda:self.ports.image(plan))
        if not isinstance(image,bytes) or not 0<len(image)<=4194304:raise ValueError('replacement_image_invalid')
        expected=next(m for m in old['meals'] if m['expected']['target_slot']==e['slot'])
        if hashlib.sha256(image).hexdigest() in {expected['image_sha256'],*(row['sha256'] for row in earlier)}:raise ValueError('replacement_failed_image_reused')
        from PIL import Image
        from io import BytesIO
        with Image.open(BytesIO(image)) as im:mime=Image.MIME[im.format];im.verify()
        key=str(uuid.uuid4());hold=self.external(e)
        metadata={'contract_version':'mina-replace-v1','request_uuid':key,'expected':expected['expected'],'precondition_digest':expected['precondition_digest'],
            'input_image_sha256':hashlib.sha256(image).hexdigest(),'original_mime':mime,'normalized_comment':plan['comment'],
            'reason':'menu_replan_after_quality_hold','migration':migration,'publication_hold':hold}
        self.contract.validate('PrepareInput',metadata)
        with self.store.transaction() as db:
            db.execute('INSERT INTO replacement_materials VALUES(?,?,?,?)',(key,canonical(plan).decode(),image,metadata['input_image_sha256']))
        self.progress(target,'P_prepared',prepare_uuid=key,discard_uuid=None,commit_uuid=None)
        return self.send(target,'candidate_prepare',metadata,image)

    def send(self,target,operation,metadata,image=None):
        e=self.episode(target);self.checkpoint(e);key=metadata['request_uuid']
        if operation=='candidate_prepare':
            fresh=self.contract.preconditions(self.client.preconditions(target),target)
            old=json.loads(e['old_source'])
            if digest_source(fresh)!=digest_source(old):raise ValueError('replacement_source_changed')
            self.policy_gate(fresh,initial=old['policy']['existing_preparation_deadline_verified'] is False)
            from .replacement_control import observe_circuit
            observe_circuit(self.store,fresh,self.clock(),allow_probe=True)
            if self.clock().timestamp()+60>e['deadline']:raise ValueError('deadline_exceeded')
            if json.loads(e['old_source'])['policy']['existing_preparation_deadline_verified'] is False:
                from .replacement_origin import verify_receipt
                with self.store.transaction() as db:
                    verify_receipt(db,target,e['slot'],metadata['migration'],e['owner'],e['epoch'])
        from .replacement_control import require_available
        require_available(self.store,self.clock())
        key_field={'candidate_prepare':'prepare_uuid','candidate_discard':'discard_uuid','meal_replace':'commit_uuid'}.get(operation)
        if key_field is None or e[key_field]!=key:raise ValueError('replacement_request_target_changed')
        self.contract.validate({'candidate_prepare':'PrepareInput','candidate_discard':'DiscardInput','meal_replace':'CommitInput'}[operation],metadata)
        body=canonical(metadata);content_type='application/json'
        if image is not None:
            boundary='mina-replacement-'+key
            body=(f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n'.encode()+body+
                f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="meal"\r\nContent-Type: {metadata["original_mime"]}\r\n\r\n'.encode()+image+f'\r\n--{boundary}--\r\n'.encode())
            content_type='multipart/form-data; boundary='+boundary
        self.journal.prepare(key,operation,target,e['slot'],e['epoch'],e['owner'],body)
        with self.store.transaction() as db:db.execute('INSERT OR IGNORE INTO replacement_wire VALUES(?,?,?)',(key,canonical(metadata).decode(),content_type))
        # startと実送信の間の停止も不明として残し、再送しない。
        wire=self.journal.start(key);self.progress(target,operation+':started')
        from .replacement_control import ReplacementPaused,remember,require_available
        require_available(self.store,self.clock())
        if operation=='candidate_prepare' and self.clock().timestamp()+60>e['deadline']:
            raise ValueError('deadline_exceeded')
        try:value=self.client.send(operation,key,wire,content_type,e['prepare_uuid'])
        except ReplacementPaused as error:
            remember(self.store,error.envelope)
            request=error.envelope.get('request')
            if request and request.get('request_uuid')==key and request.get('operation')==operation:
                self.journal.envelope(key,error.envelope)
            self.progress(target,'paused','replacement_recovery_required')
            self.observe_pause(target)
            raise
        return self.accept(target,key,value)

    def accept(self,target,key,value):
        e=self.episode(target);row=self.journal.get(key)
        value=self.contract.decode('meal_replace' if row['operation']=='meal_replace' else row['operation'],value)
        if value.get('candidate') and value['candidate']['prepare_uuid']!=e['prepare_uuid']:raise ValueError('replacement_candidate_target_changed')
        from .replacement_control import remember
        remember(self.store,value)
        state=self.journal.envelope(key,value)
        if value['request']['stage']=='paused':
            self.progress(target,'paused',value['request'].get('reason'))
            self.observe_pause(target)
            return value
        if state.get('conflict'):self.progress(target,'unknown','replacement_conflict');return value
        if row['operation']=='meal_replace':stage='saved' if state['persistence']=='saved' else ('not_saved' if value['request']['status']=='failed' and state['persistence']=='not_saved' else 'unknown')
        elif state['outcome']=='applied':
            c=value.get('candidate')
            stage='unknown'
            if row['operation']=='candidate_prepare' and c and c['candidate_state']=='ready':stage='ready'
            if row['operation']=='candidate_discard' and c and c['candidate_state']=='discarded':
                with self.store.transaction() as db:wire=db.execute('SELECT metadata FROM replacement_wire WHERE request_key=?',(key,)).fetchone()
                if value['request']['discard_input']!=json.loads(wire[0]):raise ValueError('replacement_discard_input_changed')
                stage='discarded'
        else:stage='rejected' if row['operation']=='candidate_prepare' and state['outcome']=='rejected' and value.get('candidate',{}).get('candidate_state')=='rejected' else 'unknown'
        if row['operation']=='meal_replace' and stage=='saved' and e['stage'] in ('rebuilding','new_bundle_pending_approval'):stage=e['stage']
        self.progress(target,stage);return value

    def observe_pause(self,target):
        """停止時にOPENの実GET証跡を保存する。取得失敗から復旧許可を作らない。"""
        from .replacement_control import observe_circuit
        try:
            p=self.contract.preconditions(self.client.preconditions(target),target)
            observe_circuit(self.store,p,self.clock())
        except ValueError:
            # 停止・不明のまま維持する。後続の明示resumeは証跡がなければ拒否される。
            return

    @tracked
    def reconcile(self,target):
        """再開は既存UUIDを読み、要求の操作・入力版と照合するだけ。"""
        e=self.episode(target);key=e['commit_uuid'] or e['discard_uuid'] or e['prepare_uuid']
        if not key:raise ValueError('replacement_no_request_to_reconcile')
        row=self.journal.get(key);value=self.accept(target,key,self.client.request(key,row['operation']))
        from .replacement_control import initialize
        initialize(self.store)
        with self.store.transaction() as db:
            controls=[dict(r) for r in db.execute("SELECT r.request_key,r.observation,c.original_uuid FROM meal_replacement_requests r JOIN replacement_resume_claims c ON c.control_uuid=r.request_key WHERE r.target_date=? AND r.operation='replacement_resume' AND r.phase<>'prepared'",(target,))]
        for control in controls:
            observed=json.loads(control['observation']) if control['observation'] else {}
            if observed.get('outcome') in ('applied','rejected') and not observed.get('conflict'):continue
            result=self.client.request(control['request_key'],'replacement_resume')
            if result['request'].get('original_request_uuid')!=control['original_uuid']:raise ValueError('replacement_resume_target_changed')
            final=self.journal.envelope(control['request_key'],result)
            if final.get('conflict') or final.get('outcome') not in ('applied','rejected'):raise ValueError('replacement_resume_lookup_only')
        return value

    @tracked
    def finish(self,target,rebuild,host,hosts):
        """C後の再点検と候補再構成を予算内で行う。公開は人間の新版承認後だけ。"""
        e=self.episode(target);self.checkpoint(e,saved=True)
        if e['stage'] not in ('saved','rebuilding'):raise ValueError('replacement_saved_not_confirmed')
        response=self.reconcile(target)
        self.contract.decode('meal_replace',response)
        if response['request']['persistence']!='saved' or response['result_status']!='unchanged' or response['result']!=response['current_result']:
            raise ValueError('replacement_source_changed')
        p=self.contract.preconditions(self.client.preconditions(target),target)
        result=response['current_result'];own=next(m for m in p['meals'] if m['expected']['target_slot']==e['slot'])
        with self.store.transaction() as db:
            material=dict(db.execute('SELECT * FROM replacement_materials WHERE prepare_uuid=?',(e['prepare_uuid'],)).fetchone())
        if (own['expected']['target_result_version']!=result['result_version'] or result['image_sha256']!=material['sha256']
                or own['image_sha256']!=material['sha256'] or own['analysis']['foods']!=result['analysis']['foods']
                or not 1800<=p['summary']['totalCalories']<=2000):raise ValueError('replacement_saved_result_changed')
        self.progress(target,'rebuilding')
        from dataclasses import asdict
        def build():
            x,ig,proof=rebuild(e,response,p,material)
            return {'x':[asdict(c) for c in x],'ig':[asdict(c) for c in ig],'proof':proof}
        built=self.call(e,'rebuild',build,saved=True)
        from .domain import Candidate
        from .instagram_domain import InstagramCandidate
        x=[Candidate(**v) for v in built['x']];ig=[InstagramCandidate(**v) for v in built['ig']];proof=built['proof']
        if callable(getattr(rebuild,'verify_readonly',None)):
            _,proof=rebuild.verify_readonly(target,x_candidates=x,ig_candidates=ig)
        self.external(e)
        after=self.contract.preconditions(self.client.preconditions(target),target)
        def meals_signature(value):
            return [(m['expected']['target_slot'],m['expected']['target_result_version'],m['expected']['target_share_url'],m['analysis_digest'],m['image_sha256'],m['stored_image_sha256']) for m in value['meals']]
        if meals_signature(after)!=meals_signature(p) or p['daily_share'] is not None and after['daily_share']!=p['daily_share']:raise ValueError('replacement_source_changed')
        source_digest=digest_source(after)
        if proof.get('source_digest')!=source_digest:raise ValueError('replacement_rebuild_source_changed')
        for c in x:
            if c.approval_source.get('state_version')!=after['state_version']:raise ValueError('replacement_bundle_wrong_source')
            if (c.slot==e['slot'] or c.slot=='daily' and response['affected_daily_result'] is not None) and c.approval_source.get('request_key')!=e['commit_uuid']:raise ValueError('replacement_bundle_wrong_source')
        from .replacement_bundle import stage
        stage(self.store,target,e['owner'],e['epoch'],source_digest,x,ig,host,hosts,self.clock(),proof,required_channels=self.policy.policy.get('required_channels',('x','instagram')))
        self.progress(target,'new_bundle_pending_approval')
        return {'target_date':target,'state':'approval_pending','source_digest':source_digest}

    @tracked
    def restore_old(self,target,verify):
        """全要求の終端と旧正本不変をGETで確認した場合だけ、元のキューへ戻す。"""
        e=self.episode(target)
        with self.store.transaction() as db:rows=[dict(r) for r in db.execute('SELECT * FROM meal_replacement_requests WHERE target_date=?',(target,))]
        for row in rows:
            if row['phase']=='prepared':continue
            value=self.client.request(row['request_key'],row['operation']);self.contract.decode(row['operation'],value)
            observed=self.journal.envelope(row['request_key'],value)
            if observed.get('conflict'):raise ValueError('replacement_conflict')
            if row['operation']=='meal_replace':
                if value['request']['persistence']!='not_saved' or value['request']['status']!='failed':raise ValueError('replacement_lookup_required')
            elif value['request']['outcome'] not in ('applied','rejected') or not value.get('candidate') or value['candidate']['candidate_state'] not in ('discarded','rejected','expired'):
                raise ValueError('replacement_lookup_required')
        p=self.contract.preconditions(self.client.preconditions(target),target);old=json.loads(e['old_source'])
        if digest_source(p)!=digest_source(old):raise ValueError('replacement_original_source_changed')
        self.external(e);proof=verify(target,p)
        from .replacement_client import timestamp
        if proof.get('verdict') not in ('pass','empty_queue') or proof.get('source_digest')!=digest_source(p) or not 0<=(self.clock()-timestamp(proof['read_started_at'])).total_seconds()<=120:raise ValueError('replacement_original_review_required')
        with self.store.transaction() as db:
            from .publication_hold import exists
            h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(target,)).fetchone()
            if not h or (h['owner'],h['epoch'],h['state'])!=(e['owner'],e['epoch'],'held'):raise ValueError('replacement_hold_changed')
            original=json.loads(db.execute('SELECT snapshot FROM replacement_original_queue WHERE target_date=?',(target,)).fetchone()[0])
            if proof['verdict']=='empty_queue' and any(original.get(t) for t in ('candidates','instagram_candidates')):raise ValueError('replacement_original_queue_changed')
            for table,snapshot in original.items():
                current=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE target_date=?',(target,))]
                if current!=snapshot:raise ValueError('replacement_original_queue_changed')
            db.execute("UPDATE publication_holds SET state='released',reason='unchanged_original_verified' WHERE target_date=?",(target,))
            db.execute("UPDATE replacement_episodes SET stage='released_old' WHERE target_date=?",(target,))
        return {'target_date':target,'state':'released_old'}

    def candidate(self,e):
        value=self.client.candidate(e['prepare_uuid']);self.contract.decode('candidate',value);c=value['candidate'];self.contract.candidate(c,ready=True)
        if c['prepare_uuid']!=e['prepare_uuid']:raise ValueError('replacement_candidate_target_changed')
        with self.store.transaction() as db:
            material=db.execute('SELECT * FROM replacement_materials WHERE prepare_uuid=?',(e['prepare_uuid'],)).fetchone()
            wire=db.execute('SELECT metadata FROM replacement_wire WHERE request_key=?',(e['prepare_uuid'],)).fetchone()
        metadata=json.loads(wire[0]);old=json.loads(e['old_source'])
        if old['policy']['existing_preparation_deadline_verified'] is False:
            from .replacement_origin import verify_receipt
            from .replacement_contract import timestamp
            with self.store.transaction() as db:
                verify_receipt(db,e['target_date'],e['slot'],metadata['migration'],e['owner'],e['epoch'])
            if timestamp(c['hard_deadline_at']).timestamp()!=e['deadline']:
                raise ValueError('replacement_candidate_input_changed')
        if (c['prepared_preconditions']!=metadata['expected'] or c['prepared_publication_hold']!=metadata['publication_hold']
                or c['original']['sha256']!=material['sha256'] or c['normalized_comment']!=metadata['normalized_comment']
                or c['policy_version']!=metadata['migration']['policy_version'] or c['attempt_ordinal']!=metadata['migration']['attempt_ordinal']):raise ValueError('replacement_candidate_input_changed')
        return c,dict(material)

    @tracked
    def decide_candidate(self,target):
        e=self.episode(target);self.checkpoint(e)
        if e['stage']!='ready':raise ValueError('replacement_candidate_not_ready')
        with self.store.transaction() as db:
            if db.execute("UPDATE replacement_episodes SET stage='reviewing' WHERE target_date=? AND stage='ready'",(target,)).rowcount!=1:raise ValueError('replacement_concurrent_operation')
        c,material=self.candidate(e)
        images={role:self.client.image(e['prepare_uuid'],role,ImageDescriptor(c['candidate_version'],c[role]['sha256'],c[role]['byte_length'],c[role]['mime'])) for role in ('original','stored')}
        if images['original']!=material['image']:raise ValueError('replacement_candidate_input_changed')
        review=self.call(e,'review',lambda:self.ports.review(images,json.loads(material['plan']),c))
        if set(review)!= {'verdict','observations'} or review['verdict'] not in ('pass','fail') or not isinstance(review['observations'],list):raise ValueError('replacement_review_invalid')
        fresh=self.contract.preconditions(self.client.preconditions(target),target)
        self.target_policy(fresh,e)
        reason=None
        if review['verdict']=='fail':reason='independent_review_failed'
        else:
            try:nutrition=self.contract.nutrition(fresh,c)
            except ValueError as error:
                if str(error)!='nutrition_budget_failed':raise
                reason='nutrition_budget_failed'
        if reason:
            evidence={'candidate_version':c['candidate_version'],'analysis_digest':c['analysis_digest'],'original_sha256':c['original']['sha256'],
                'stored_sha256':c['stored']['sha256'],'reviewer_role':'independent_reviewer','reviewed_at':self.called_at(e,'review'),'verdict':'fail',
                'observations':['nutrition_budget_failed'] if reason=='nutrition_budget_failed' else review['observations']}
            metadata={'contract_version':'mina-replace-v1','request_uuid':str(uuid.uuid4()),'prepare_uuid':e['prepare_uuid'],
                'candidate_version':c['candidate_version'],'analysis_digest':c['analysis_digest'],'expected_control_revision':c['control_revision'],
                'reason':reason,'review':evidence,'review_evidence_digest':digest(evidence)}
            self.progress(target,'D_prepared',discard_uuid=metadata['request_uuid']);return self.send(target,'candidate_discard',metadata)
        review={'candidate_version':c['candidate_version'],'analysis_digest':c['analysis_digest'],'original_sha256':c['original']['sha256'],
            'stored_sha256':c['stored']['sha256'],'reviewer_role':'independent_reviewer','review_method':'candidate-image-food-independent-v1','reviewed_at':self.called_at(e,'review'),'verdict':'pass'}
        drive=self.call(e,'drive',lambda:self.ports.drive(images['original'],c))
        self.contract.validate('DriveCheck',drive)
        previous=next(m for m in json.loads(e['old_source'])['meals'] if m['expected']['target_slot']==e['slot'])['image_sha256']
        if (drive['candidate_version']!=c['candidate_version'] or drive['original_sha256']!=c['original']['sha256']
                or drive['original_download_sha256']!=c['original']['sha256'] or drive['previous_original_sha256']!=previous
                or drive['previous_download_sha256']!=previous):raise ValueError('replacement_drive_evidence_invalid')
        # 最後の外部照会と正本照会を点検・Drive処理後にやり直す。
        hold=self.external(e);fresh=self.client.preconditions(target);self.target_policy(fresh,e);nutrition=self.contract.nutrition(fresh,c)
        metadata={'contract_version':'mina-replace-v1','request_uuid':str(uuid.uuid4()),'prepare_uuid':e['prepare_uuid'],
            'candidate_version':c['candidate_version'],'analysis_digest':c['analysis_digest'],'input_image_sha256':c['original']['sha256'],
            'expected_control_revision':c['control_revision'],'expected':c['prepared_preconditions'],'precondition_digest':c['precondition_digest'],
            'publication_hold':hold,'review':review,'review_evidence_digest':digest(review),'drive_check':drive,'drive_evidence_digest':digest(drive),'nutrition_check':nutrition}
        self.progress(target,'C_prepared',commit_uuid=metadata['request_uuid']);return self.send(target,'meal_replace',metadata)
