"""救済発行へ渡す原本証跡。形式上のdigestだけで確認済みにしない。"""
import hashlib
import json
from pathlib import Path
from .publication_hold import exists
from .request_ledger import RequestLedger
from .replacement_contract import digest, stamp
from .replacement_client import timestamp
from .domain import approval_time, date
from .rescue_contract import TARGET


def capture(store, precondition, owner, epoch, now):
    requests = RequestLedger(store)
    with store.transaction() as db:
        h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone()
        if not h or (h['owner'],h['epoch'],h['state'])!=(owner,epoch,'held'):
            raise ValueError('rescue_hold_changed')
        if not exists(db,'preparation_steps'):
            raise ValueError('rescue_origin_missing')
        steps=[dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=? ORDER BY step',(TARGET,))]
        if not steps or any(r['state'] not in ('complete','held') for r in steps):
            raise ValueError('rescue_origin_unresolved')
        rows=[dict(r) for r in db.execute('SELECT * FROM mina_requests WHERE target_date=? ORDER BY request_key',(TARGET,))]
        if not rows or any(r['phase']!='complete' or json.loads(r['observation'] or '{}').get('persistence')!='saved' for r in rows):
            raise ValueError('rescue_origin_unresolved')
        audit=[dict(r) for r in db.execute("SELECT * FROM operation_events WHERE target_date=? AND stage IN ('image','meal_replan') ORDER BY id",(TARGET,))]
        ledgers={}
        for table in ('replacement_attempts','replacement_origin_receipts','replacement_external_calls',
                      'replacement_waived_calls','replacement_cost_reservations','meal_replacement_requests'):
            if not exists(db,table): continue
            values=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE target_date=? ORDER BY rowid',(TARGET,))]
            for row in values:
                if 'state' in row and row['state']!='complete': raise ValueError('rescue_cost_or_call_unresolved')
                if table=='meal_replacement_requests':
                    observation=json.loads(row['observation'] or '{}')
                    field='persistence' if row['operation']=='meal_replace' else 'outcome'
                    if observation.get('conflict') or observation.get(field) not in ('saved','not_saved','applied','rejected'):
                        raise ValueError('rescue_origin_unresolved')
                for key,value in list(row.items()):
                    if isinstance(value,bytes):row[key]={'sha256':hashlib.sha256(value).hexdigest(),'length':len(value)}
            ledgers[table]=values
    for row in rows:
        restored=requests.restore(row['request_key'])
        row['image']=None if restored['image'] is None else {'sha256':restored['image_sha256'],'length':len(restored['image'])}
    matched=[]
    for meal in precondition['meals']:
        slot=meal['expected']['target_slot']
        found=[]
        for step in steps:
            if not step['step'].startswith(('meal_register:'+slot,'meal_correction:'+slot)) or step['state']!='complete': continue
            result=json.loads(step['result'] or '{}').get('result')
            if result and (result.get('result_version'),result.get('image_sha256'),result.get('stored_image_sha256'))==(
                    meal['expected']['target_result_version'],meal['image_sha256'],meal['stored_image_sha256']):
                found.append(step['step'])
        if not found: raise ValueError('rescue_saved_origin_missing')
        matched.append({'slot':slot,'steps':found})
    holds=[json.loads(r['result'] or '{}') for r in steps if 'breakfast' in r['step'] and
           (r['step'].startswith('meal_review:') or r['step']=='explicit_saved_breakfast_recheck')]
    if not any((r.get('status')=='hold' or r.get('classification')=='quality_hold') and
               r.get('reason_codes') and set(r['reason_codes'])<={'image_analysis_mismatch','nutrition_semantic_mismatch'} for r in holds):
        raise ValueError('rescue_quality_hold_missing')
    ordinals=set();starts=[]
    for row in steps:
        if not row['step'].startswith('meal_replan:breakfast:'): continue
        number=row['step'].split(':')[-1]
        if number not in ('1','2') or row['state']!='complete' or not 0<row['started']<=now.timestamp():
            raise ValueError('rescue_attempt_origin_invalid')
        result=json.loads(row['result'] or '{}')
        if not result or result.get('reservation_only'): raise ValueError('rescue_attempt_origin_invalid')
        ordinals.add(int(number));starts.append(row['started'])
    for row in ledgers.get('replacement_attempts',[]):
        if row['slot']!='breakfast': continue
        if row['ordinal'] not in (1,2) or not 0<row['first_started']<=now.timestamp():
            raise ValueError('rescue_attempt_origin_invalid')
        ordinals.add(row['ordinal']);starts.append(row['first_started'])
    by_step={r['step']:r for r in steps}
    known_rounds=set()
    for row in steps:
        if row['step'].startswith(('automatic_image_repair:breakfast','meal_replan:breakfast:')):
            value=json.loads(row['result'] or '{}')
            if value.get('round'):
                from .clients import MealClient
                MealClient.request_key(value['round']);known_rounds.add(value['round'])
    for row in steps:
        if row['step'].startswith('image:breakfast:') and row['step'].split(':')[-1] not in known_rounds:
            raise ValueError('rescue_orphan_generation')
        if row['step'].startswith('image:'):
            image=json.loads(row['result'] or '{}')
            if 'path' not in image or 'sha256' not in image or 'size' not in image:
                raise ValueError('rescue_image_origin_missing')
            path=Path(image['path'])
            if path.is_symlink(): raise ValueError('rescue_image_origin_missing')
            raw=path.read_bytes()
            if len(raw)!=image['size'] or hashlib.sha256(raw).hexdigest()!=image['sha256']:
                raise ValueError('rescue_image_origin_changed')
    for row in ledgers.get('replacement_attempts',[]):
        if row['slot']!='breakfast': continue
        step=by_step.get('meal_replan:breakfast:'+str(row['ordinal']))
        receipt=json.loads(row['receipt'])
        if (not step or json.loads(step['result'] or '{}').get('receipt')!=receipt
                or step['digest']!=digest(receipt)):
            raise ValueError('rescue_attempt_origin_invalid')
        from .replacement_origin import verify_receipt
        with store.transaction() as db:
            origin=db.execute('SELECT owner,epoch FROM replacement_origin_receipts WHERE target_date=? AND slot=? AND ordinal=?',
                (TARGET,'breakfast',row['ordinal'])).fetchone()
            if not origin: raise ValueError('rescue_attempt_origin_invalid')
            verify_receipt(db,TARGET,'breakfast',receipt,origin['owner'],origin['epoch'])
    costs=ledgers.get('replacement_waived_calls',[])+ledgers.get('replacement_cost_reservations',[])
    if not ordinals and (ledgers.get('meal_replacement_requests') or ledgers.get('replacement_external_calls')
            or any(not row['kind'].startswith('collection_') for row in costs)):
        raise ValueError('rescue_orphan_generation')
    for event in audit:
        details=json.loads(event['details'])
        if event['stage']=='meal_replan' and event['slot']=='breakfast':
            if event['event']=='observed' and details.get('attempt') not in ordinals: raise ValueError('rescue_orphan_audit')
            if event['event']!='observed' and details.get('step_sha256') not in {r['digest'] for r in steps if r['step'].startswith('meal_replan:breakfast:')}:
                raise ValueError('rescue_orphan_audit')
        if event['stage']=='image' and details.get('step_sha256') not in {r['digest'] for r in steps if r['step'].startswith('image:')}:
            raise ValueError('rescue_orphan_audit')
    ordered=sorted(ordinals)
    if ordered not in ([],[1]) or any(n not in ordered for n in precondition['policy']['used_replan_ordinals']):
        raise ValueError('replan_limit_exceeded')
    # 再計画が実証された場合だけ真の初回を採用。画像の同メニュー修復は別履歴のまま保持。
    first=min(starts) if starts else None
    old=approval_time(date.fromisoformat(TARGET))
    from datetime import UTC,datetime,timedelta
    first_time=datetime.fromtimestamp(first,UTC) if first else None
    hard=min(old,first_time+timedelta(minutes=15)) if first_time else old
    document={'target_date':TARGET,'owner':owner,'epoch':epoch,'steps':steps,'requests':rows,'matched':matched,'ledgers':ledgers,'generation_audit':audit}
    return {'document':document,'source_ledger_digest':digest(document),'cost_history_digest':digest(ledgers),
            'original_first_replan_started_at':stamp(first_time) if first_time else None,
            'original_preparation_deadline_at':stamp(old),'original_hard_deadline_at':stamp(hard),
            'baseline_consumed_ordinals':ordered}
