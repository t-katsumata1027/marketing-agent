"""本人が明示した追加4点検一組だけを同caseへ追記する。旧履歴は変更しない。"""
import json
from .replacement_contract import Contract,digest,stamp
from .scoped_review_history import parent_history,complete_history,call_key
from .scoped_management_proofs import unissued


def source_digest(pre,anchors,review_policy,owner,epoch):
    keys=('target_slot','target_result_version','original_sha256','stored_sha256','analysis_digest','ogp_input_digest')
    return digest({'state_version':pre['state_version'],'meals':sorted([
        {k:m[k] for k in ('expected','image_sha256','stored_image_sha256','analysis_digest')} for m in pre['meals']],
        key=lambda m:m['expected']['target_slot']),
        'anchors':sorted([{k:a[k] for k in keys} for a in anchors],key=lambda a:a['target_slot']),'review_policy_version':review_policy,'owner':owner,'epoch':epoch,
        'policy':{k:pre['policy'][k] for k in ('policy_version','budget_policy_digest','analysis_provider','cost_control')}})


def approval(flow):
    value=getattr(flow,'pregrant_refresh_approval',None)
    fixed={'version':1,'approved':True,'target_date':flow.target,'target_slot':flow.slot,'case_uuid':flow.journal.scope[3],
        'max_rounds':1,'max_calls':4}
    keys=set(fixed)|{'planned_prepare_uuid','parent_history_digest','source_digest','not_after'}
    if (not isinstance(value,dict) or set(value)!=keys or not getattr(flow,'execution_approval_sha256',None)
            or any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in fixed.items())):
        raise ValueError('scoped_rescue_refresh_not_approved')
    from .clients import MealClient
    MealClient.request_key(value['planned_prepare_uuid'])
    if value['planned_prepare_uuid']!=getattr(flow,'planned_prepare_uuid',None):
        raise ValueError('scoped_rescue_refresh_plan_changed')
    from .replacement_client import timestamp
    if (not getattr(flow,'preparation_window',None) or value['not_after']!=flow.preparation_window['not_after']
            or flow.clock()>=timestamp(value['not_after'])):raise ValueError('scoped_rescue_refresh_deadline')
    return value


def active_claim(flow,expected):
    approved=approval(flow)
    with flow.store.transaction() as db:
        rows=flow.journal.rows('events',"AND kind='pregrant_refresh_claim'",db=db)
        if len(rows)!=1:raise ValueError('scoped_rescue_refresh_claim_missing')
        value=json.loads(rows[0]['document']);_,parent=parent_history(db,flow.journal.scope)
        if (digest(value)!=expected or value['approval_digest']!=digest(approved)
                or value['parent_history_digest']!=parent or value['execution_approval_sha256']!=flow.execution_approval_sha256
                or value['integration']!=flow.integration or value['source_digest']!=approved['source_digest']):raise ValueError('scoped_rescue_refresh_claim_changed')
        h=db.execute('SELECT owner,epoch,state FROM publication_holds WHERE target_date=?',(flow.target,)).fetchone()
        if not h or (h['owner'],h['epoch'],h['state'])!=(value['owner'],value['epoch'],'held'):
            raise ValueError('scoped_rescue_hold_changed')
        allowed={call_key(flow.journal.scope[3],s,k,r) for s in {'breakfast','lunch','dinner'}-{flow.slot}
                 for k in ('food','ogp') for r in (0,1)}
        keys={r['call_key'] for r in flow.journal.rows('calls',db=db)}
        if not keys<=allowed:raise ValueError('scoped_rescue_review_unknown_key')
        costs={r['call_key'] for r in db.execute('SELECT call_key FROM replacement_waived_calls WHERE target_date=? AND call_key LIKE ?',(flow.target,flow.journal.scope[3]+':%'))}
        if keys!=costs:raise ValueError('scoped_rescue_review_cost_unresolved')
    return value


