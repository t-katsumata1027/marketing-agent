"""本人承認caseだけを実行するv3入口。通常巡回・管理権限発行へ接続しない。"""
import hashlib
import json
import os
import uuid
from datetime import timedelta
from .external_guard import managed_call
from .replacement_contract import Contract, canonical, digest, stamp
from .replacement_client import ImageDescriptor, timestamp
from .replacement_control import observe_circuit, require_available, remember, ReplacementPaused
from .scoped_rescue_contract import VERSION, SCHEMA_SHA, ScopedRescueContract
from .scoped_rescue_storage import ScopedRescueJournal
from .rescue_contract import POLICY, BUDGET, APPROVAL


class ScopedRescueFlow:
    def __init__(self, store, client, normal, policy, inventory, ports, clock, target, slot, case_uuid):
        self.store,self.client,self.normal,self.policy,self.inventory,self.ports,self.clock = store,client,normal,policy,inventory,ports,clock
        self.target,self.slot = target,slot
        self.contract = ScopedRescueContract(clock,target,slot)
        self.journal = ScopedRescueJournal(store,clock,target,slot,case_uuid)
        self.acceptance_check=lambda:None
        self.preparation_window=None
        from .replacement_ports import PreparationPorts
        if isinstance(ports,PreparationPorts):
            from .scoped_rescue_rpc import GuardedAI,guarded_drive
            ports.ai=GuardedAI(self,ports.ai);ports.drive_client=guarded_drive(self,ports.drive_client)

    def enabled(self, safe=False):
        names = ('MINA_MEAL_REPLACEMENT_ENABLED','MINA_LIVE_WRITES') + (() if safe else ('MINA_SCOPED_RESCUE_ENABLED',))
        if any(os.getenv(k,'false').lower() != 'true' for k in names): raise ValueError('scoped_rescue_execution_disabled')
        if not self.policy.waived or self.policy.policy_digest != BUDGET or self.policy.policy.get('approval_reference_digest') != APPROVAL:
            raise ValueError('scoped_rescue_policy_unverified')

    def schedule_gate(self,not_after):
        """本人の絶対期限は維持し、全4枠を作れる実効時間だけを検査する。"""
        from .domain import slot_time,date
        first=slot_time(date.fromisoformat(self.target),'breakfast')
        if self.preparation_window is not None:
            from .scoped_preparation import validate
            first=validate(self.preparation_window,self.target,self.slot,self.journal.scope[3],self.clock())
            if timestamp(not_after)>first:raise ValueError('scoped_preparation_deadline')
        if self.clock()+timedelta(seconds=60)>min(timestamp(not_after),self.clock()+timedelta(minutes=15),first):
            raise ValueError('scoped_rescue_publication_schedule_expired')

    def hold(self, db, job):
        state = 'saved_pending_review' if self.journal.saved(db) else 'held'
        h = db.execute('SELECT * FROM publication_holds WHERE target_date=?',(self.target,)).fetchone()
        if not h or (h['owner'],h['epoch'],h['state']) != (job['owner'],job['epoch'],state):
            raise ValueError('scoped_rescue_hold_changed')

    def checkpoint(self, db=None, *, remaining=0, safe=False, saved=False):
        self.enabled(safe=safe)
        if db is None:
            with self.store.transaction() as connection: return self.checkpoint(connection,remaining=remaining,safe=safe,saved=saved)
        job = self.journal.job(db); self.hold(db,job)
        self.acceptance_check()
        seals=self.journal.rows('events',"AND kind='execution_seal'",db=db)
        if len(seals)!=1:raise ValueError('scoped_rescue_origin_missing')
        from .scoped_rescue_storage import verify_execution_seal
        verify_execution_seal(db,self.target,json.loads(seals[0]['document']),self.journal.scope[3])
        if any(json.loads(r['observation'] or '{}').get('conflict') for r in self.journal.rows('requests',db=db)):
            raise ValueError('scoped_rescue_reconciliation_required')
        c = json.loads(job['case_observation'] or 'null')
        if not c: raise ValueError('scoped_rescue_case_unconfirmed')
        self.contract.case(c)
        if saved or self.journal.saved(db):
            if c['state'] != 'completed' or c['saved_commit_uuid'] is None: raise ValueError('scoped_rescue_saved_case_unconfirmed')
            deadline = timestamp(c['ogp_finish_deadline_at'])
        else: deadline = timestamp(c['hard_deadline_at'])
        from .domain import slot_time,date
        if not (safe and saved):
            limit=slot_time(date.fromisoformat(self.target),'breakfast')
            if self.preparation_window is not None:
                from .scoped_preparation import validate
                limit=validate(self.preparation_window,self.target,self.slot,self.journal.scope[3],self.clock())
            deadline=min(deadline,limit)
        expired=self.clock()>=deadline if remaining==0 else self.clock()+timedelta(seconds=remaining)>deadline
        if not safe and (c['state'] != 'active' and not self.journal.saved(db) or expired):
            raise ValueError('scoped_rescue_deadline')
        if safe and saved and self.clock() >= deadline: raise ValueError('scoped_rescue_deadline')
        return job

    def policy_gate(self, pre, probe=False):
        p = pre['policy']
        if (p['policy_version'] != POLICY or p['budget_policy_digest'] != BUDGET or p.get('analysis_provider') != 'gemini'
                or p.get('cost_control') != {'mode':'waived','approval_reference_digest':APPROVAL}
                or p['budget_configured'] is not False
                or any(r not in ('deadline_unverified','replacement_cooldown') for r in p['blocking_reasons'])):
            raise ValueError('scoped_rescue_policy_unverified')
        proof = observe_circuit(self.store,pre,self.clock(),allow_probe=probe)
        require_available(self.store,self.clock())
        if 'replacement_cooldown' in p['blocking_reasons'] and not proof: raise ValueError('replacement_cooldown')
        return proof

    def external(self):
        with self.store.transaction() as db:
            job = self.journal.job(db); self.hold(db,job)
        receipt = self.inventory.check(self.target)
        self.journal.event('inventory',digest(receipt),receipt)
        return {'hold_epoch':job['epoch'],'pause_receipt_digest':digest({'target_date':self.target,'owner':job['owner'],'epoch':job['epoch']}),
                'provider_check_digest':digest(receipt),'provider_checked_at':receipt['checked_at']}

    def stage_issue(self, issue, approval, evidence, *, owner, epoch, agent_commit, body_commit):
        """管理側へ渡す本人承認原本を束縛する。権限自体は発行しない。"""
        self.contract.validate('GrantIssueInput',issue); b = issue['basis']
        self.schedule_gate(b['not_after'])
        if (b['target_date'],b['target_slot']) != (self.target,self.slot): raise ValueError('scoped_rescue_target_changed')
        self.contract.other_meals(b,issuing=True)
        from .scoped_rescue_reviews import verify_receipts
        verify_receipts(self,b)
        integration = {'agent_commit':agent_commit,'body_commit':body_commit,'schema_sha256':SCHEMA_SHA}
        import re
        if any(not re.fullmatch('[a-f0-9]{40}',v) for v in (agent_commit,body_commit)):
            raise ValueError('scoped_rescue_integration_unverified')
        required = {'action':'issue','approved':True,'case_uuid':issue['case_uuid'],'planned_prepare_uuid':issue['planned_prepare_uuid'],
            'basis_sha256':digest({k:v for k,v in b.items() if k!='operator_approval_digest'}),'not_after':b['not_after'],
            'expected_review_policy_version':b['review_policy_version'],'expected_agent_commit':agent_commit,
            'expected_body_commit':body_commit,'expected_schema_sha256':SCHEMA_SHA}
        if any(approval.get(k)!=v for k,v in required.items()) or digest(approval)!=b['operator_approval_digest']:
            raise ValueError('scoped_rescue_operator_approval_required')
        if evidence.get('integration') != integration: raise ValueError('scoped_rescue_integration_unverified')
        from .scoped_rescue_origin import capture
        pre = Contract(self.clock).preconditions(self.normal.preconditions(self.target),self.target); self.policy_gate(pre)
        origin = capture(self.store,pre,owner,epoch,self.clock(),self.target,self.slot,self.journal.scope[3])
        ledger,costs,publication = evidence['source_ledger'],evidence['cost_history'],evidence['publication']
        if (ledger.get('native_origin') != origin or digest(ledger)!=b['source_ledger_digest'] or digest(costs)!=b['cost_history_digest']
                or ledger.get('other_meals_reviews') != b['other_meals_reviews']
                or not ledger.get('all_sources_enumerated') or ledger.get('all_native_meal_requests_terminal') is not True
                or ledger.get('unfinished_native_meal_request_count') != 0 or ledger.get('same_menu_repair_history_preserved') is not True
                or ledger.get('publication_evidence_sha256') != digest(publication)
                or costs != {'history_complete':True,'unresolved_cost_count':0,'waived_approval_digest':APPROVAL,'ledgers':origin['document']['ledgers']}):
            raise ValueError('scoped_rescue_origin_changed')
        for key in ('original_context_present','original_first_replan_started_at','original_preparation_deadline_at','original_hard_deadline_at','baseline_consumed_ordinals'):
            if b[key] != origin[key]: raise ValueError('scoped_rescue_origin_changed')
        if ledger.get('consumed_ordinals') != b['baseline_consumed_ordinals'] or any(ledger.get(k)!=b[k] for k in ('original_context_present','original_first_replan_started_at','original_preparation_deadline_at','original_hard_deadline_at')):
            raise ValueError('scoped_rescue_origin_changed')
        own = next(m for m in pre['meals'] if m['expected']['target_slot']==self.slot)
        if any(b[k]!=v for k,v in {'initial_expected':own['expected'],'original_image_sha256':own['image_sha256'],
            'original_stored_image_sha256':own['stored_image_sha256'],'target_analysis_digest':own['analysis_digest']}.items()):
            raise ValueError('scoped_rescue_source_changed')
        if publication.get('hold_epoch') != epoch or publication.get('pause_receipt_digest') != b['pause_receipt_digest'] or not 0 <= (self.clock()-timestamp(publication['checked_at'])).total_seconds() <= 120:
            raise ValueError('external_dispatch_unverified')
        if (b['hold_epoch']!=epoch or b['pause_receipt_digest']!=digest({'target_date':self.target,'owner':owner,'epoch':epoch})
                or publication.get('all_pages_checked') is not True or publication.get('all_providers_checked') is not True
                or any(publication.get(k)!=0 for k in ('dispatching_count','unknown_count','external_accepted_count','published_count'))):
            raise ValueError('external_dispatch_unverified')
        # 実inventoryを再取得し、本人artifactの原本を時刻更新で作り替えない。
        current = self.inventory.check(self.target)
        if not current: raise ValueError('external_dispatch_unverified')
        self.journal.issue(issue,owner,epoch,{'approval':approval,'evidence':evidence,'preconditions':pre,'origin':origin})

    def bind_case(self):
        c = self.client.case(self.journal.scope[3]); self.contract.decode('case',c)
        self.journal.bind(c['case']); return c['case']

    def review_other_meals(self,*args,**kwargs):
        from .scoped_rescue_reviews import review_other_meals
        return review_other_meals(self,*args,**kwargs)

    def operator_bundle(self):
        """承認原本と実点検receiptを渡すだけ。管理CLIは呼び出さない。"""
        job=self.journal.job();issue=json.loads(job['issue_json'])
        self.contract.validate('GrantIssueInput',issue);self.contract.other_meals(issue['basis'],issuing=True)
        from .scoped_rescue_reviews import verify_receipts
        from .scoped_rescue_storage import verify_execution_seal
        verify_receipts(self,issue['basis'])
        with self.store.transaction() as db:
            self.hold(db,job);self.acceptance_check()
            evidence=self.journal.rows('events',"AND kind='issue_evidence'",db=db)
            seals=self.journal.rows('events',"AND kind='execution_seal'",db=db)
            if len(evidence)!=1 or len(seals)!=1:raise ValueError('scoped_rescue_origin_missing')
            verify_execution_seal(db,self.target,json.loads(seals[0]['document']),job['case_uuid'])
        value=json.loads(evidence[0]['document'])
        if digest(value['approval'])!=issue['basis']['operator_approval_digest']:raise ValueError('scoped_rescue_operator_approval_required')
        return {'input':issue,'approval':value['approval'],'evidence':value['evidence'],'agent_issues_authority':False}

    def preconditions(self, c, *, probe=False):
        pre = self.contract.preconditions(self.client.preconditions(self.target,c['case_uuid']),self.target)
        observed=pre['rescue_case'];self.contract.case(observed)
        self.journal.bind(observed)
        if observed!=c:raise ValueError('scoped_rescue_authorization_changed')
        b = c['basis']; own = next(m for m in pre['meals'] if m['expected']['target_slot']==self.slot)
        if (own['expected'] != b['initial_expected'] or own['image_sha256'] != b['original_image_sha256']
                or own['stored_image_sha256'] != b['original_stored_image_sha256'] or own['analysis_digest'] != b['target_analysis_digest']):
            raise ValueError('scoped_rescue_source_changed')
        for a in b['other_meals_expected']:
            m = next(m for m in pre['meals'] if m['expected']['target_slot']==a['target_slot'])
            if (m['expected']['target_result_version'] != a['target_result_version'] or m['image_sha256'] != a['original_sha256']
                    or m['stored_image_sha256'] != a['stored_sha256'] or m['analysis_digest'] != a['analysis_digest']
                    or m['analysis']['quality']['status'] != 'pass'):
                raise ValueError('scoped_rescue_other_meals_changed')
        self.policy_gate(pre,probe=probe); return pre

    @staticmethod
    def ref(c):
        return {'case_uuid':c['case_uuid'],'authorization_digest':c['authorization_digest'],'expected_authorization_revision':c['authorization_revision']}

    def claim_probe(self,proof,*,existing=False):
        if not proof:return
        job=self.journal.job()
        with self.store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS replacement_probe_claims(retry_at TEXT PRIMARY KEY,owner TEXT,epoch INTEGER,target_date TEXT)')
            row=db.execute('SELECT * FROM replacement_probe_claims WHERE retry_at=?',(proof['retry_at'],)).fetchone()
            if row:
                if existing and (row['owner'],row['epoch'],row['target_date'])==(job['case_uuid'],job['epoch'],self.target):return
                raise ValueError('replacement_recovery_required')
            db.execute('INSERT INTO replacement_probe_claims VALUES(?,?,?,?)',(proof['retry_at'],job['case_uuid'],job['epoch'],self.target))

    def call(self, key, kind, inputs, operation, *, saved=False, sensitive=False):
        self.checkpoint(saved=saved,remaining=0 if saved else 60); full = self.journal.scope[3]+':'+key; sha = digest(inputs)
        with self.store.transaction() as db:
            rows = self.journal.rows('calls','AND call_key=?',(full,),db)
            if rows:
                row = rows[0]
                if row['state'] != 'complete' or row['input_digest'] != sha: raise ValueError('scoped_rescue_external_lookup_only')
                if row['encoding']=='redacted':raise ValueError('scoped_rescue_sensitive_lookup_only')
                return bytes(row['value']) if row['encoding']=='bytes' else json.loads(row['value'])
            db.execute("INSERT INTO scoped_rescue_calls VALUES(?,?,?,?,?,?,'started',?,NULL,NULL,NULL)",(*self.journal.scope,full,kind,sha))
        # 実RPCを個別予約するportでは、親の調整処理を実課金として二重予約しない。
        from .scoped_rescue_rpc import GuardedAI
        aggregate=(key in ('menu','image','review','drive') and isinstance(getattr(self.ports,'ai',None),GuardedAI)) or key=='rebuild'
        if not aggregate:self.policy.reserve(self.target,full,kind)
        self.checkpoint(saved=saved,remaining=0 if saved else 60)
        from .scoped_rescue_rpc import CURRENT
        token=CURRENT.set({'key':key,'kind':kind,'saved':saved,'ordinal':0})
        try: value = managed_call(operation)
        except BaseException:
            if not aggregate:self.policy.called(full,False)
            raise
        finally:CURRENT.reset(token)
        if not aggregate:self.policy.called(full,True)
        raw=value if isinstance(value,bytes) else canonical(value)
        stored=canonical({'sha256':hashlib.sha256(raw).hexdigest(),'byte_length':len(raw),'sensitive_result':True}) if sensitive else raw
        with self.store.transaction() as db:
            db.execute("UPDATE scoped_rescue_calls SET state='complete',encoding=?,value=?,finished_at=? WHERE call_key=?",
                ('redacted' if sensitive else 'bytes' if isinstance(value,bytes) else 'json',stored,stamp(self.clock()),full))
        self.checkpoint(saved=saved); return value

    def prepare_candidate(self):
        self.enabled(); c = self.bind_case(); self.contract.case(c,active=True); self.checkpoint(remaining=60)
        self.schedule_gate(c['basis']['not_after'])
        if self.journal.rows('requests'): raise ValueError('scoped_rescue_lookup_only')
        pre = self.preconditions(c,probe=True)
        self.claim_probe(self.policy_gate(pre,probe=True))
        plan = self.call('menu','menu',{'preconditions':pre,'case':c},lambda:self.ports.plan(self.slot,pre,c['attempt_ordinal']))
        image = self.call('image','image',plan,lambda:self.ports.image(plan)); sha = hashlib.sha256(image).hexdigest()
        if sha == c['basis']['original_image_sha256']: raise ValueError('scoped_rescue_failed_image_reused')
        meta = {'contract_version':VERSION,'request_uuid':c['planned_prepare_uuid'],'expected':c['basis']['initial_expected'],
            'precondition_digest':digest(c['basis']['initial_expected'],'pc1_'),'publication_hold':self.external(),
            'input_image_sha256':sha,'original_mime':'image/jpeg','normalized_comment':plan['comment'],
            'attempt_ordinal':c['attempt_ordinal'],'rescue':self.ref(c),'reason':'menu_replan_after_quality_hold'}
        return self.send('candidate_prepare',meta,image)

    def send(self, operation, meta, image=None):
        safe = operation == 'candidate_discard' or operation == 'replacement_resume' and self.journal.saved()
        job = self.checkpoint(safe=safe,saved=operation=='replacement_resume' and self.journal.saved())
        self.contract.validate({'candidate_prepare':'PrepareInput','candidate_discard':'DiscardInput','meal_replace':'CommitInput','replacement_resume':'ResumeInput'}[operation],meta)
        require_available(self.store,self.clock())
        if operation == 'candidate_prepare':
            self.checkpoint(remaining=60); c = self.bind_case(); self.contract.case(c,active=True); pre=self.preconditions(c,probe=True)
            self.claim_probe(self.policy_gate(pre,probe=True),existing=True)
            if meta['rescue'] != self.ref(c): raise ValueError('scoped_rescue_authorization_changed')
        body, content = canonical(meta),'application/json'
        if image is not None:
            boundary = 'mina-scoped-'+meta['request_uuid']
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n'.encode()+body+
                f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="meal"\r\nContent-Type: {meta["original_mime"]}\r\n\r\n'.encode()+image+f'\r\n--{boundary}--\r\n'.encode())
            content = 'multipart/form-data; boundary='+boundary
        if operation == 'candidate_discard':
            c = self.client.candidate(job['prepare_uuid'])['candidate']; self.contract.candidate(c)
            if c['candidate_state'] != 'ready' or any(meta[k] != c[k] for k in ('rescue','candidate_version','analysis_digest')):
                raise ValueError('scoped_rescue_discard_changed')
        key = meta['request_uuid']; self.journal.prepare(meta,operation,body,content)
        row = self.journal.start(key,lambda db:self.checkpoint(db,remaining=60 if operation=='candidate_prepare' else 0,safe=safe,saved=operation=='replacement_resume' and self.journal.saved(db)))
        self.checkpoint(remaining=60 if operation=='candidate_prepare' else 0,safe=safe,saved=operation=='replacement_resume' and self.journal.saved())
        try: value = self.client.send(operation,key,bytes(row['body']),content,job['prepare_uuid'])
        except ReplacementPaused as error:
            remember(self.store,error.envelope); self.journal.event('server_rejection',key,error.envelope)
            if error.envelope.get('request'): self.accept(key,error.envelope)
            self.observe_pause()
            self.journal.stage('paused'); raise
        return self.accept(key,value)

    def accept(self, key, value):
        row = self.journal.request(key); self.contract.decode(row['operation'],value); remember(self.store,value)
        c = value['rescue_case']; self.journal.bind(c); job = self.journal.job()
        candidate = value.get('candidate')
        if candidate and (candidate['prepare_uuid'] != job['prepare_uuid'] or candidate['rescue']['case_uuid'] != c['case_uuid']):
            raise ValueError('scoped_rescue_candidate_changed')
        if row['operation']=='meal_replace' and value['request']['persistence']=='saved' and (c['state']!='completed' or c['saved_commit_uuid']!=key):
            raise ValueError('scoped_rescue_saved_case_unconfirmed')
        state = self.journal.observe(key,value)
        if state.get('conflict'): stage = 'unknown'
        elif self.journal.saved(): stage = 'saved'
        elif row['operation']=='candidate_discard' and state.get('outcome')=='applied': stage = 'rejected'
        elif row['operation']=='candidate_prepare' and state.get('outcome')=='applied' and candidate and candidate['candidate_state']=='ready': stage = 'ready'
        else: stage = 'unknown'
        if value['request'].get('stage')=='paused':
            self.observe_pause()
            if stage!='saved':stage='paused'
        self.journal.stage(stage); return value

    def observe_pause(self):
        """実OPENのGET証拠だけを保存し、時間経過から解除許可を作らない。"""
        try:
            pre=self.contract.preconditions(self.client.preconditions(self.target,self.journal.scope[3]),self.target)
            self.journal.event('circuit',self.journal.scope[3],pre['circuit'])
            observe_circuit(self.store,pre,self.clock())
        except (ValueError,KeyError):
            return

    def reconcile(self, key):
        row = self.journal.request(key)
        if row['phase']=='prepared': raise ValueError('scoped_rescue_request_not_sent')
        return self.accept(key,self.client.request(key,row['operation']))

    def candidate(self):
        job = self.journal.job(); value = self.client.candidate(job['prepare_uuid']); self.contract.decode('candidate',value)
        c = self.contract.candidate(value['candidate'],ready=True); meta = json.loads(self.journal.request(job['prepare_uuid'])['metadata'])
        if any(c[k]!=v for k,v in {'prepared_preconditions':meta['expected'],'prepared_publication_hold':meta['publication_hold'],
            'rescue':meta['rescue'],'attempt_ordinal':meta['attempt_ordinal'],'hard_deadline_at':job['deadline']}.items()) or c['original']['sha256']!=meta['input_image_sha256']:
            raise ValueError('scoped_rescue_candidate_changed')
        return c

    def decide(self):
        self.checkpoint(); c = self.candidate(); case = self.bind_case(); self.contract.case(case,active=True)
        if c['rescue']!=self.ref(case): raise ValueError('scoped_rescue_authorization_changed')
        images = {r:self.client.image(c['prepare_uuid'],r,ImageDescriptor(c['candidate_version'],c[r]['sha256'],c[r]['byte_length'],c[r]['mime'])) for r in ('original','stored')}
        menus = self.journal.rows('calls',"AND call_key=? AND state='complete'",(self.journal.scope[3]+':menu',))
        if len(menus)!=1: raise ValueError('scoped_rescue_plan_missing')
        plan = json.loads(menus[0]['value'])
        review = self.call('review','review',{'candidate':c,'image_sha':{r:hashlib.sha256(v).hexdigest() for r,v in images.items()}},lambda:self.ports.review(images,plan,c))
        if review not in ({'verdict':'pass','observations':[]},{'verdict':'fail','observations':['image_analysis_mismatch']}): raise ValueError('scoped_rescue_review_invalid')
        pre = self.preconditions(case); reason = 'independent_review_failed' if review['verdict']=='fail' else None
        try: nutrition = self.contract.nutrition(pre,c)
        except ValueError as error:
            if str(error)!='nutrition_budget_failed': raise
            reason = 'nutrition_budget_failed'
        reviewed = self.journal.rows('calls',"AND call_key=?",(self.journal.scope[3]+':review',))[0]['finished_at']
        proof = {'candidate_version':c['candidate_version'],'analysis_digest':c['analysis_digest'],'original_sha256':c['original']['sha256'],
            'stored_sha256':c['stored']['sha256'],'reviewer_role':'independent_reviewer','reviewed_at':reviewed,'verdict':'fail' if reason else 'pass'}
        meta = {'contract_version':VERSION,'request_uuid':str(uuid.uuid4()),'prepare_uuid':c['prepare_uuid'],'candidate_version':c['candidate_version'],
            'analysis_digest':c['analysis_digest'],'expected_control_revision':c['control_revision'],'rescue':self.ref(case)}
        if reason:
            proof['observations'] = ['nutrition_budget_failed'] if reason=='nutrition_budget_failed' else review['observations']
            meta.update(reason=reason,review=proof,review_evidence_digest=digest(proof))
            return self.send('candidate_discard',meta)
        proof['review_method'] = 'candidate-image-food-independent-v1'
        drive = self.call('drive','drive',{'candidate':c},lambda:self.ports.drive(images['original'],c)); self.contract.validate('DriveCheck',drive)
        if (drive['candidate_version']!=c['candidate_version'] or drive['original_sha256']!=c['original']['sha256']
                or drive['original_download_sha256']!=c['original']['sha256']
                or drive['previous_original_sha256']!=case['basis']['original_image_sha256']
                or drive['previous_download_sha256']!=case['basis']['original_image_sha256']
                or not 0<=(self.clock()-timestamp(drive['checked_at'])).total_seconds()<=120
                or not 0<=(self.clock()-timestamp(proof['reviewed_at'])).total_seconds()<=120):
            raise ValueError('scoped_rescue_drive_changed')
        fresh = self.preconditions(case); nutrition = self.contract.nutrition(fresh,c)
        if self.bind_case()!=case: raise ValueError('scoped_rescue_authorization_changed')
        meta.update(expected=c['prepared_preconditions'],precondition_digest=c['precondition_digest'],publication_hold=self.external(),
            input_image_sha256=c['original']['sha256'],review=proof,review_evidence_digest=digest(proof),drive_check=drive,drive_evidence_digest=digest(drive),nutrition_check=nutrition)
        return self.send('meal_replace',meta)

    def resume(self, original_key):
        """pausedの実照会に基づく明示操作。既知saved以外はOFFで再開しない。"""
        row = self.journal.request(original_key); value = self.reconcile(original_key); r = value['request']
        saved = row['operation']=='meal_replace' and r.get('persistence')=='saved'
        if (r['stage']!='paused' or type(value.get('resume_stage_version')) is not int
                or row['operation'] not in ('candidate_prepare','meal_replace') or row['operation']=='meal_replace' and not saved):
            raise ValueError('scoped_rescue_resume_unknown')
        self.checkpoint(safe=saved,saved=saved); c = self.bind_case()
        if saved:
            if c['state']!='completed' or c['saved_commit_uuid']!=original_key or self.clock()>=timestamp(c['ogp_finish_deadline_at']):
                raise ValueError('scoped_rescue_deadline')
        else: self.contract.case(c,active=True)
        pre = self.contract.preconditions(self.client.preconditions(self.target,c['case_uuid']),self.target)
        self.claim_probe(self.policy_gate(pre,probe=True))
        from .replacement_control import initialize
        initialize(self.store); key = str(uuid.uuid4())
        with self.store.transaction() as db:
            if db.execute('SELECT 1 FROM replacement_resume_claims WHERE original_uuid=? AND stage_version=?',(original_key,value['resume_stage_version'])).fetchone():
                raise ValueError('scoped_rescue_lookup_only')
            db.execute('INSERT INTO replacement_resume_claims VALUES(?,?,?)',(original_key,value['resume_stage_version'],key))
        meta = json.loads(row['metadata'])
        control = {'contract_version':VERSION,'request_uuid':key,'original_request_uuid':original_key,
            'expected_stage_version':value['resume_stage_version'],'expected_input_sha256':meta['input_image_sha256'],
            'policy_version':POLICY,'budget_policy_digest':BUDGET,'publication_hold':self.external(),'rescue':self.ref(c)}
        result = self.send('replacement_resume',control)
        if result['request'].get('outcome')!='applied': raise ValueError('scoped_rescue_resume_unresolved')
        return self.reconcile(original_key)

    def discard_reviewed(self):
        """OFFでも実在する不合格点検・ready候補の準備時refだけでDを送る。"""
        job = self.checkpoint(safe=True)
        previous = self.journal.rows('requests',"AND operation='candidate_discard'")
        if previous:
            r = previous[0]
            return self.reconcile(r['request_key']) if r['phase']!='prepared' else self.send('candidate_discard',json.loads(r['metadata']))
        reviews = self.journal.rows('calls',"AND call_key=? AND state='complete'",(self.journal.scope[3]+':review',))
        c = self.client.candidate(job['prepare_uuid'])['candidate']; self.contract.candidate(c)
        if len(reviews)!=1 or c['candidate_state']!='ready' or json.loads(reviews[0]['value'])!={'verdict':'fail','observations':['image_analysis_mismatch']}:
            raise ValueError('scoped_rescue_known_failed_review_required')
        proof = {'candidate_version':c['candidate_version'],'analysis_digest':c['analysis_digest'],
            'original_sha256':c['original']['sha256'],'stored_sha256':c['stored']['sha256'],'reviewer_role':'independent_reviewer',
            'reviewed_at':reviews[0]['finished_at'],'verdict':'fail','observations':['image_analysis_mismatch']}
        return self.send('candidate_discard',{'contract_version':VERSION,'request_uuid':str(uuid.uuid4()),'prepare_uuid':job['prepare_uuid'],
            'candidate_version':c['candidate_version'],'analysis_digest':c['analysis_digest'],'expected_control_revision':c['control_revision'],
            'reason':'independent_review_failed','review':proof,'review_evidence_digest':digest(proof),'rescue':c['rescue']})

    def finish(self, commit_key, rebuilder):
        """保存後の新版候補を構成し、再承認待ちで保持する。公開や承認通知は送らない。"""
        job = self.checkpoint(saved=True); response = self.reconcile(commit_key)
        if response['request']['persistence']!='saved' or response['result_status']!='unchanged' or response['result']!=response['current_result']:
            raise ValueError('scoped_rescue_saved_source_changed')
        from .m1_contract import validate_state
        state = rebuilder.meal.day_state(self.target); validate_state(state,self.target,self.clock())
        if not 1800<=sum(m['analysis']['summary']['totalCalories'] for m in state['meals'])<=2000:
            raise ValueError('nutrition_budget_failed')
        menu = self.journal.rows('calls',"AND call_key=? AND state='complete'",(self.journal.scope[3]+':menu',))
        image = self.journal.rows('calls',"AND call_key=? AND state='complete'",(self.journal.scope[3]+':image',))
        if len(menu)!=1 or len(image)!=1: raise ValueError('scoped_rescue_material_missing')
        e = {'target_date':self.target,'slot':self.slot,'prepare_uuid':job['prepare_uuid'],'commit_uuid':commit_key,
             'owner':job['owner'],'epoch':job['epoch'],'contract_version':VERSION}
        rebuilder.response_reader,rebuilder.response_contract,rebuilder.checkpoint = self.client,self.contract,lambda:self.checkpoint(saved=True)
        rebuilder.preparation_window=self.preparation_window
        from .scoped_rescue_rpc import GuardedAI,guarded_drive
        if not isinstance(rebuilder.ai,GuardedAI):rebuilder.ai=GuardedAI(self,rebuilder.ai)
        rebuilder.drive=guarded_drive(self,rebuilder.drive)
        material = {'plan':bytes(menu[0]['value']).decode(),'image':bytes(image[0]['value'])}
        from dataclasses import asdict
        def rebuild():
            normal = Contract(self.clock).preconditions(self.normal.preconditions(self.target),self.target)
            xs,igs,proof = rebuilder(e,response,normal,material)
            return {'x':[asdict(c) for c in xs],'instagram':[asdict(c) for c in igs],'proof':proof}
        result = self.call('rebuild','rebuild',{'commit_uuid':commit_key,'input_hash':response['request']['input_hash']},rebuild,saved=True)
        from .domain import Candidate
        from .instagram_domain import InstagramCandidate
        from .replacement_bundle import stage
        from .instagram_media import HOSTS
        xs = [Candidate(**v) for v in result['x']]; igs = [InstagramCandidate(**v) for v in result['instagram']]
        with self.store.transaction() as db:
            from .publication_hold import exists
            old = db.execute('SELECT 1 FROM replacement_bundles WHERE target_date=?',(self.target,)).fetchone() if exists(db,'replacement_bundles') else None
        if not old: stage(self.store,self.target,job['owner'],job['epoch'],result['proof']['source_digest'],xs,igs,rebuilder.host,HOSTS,self.clock(),result['proof'],required_channels=('x','instagram') if igs else ('x',),checkpoint=lambda db:self.checkpoint(db,saved=True),preparation_window=self.preparation_window)
        else:
            with self.store.transaction() as db:
                self.checkpoint(db,saved=True)
                bundle=db.execute('SELECT * FROM replacement_bundles WHERE target_date=?',(self.target,)).fetchone()
                members=list(db.execute('SELECT channel,slot,revision,digest FROM replacement_bundle_members WHERE target_date=? ORDER BY channel,slot',(self.target,)))
                expected=[]
                for channel,candidate in [('x',c) for c in xs]+[('instagram',c) for c in igs]:
                    table='candidates' if channel=='x' else 'instagram_candidates'
                    row=db.execute('SELECT revision,payload,digest FROM '+table+' WHERE target_date=? AND slot=?',(self.target,candidate.slot)).fetchone()
                    if not row or row['digest']!=candidate.digest or json.loads(row['payload'])!=asdict(candidate):raise ValueError('scoped_rescue_bundle_changed')
                    expected.append((channel,candidate.slot,row['revision'],candidate.digest))
                expected.sort()
                from .replacement_bundle import proof_valid
                if ((bundle['owner'],bundle['epoch'],bundle['source_digest'])!=(job['owner'],job['epoch'],result['proof']['source_digest'])
                        or [tuple(m) for m in members]!=expected or json.loads(bundle['required'])!=[list(v) for v in expected]
                        or bundle['digest']!=digest(expected) or bundle['state']!='approval_pending'
                        or not proof_valid(result['proof'],bundle['source_digest'],self.clock())):
                    raise ValueError('scoped_rescue_bundle_changed')
        with self.store.transaction() as db:
            self.checkpoint(db,saved=True)
            # 旧UUID・期限・初回・消費・原本は保持し、完成後の読取承認照合だけを通常巡回へ渡す。
            changed=db.execute("UPDATE replacement_episodes SET stage='new_bundle_pending_approval' WHERE target_date=? AND slot=? AND owner=? AND epoch=?",
                (self.target,self.slot,job['owner'],job['epoch'])).rowcount
            if changed!=1:raise ValueError('scoped_rescue_origin_changed')
            db.execute("UPDATE scoped_rescue_jobs SET stage='all_meals_reviewed' WHERE contract=? AND target_date=? AND slot=? AND case_uuid=?",self.journal.scope)
            self.journal.event('all_meals_reviewed',commit_key,result,db)
            self.journal.event('readonly_approval_handoff',commit_key,{'normal_stage':'new_bundle_pending_approval','old_deadline_preserved':True,'old_request_uuids_preserved':True},db)
            self.checkpoint(db,saved=True)
            db.execute("UPDATE preparation SET state='complete' WHERE target_date=?",(self.target,))
            db.execute("UPDATE preparation_progress SET status='complete' WHERE target_date=?",(self.target,))
        return {**result,'publication_hold':True,'approval_required':True,'discord_sent':False}


def _trace(method):
    from functools import wraps
    @wraps(method)
    def invoke(self,*args,**kwargs):
        try:return method(self,*args,**kwargs)
        except Exception as error:
            known=set(self.contract.schema['$defs']['Reason']['enum'])
            # 固定の内部理由コードだけを記録し、食品原文・応答・秘密を例外文から残さない。
            reason=str(error)
            from .scoped_rescue_reasons import INTERNAL_REASONS
            if reason not in known and reason not in INTERNAL_REASONS:
                reason='scoped_rescue_internal_unresolved'
            self.journal.event('stopped',method.__name__,{'stage':method.__name__,'reason':reason,
                'automatic_retry_allowed':False,'saved':self.journal.saved()})
            raise
    return invoke


for _name in ('stage_issue','operator_bundle','review_other_meals','prepare_candidate','decide','reconcile','resume','discard_reviewed','finish'):
    setattr(ScopedRescueFlow,_name,_trace(getattr(ScopedRescueFlow,_name)))
