"""保存後品質保留の明示接続点。提供側・予算・外部adapter未受入では構築しない。"""
import os
from .preparation_status import QualityHold

class ReplacementCoordinator:
    def __init__(self,flow,rebuild,host,hosts,*,readiness):
        required=('contract_verified','provider_qa_verified','external_inventory_adapter_verified','budget_allocation_verified')
        if os.getenv('MINA_MEAL_REPLACEMENT_ENABLED','false').lower()!='true' or any(readiness.get(k) is not True for k in required):
            raise ValueError('replacement_integration_not_enabled')
        self.flow,self.rebuild,self.host,self.hosts=flow,rebuild,host,hosts

    def handle_saved(self,target,error):
        if not isinstance(error,QualityHold):raise error
        parts=error.step.split(':')
        if len(parts)<2 or parts[0]!='meal_review' or parts[1] not in ('breakfast','lunch','dinner'):raise error
        self.flow.begin(target,parts[1],quality_hold=error)
        return self.advance(target)

    def advance(self,target):
        """通常成功・明示不採用は同一巡回内で続行し、不明時は照会へ戻す。"""
        for _ in range(6):
            e=self.flow.episode(target);stage=e['stage']
            if stage=='ready':self.flow.decide_candidate(target)
            elif stage in ('discarded','rejected'):self.flow.next_candidate(target)
            elif stage in ('saved','rebuilding'):return self.flow.finish(target,self.rebuild,self.host,self.hosts)
            else:return {'target_date':target,'state':'held:replacement','stage':stage}
        raise ValueError('replacement_cycle_limit')

    def resume(self,target):
        # 再起動時にbeginや送信を繰り返さない。終端照会後の未送信工程だけを続行する。
        e=self.flow.episode(target)
        if e['stage']=='checking':self.flow.resume_checking(target)
        elif all(e.get(k) is None for k in ('prepare_uuid','discard_uuid','commit_uuid')):
            return {'target_date':target,'state':'held:replacement','stage':e['stage'],'reason':e['reason']}
        else:self.flow.reconcile(target)
        return self.advance(target)

    def refresh_approvals(self,target,verify):
        """期限前に完成した新版の承認は通常期限で受領し、解除直前に再照合する。"""
        from .replacement_bundle import refresh
        receipt=self.flow.inventory.check(target)
        source_digest,proof=verify(target)
        proof['read_started_at']=min(proof['read_started_at'],receipt['read_started_at'])
        refresh(self.flow.store,target,source_digest,proof,self.flow.clock())