def refresh_other_meals(flow,meal,ai,capture,draft,saved,host,anchors,review_policy_version,not_after,owner,epoch,*,management_reference):
    """未発行・全旧pass・固定原本を確認し、有料開始前に永続claimを一度だけ取得する。"""
    approved=approval(flow);flow.enabled();flow.acceptance_check();flow.schedule_gate(not_after)
    if not_after!=approved['not_after']:raise ValueError('scoped_rescue_refresh_deadline')
    unissued(management_reference,flow.journal.scope,flow.integration,flow.clock,approved['planned_prepare_uuid'])
    pre=Contract(flow.clock).preconditions(flow.normal.preconditions(flow.target),flow.target);flow.policy_gate(pre)
    from .m1_contract import validate_state
    state=meal.day_state(flow.target)
    if validate_state(state,flow.target,flow.clock())!=pre['state_version']:raise ValueError('scoped_rescue_source_changed')
    if source_digest(pre,anchors,review_policy_version,owner,epoch)!=approved['source_digest']:
        raise ValueError('scoped_rescue_refresh_source_changed')
    from .scoped_rescue_reviews import material,review_other_meals
    with flow.store.transaction() as db:
        old=complete_history(db,flow.journal.scope)
        if old['round'] or old['claims']:raise ValueError('scoped_rescue_refresh_already_claimed')
        _,parent=parent_history(db,flow.journal.scope)
    if parent!=approved['parent_history_digest']:raise ValueError('scoped_rescue_refresh_parent_changed')
    from .scoped_rescue_origin import capture as capture_origin
    origin=capture_origin(flow.store,pre,owner,epoch,flow.clock(),flow.target,flow.slot,flow.journal.scope[3])
    materials={}
    for receipt in old['selected']:
        slot=receipt['formal']['target_slot'];m=next(m for m in pre['meals'] if m['expected']['target_slot']==slot)
        r=next(r for r in state['meals'] if r['meal']['meal_type']==slot);anchor=next(a for a in anchors if a['target_slot']==slot)
        expected={k:v for k,v in anchor.items() if k!='independent_review_digest'}
        value=material(meal,capture,draft,saved,host,m,r,expected,review_policy_version)
        if digest(value[2])!=digest(receipt['inputs']):raise ValueError('scoped_rescue_refresh_source_changed')
        materials[slot]=value
    if len(materials)!=2:raise ValueError('scoped_rescue_review_history_incomplete')
    with flow.store.transaction() as db:
        if (db.execute('SELECT 1 FROM scoped_rescue_jobs WHERE target_date=? AND slot=?',(flow.target,flow.slot)).fetchone()
                or db.execute('SELECT 1 FROM scoped_rescue_requests WHERE target_date=? AND slot=?',(flow.target,flow.slot)).fetchone()
                or flow.journal.rows('events',"AND kind='pregrant_refresh_claim'",db=db)):
            raise ValueError('scoped_rescue_refresh_already_claimed')
        h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(flow.target,)).fetchone()
        if not h or (h['owner'],h['epoch'],h['state'])!=(owner,epoch,'held'):raise ValueError('scoped_rescue_hold_changed')
        current=complete_history(db,flow.journal.scope);_,again=parent_history(db,flow.journal.scope)
        if current['claims'] or again!=parent:raise ValueError('scoped_rescue_refresh_parent_changed')
        if {r['call_key'] for r in flow.journal.rows('calls',db=db)}!={r['call_key'] for r in current['calls']}:
            raise ValueError('scoped_rescue_review_unknown_key')
        for table in ('replacement_waived_calls','replacement_cost_reservations'):
            if db.execute('SELECT 1 FROM '+table+' WHERE target_date=? AND state!=?',(flow.target,'complete')).fetchone():
                raise ValueError('scoped_rescue_review_cost_unresolved')
        flow.enabled();flow.acceptance_check();flow.schedule_gate(not_after)
        unissued(management_reference,flow.journal.scope,flow.integration,flow.clock,approved['planned_prepare_uuid'])
        claim={'review_round':1,'parent_history_digest':parent,'approval_digest':digest(approved),
            'execution_approval_sha256':flow.execution_approval_sha256,
            'source_digest':approved['source_digest'],'owner':owner,'epoch':epoch,'integration':flow.integration,
            'origin_digest':origin['source_ledger_digest'],'cost_history_digest':origin['cost_history_digest'],
            'management_reference':management_reference,'claimed_at':stamp(flow.clock())}
        flow.journal.event('pregrant_refresh_claim','1',claim,db)
    claim_digest=digest(claim)
    result=review_other_meals(flow,meal,ai,capture,draft,saved,host,anchors,review_policy_version,not_after,owner,epoch,
        _round=1,_claim=claim_digest,_materials=materials)
    from .scoped_review_history import round_receipts
    with flow.store.transaction() as db:
        latest=round_receipts(db,flow.journal.scope,1)
        flow.journal.event('pregrant_refresh_complete','1',{'claim_digest':claim_digest,'receipts_digest':digest(latest['receipts'])},db)
        complete_history(db,flow.journal.scope)
    return result
