"""限定救済の明示入口。通常daemonへ自動接続せず、本番操作の別承認を維持する。"""
from .external_guard import managed_call
import hashlib
import json
import os
import uuid
from datetime import timedelta
from .clients import MealClient
from .publication_hold import acquire
from .replacement_contract import Contract, canonical, digest, stamp
from .replacement_client import ImageDescriptor, timestamp
from .replacement_control import observe_circuit, require_available, remember, ReplacementPaused
from .rescue_contract import RescueContract, TARGET, POLICY, BUDGET, APPROVAL, SCHEMA_SHA
from .rescue_collection import CollectionPermit, GuardedPort
from .rescue_storage import RescueJournal, reserve_uuid


class RescueFlow:
    def __init__(self, store, client, normal_reader, policy, inventory, ports, clock):
        self.store,self.client,self.normal = store,client,normal_reader
        self.policy,self.inventory,self.ports,self.clock = policy,inventory,ports,clock
        self.contract=RescueContract(clock)
        self.journal=RescueJournal(store,clock)
        self.permit=CollectionPermit(store,clock)

    def enabled(self, safe=False):
        if not safe and any(os.getenv(k,'false').lower()!='true' for k in (
                'MINA_SAME_DAY_RESCUE_ENABLED','MINA_MEAL_REPLACEMENT_ENABLED','MINA_LIVE_WRITES')):
            raise ValueError('rescue_execution_disabled')
        if (not self.policy.waived or self.policy.policy_digest!=BUDGET
                or self.policy.policy.get('approval_reference_digest')!=APPROVAL):
            raise ValueError('rescue_policy_unverified')

    def hold(self, owner, epoch, db=None, saved=False):
        if db is None:
            with self.store.transaction() as connection: return self.hold(owner,epoch,connection,saved)
        h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone()
        if not h or (h['owner'],h['epoch'],h['state'])!=(owner,epoch,'saved_pending_review' if saved else 'held'):
            raise ValueError('rescue_hold_changed')

    def external(self, job):
        self.hold(job['owner'],job['epoch'],saved=self.is_saved())
        receipt=self.inventory.check(TARGET)
        self.journal.event('inventory',digest(receipt),receipt)
        return {'hold_epoch':job['epoch'],
            'pause_receipt_digest':digest({'target_date':TARGET,'owner':job['owner'],'epoch':job['epoch']}),
            'provider_check_digest':digest(receipt),'provider_checked_at':receipt['checked_at']}

    def collect(self, approval, owner, code_commit, draft, saved, directory, meal, ai, host, capture):
        """昼夕だけを通常登録。朝食の合格・日次生成へは進まない。"""
        self.enabled()
        hold=acquire(self.store,TARGET,owner,self.clock())
        if hold['state']!='held': raise ValueError('external_dispatch_unverified')
        self.external({'owner':owner,'epoch':hold['epoch']})
        from .rescue_collection import verify_menu_budget
        verify_menu_budget(self.store,draft)
        images={s:saved['images'][s]['sha256'] for s in ('lunch','dinner')}
        self.permit.issue(approval,owner,hold['epoch'],code_commit,images)
        inputs={'draft':draft,'saved':saved}
        with self.store.transaction() as db:
            old=db.execute("SELECT document FROM rescue_events WHERE kind='collection_inputs' ORDER BY id LIMIT 1").fetchone()
            if old and json.loads(old[0])!=inputs: raise ValueError('rescue_collection_input_changed')
            if not old: self.journal.event('collection_inputs',owner,inputs,db)
        from .registration import register_day
        with self.permit.scope():
            def checked_capture(*args,**kwargs):
                self.permit.checkpoint()
                key=str(uuid.uuid4());self.policy.reserve(TARGET,key,'collection_capture')
                try: value=capture(*args,**kwargs)
                except BaseException:
                    self.policy.called(key,False);raise
                self.policy.called(key,True);self.permit.checkpoint();return value
            result=register_day(self.store,draft,saved,directory,GuardedPort(meal,self.permit,self.policy,before_write=lambda:self.external(hold)),
                GuardedPort(ai,self.permit,self.policy),host,capture=checked_capture,
                allow_correction=True,collection_slots=('lunch','dinner'))
        self.journal.event('collection_complete',owner,result)
        return result

    def policy_gate(self, pre, *, probe=False):
        p=pre['policy']
        if (p['policy_version']!=POLICY or p['budget_policy_digest']!=BUDGET
                or p.get('analysis_provider')!='gemini'
                or p.get('cost_control')!={'mode':'waived','approval_reference_digest':APPROVAL}
                or p['budget_configured'] is not False
                or any(reason not in ('deadline_unverified','replacement_cooldown') for reason in p['blocking_reasons'])):
            raise ValueError('rescue_policy_unverified')
        proof=observe_circuit(self.store,pre,self.clock(),allow_probe=probe)
        require_available(self.store,self.clock())
        if 'replacement_cooldown' in p['blocking_reasons'] and not proof:
            raise ValueError('replacement_cooldown')
        return proof

    def refresh_other_meals(self, meal, ai, capture, host):
        """L期限内に同じ昼夕だけを一度再点検。登録・訂正・画像生成は行わない。"""
        self.enabled();permit=self.permit.checkpoint()
        with self.store.transaction() as db:
            inputs=db.execute("SELECT document FROM rescue_events WHERE kind='collection_inputs' ORDER BY id LIMIT 1").fetchone()
        if not inputs: raise ValueError('rescue_collection_input_missing')
        inputs=json.loads(inputs);draft,saved=inputs['draft'],inputs['saved']
        pre=Contract(self.clock).preconditions(self.normal.preconditions(TARGET),TARGET);self.policy_gate(pre)
        state=meal.day_state(TARGET)
        from .m1_contract import validate_state
        if validate_state(state,TARGET,self.clock())!=pre['state_version']: raise ValueError('rescue_source_changed')
        reviews=[]
        from pathlib import Path
        from .ogp import verify_image_reference
        for slot in ('lunch','dinner'):
            self.permit.checkpoint();m=next(m for m in pre['meals'] if m['expected']['target_slot']==slot)
            result=next(m for m in state['meals'] if m['meal']['meal_type']==slot)
            image=saved['images'][slot];path=Path(image['path'])
            if path.is_symlink(): raise ValueError('rescue_image_origin_changed')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=image['sha256'] or image['sha256']!=m['image_sha256']:
                raise ValueError('rescue_image_origin_changed')
            read_key=str(uuid.uuid4());self.policy.reserve(TARGET,read_key,'collection_capture')
            self.journal.event('read_started',read_key,{'slot':slot,'operation':'existing_ogp_GET','result_version':result['result_version']})
            try: snapshot=capture(result['share']['url'],host)
            except BaseException:
                self.policy.called(read_key,False)
                self.journal.event('read_unknown',read_key,{'operation':'existing_ogp_GET'})
                raise
            self.policy.called(read_key,True)
            self.journal.event('read_complete',read_key,{'operation':'existing_ogp_GET','digest':snapshot['digest']})
            self.permit.checkpoint()
            verify_image_reference(snapshot,result['ogp'],result['share']['url'],host)
            evidence={'slot':slot,'result_version':result['result_version'],'original_sha256':m['image_sha256'],
                'stored_sha256':m['stored_image_sha256'],'analysis_digest':m['analysis_digest']}
            food,reviewed=self.collection_review(slot,'meal',evidence,
                lambda:ai.review_meal(raw,draft['meals'][slot],{'result':result},snapshot['meta']))
            ogp,_=self.collection_review(slot,'ogp',{**evidence,'ogp_digest':snapshot['digest']},
                lambda:ai.review_ogp([snapshot['review_image'],raw],result,snapshot['meta']))
            if food!={'status':'publishable','reason_codes':[]} or ogp!={'status':'publishable','reason_codes':[]}:
                raise ValueError('rescue_other_meals_review_required')
            reviews.append({'target_slot':slot,'result_version':result['result_version'],
                'original_sha256':m['image_sha256'],'stored_sha256':m['stored_image_sha256'],
                'analysis_digest':m['analysis_digest'],'quality_status':'pass','verdict':'pass',
                'reviewer_role':'independent_reviewer','reviewed_at':reviewed})
        self.permit.checkpoint()
        if validate_state(meal.day_state(TARGET),TARGET,self.clock())!=pre['state_version']:
            raise ValueError('rescue_source_changed')
        self.journal.event('other_meals_reviews',TARGET,reviews)
        return reviews

    def collection_review(self, slot, kind, evidence, operation):
        self.permit.checkpoint();key=TARGET+':collection-review-refresh:'+slot+':'+kind
        with self.store.transaction() as db:
            previous=db.execute('SELECT * FROM rescue_calls WHERE call_key=?',(key,)).fetchone()
            if previous:
                if previous['state']!='complete' or previous['encoding']!=digest(evidence):
                    raise ValueError('rescue_external_lookup_only')
                return json.loads(previous['value']),previous['finished_at']
            db.execute("INSERT INTO rescue_calls VALUES(?,?,'started',?,NULL,NULL)",(key,'collection_review',digest(evidence)))
        self.policy.reserve(TARGET,key,'collection_review')
        try: value=managed_call(operation)
        except BaseException:
            self.policy.called(key,False);raise
        self.policy.called(key,True);finished=stamp(self.clock())
        with self.store.transaction() as db:
            db.execute("UPDATE rescue_calls SET state='complete',value=?,finished_at=? WHERE call_key=?",(canonical(value),finished,key))
        self.permit.checkpoint();return value,finished

    def prepare_issue(self, approval_reference, not_after):
        """運用者へ渡す入力と原本を保存するだけ。管理資格・発行口を持たない。"""
        self.enabled();permit=self.permit.checkpoint()
        if not isinstance(approval_reference,str) or not approval_reference.strip():
            raise ValueError('rescue_operator_approval_required')
        end=timestamp(not_after)
        from .domain import JST
        if end.astimezone(JST).date().isoformat()!=TARGET or self.clock()+timedelta(seconds=60)>end:
            raise ValueError('rescue_deadline')
        with self.store.transaction() as db:
            existing=db.execute('SELECT * FROM rescue_jobs WHERE target_date=?',(TARGET,)).fetchone()
            if existing and (existing['case_observation'] is not None or existing['stage']!='awaiting_approval'):
                return json.loads(existing['issue_json'])
            existing=dict(existing) if existing else None
        pre=Contract(self.clock).preconditions(self.normal.preconditions(TARGET),TARGET)
        self.policy_gate(pre)
        hold=self.external(permit)
        # 他2食の保存後点検完了の実在を必須とする。原画像の事前合格だけでは足りない。
        with self.store.transaction() as db:
            completed=db.execute("SELECT document FROM rescue_events WHERE kind='collection_complete' ORDER BY id DESC LIMIT 1").fetchone()
        if not completed or set(json.loads(completed[0]))!={'lunch','dinner'}:
            raise ValueError('rescue_other_meals_review_required')
        from .rescue_origin import capture
        origin=capture(self.store,pre,permit['owner'],permit['epoch'],self.clock())
        own=next(m for m in pre['meals'] if m['expected']['target_slot']=='breakfast')
        with self.store.transaction() as db:
            inv=db.execute("SELECT document FROM rescue_events WHERE kind='inventory' AND reference=? ORDER BY id DESC LIMIT 1",(hold['provider_check_digest'],)).fetchone()
        receipt=json.loads(inv[0]) if inv else None
        if (not receipt or {p['channel'] for p in receipt['pages']}!={'x','instagram'}
                or not 0<=(self.clock()-timestamp(receipt['read_started_at'])).total_seconds()<=120):
            raise ValueError('external_dispatch_unverified')
        publication={'checked_at':receipt['checked_at'],'all_pages_checked':True,'all_providers_checked':True,
            'hold_epoch':permit['epoch'],'pause_receipt_digest':hold['pause_receipt_digest'],
            'dispatching_count':0,'unknown_count':0,'external_accepted_count':0,'published_count':0,'receipt':receipt}
        reviews=[]
        from datetime import UTC,datetime
        with self.store.transaction() as db:
            for slot in ('lunch','dinner'):
                output=json.loads(completed[0])[slot]
                meal=next(m for m in pre['meals'] if m['expected']['target_slot']==slot)
                event=db.execute("SELECT occurred FROM operation_events WHERE target_date=? AND slot=? AND stage='meal_review' AND event='completed' ORDER BY id DESC LIMIT 1",(TARGET,slot)).fetchone()
                if (not event or meal['analysis']['quality']['status']!='pass'
                        or output['approval_source']['result_version']!=meal['expected']['target_result_version']
                        or output['secondary_review']!='publishable'):
                    raise ValueError('rescue_other_meals_review_required')
                reviews.append({'target_slot':slot,'result_version':meal['expected']['target_result_version'],
                    'original_sha256':meal['image_sha256'],'stored_sha256':meal['stored_image_sha256'],
                    'analysis_digest':meal['analysis_digest'],'quality_status':'pass','verdict':'pass',
                    'reviewer_role':'independent_reviewer','reviewed_at':stamp(datetime.fromtimestamp(event['occurred'],UTC))})
        with self.store.transaction() as db:
            refreshed=db.execute("SELECT document FROM rescue_events WHERE kind='other_meals_reviews' ORDER BY id DESC LIMIT 1").fetchone()
        if not refreshed and any(not 0<=(self.clock()-timestamp(r['reviewed_at'])).total_seconds()<=120 for r in reviews):
            raise ValueError('rescue_other_meals_review_required')
        if refreshed:
            fresh_reviews=json.loads(refreshed[0])
            if len(fresh_reviews)!=2 or {r['target_slot'] for r in fresh_reviews}!={'lunch','dinner'}:
                raise ValueError('rescue_other_meals_review_required')
            if all(0<=(self.clock()-timestamp(r['reviewed_at'])).total_seconds()<=120 and any(
                    m['expected']['target_slot']==r['target_slot'] and m['expected']['target_result_version']==r['result_version']
                    and m['image_sha256']==r['original_sha256'] and m['analysis_digest']==r['analysis_digest'] for m in pre['meals']) for r in fresh_reviews):
                reviews=fresh_reviews
            else: raise ValueError('rescue_other_meals_review_required')
        costs={'history_complete':True,'unresolved_cost_count':0,'waived_approval_digest':APPROVAL,
            'ledgers':origin['document']['ledgers']}
        ledger={**origin['document'],'all_sources_enumerated':True,'all_requests_terminal':True,
            'unfinished_request_count':0,'same_menu_repair_history_preserved':True,
            'consumed_ordinals':origin['baseline_consumed_ordinals'],'publication_evidence_sha256':digest(publication),
            'other_meals_reviews':reviews,
            **{k:origin[k] for k in ('original_first_replan_started_at','original_preparation_deadline_at','original_hard_deadline_at')}}
        origin['source_ledger_digest']=digest(ledger);origin['cost_history_digest']=digest(costs)
        agent_evidence={'source_ledger':ledger,'cost_history':costs,'publication':publication}
        basis={'target_date':TARGET,'target_slot':'breakfast','initial_expected':own['expected'],
            'original_image_sha256':own['image_sha256'],
            **{k:v for k,v in origin.items() if k!='document'},
            'source_requests_terminal':True,'hold_epoch':permit['epoch'],
            'pause_receipt_digest':hold['pause_receipt_digest'],'policy_version':POLICY,
            'budget_policy_digest':BUDGET,'cost_approval_digest':APPROVAL,
            'not_after':not_after}
        case_uuid,key=(existing['case_uuid'],existing['prepare_uuid']) if existing else (str(uuid.uuid4()),str(uuid.uuid4()))
        value={'contract_version':'mina-rescue-v1','case_uuid':case_uuid,'basis':basis,
            'planned_prepare_uuid':key,'attempt_ordinal':len(basis['baseline_consumed_ordinals'])+1}
        # 本人承認の原本がまだないのでoperator_approval_digestを作らない。
        with self.store.transaction() as db:
            self.hold(permit['owner'],permit['epoch'],db)
            reserve_uuid(db,case_uuid,'case',case_uuid)
            reserve_uuid(db,key,'candidate_prepare',case_uuid)
            if existing:
                if (existing['owner'],existing['epoch'])!=(permit['owner'],permit['epoch']) or json.loads(existing['issue_json'])['attempt_ordinal']!=value['attempt_ordinal']:
                    raise ValueError('rescue_origin_changed')
                changed=db.execute("UPDATE rescue_jobs SET issue_json=? WHERE target_date=? AND stage='awaiting_approval' AND case_observation IS NULL",(canonical(value).decode(),TARGET)).rowcount
                if changed!=1: raise ValueError('rescue_operator_approval_immutable')
            else:
                db.execute("INSERT INTO rescue_jobs VALUES(?,?,?,?,?,?,NULL,'awaiting_approval',NULL)",
                    (TARGET,permit['owner'],permit['epoch'],canonical(value).decode(),case_uuid,key))
            self.journal.event('origin',case_uuid,origin['document'],db)
            self.journal.event('precondition',case_uuid,pre,db)
            self.journal.event('operator_evidence',case_uuid,agent_evidence,db)
        return {'grant_draft':value,'approval_required':True,'basis_sha256':digest(basis),'operator_approval_reference':approval_reference}

    def verify_origin(self):
        """参照digestだけでなく、保存した原本と現在の実体・版の一致を検査する。"""
        job=self.journal.job()
        with self.store.transaction() as db:
            row=db.execute("SELECT document FROM rescue_events WHERE kind='origin' AND reference=? ORDER BY id DESC LIMIT 1",(job['case_uuid'],)).fetchone()
            if not row: raise ValueError('rescue_origin_missing')
            origin=json.loads(row[0])
            for step in origin['steps']:
                current=db.execute('SELECT * FROM preparation_steps WHERE target_date=? AND step=?',(TARGET,step['step'])).fetchone()
                if not current or dict(current)!=step: raise ValueError('rescue_origin_changed')
                if step['step'].startswith('image:'):
                    from pathlib import Path
                    image=json.loads(step['result']);path=Path(image['path'])
                    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=image['sha256']:
                        raise ValueError('rescue_origin_changed')
            for record in origin['requests']:
                current=db.execute('SELECT * FROM mina_requests WHERE request_key=?',(record['request_key'],)).fetchone()
                if not current: raise ValueError('rescue_origin_changed')
                current=dict(current);raw=current['image']
                current['image']=None if raw is None else {'sha256':hashlib.sha256(raw).hexdigest(),'length':len(raw)}
                if current!=record: raise ValueError('rescue_origin_changed')
        with self.store.transaction() as db:
            pre=db.execute("SELECT document FROM rescue_events WHERE kind='precondition' AND reference=? ORDER BY id DESC LIMIT 1",(job['case_uuid'],)).fetchone()
        if not pre: raise ValueError('rescue_origin_missing')
        from .rescue_origin import capture
        current=capture(self.store,json.loads(pre[0]),job['owner'],job['epoch'],self.clock())
        if current['document']!=origin: raise ValueError('rescue_origin_changed')
        return origin

    def operator_bundle(self):
        """承認済みの発行入力・承認原本・実証跡だけを運用者へ引き渡す。"""
        job=self.journal.job();issue=json.loads(job['issue_json']);self.contract.validate('GrantIssueInput',issue)
        self.verify_origin()
        with self.store.transaction() as db:
            approval=db.execute("SELECT document FROM rescue_events WHERE kind='operator_approval' ORDER BY id LIMIT 1").fetchone()
            evidence=db.execute("SELECT document FROM rescue_events WHERE kind='operator_evidence' ORDER BY id DESC LIMIT 1").fetchone()
        if not approval or not evidence: raise ValueError('rescue_operator_evidence_missing')
        artifact=json.loads(approval[0]);proof=json.loads(evidence[0])
        if (digest(artifact)!=issue['basis']['operator_approval_digest']
                or digest(proof['source_ledger'])!=issue['basis']['source_ledger_digest']
                or digest(proof['cost_history'])!=issue['basis']['cost_history_digest']):
            raise ValueError('rescue_operator_evidence_changed')
        return {'input':issue,'approval':artifact,'evidence':proof,'agent_issues_authority':False}

    def authorize_issue(self, artifact):
        """運用者へ渡す本人承認原本を束縛。Agent自身では発行しない。"""
        self.enabled();permit=self.permit.checkpoint();job=self.journal.job();issue=json.loads(job['issue_json'])
        self.verify_origin()
        if job['case_observation'] is not None or job['stage'] not in ('awaiting_approval','awaiting_operator'):
            raise ValueError('rescue_operator_approval_immutable')
        basis={k:v for k,v in issue['basis'].items() if k!='operator_approval_digest'}
        required={'action':'issue','approved':True,'case_uuid':job['case_uuid'],
            'planned_prepare_uuid':job['prepare_uuid'],'basis_sha256':digest(basis),
            'not_after':basis['not_after'],'target_date':TARGET,'target_slot':'breakfast',
            'scope':'same_day_breakfast_single_rescue','code_commit':permit['code_commit'],
            'schema_sha256':SCHEMA_SHA,
            'hold_owner':job['owner'],'hold_epoch':job['epoch'],'original_image_sha256':basis['original_image_sha256']}
        if any(artifact.get(k)!=v for k,v in required.items()) or not artifact.get('operator_approval_reference'):
            raise ValueError('rescue_operator_approval_required')
        value={**issue,'basis':{**basis,'operator_approval_digest':digest(artifact)}}
        self.contract.validate('GrantIssueInput',value)
        with self.store.transaction() as db:
            old=db.execute("SELECT document FROM rescue_events WHERE kind='operator_approval' ORDER BY id LIMIT 1").fetchone()
            if old and json.loads(old[0])!=artifact: raise ValueError('rescue_operator_approval_immutable')
            if not old:self.journal.event('operator_approval',job['case_uuid'],artifact,db)
            db.execute("UPDATE rescue_jobs SET issue_json=?,stage='awaiting_operator' WHERE target_date=?",
                (canonical(value).decode(),TARGET))
        return value

    def bind_case(self):
        """発行応答不明も同case GETだけ。予定UUID・実原本との一致を確認する。"""
        job=self.journal.job()
        self.contract.validate('GrantIssueInput',json.loads(job['issue_json']))
        value=self.client.case(job['case_uuid'])
        self.contract.decode('case',value);c=value['case'];issue=json.loads(job['issue_json'])
        if any(c[k]!=issue[k] for k in ('case_uuid','basis','planned_prepare_uuid','attempt_ordinal')):
            raise ValueError('rescue_case_origin_changed')
        old=json.loads(job['case_observation']) if job['case_observation'] else None
        if old and (old['authorization_digest']!=c['authorization_digest']
                or old['hard_deadline_at']!=c['hard_deadline_at']
                or old['authorization_revision']>c['authorization_revision']):
            raise ValueError('rescue_case_regressed')
        self.journal.event('case',job['case_uuid'],value)
        with self.store.transaction() as db:
            current=db.execute('SELECT case_observation FROM rescue_jobs WHERE target_date=?',(TARGET,)).fetchone()
            if current['case_observation'] and json.loads(current['case_observation'])['authorization_revision']>c['authorization_revision']:
                raise ValueError('rescue_case_regressed')
            db.execute('UPDATE rescue_jobs SET case_observation=?,deadline=? WHERE target_date=?',
                       (canonical(c).decode(),c['hard_deadline_at'],TARGET))
        return c

    def is_saved(self, db=None):
        if db is None:
            with self.store.transaction() as connection: return self.is_saved(connection)
        for row in db.execute("SELECT observation FROM rescue_requests WHERE operation='meal_replace'"):
            if json.loads(row['observation'] or '{}').get('persistence')=='saved': return True
        return False

    def checkpoint(self, db=None, *, saved=False, remaining=0, safe_discard=False, safe_finish=False):
        self.enabled(safe=safe_discard or safe_finish);job=self.journal.job() if db is None else dict(db.execute('SELECT * FROM rescue_jobs WHERE target_date=?',(TARGET,)).fetchone())
        if db is None:
            with self.store.transaction() as connection:
                conflict=any(json.loads(r[0] or '{}').get('conflict') for r in connection.execute('SELECT observation FROM rescue_requests'))
        else:
            conflict=any(json.loads(r[0] or '{}').get('conflict') for r in db.execute('SELECT observation FROM rescue_requests'))
        if conflict: raise ValueError('rescue_reconciliation_required')
        saved=saved or self.is_saved(db)
        self.hold(job['owner'],job['epoch'],db,saved)
        if saved:
            case=json.loads(job['case_observation'] or '{}')
            self.contract.case(case)
            if case['state']!='completed' or not case['saved_commit_uuid']:
                raise ValueError('rescue_saved_case_unconfirmed')
            job['deadline']=stamp(min(timestamp(job['deadline']),timestamp(case['ogp_finish_deadline_at'])))
        if not safe_discard and (not job['deadline'] or (self.clock()>=timestamp(job['deadline']) if remaining==0 else self.clock()+timedelta(seconds=remaining)>timestamp(job['deadline']))):
            raise ValueError('rescue_deadline')
        return job

    def case_ref(self, c):
        return {k:c[k] for k in ('case_uuid','authorization_digest')} | {'expected_authorization_revision':c['authorization_revision']}

    def call(self, kind, operation, *, saved=False):
        job=self.checkpoint(saved=saved);key=job['case_uuid']+':'+kind
        with self.store.transaction() as db:
            previous=db.execute('SELECT * FROM rescue_calls WHERE call_key=?',(key,)).fetchone()
            if previous:
                if previous['state']!='complete': raise ValueError('rescue_external_lookup_only')
                return bytes(previous['value']) if previous['encoding']=='bytes' else json.loads(previous['value'])
            db.execute("INSERT INTO rescue_calls VALUES(?,?,'started',NULL,NULL,NULL)",(key,kind))
        self.policy.reserve(TARGET,key,kind)
        try: value=managed_call(operation)
        except BaseException:
            self.policy.called(key,False);raise
        raw=value if isinstance(value,bytes) else canonical(value)
        self.policy.called(key,True)
        with self.store.transaction() as db:
            db.execute("UPDATE rescue_calls SET state='complete',encoding=?,value=?,finished_at=? WHERE call_key=?",
                ('bytes' if isinstance(value,bytes) else 'json',raw,stamp(self.clock()),key))
        self.checkpoint(saved=saved)
        return value

    def prepare_candidate(self):
        self.enabled();c=self.bind_case();self.contract.case(c,active=True)
        job=self.checkpoint();require_available(self.store,self.clock())
        with self.store.transaction() as db:
            if db.execute('SELECT 1 FROM rescue_requests').fetchone(): raise ValueError('rescue_lookup_only')
        pre=self.contract.preconditions(self.client.preconditions(TARGET,job['case_uuid']),TARGET)
        probe=self.policy_gate(pre,probe=True)
        if probe:
            with self.store.transaction() as db:
                if db.execute('SELECT 1 FROM replacement_probe_claims WHERE retry_at=?',(probe['retry_at'],)).fetchone():
                    raise ValueError('replacement_recovery_required')
                db.execute('INSERT INTO replacement_probe_claims VALUES(?,?,?,?)',(probe['retry_at'],job['owner'],job['epoch'],TARGET))
        own=next(m for m in pre['meals'] if m['expected']['target_slot']=='breakfast')
        if own['expected']!=c['basis']['initial_expected']: raise ValueError('rescue_source_changed')
        plan=self.call('menu',lambda:self.ports.plan('breakfast',pre,c['attempt_ordinal']))
        image=self.call('image',lambda:self.ports.image(plan))
        sha=hashlib.sha256(image).hexdigest()
        if sha==c['basis']['original_image_sha256']: raise ValueError('rescue_failed_image_reused')
        metadata={'contract_version':'mina-rescue-v1','request_uuid':job['prepare_uuid'],'expected':own['expected'],
            'precondition_digest':own['precondition_digest'],'publication_hold':self.external(job),
            'input_image_sha256':sha,'original_mime':'image/jpeg','normalized_comment':plan['comment'],
            'attempt_ordinal':c['attempt_ordinal'],'rescue':self.case_ref(c),'reason':'menu_replan_after_quality_hold'}
        return self.send('candidate_prepare',metadata,image)

    def send(self, operation, metadata, image=None):
        safe_discard=operation=='candidate_discard'
        safe_finish=operation=='replacement_resume' and self.is_saved()
        self.enabled(safe=safe_discard or safe_finish)
        job=self.checkpoint(saved=safe_finish,safe_discard=safe_discard,safe_finish=safe_finish)
        names={'candidate_prepare':'PrepareInput','candidate_discard':'DiscardInput','meal_replace':'CommitInput','replacement_resume':'ResumeInput'}
        self.contract.validate(names[operation],metadata)
        require_available(self.store,self.clock())
        if operation=='candidate_prepare':
            self.checkpoint(remaining=60)
            c=self.bind_case();self.contract.case(c,active=True)
            pre=self.contract.preconditions(self.client.preconditions(TARGET,job['case_uuid']),TARGET)
            self.policy_gate(pre,probe=True)
            if metadata['expected']!=c['basis']['initial_expected'] or metadata['rescue']!=self.case_ref(c):
                raise ValueError('rescue_source_changed')
        body=canonical(metadata);content_type='application/json'
        if image is not None:
            boundary='mina-rescue-'+metadata['request_uuid']
            body=(f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n'.encode()+body+
                f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="meal"\r\nContent-Type: {metadata["original_mime"]}\r\n\r\n'.encode()+image+f'\r\n--{boundary}--\r\n'.encode())
            content_type='multipart/form-data; boundary='+boundary
        if safe_discard:
            current=self.client.candidate(job['prepare_uuid'])['candidate']
            self.contract.candidate(current)
            if (current['candidate_state']!='ready' or metadata['rescue']!=current['rescue']
                    or metadata['candidate_version']!=current['candidate_version']
                    or metadata['analysis_digest']!=current['analysis_digest']):
                raise ValueError('rescue_safe_discard_changed')
        key=metadata['request_uuid'];self.journal.prepare(metadata,operation,body,content_type)
        row=self.journal.start(key,lambda db:self.checkpoint(db,saved=safe_finish,safe_discard=safe_discard,safe_finish=safe_finish))
        self.checkpoint(saved=safe_finish,remaining=60 if operation=='candidate_prepare' else 0,safe_discard=safe_discard,safe_finish=safe_finish)
        try: value=self.client.send(operation,key,bytes(row['body']),content_type,job['prepare_uuid'])
        except ReplacementPaused as error:
            self.journal.event('server_rejection',key,error.envelope)
            remember(self.store,error.envelope)
            self.observe_pause()
            if error.envelope.get('request'): self.accept(key,error.envelope)
            self.journal.stage('paused');raise
        return self.accept(key,value)

    def accept(self, key, value):
        row=self.journal.request(key);job=self.journal.job()
        self.contract.decode(row['operation'],value);remember(self.store,value)
        actual=value['rescue_case'];issue=json.loads(job['issue_json'])
        if any(actual[k]!=issue[k] for k in ('case_uuid','basis','planned_prepare_uuid','attempt_ordinal')):
            raise ValueError('rescue_response_case_changed')
        previous=json.loads(job['case_observation']) if job['case_observation'] else None
        if previous and (actual['authorization_digest']!=previous['authorization_digest'] or actual['authorization_revision']<previous['authorization_revision']):
            raise ValueError('rescue_response_case_changed')
        c=value.get('candidate')
        if c and (c['prepare_uuid']!=job['prepare_uuid'] or c['rescue']['case_uuid']!=job['case_uuid']):
            raise ValueError('rescue_candidate_changed')
        if row['operation']=='meal_replace' and value['request']['persistence']=='saved' and (actual['state']!='completed' or actual['saved_commit_uuid']!=key):
            raise ValueError('rescue_saved_case_unconfirmed')
        state=self.journal.observe(key,value)
        with self.store.transaction() as db:
            latest=db.execute('SELECT case_observation FROM rescue_jobs WHERE target_date=?',(TARGET,)).fetchone()
            if latest['case_observation'] and json.loads(latest['case_observation'])['authorization_revision']>actual['authorization_revision']:
                raise ValueError('rescue_case_regressed')
            db.execute('UPDATE rescue_jobs SET case_observation=? WHERE target_date=?',(canonical(actual).decode(),TARGET))
        if state.get('conflict'): self.journal.stage('unknown');return value
        if row['operation']=='meal_replace':
            stage='saved' if state['persistence']=='saved' else 'unknown'
        elif row['operation']=='candidate_discard':
            if value['request'].get('discard_input')!=json.loads(row['metadata']): raise ValueError('rescue_discard_changed')
            stage='rejected' if state['outcome']=='applied' and value['rescue_case']['state']=='rejected' else 'unknown'
        else: stage='ready' if state.get('outcome')=='applied' and c and c['candidate_state']=='ready' else 'unknown'
        if self.is_saved(): stage='saved'
        if value['request'].get('stage')=='paused':
            self.observe_pause()
            if stage!='saved': stage='paused'
        self.journal.stage(stage);return value

    def observe_pause(self):
        """OPENの実GET証拠を保存。時間経過だけでHALF_OPENの許可を作らない。"""
        job=self.journal.job()
        try:
            pre=self.client.preconditions(TARGET,job['case_uuid'])
            self.contract.preconditions(pre,TARGET)
            self.journal.event('circuit',job['case_uuid'],pre['circuit'])
            observe_circuit(self.store,pre,self.clock())
        except Exception:
            # OPEN観測は待機例外になる。未取得・不明から解除許可を作らない。
            return

    def reconcile(self, key):
        row=self.journal.request(key)
        if row['phase']=='prepared': raise ValueError('rescue_request_not_sent')
        # GETには期限後も到達できる。期限を理由にunknownを解放しない。
        return self.accept(key,self.client.request(key,row['operation']))

    def candidate(self):
        job=self.journal.job();value=self.client.candidate(job['prepare_uuid'])
        self.contract.decode('candidate',value);c=self.contract.candidate(value['candidate'],ready=True)
        record=self.journal.request(job['prepare_uuid']);metadata=json.loads(record['metadata'])
        if (c['prepared_preconditions']!=metadata['expected'] or c['rescue']!=metadata['rescue']
                or c['prepared_publication_hold']!=metadata['publication_hold']
                or c['original']['sha256']!=metadata['input_image_sha256']
                or c['attempt_ordinal']!=metadata['attempt_ordinal']
                or c['hard_deadline_at']!=job['deadline']): raise ValueError('rescue_candidate_input_changed')
        return c

    def decide(self):
        self.checkpoint();job=self.journal.job();c=self.candidate()
        case=self.bind_case();self.contract.case(case,active=True)
        if c['rescue']!=self.case_ref(case): raise ValueError('rescue_authorization_changed')
        images={r:self.client.image(job['prepare_uuid'],r,ImageDescriptor(c['candidate_version'],c[r]['sha256'],c[r]['byte_length'],c[r]['mime'])) for r in ('original','stored')}
        with self.store.transaction() as db:
            plan=json.loads(db.execute("SELECT value FROM rescue_calls WHERE kind='menu'").fetchone()[0])
        review=self.call('review',lambda:self.ports.review(images,plan,c))
        if review not in ({'verdict':'pass','observations':[]},{'verdict':'fail','observations':['image_analysis_mismatch']}):
            raise ValueError('rescue_review_invalid')
        fresh=self.contract.preconditions(self.client.preconditions(TARGET,job['case_uuid']),TARGET);self.policy_gate(fresh)
        reason='independent_review_failed' if review['verdict']=='fail' else None
        try: nutrition=self.contract.nutrition(fresh,c)
        except ValueError as error:
            if str(error)!='nutrition_budget_failed': raise
            reason='nutrition_budget_failed'
        with self.store.transaction() as db:
            reviewed=db.execute("SELECT finished_at FROM rescue_calls WHERE kind='review'").fetchone()[0]
        proof={'candidate_version':c['candidate_version'],'analysis_digest':c['analysis_digest'],
            'original_sha256':c['original']['sha256'],'stored_sha256':c['stored']['sha256'],
            'reviewer_role':'independent_reviewer','reviewed_at':reviewed,'verdict':'fail' if reason else 'pass'}
        metadata={'contract_version':'mina-rescue-v1','request_uuid':str(uuid.uuid4()),'prepare_uuid':job['prepare_uuid'],
            'candidate_version':c['candidate_version'],'analysis_digest':c['analysis_digest'],
            'expected_control_revision':c['control_revision'],'rescue':self.case_ref(case)}
        if reason:
            proof['observations']=['nutrition_budget_failed'] if reason=='nutrition_budget_failed' else review['observations']
            metadata.update(reason=reason,review=proof,review_evidence_digest=digest(proof))
            return self.send('candidate_discard',metadata)
        proof['review_method']='candidate-image-food-independent-v1'
        drive=self.call('drive',lambda:self.ports.drive(images['original'],c))
        self.contract.validate('DriveCheck',drive)
        if (drive['candidate_version']!=c['candidate_version'] or drive['original_sha256']!=c['original']['sha256']
                or drive['original_download_sha256']!=c['original']['sha256']
                or drive['previous_original_sha256']!=case['basis']['original_image_sha256']
                or drive['previous_download_sha256']!=case['basis']['original_image_sha256']):
            raise ValueError('rescue_drive_changed')
        publication=self.external(job)
        fresh=self.contract.preconditions(self.client.preconditions(TARGET,job['case_uuid']),TARGET);self.policy_gate(fresh)
        nutrition=self.contract.nutrition(fresh,c)
        latest=self.bind_case()
        if latest!=case: raise ValueError('rescue_authorization_changed')
        metadata.update(expected=c['prepared_preconditions'],precondition_digest=c['precondition_digest'],
            publication_hold=publication,input_image_sha256=c['original']['sha256'],review=proof,
            review_evidence_digest=digest(proof),drive_check=drive,drive_evidence_digest=digest(drive),nutrition_check=nutrition)
        return self.send('meal_replace',metadata)

    def finish(self, commit_key, meal, ai, capture, draft, images, host):
        """C後の3食・日次を再点検して原本を保存する。承認通知・公開は行わない。"""
        job=self.checkpoint(saved=True)
        response=self.reconcile(commit_key)
        if (response['request']['persistence']!='saved' or response['result_status']!='unchanged'
                or response['result']!=response['current_result']):
            raise ValueError('rescue_saved_source_changed')
        self.journal.stage('saved')
        state=meal.day_state(TARGET)
        from .m1_contract import validate_state, CONTRACT, validate_write, completed_request
        version=validate_state(state,TARGET,self.clock())
        result=response['current_result']
        if (response['current_state_version']!=version or len(state['meals'])!=3
                or next(m for m in state['meals'] if m['meal']['meal_type']=='breakfast')!=result
                or not 1800<=sum(m['analysis']['summary']['totalCalories'] for m in state['meals'])<=2000):
            raise ValueError('rescue_saved_source_changed')
        if set(images)!={'breakfast','lunch','dinner'} or any(hashlib.sha256(images[m['meal']['meal_type']]).hexdigest()!=m['image_sha256'] for m in state['meals']):
            raise ValueError('rescue_saved_image_changed')
        import copy
        with self.store.transaction() as db:
            initial=db.execute("SELECT document FROM rescue_events WHERE kind='collection_inputs' ORDER BY id LIMIT 1").fetchone()
            menu=db.execute("SELECT value FROM rescue_calls WHERE kind='menu' AND state='complete'").fetchone()
        if not initial or not menu: raise ValueError('rescue_plan_origin_missing')
        baseline=json.loads(initial[0]);adopted=copy.deepcopy(baseline['draft'])
        if any(hashlib.sha256(images[s]).hexdigest()!=baseline['saved']['images'][s]['sha256'] for s in ('lunch','dinner')):
            raise ValueError('rescue_other_meal_image_changed')
        adopted['meals']['breakfast']=json.loads(menu[0])
        if draft!=adopted: raise ValueError('rescue_adopted_plan_required')
        if state['daily_share'] is None:
            from .request_ledger import RequestLedger
            from .publication_hold import replacement_daily_scope
            requests=RequestLedger(self.store)
            key=str(uuid.uuid4())
            with self.store.transaction() as db:
                old=db.execute("SELECT document FROM rescue_events WHERE kind='daily_request' ORDER BY id LIMIT 1").fetchone()
            if old: payload_record=json.loads(old[0]);key=payload_record['key'];payload=payload_record['payload']
            else:
                payload={'contract_version':CONTRACT,'date':TARGET,'comment':draft['daily_comment'],
                    'expected_meal_count':3,'expected_state_version':version}
                self.journal.event('daily_request',key,{'key':key,'payload':payload})
            requests.prepare(key,'daily_share',TARGET,'daily',payload)
            record=requests.restore(key)
            if record['phase']=='prepared':
                with replacement_daily_scope(TARGET,job['owner'],job['epoch']):
                    daily=self.call('rebuild',lambda:requests.execute_once(key,meal,
                        lambda:meal.json_write('/api/internal/mina/daily-share',payload,request_key=key)),saved=True)
            else:
                lookup=meal.request_state(key,'daily_share')
                if lookup.get('persistence')!='saved' or not lookup.get('current_result'):
                    raise ValueError('rescue_daily_lookup_only')
                daily={'result':lookup['current_result'],'request':lookup}
            completed_request(meal.request_state(key,'daily_share'),key,'daily_share',daily['result'])
            requests.record(key,'complete',{'persistence':'saved','stage':'completed'})
        # 日次の保存後も元CのGETで現在正本・版を読み直す。
        response=self.reconcile(commit_key)
        if response['request']['persistence']!='saved' or response['result_status']!='unchanged':
            raise ValueError('rescue_saved_source_changed')
        from .replacement_postreview import review_posts
        def guarded_capture(*args):
            self.checkpoint(saved=True)
            value=capture(*args)
            self.checkpoint(saved=True)
            return value
        class ReviewedAI:
            def review_meal(inner,*args):
                key='finish-meal-'+digest(args[2])
                return self.call_review(key,lambda:ai.review_meal(*args))
            def review_ogp(inner,*args):
                key='finish-ogp-'+digest(args[1:])
                return self.call_review(key,lambda:ai.review_ogp(*args))
        reviewed,proof=review_posts(self.store,meal,ReviewedAI(),guarded_capture,TARGET,commit_key,response,draft,images,host,self.clock)
        latest=self.client.preconditions(TARGET,job['case_uuid'])
        self.contract.preconditions(latest,TARGET)
        if not 1800<=latest['summary']['totalCalories']<=2000: raise ValueError('nutrition_budget_failed')
        self.journal.event('all_meals_reviewed',commit_key,{'reviewed':reviewed,'proof':proof})
        self.journal.stage('all_meals_reviewed')
        return {'reviewed':reviewed,'proof':proof,'publication_hold':True,'approval_required':True}

    def call_review(self, key, operation):
        # 各食・各OGP点検を異なる不変キーで記録し、再実行課金を防ぐ。
        return self.call_named(key,'review',operation,saved=True)

    def call_named(self, key, kind, operation, *, saved=False):
        job=self.checkpoint(saved=saved);full=job['case_uuid']+':'+key
        with self.store.transaction() as db:
            old=db.execute('SELECT * FROM rescue_calls WHERE call_key=?',(full,)).fetchone()
            if old:
                if old['state']!='complete': raise ValueError('rescue_external_lookup_only')
                return json.loads(old['value'])
            db.execute("INSERT INTO rescue_calls VALUES(?,?,'started',NULL,NULL,NULL)",(full,kind))
        self.policy.reserve(TARGET,full,kind)
        try: value=managed_call(operation)
        except BaseException:
            self.policy.called(full,False);raise
        self.policy.called(full,True)
        with self.store.transaction() as db:
            db.execute("UPDATE rescue_calls SET state='complete',encoding='json',value=?,finished_at=? WHERE call_key=?",
                       (canonical(value),stamp(self.clock()),full))
        self.checkpoint(saved=saved)
        return value

    def resume(self, original_key):
        """明示操作だけ。未発行stageと元画像を固定し、制御UUIDも再送しない。"""
        job=self.journal.job();row=self.journal.request(original_key)
        value=self.reconcile(original_key);request=value['request']
        if request['stage']!='paused' or type(value.get('resume_stage_version')) is not int:
            raise ValueError('rescue_resume_not_paused')
        saved=row['operation']=='meal_replace' and request.get('persistence')=='saved'
        if row['operation'] not in ('candidate_prepare','meal_replace') or row['operation']=='meal_replace' and not saved:
            raise ValueError('rescue_resume_unknown')
        self.checkpoint(saved=saved,safe_finish=saved)
        case=self.bind_case()
        if saved:
            if case['state']!='completed' or self.clock()>=timestamp(case['ogp_finish_deadline_at']):
                raise ValueError('rescue_finish_deadline')
        else: self.contract.case(case,active=True)
        pre=self.client.preconditions(TARGET,job['case_uuid']);self.contract.preconditions(pre,TARGET)
        probe=self.policy_gate(pre,probe=True)
        metadata=json.loads(row['metadata'])
        from .replacement_control import initialize
        initialize(self.store)
        key=str(uuid.uuid4())
        with self.store.transaction() as db:
            if probe:
                if db.execute('SELECT 1 FROM replacement_probe_claims WHERE retry_at=?',(probe['retry_at'],)).fetchone():
                    raise ValueError('replacement_recovery_required')
                db.execute('INSERT INTO replacement_probe_claims VALUES(?,?,?,?)',(probe['retry_at'],job['owner'],job['epoch'],TARGET))
            if db.execute('SELECT 1 FROM replacement_resume_claims WHERE original_uuid=? AND stage_version=?',
                    (original_key,value['resume_stage_version'])).fetchone(): raise ValueError('rescue_lookup_only')
            db.execute('INSERT INTO replacement_resume_claims VALUES(?,?,?)',(original_key,value['resume_stage_version'],key))
        body={'contract_version':'mina-rescue-v1','request_uuid':key,'original_request_uuid':original_key,
            'expected_stage_version':value['resume_stage_version'],'expected_input_sha256':metadata['input_image_sha256'],
            'policy_version':POLICY,'budget_policy_digest':BUDGET,'publication_hold':self.external({**job,'stage':'saved' if saved else job['stage']}),
            'rescue':self.case_ref(case)}
        result=self.send('replacement_resume',body)
        if result['request'].get('outcome')!='applied':
            raise ValueError(result['request'].get('reason') or 'rescue_resume_unresolved')
        return self.reconcile(original_key)

    def discard_reviewed(self):
        """OFF・取消・期限後も、点検済みready候補だけを安全に不採用へ移す。"""
        job=self.checkpoint(safe_discard=True)
        with self.store.transaction() as db:
            previous=db.execute("SELECT metadata,request_key,phase FROM rescue_requests WHERE operation='candidate_discard'").fetchone()
            record=db.execute("SELECT value,finished_at FROM rescue_calls WHERE kind='review' AND state='complete'").fetchone()
        if previous:
            if previous['phase']!='prepared': return self.reconcile(previous['request_key'])
            return self.send('candidate_discard',json.loads(previous['metadata']))
        value=self.client.candidate(job['prepare_uuid']);self.contract.decode('candidate',value)
        c=self.contract.candidate(value['candidate'])
        if (c['candidate_state']!='ready' or not record
                or json.loads(record['value'])!={'verdict':'fail','observations':['image_analysis_mismatch']}):
            raise ValueError('rescue_known_failed_review_required')
        review={'candidate_version':c['candidate_version'],'analysis_digest':c['analysis_digest'],
            'original_sha256':c['original']['sha256'],'stored_sha256':c['stored']['sha256'],
            'reviewer_role':'independent_reviewer','reviewed_at':record['finished_at'],'verdict':'fail',
            'observations':['image_analysis_mismatch']}
        body={'contract_version':'mina-rescue-v1','request_uuid':str(uuid.uuid4()),'prepare_uuid':job['prepare_uuid'],
            'candidate_version':c['candidate_version'],'analysis_digest':c['analysis_digest'],
            'expected_control_revision':c['control_revision'],'reason':'independent_review_failed',
            'review':review,'review_evidence_digest':digest(review),'rescue':c['rescue']}
        return self.send('candidate_discard',body)


def _trace(method):
    from functools import wraps
    @wraps(method)
    def invoke(self,*args,**kwargs):
        try: return method(self,*args,**kwargs)
        except Exception as error:
            known=set(self.contract.schema['$defs']['Reason']['enum']) | {
                'rescue_deadline','rescue_hold_changed','rescue_policy_unverified','rescue_source_changed',
                'rescue_lookup_only','rescue_external_lookup_only','rescue_saved_source_changed',
                'rescue_response_case_changed','rescue_authorization_changed','rescue_adopted_plan_required',
                'rescue_execution_disabled','rescue_origin_unresolved','rescue_orphan_generation',
                'rescue_collection_deadline','rescue_collection_disabled','external_dispatch_present',
                'external_dispatch_unverified','rescue_collection_target_changed','rescue_uuid_collision'}
            reason=str(error) if str(error) in known else 'unconfirmed'
            with self.store.transaction() as db:
                row=db.execute('SELECT stage,case_uuid,prepare_uuid FROM rescue_jobs WHERE target_date=?',(TARGET,)).fetchone()
                context=dict(row) if row else {'stage':'collection_or_admission'}
            self.journal.event('stopped',method.__name__,{**context,'operation':method.__name__,'reason':reason,
                'exception_type':type(error).__name__,'action':'original_uuid_lookup_or_operator_review'})
            raise
    return invoke


for _name in ('collect','refresh_other_meals','prepare_issue','operator_bundle','authorize_issue','bind_case','prepare_candidate','decide','send','reconcile','finish','resume','discard_reviewed'):
    setattr(RescueFlow,_name,_trace(getattr(RescueFlow,_name)))