def build_coordinator(store,directory,draft,saved,evidence,commit,host,ai,drive,meal,clock):
    """通常準備からの接続。専用受入証跡・予算設定のない有効化を拒否する。"""
    if os.getenv('MINA_MEAL_REPLACEMENT_ENABLED','false').lower()!='true':return None
    import json
    from pathlib import Path
    from .replacement_contract import Contract,RUNTIME_SCHEMA_SHA256
    from .replacement_policy import ReplacementPolicy
    from .replacement_client import ReplacementClient
    from .replacement_inventory import BufferInventoryAdapter,ExternalInventory
    from .replacement_flow import ReplacementFlow
    from .replacement_ports import PreparationPorts
    from .replacement_rebuild import ReplacementRebuilder
    from .buffer_publisher import production_client
    from .instagram_media import HOSTS
    path=os.getenv('MINA_REPLACEMENT_AGENT_CONFIG')
    if not path:raise ValueError('replacement_integration_not_configured')
    settings=json.loads(Path(path).read_text(encoding='utf-8'))
    readiness=settings['readiness']
    if any(readiness.get(k) is not True for k in ('contract_verified','provider_qa_verified','external_inventory_adapter_verified','budget_allocation_verified')):
        raise ValueError('replacement_integration_not_enabled')
    if (readiness.get('contract_schema_sha256')!=RUNTIME_SCHEMA_SHA256 or not readiness.get('provider_deployment_verified')
            or not readiness.get('qa_evidence_digest') or not readiness.get('deployment_commit')):raise ValueError('replacement_provider_not_verified')
    # readinessは受入結果の参照であり、未検証の外部事実をtrueへ補完しない。
    contract=Contract(clock);policy=ReplacementPolicy(store,settings['policy'],clock)
    api=production_client(store);inventory=ExternalInventory(store,BufferInventoryAdapter(api,settings['organization_id']),clock,settings['channels'])
    ports_path=Path(directory)/'replacement-jobs'
    ports=PreparationPorts(ai,drive,draft,saved,ports_path,clock)
    client=ReplacementClient(meal.base,meal.token,decoder=contract.decode,clock=clock,write_authorized=True)
    flow=ReplacementFlow(store,client,policy,inventory,ports,clock)
    def inspect_composition(target):
        from .m1_contract import validate_state
        observed=meal.day_state(target);validate_state(observed,target,clock())
        if len(observed['meals'])!=3 or {v['meal']['meal_type'] for v in observed['meals']}!={'breakfast','lunch','dinner'}:
            raise ValueError('replacement_source_incomplete')
    flow.inspect_composition=inspect_composition
    from .replacement_collection import Collection
    def admit_collection():
        # 完全な既存日のGETは提供側設定の読取にのみ使用し、対象日の期限・版へ転用しない。
        from .domain import JST
        today=clock().astimezone(JST).date().isoformat()
        observed=contract.preconditions(client.preconditions(today),today)
        p=observed['policy'];c=observed.get('circuit')
        if (not policy.waived or p.get('analysis_provider')!='gemini'
                or p['policy_version']!=policy.policy['policy_version'] or p['budget_policy_digest']!=policy.policy_digest
                or p.get('cost_control')!={'mode':'waived','approval_reference_digest':policy.policy['approval_reference_digest']}
                or p['budget_configured'] is not False
                or set(p['blocking_reasons'])-{'deadline_unverified','deadline_exceeded','replan_limit_exceeded'}
                or not c or c['state']!='closed' or c['recovery_required']):raise ValueError('replacement_policy_unverified')
    # 3食完成日の元hold証明は収集OFFでも行い、書込みの許可とは分離する。
    flow.collect_missing=Collection(store,directory,draft,saved,meal,ai,host,policy,inventory,clock,admit_collection)
    rebuilder=ReplacementRebuilder(store,meal,ai,drive,Path(directory).parent,settings['instagram_directory'],evidence,commit,host,clock,
        api=api,ports_directory=ports_path,instagram_enabled='instagram' in settings['policy']['required_channels'])
    with store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS replacement_runtime_context (target_date TEXT PRIMARY KEY,context TEXT NOT NULL)')
        context={'directory':str(directory),'draft':draft,'saved':saved,'evidence':evidence,'commit':commit,'host':host}
        old=db.execute('SELECT context FROM replacement_runtime_context WHERE target_date=?',(Path(directory).name,)).fetchone()
        if old and json.loads(old[0])!=context:raise ValueError('replacement_context_changed')
        db.execute('INSERT OR IGNORE INTO replacement_runtime_context VALUES(?,?)',(Path(directory).name,json.dumps(context,ensure_ascii=False)))
    return ReplacementCoordinator(flow,rebuilder,host,HOSTS,readiness=readiness)

