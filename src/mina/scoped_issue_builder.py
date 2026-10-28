"""実台帳・最新正本から既存v3の発行資料を作る。権限発行そのものは行わない。"""
from .replacement_contract import Contract,digest
from .scoped_rescue_origin import capture
from .rescue_contract import POLICY,BUDGET,APPROVAL


def stage(flow,reviews,anchors,snapshot,owner,epoch):
    approved=flow.pregrant_refresh_approval
    pre=Contract(flow.clock).preconditions(flow.normal.preconditions(flow.target),flow.target);flow.policy_gate(pre)
    origin=capture(flow.store,pre,owner,epoch,flow.clock(),flow.target,flow.slot,flow.journal.scope[3])
    receipt=flow.inventory.check(flow.target)
    publication={'checked_at':receipt['checked_at'],'all_pages_checked':True,'all_providers_checked':True,'hold_epoch':epoch,
        'pause_receipt_digest':digest({'target_date':flow.target,'owner':owner,'epoch':epoch}),
        'dispatching_count':0,'unknown_count':0,'external_accepted_count':0,'published_count':0,'receipt':receipt}
    keys=('original_context_present','original_first_replan_started_at','original_preparation_deadline_at','original_hard_deadline_at')
    ledger={'native_origin':origin,'all_sources_enumerated':True,'all_native_meal_requests_terminal':True,
        'unfinished_native_meal_request_count':0,'same_menu_repair_history_preserved':True,
        'consumed_ordinals':origin['baseline_consumed_ordinals'],'other_meals_reviews':reviews,
        'publication_evidence_sha256':digest(publication),'native_anchor_artifact':snapshot['native_reference'],
        'management_unissued_artifact':snapshot['unissued_reference'],**{k:origin[k] for k in keys}}
    costs={'history_complete':True,'unresolved_cost_count':0,'waived_approval_digest':APPROVAL,'ledgers':origin['document']['ledgers']}
    own=next(m for m in pre['meals'] if m['expected']['target_slot']==flow.slot)
    basis={'target_date':flow.target,'target_slot':flow.slot,'initial_expected':own['expected'],
        'original_image_sha256':own['image_sha256'],'original_stored_image_sha256':own['stored_image_sha256'],
        'target_analysis_digest':own['analysis_digest'],**{k:origin[k] for k in (*keys,'baseline_consumed_ordinals')},
        'source_ledger_digest':digest(ledger),'cost_history_digest':digest(costs),'source_requests_terminal':True,
        'hold_epoch':epoch,'pause_receipt_digest':publication['pause_receipt_digest'],'policy_version':POLICY,
        'budget_policy_digest':BUDGET,'cost_approval_digest':APPROVAL,'not_after':approved['not_after'],
        'exception_kind':'operator_scoped_meal_replacement','other_meals_reviews':reviews,
        'other_meals_expected':[{**a,'independent_review_digest':digest(next(r for r in reviews if r['target_slot']==a['target_slot']))} for a in anchors],
        'review_policy_version':reviews[0]['review_policy_version']}
    approval={'action':'issue','approved':True,'case_uuid':flow.journal.scope[3],'planned_prepare_uuid':approved['planned_prepare_uuid'],
        'basis_sha256':digest(basis),'not_after':basis['not_after'],'expected_review_policy_version':basis['review_policy_version'],
        'expected_agent_commit':flow.integration['agent_commit'],'expected_body_commit':flow.integration['body_commit'],
        'expected_schema_sha256':flow.integration['schema_sha256'],'human_approval_artifact_sha256':flow.execution_approval_sha256}
    basis['operator_approval_digest']=digest(approval)
    issue={'contract_version':'mina-rescue-v3','case_uuid':flow.journal.scope[3],'planned_prepare_uuid':approved['planned_prepare_uuid'],
        'attempt_ordinal':len(origin['baseline_consumed_ordinals'])+1,'basis':basis}
    proof={'source_ledger':ledger,'cost_history':costs,'publication':publication,'integration':flow.integration}
    flow.stage_issue(issue,approval,proof,owner=owner,epoch=epoch,
        agent_commit=flow.integration['agent_commit'],body_commit=flow.integration['body_commit'])
    return flow.operator_bundle()
