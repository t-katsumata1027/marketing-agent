"""権限発行前の他2食を実点検し、正式receiptと実入力原本を同じcaseへ保存する。"""
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from .external_guard import managed_call
from .replacement_contract import Contract,canonical,digest,stamp
from .replacement_client import timestamp
from .scoped_rescue_contract import ScopedRescueContract
from .scoped_rescue_storage import execution_seal,verify_execution_seal


def review_other_meals(flow,meal,ai,capture,draft,saved,host,anchors,review_policy_version,not_after,owner,epoch):
    """既存画像を点検するだけ。正常M1の再登録・訂正・生成は行わない。"""
    from .domain import JST
    from .m1_contract import validate_state
    end=timestamp(not_after)
    def guard():
        flow.enabled();flow.acceptance_check()
        flow.schedule_gate(not_after)
        today=flow.clock().astimezone(JST).date()
        if flow.target not in (today.isoformat(),(today+timedelta(days=1)).isoformat()) or flow.clock()+timedelta(seconds=60)>end:
            raise ValueError('scoped_rescue_deadline')
        with flow.store.transaction() as db:
            h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(flow.target,)).fetchone()
            if not h or (h['owner'],h['epoch'],h['state'])!=(owner,epoch,'held'):raise ValueError('scoped_rescue_hold_changed')
            seals=flow.journal.rows('events',"AND kind='pregrant_seal'",db=db)
            if not seals:flow.journal.event('pregrant_seal',flow.journal.scope[3],execution_seal(db,flow.target),db)
            elif len(seals)!=1:raise ValueError('scoped_rescue_origin_changed')
            else:verify_execution_seal(db,flow.target,json.loads(seals[0]['document']),flow.journal.scope[3])
    def call(slot,kind,inputs,operation):
        guard();key=flow.journal.scope[3]+':pregrant:'+slot+':'+kind;sha=digest(inputs)
        with flow.store.transaction() as db:
            rows=flow.journal.rows('calls','AND call_key=?',(key,),db)
            if rows:
                row=rows[0]
                if row['input_digest']!=sha or row['state']!='complete' or not 0<=(flow.clock()-timestamp(row['finished_at'])).total_seconds()<=120:
                    raise ValueError('scoped_rescue_external_lookup_only')
                return json.loads(row['value']),row['finished_at']
            db.execute("INSERT INTO scoped_rescue_calls VALUES(?,?,?,?,?,?,'started',?,NULL,NULL,NULL)",(*flow.journal.scope,key,'review',sha))
        flow.policy.reserve(flow.target,key,'review');guard()
        try:value=managed_call(operation)
        except BaseException:
            flow.policy.called(key,False);raise
        flow.policy.called(key,True);finished=stamp(flow.clock())
        with flow.store.transaction() as db:
            db.execute("UPDATE scoped_rescue_calls SET state='complete',encoding='json',value=?,finished_at=? WHERE call_key=?",(canonical(value),finished,key))
        guard();return value,finished
    guard();pre=Contract(flow.clock).preconditions(flow.normal.preconditions(flow.target),flow.target);flow.policy_gate(pre)
    state=meal.day_state(flow.target)
    if validate_state(state,flow.target,flow.clock())!=pre['state_version']:raise ValueError('scoped_rescue_source_changed')
    slots={'breakfast','lunch','dinner'}-{flow.slot}
    if len(anchors)!=2 or {a['target_slot'] for a in anchors}!=slots:raise ValueError('scoped_rescue_other_meals_invalid')
    reviews=[]
    from .ogp import verify_image_reference
    for slot in sorted(slots):
        guard();m=next(m for m in pre['meals'] if m['expected']['target_slot']==slot)
        r=next(r for r in state['meals'] if r['meal']['meal_type']==slot);anchor=next(a for a in anchors if a['target_slot']==slot)
        expected={k:v for k,v in anchor.items() if k!='independent_review_digest'}
        if (set(expected)!={'target_slot','target_result_version','original_sha256','stored_sha256','analysis_digest','ogp_input_digest'}
                or expected['target_result_version']!=r['result_version'] or expected['original_sha256']!=r['image_sha256']
                or expected['stored_sha256']!=r['stored_image_sha256'] or expected['analysis_digest']!=m['analysis_digest']
                or m['expected']['target_result_version']!=r['result_version'] or m['analysis']['quality']['status']!='pass' or r['quality']['status']!='pass'):
            raise ValueError('scoped_rescue_other_meals_changed')
        path=Path(saved['images'][slot]['path'])
        if path.is_symlink():raise ValueError('scoped_rescue_origin_file_changed')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=r['image_sha256']:raise ValueError('scoped_rescue_origin_file_changed')
        guard();snapshot=capture(r['share']['url'],host);guard();verify_image_reference(snapshot,r['ogp'],r['share']['url'],host)
        inputs={'anchor':expected,'m1_result':r,'descriptor':m['analysis'],'original_sha256':hashlib.sha256(raw).hexdigest(),
            'ogp_snapshot_digest':snapshot['digest'],'review_policy_version':review_policy_version}
        food,food_at=call(slot,'food',inputs,lambda:ai.review_meal(raw,draft['meals'][slot],{'result':r},snapshot['meta']))
        ogp,ogp_at=call(slot,'ogp',inputs,lambda:ai.review_ogp([snapshot['review_image'],raw],r,snapshot['meta']))
        if food!={'status':'publishable','reason_codes':[]} or ogp!={'status':'publishable','reason_codes':[]}:
            raise ValueError('scoped_rescue_other_meals_review_required')
        review={**expected,'reviewer_role':'independent_reviewer','verdict':'pass','quality_status':'pass',
                'review_policy_version':review_policy_version,'reviewed_at':min(food_at,ogp_at)}
        ScopedRescueContract(flow.clock).validate('OtherMealReview',review)
        receipt={'formal':review,'inputs':inputs,'food_call_key':flow.journal.scope[3]+':pregrant:'+slot+':food',
            'ogp_call_key':flow.journal.scope[3]+':pregrant:'+slot+':ogp','method':'original-image-and-saved-ogp-v1'}
        previous=flow.journal.rows('events',"AND kind='other_meal_review' AND reference=?",(slot,))
        if previous and any(json.loads(e['document'])!=receipt for e in previous):raise ValueError('scoped_rescue_review_immutable')
        if not previous:flow.journal.event('other_meal_review',slot,receipt)
        reviews.append(review)
    guard()
    if validate_state(meal.day_state(flow.target),flow.target,flow.clock())!=pre['state_version']:raise ValueError('scoped_rescue_source_changed')
    return reviews


def verify_receipts(flow,basis):
    for review in basis['other_meals_reviews']:
        receipts=flow.journal.rows('events',"AND kind='other_meal_review' AND reference=?",(review['target_slot'],))
        if len(receipts)!=1:raise ValueError('scoped_rescue_review_origin_missing')
        receipt=json.loads(receipts[0]['document'])
        if receipt['formal']!=review or receipt['inputs']['review_policy_version']!=basis['review_policy_version']:
            raise ValueError('scoped_rescue_other_meals_changed')
        for kind in ('food','ogp'):
            rows=flow.journal.rows('calls','AND call_key=?',(receipt[kind+'_call_key'],))
            if (len(rows)!=1 or rows[0]['state']!='complete' or rows[0]['input_digest']!=digest(receipt['inputs'])
                    or json.loads(rows[0]['value'])!={'status':'publishable','reason_codes':[]}):
                raise ValueError('scoped_rescue_review_origin_missing')
