"""点検後のチャット往復を排し、資格分離した管理側と原本だけで協調する。"""
from datetime import timedelta
from .replacement_contract import Contract,digest,stamp
from .replacement_client import timestamp
from .scoped_review_history import complete_history
from .scoped_review_refresh import approval,active_claim,source_digest
from .scoped_coordination import ManagementStopped


def scope_for(flow,review_policy):
    approved=approval(flow)
    return {'case_uuid':flow.journal.scope[3],'planned_prepare_uuid':approved['planned_prepare_uuid'],
        'target_date':flow.target,'target_slot':flow.slot,'round':1,'parent_history_digest':approved['parent_history_digest'],
        'human_approval_sha256':flow.execution_approval_sha256,**flow.integration,'review_policy_version':review_policy,
        'policy_digest':flow.policy.policy_digest,'not_after':approved['not_after']}


def native(flow,value,pre):
    fixed={'artifact_spec':'mina-native-anchors-artifact-v1','complete':True,'metadata_only':True,
        'independent_review_provided':False,'image_bytes_verified':False,'source_body_commit':flow.integration['body_commit'],
        'schema_sha256':flow.integration['schema_sha256'],'date':flow.target,'target_slot':flow.slot}
    if (set(value)!=set(fixed)|{'artifact_sha256','read_started_at','checked_at','valid_until','other_meals_native','target_native','state_version'}
            or any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in fixed.items())):
        raise ValueError('scoped_coordination_snapshot_invalid')
    if value['artifact_sha256']!=digest({k:v for k,v in value.items() if k!='artifact_sha256'}):
        raise ValueError('scoped_coordination_reference_changed')
    if not timestamp(value['read_started_at'])<=timestamp(value['checked_at'])<=flow.clock()<timestamp(value['valid_until']) or not 0<(timestamp(value['valid_until'])-timestamp(value['read_started_at'])).total_seconds()<=120:
        raise ValueError('scoped_coordination_snapshot_stale')
    others=value['other_meals_native'];own=value['target_native']
    if len(others)!=2 or {a['target_slot'] for a in others}!={'breakfast','lunch','dinner'}-{flow.slot} or own['target_slot']!=flow.slot:
        raise ValueError('scoped_coordination_snapshot_invalid')
    if value['state_version']!=pre['state_version']:raise ValueError('scoped_coordination_source_changed')
    for anchor in [*others,own]:
        if set(anchor)!={'target_slot','target_result_version','original_sha256','stored_sha256','analysis_digest','ogp_input_digest','calories','api_quality','account_generation','day_generation','meal_generation'}:
            raise ValueError('scoped_coordination_snapshot_invalid')
        m=next(m for m in pre['meals'] if m['expected']['target_slot']==anchor['target_slot'])
        quality=m['analysis']['quality']
        expected_quality={k:quality[k] for k in ('status','atwater_calories','calorie_difference','calorie_difference_ratio')}
        expected_quality['warning_codes']=[w['code'] for w in quality['warnings']]
        if (anchor['target_result_version']!=m['expected']['target_result_version'] or anchor['original_sha256']!=m['image_sha256']
                or anchor['stored_sha256']!=m['stored_image_sha256'] or anchor['analysis_digest']!=m['analysis_digest']
                or anchor['calories']!=m['analysis']['summary']['totalCalories'] or anchor['api_quality']!=expected_quality
                or any(anchor[k]!=m['expected'][k] for k in ('account_generation','day_generation','meal_generation'))):
            raise ValueError('scoped_coordination_source_changed')
    stable={**fixed,'state_version':value['state_version'],'target_native':own,'other_meals_native':sorted(others,key=lambda a:a['target_slot'])}
    keys=('target_slot','target_result_version','original_sha256','stored_sha256','analysis_digest','ogp_input_digest')
    return [{k:a[k] for k in keys} for a in sorted(others,key=lambda a:a['target_slot'])],digest(stable)


def coordinate(flow,port,meal,ai,capture,draft,saved,host,review_policy,owner,epoch,stage_factory):
    """case発行は管理側だけ。結果不明でもAgentから発行要求を再送しない。"""
    # stage済みなら管理側の発行結果が不明でも同caseのGET以外へ戻らない。
    with flow.store.transaction() as db:
        staged=flow.journal.rows('jobs',db=db)
    if staged:
        flow.acceptance_check()
        return flow.bind_case()
    expected=scope_for(flow,review_policy)
    if port.scope!=expected:raise ValueError('scoped_coordination_scope_changed')
    if port.helper_shas!=flow.management_helpers:raise ValueError('scoped_coordination_helpers_unapproved')
    def guard():flow.enabled();flow.acceptance_check();flow.schedule_gate(expected['not_after'])
    guard()
    initial=port.snapshot('initial-snapshot-ready')
    pre=Contract(flow.clock).preconditions(flow.normal.preconditions(flow.target),flow.target);flow.policy_gate(pre)
    anchors,source=native(flow,initial['native'],pre)
    with flow.store.transaction() as db:history=complete_history(db,flow.journal.scope)
    if history['claims']:
        # 完了済みの追加点検は実時刻のまま読取りで再開する。未完了は上の全履歴検査で停止する。
        active_claim(flow,digest(history['claims'][0]))
        if source_digest(pre,anchors,review_policy,owner,epoch)!=approval(flow)['source_digest']:
            raise ValueError('scoped_rescue_refresh_source_changed')
        reviews=[r['formal'] for r in history['selected']]
        if any(not 0<=(flow.clock()-timestamp(r['reviewed_at'])).total_seconds()<=120 for r in reviews):
            raise ValueError('scoped_rescue_other_meals_stale')
    else:
        reviews=flow.refresh_other_meals(meal,ai,capture,draft,saved,host,anchors,review_policy,expected['not_after'],owner,epoch,
            management_reference=initial['unissued_reference'])
    guard()
    with flow.store.transaction() as db:history=complete_history(db,flow.journal.scope)
    payload={'review_round':1,'parent_history_digest':expected['parent_history_digest'],
        'claim_digest':digest(history['claims'][0]),'formal_reviews':reviews,'history':history}
    until=min(timestamp(expected['not_after']),*(timestamp(r['reviewed_at'])+timedelta(seconds=120) for r in reviews))
    port.publish('reviews-ready',payload,stamp(until))
    fresh=port.snapshot('snapshot-ready');guard()
    pre=Contract(flow.clock).preconditions(flow.normal.preconditions(flow.target),flow.target);flow.policy_gate(pre)
    current,observed=native(flow,fresh['native'],pre)
    if source!=observed:raise ValueError('scoped_coordination_source_changed')
    from .scoped_management_proofs import unissued
    unissued(fresh['unissued_reference'],flow.journal.scope,flow.integration,flow.clock,expected['planned_prepare_uuid'])
    packet=stage_factory(reviews,current,fresh)
    guard();port.publish_bundle(packet)
    try:
        response,_=port.wait('grant-result')
        flow.contract.decode('case',response)
    except ManagementStopped as stopped:
        if not stopped.issue_started:raise
        # started後の不明は元caseのGETでのみ復旧する。新issueへ戻らない。
    except ValueError as error:
        if str(error)!='scoped_coordination_wait_exhausted':raise
    guard()
    return flow.bind_case()