def maintain(store,clock,*,factory=None):
    """通常巡回の再照会。既定無効、日別バックオフ・回数上限を永続保持する。"""
    if os.getenv('MINA_MEAL_REPLACEMENT_ENABLED','false').lower()!='true':return []
    import json
    from .publication_hold import exists
    with store.transaction() as db:
        if not exists(db,'replacement_episodes') or not exists(db,'replacement_runtime_context'):return []
        db.execute('CREATE TABLE IF NOT EXISTS replacement_poll (target_date TEXT PRIMARY KEY,next_at REAL NOT NULL,count INTEGER NOT NULL)')
        rows=[dict(r) for r in db.execute("SELECT e.*,c.context FROM replacement_episodes e JOIN replacement_runtime_context c USING(target_date) JOIN publication_holds h USING(target_date) WHERE h.state IN ('held','saved_pending_review') AND e.stage<>'released_old'")]
    outcomes=[]
    for e in rows:
        target=e['target_date'];now=clock()
        if e['stage']=='new_bundle_pending_approval':
            from .domain import approval_time,date
            if now>=approval_time(date.fromisoformat(target)):
                from .instagram_store import InstagramStore
                ig=InstagramStore(store.path)
                for row in ig.rows(target):
                    if row['state']=='approval_pending':ig.queue_approval(row)
            with store.transaction() as db:
                members=list(db.execute('SELECT * FROM replacement_bundle_members WHERE target_date=?',(target,)))
                def decided(m):
                    table='candidates' if m['channel']=='x' else 'instagram_candidates'
                    r=db.execute('SELECT state,actor FROM '+table+' WHERE target_date=? AND slot=?',(target,m['slot'])).fetchone()
                    return r and r['actor'] and r['state'] in ('approved','cancelled')
                if not members or not all(decided(m) for m in members):continue
        with store.transaction() as db:
            poll=db.execute('SELECT * FROM replacement_poll WHERE target_date=?',(target,)).fetchone()
            if poll and (poll['next_at']>now.timestamp() or poll['count']>=64 and e['stage']!='new_bundle_pending_approval'):continue
            count=poll['count']+1 if poll else 1
            db.execute('INSERT OR REPLACE INTO replacement_poll VALUES(?,?,?)',(target,now.timestamp()+min(300,30*2**min(count-1,4)),count))
        try:
            ctx=json.loads(e['context'])
            if factory:coordinator=factory(ctx)
            else:
                from .clients import preparation_client
                from .codex_client import generation_client
                from .drive import DriveClient
                # 期限後・承認待ちの確認では生成器を作らず、GET専用のクライアントを使う。
                readonly=e['stage']=='new_bundle_pending_approval' or now.timestamp()>=e['deadline']
                coordinator=build_coordinator(store,ctx['directory'],ctx['draft'],ctx['saved'],ctx['evidence'],ctx['commit'],ctx['host'],
                    None if readonly else generation_client(),None if readonly else DriveClient.from_env(),
                    preparation_client(os.getenv('MINA_MEAL_API_BASE_URL',''),os.getenv('MINA_AGENT_API_TOKEN','')),clock)
            if e['stage']=='new_bundle_pending_approval':
                coordinator.refresh_approvals(target,coordinator.rebuild.verify_readonly)
                outcome={'state':'approval_checked'}
            elif now.timestamp()>=e['deadline']:
                no_request=all(k in e and e[k] is None for k in ('prepare_uuid','discard_uuid','commit_uuid'))
                if e['stage']!='checking' and not no_request:coordinator.flow.reconcile(target)
                outcome={'state':'held:deadline'}
            else:outcome=coordinator.resume(target)
            if outcome.get('state')=='approval_pending':store.preparation_done(target)
            outcomes.append({'target_date':target,**outcome})
        except Exception as error:
            # 認証、UUID、原文を表示せず、保護台帳の既存工程記録から調査する。
            reason=getattr(error,'audit_reason',None)
            fixed={'external_dispatch_present','external_dispatch_unverified','external_inventory_incomplete','external_inventory_stale',
                   'replacement_source_incomplete','replacement_preflight_busy','replacement_quality_hold_required'}
            if reason in fixed:store.record_preparation_failure(target,'registration',error,clock())
            else:store.audit.emit('creation',target,None,None,'reconciliation','held','outcome_unknown',now=clock())
            outcomes.append({'target_date':target,'state':'held:replacement_lookup'})
    return outcomes


def explicit_resume(store,target,clock):
    """運用者が指定した既存日のpausedだけを、元の実行文脈で再開する。"""
    import json
    from .replacement_control import resume_paused
    from .clients import MealClient
    from .codex_client import generation_client
    from .drive import DriveClient
    from .domain import date
    if date.fromisoformat(target).isoformat()!=target:
        raise ValueError('replacement_date_invalid')
    if os.getenv('MINA_MEAL_REPLACEMENT_ENABLED','false').lower()!='true' or os.getenv('MINA_LIVE_WRITES','false').lower()!='true':
        raise ValueError('replacement_write_disabled')
    with store.transaction() as db:
        row=db.execute('SELECT context FROM replacement_runtime_context WHERE target_date=?',(target,)).fetchone()
        if not row:raise ValueError('replacement_context_missing')
        ctx=json.loads(row['context'])
    coordinator=build_coordinator(store,ctx['directory'],ctx['draft'],ctx['saved'],ctx['evidence'],ctx['commit'],ctx['host'],
        generation_client(),DriveClient.from_env(),MealClient(os.getenv('MINA_MEAL_API_BASE_URL',''),os.getenv('MINA_AGENT_API_TOKEN','')),clock)
    resume_paused(coordinator.flow,target)
    return coordinator.advance(target)
