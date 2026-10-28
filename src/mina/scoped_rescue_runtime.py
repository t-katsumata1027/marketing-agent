"""v3の明示構築。独立受入・配置・実行承認の原本がない限り接続しない。"""
import hashlib
import json
import os
import re
from datetime import timedelta
from pathlib import Path
from .scoped_rescue_contract import VERSION, SCHEMA_SHA, ScopedRescueClient, ScopedRescueContract, target_scope


def acceptance(settings, commit):
    target,slot = target_scope(settings['target_date'],settings['target_slot'])
    from .clients import MealClient
    MealClient.request_key(settings['case_uuid'])
    if not re.fullmatch('[a-f0-9]{40}',commit) or not re.fullmatch('[a-f0-9]{40}',settings.get('body_commit','')):
        raise ValueError('scoped_rescue_version_unverified')
    if settings.get('contract_version')!=VERSION or settings.get('schema_sha256')!=SCHEMA_SHA or settings.get('agent_commit')!=commit:
        raise ValueError('scoped_rescue_acceptance_version_changed')
    for role in ('provider_acceptance','agent_acceptance','implementation_review','deployment_approval','execution_approval'):
        ref = settings.get(role,{})
        path = Path(ref.get('path',''))
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=ref.get('sha256'):
            raise ValueError('scoped_rescue_acceptance_artifact_missing')
        document = json.loads(path.read_text(encoding='utf-8'))
        fixed = {'role':role,'contract_version':VERSION,'scope':'operator_scoped_meal_replacement','target_date':target,
            'target_slot':slot,'case_uuid':settings['case_uuid'],'agent_commit':commit,'body_commit':settings['body_commit'],
            'schema_sha256':SCHEMA_SHA,'verdict':'approved' if role.endswith('_approval') else 'Go'}
        if any(document.get(k)!=v for k,v in fixed.items()): raise ValueError('scoped_rescue_acceptance_scope_changed')
        if role=='execution_approval' and document.get('preparation_window')!=settings.get('preparation_window'):
            raise ValueError('scoped_rescue_preparation_approval_changed')
        if role=='execution_approval' and document.get('pregrant_refresh')!=settings.get('pregrant_refresh'):
            raise ValueError('scoped_rescue_refresh_not_approved')
        if role=='execution_approval' and document.get('management_helpers')!=settings.get('management_helpers'):
            raise ValueError('scoped_coordination_helpers_unapproved')
        if role=='execution_approval' and settings.get('pregrant_refresh') is not None:
            if (document.get('planned_prepare_uuid')!=settings.get('planned_prepare_uuid')
                    or settings.get('pregrant_refresh',{}).get('planned_prepare_uuid')!=settings.get('planned_prepare_uuid')):
                raise ValueError('scoped_rescue_refresh_plan_changed')
            if document.get('context_reference')!=settings.get('context_reference'):
                raise ValueError('scoped_rescue_origin_file_changed')
    if settings.get('preparation_window') is not None:
        from .scoped_preparation import validate
        from .replacement_client import timestamp
        # 実時刻による期限検査は各checkpointで行い、ここでは範囲と承認原本を照合する。
        window=settings['preparation_window']
        validate(window,target,slot,settings['case_uuid'],timestamp(window['not_after'])-timedelta(seconds=1))
    return target,slot


def build_flow(store, directory, draft, saved, commit, ai, drive, meal, clock, settings=None, *, recovery=False):
    if not recovery and os.getenv('MINA_SCOPED_RESCUE_ENABLED','false').lower()!='true':
        raise ValueError('scoped_rescue_execution_disabled')
    if settings is None:
        path = Path(os.environ.get('MINA_SCOPED_RESCUE_CONFIG',''))
        if path.is_symlink() or not path.is_file(): raise ValueError('scoped_rescue_configuration_missing')
        settings = json.loads(path.read_text(encoding='utf-8'))
    target,slot = acceptance(settings,commit)
    from .readiness import release_commit
    root=Path(__file__).resolve().parents[2]
    release_commit(root,commit)
    code_paths=[*sorted((root/'src/mina').rglob('*.py')),root/'src/mina/contracts/rescue_v3/mina-rescue-v3.schema.json']
    code_seal={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in code_paths}
    def check_acceptance():
        acceptance(settings,commit)
        if any(p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha for p,sha in code_seal.items()):
            raise ValueError('scoped_rescue_code_changed')
    from .replacement_policy import ReplacementPolicy
    from .replacement_inventory import ExternalInventory,BufferInventoryAdapter
    from .replacement_ports import PreparationPorts
    from .replacement_client import ReplacementReadClient
    from .replacement_contract import Contract
    from .buffer_publisher import production_client
    from .scoped_rescue_flow import ScopedRescueFlow
    policy = ReplacementPolicy(store,settings['policy'],clock)
    api = production_client(store)
    inventory = ExternalInventory(store,BufferInventoryAdapter(api,settings['organization_id']),clock,settings['channels'])
    if recovery:
        from .publication_hold import exists
        with store.transaction() as db:
            if not exists(db,'scoped_rescue_jobs') or not db.execute('SELECT 1 FROM scoped_rescue_jobs WHERE contract=? AND target_date=? AND slot=? AND case_uuid=?',(VERSION,target,slot,settings['case_uuid'])).fetchone():
                raise ValueError('scoped_rescue_recovery_job_missing')
        class LookupOnlyPorts:
            def __getattr__(self,name): raise ValueError('scoped_rescue_recovery_generation_forbidden')
        ports = LookupOnlyPorts()
    else: ports = PreparationPorts(ai,drive,draft,saved,Path(directory)/'scoped-rescue'/settings['case_uuid'],clock)
    contract = ScopedRescueContract(clock,target,slot)
    client = ScopedRescueClient(meal.base,meal.token,decoder=contract.decode,clock=clock,write_authorized=True)
    normal = Contract(clock)
    reader = ReplacementReadClient(meal.base,meal.token,decoder=normal.decode,clock=clock)
    flow=ScopedRescueFlow(store,client,reader,policy,inventory,ports,clock,target,slot,settings['case_uuid'])
    flow.preparation_window=settings.get('preparation_window')
    flow.pregrant_refresh_approval=settings.get('pregrant_refresh')
    flow.execution_approval_sha256=settings['execution_approval']['sha256']
    flow.integration={k:settings[k] for k in ('agent_commit','body_commit','schema_sha256')}
    flow.management_helpers=settings.get('management_helpers')
    flow.planned_prepare_uuid=settings.get('planned_prepare_uuid')
    def check_bound_acceptance():
        check_acceptance()
        if (flow.pregrant_refresh_approval!=settings.get('pregrant_refresh')
                or flow.execution_approval_sha256!=settings['execution_approval']['sha256']
                or flow.management_helpers!=settings.get('management_helpers')
                or flow.planned_prepare_uuid!=settings.get('planned_prepare_uuid')
                or flow.integration!={k:settings[k] for k in ('agent_commit','body_commit','schema_sha256')}):
            raise ValueError('scoped_rescue_refresh_acceptance_changed')
    flow.acceptance_check=check_bound_acceptance
    return flow
