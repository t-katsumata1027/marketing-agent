"""権限発行前点検の全履歴を検証する。新旧のpassを混ぜず、費用原本を保持する。"""
import json
import hashlib
from functools import lru_cache
from .replacement_contract import digest


SLOTS={'breakfast','lunch','dinner'}
PASS={'status':'publishable','reason_codes':[]}


@lru_cache(maxsize=1)
def _compiled_contract():
    from .scoped_rescue_contract import ScopedRescueContract
    from datetime import datetime,UTC
    return ScopedRescueContract(lambda:datetime.now(UTC))


def review_contract():
    from .scoped_rescue_contract import SCHEMA_PATH,SCHEMA_SHA
    if SCHEMA_PATH.is_symlink() or hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()!=SCHEMA_SHA:
        raise ValueError('scoped_rescue_schema_changed')
    return _compiled_contract()


def call_key(case,slot,kind,round_number=0):
    prefix=case+':pregrant:'
    return prefix+(f'refresh1:' if round_number else '')+slot+':'+kind


def safe_row(row):
    import hashlib
    return {k:({'sha256':hashlib.sha256(v).hexdigest(),'byte_length':len(v)} if isinstance(v,bytes) else v)
            for k,v in dict(row).items()}


def _events(db,scope,kind):
    return [dict(r) for r in db.execute('SELECT * FROM scoped_rescue_events WHERE contract=? AND target_date=? AND slot=? AND case_uuid=? AND kind=? ORDER BY id',(*scope,kind))]


def round_receipts(db,scope,round_number=0):
    kind='other_meal_review_refresh' if round_number else 'other_meal_review'
    events=_events(db,scope,kind);slots=SLOTS-{scope[2]}
    if len(events)!=2:raise ValueError('scoped_rescue_review_history_incomplete')
    receipts=[];calls=[];costs=[]
    for slot in sorted(slots):
        reference=f'1:{slot}' if round_number else slot
        selected=[e for e in events if e['reference']==reference]
        if len(selected)!=1:raise ValueError('scoped_rescue_review_history_incomplete')
        receipt=json.loads(selected[0]['document'])
        required={'formal','inputs','food_call_key','ogp_call_key','method'}|({'review_round','refresh_claim_digest'} if round_number else set())
        if set(receipt)!=required or set(receipt['inputs']['anchor'])!={'target_slot','target_result_version','original_sha256','stored_sha256','analysis_digest','ogp_input_digest'}:
            raise ValueError('scoped_rescue_review_origin_missing')
        review_contract().validate('OtherMealReview',receipt['formal'])
        if receipt['formal'].get('evidence_digest',digest(receipt['formal']))!=digest({k:v for k,v in receipt['formal'].items() if k!='evidence_digest'}):
            raise ValueError('scoped_rescue_review_origin_missing')
        if (receipt.get('method')!='original-image-and-saved-ogp-v1' or receipt['formal']['target_slot']!=slot
                or receipt['formal']['verdict']!='pass' or receipt['formal']['quality_status']!='pass'
                or receipt['inputs']['review_policy_version']!=receipt['formal']['review_policy_version']):
            raise ValueError('scoped_rescue_review_origin_missing')
        for kind in ('food','ogp'):
            key=call_key(scope[3],slot,kind,round_number)
            if receipt[kind+'_call_key']!=key:raise ValueError('scoped_rescue_review_origin_missing')
            row=db.execute('SELECT * FROM scoped_rescue_calls WHERE contract=? AND target_date=? AND slot=? AND case_uuid=? AND call_key=?',(*scope,key)).fetchone()
            if (not row or row['state']!='complete' or row['kind']!='review' or row['input_digest']!=digest(receipt['inputs'])
                    or row['encoding']!='json' or json.loads(row['value'])!=PASS or not row['finished_at']):
                raise ValueError('scoped_rescue_review_origin_missing')
            cost=db.execute('SELECT * FROM replacement_waived_calls WHERE target_date=? AND call_key=?',(scope[1],key)).fetchone()
            if not cost or cost['state']!='complete' or cost['kind']!='review':raise ValueError('scoped_rescue_review_cost_unresolved')
            calls.append(safe_row(row));costs.append(safe_row(cost))
        if (receipt['formal']['reviewed_at']!=min(r['finished_at'] for r in calls[-2:])
                or any(receipt['formal'].get(k)!=v for k,v in receipt['inputs']['anchor'].items())):
            raise ValueError('scoped_rescue_review_origin_missing')
        if round_number and receipt.get('review_round')!=1:raise ValueError('scoped_rescue_review_origin_missing')
        receipts.append(receipt)
    return {'receipts':receipts,'calls':calls,'costs':costs}


def parent_history(db,scope):
    value=round_receipts(db,scope)
    return value,digest(value)


def complete_history(db,scope):
    """全既知keyを照合し、最新の完全な一組だけを選ぶ。失敗時は旧passへ戻らない。"""
    actual={r['call_key'] for r in db.execute('SELECT call_key FROM scoped_rescue_calls WHERE contract=? AND target_date=? AND slot=? AND case_uuid=? AND call_key LIKE ?',(*scope,scope[3]+':pregrant:%'))}
    base_events=_events(db,scope,'other_meal_review')
    claims=_events(db,scope,'pregrant_refresh_claim')
    refreshed=_events(db,scope,'other_meal_review_refresh')
    finished=_events(db,scope,'pregrant_refresh_complete')
    if not actual and not base_events and not claims and not refreshed and not finished:
        return {'receipts':[],'calls':[],'costs':[],'claims':[],'selected':[],'round':0}
    base,parent=parent_history(db,scope);selected=base['receipts'];round_number=0
    all_calls=list(base['calls']);all_costs=list(base['costs'])
    if claims:
        if len(claims)!=1 or len(finished)!=1:raise ValueError('scoped_rescue_refresh_incomplete')
        claim=json.loads(claims[0]['document']);done=json.loads(finished[0]['document'])
        if type(claim.get('review_round')) is not int or claim['review_round']!=1 or claim['parent_history_digest']!=parent:
            raise ValueError('scoped_rescue_refresh_parent_changed')
        current=round_receipts(db,scope,1)
        if (done!={'claim_digest':digest(claim),'receipts_digest':digest(current['receipts'])}
                or any(r.get('refresh_claim_digest')!=digest(claim) for r in current['receipts'])):
            raise ValueError('scoped_rescue_refresh_incomplete')
        selected=current['receipts'];round_number=1
        all_calls+=current['calls'];all_costs+=current['costs']
    elif refreshed or finished:raise ValueError('scoped_rescue_refresh_claim_missing')
    expected={r['call_key'] for r in all_calls}
    costs={r['call_key'] for r in db.execute('SELECT call_key FROM replacement_waived_calls WHERE target_date=? AND call_key LIKE ?',(scope[1],scope[3]+':pregrant:%'))}
    if actual!=expected or costs!=expected:raise ValueError('scoped_rescue_review_unknown_key')
    return {'receipts':base['receipts']+(selected if round_number else []),'calls':all_calls,'costs':all_costs,
            'claims':[json.loads(r['document']) for r in claims],'selected':selected,'round':round_number}
