"""限定救済の運用者向け明示構築。通常巡回へ自動登録しない。"""
import hashlib
import json
import os
from pathlib import Path
from .rescue_contract import RescueClient,RescueContract,SCHEMA_SHA,TARGET


def acceptance(settings, commit):
    """両側の固定版受入artifactを実読取し、通常ONの転記を拒否する。"""
    if settings.get('contract_schema_sha256')!=SCHEMA_SHA or settings.get('agent_commit')!=commit:
        raise ValueError('rescue_acceptance_version_changed')
    for name in ('provider_acceptance','agent_acceptance','implementation_review','deployment_approval','execution_approval'):
        item=settings.get(name,{})
        path=Path(item.get('path',''))
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item.get('sha256'):
            raise ValueError('rescue_acceptance_artifact_missing')
        document=json.loads(path.read_text(encoding='utf-8'))
        if (document.get('role')!=name or document.get('target_date')!=TARGET
                or document.get('agent_commit')!=commit or document.get('provider_commit')!=settings.get('provider_commit')
                or document.get('schema_sha256')!=SCHEMA_SHA
                or document.get('scope')!='same_day_rescue'
                or document.get('verdict')!=('approved' if name.endswith('_approval') else 'Go')):
            raise ValueError('rescue_acceptance_artifact_scope_changed')
    if not settings.get('provider_commit') or settings.get('target_date')!=TARGET:
        raise ValueError('rescue_provider_not_verified')


def build_flow(store, directory, draft, saved, commit, ai, drive, meal, clock, settings=None, *, recovery=False):
    """設定ファイルと受入原本がない限り救済clientを構築しない。秘密を保存しない。"""
    if not recovery and os.getenv('MINA_SAME_DAY_RESCUE_ENABLED','false').lower()!='true':
        raise ValueError('rescue_execution_disabled')
    if settings is None:
        config=os.getenv('MINA_SAME_DAY_RESCUE_CONFIG')
        if not config: raise ValueError('rescue_configuration_missing')
        path=Path(config)
        if path.is_symlink(): raise ValueError('rescue_configuration_link')
        settings=json.loads(path.read_text(encoding='utf-8'))
    acceptance(settings,commit)
    from .replacement_policy import ReplacementPolicy
    from .replacement_inventory import ExternalInventory,BufferInventoryAdapter
    from .replacement_ports import PreparationPorts
    from .replacement_client import ReplacementReadClient
    from .replacement_contract import Contract
    from .buffer_publisher import production_client
    from .rescue_flow import RescueFlow
    policy=ReplacementPolicy(store,settings['policy'],clock)
    api=production_client(store)
    inventory=ExternalInventory(store,BufferInventoryAdapter(api,settings['organization_id']),clock,settings['channels'])
    path=Path(directory)/'same-day-rescue'
    if recovery:
        from .publication_hold import exists
        with store.transaction() as db:
            if not exists(db,'rescue_jobs') or not db.execute('SELECT 1 FROM rescue_jobs WHERE target_date=?',(TARGET,)).fetchone():
                raise ValueError('rescue_recovery_job_missing')
        class LookupOnlyPorts:
            def __getattr__(self,name): raise ValueError('rescue_recovery_generation_forbidden')
        ports=LookupOnlyPorts()
    else:
        ports=PreparationPorts(ai,drive,draft,saved,path,clock)
    contract=RescueContract(clock)
    client=RescueClient(meal.base,meal.token,decoder=contract.decode,clock=clock,write_authorized=True)
    normal=Contract(clock)
    reader=ReplacementReadClient(meal.base,meal.token,decoder=normal.decode,clock=clock)
    return RescueFlow(store,client,reader,policy,inventory,ports,clock)
