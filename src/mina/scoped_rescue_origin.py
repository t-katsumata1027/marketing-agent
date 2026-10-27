"""v3の原本証跡。旧時間とnative品質保留を改変せず、管理側のcontext実照合も必須とする。"""
import hashlib
import json
from pathlib import Path
from .publication_hold import exists
from .request_ledger import RequestLedger
from .replacement_contract import digest, stamp
from .replacement_client import timestamp
from .domain import approval_time, date


def capture(store, precondition, owner, epoch, now, target, slot, case_uuid=None):
    requests = RequestLedger(store)
    with store.transaction() as db:
        h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(target,)).fetchone()
        if not h or (h['owner'],h['epoch'],h['state'])!=(owner,epoch,'held'):
            raise ValueError('rescue_hold_changed')
        if not exists(db,'preparation_steps'):
            raise ValueError('rescue_origin_missing')
        steps=[dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=? ORDER BY step',(target,))]
        if not steps or any(r['state'] not in ('complete','held') for r in steps):
            raise ValueError('rescue_origin_unresolved')
        rows=[dict(r) for r in db.execute('SELECT * FROM mina_requests WHERE target_date=? ORDER BY request_key',(target,))]
        if not rows or any(r['phase']!='complete' or json.loads(r['observation'] or '{}').get('persistence')!='saved' for r in rows):
            raise ValueError('rescue_origin_unresolved')
        audit=[dict(r) for r in db.execute("SELECT * FROM operation_events WHERE target_date=? AND stage IN ('image','meal_replan') ORDER BY id",(target,))]
        ledgers={}
        for table in ('replacement_attempts','replacement_origin_receipts','replacement_external_calls',
                      'replacement_waived_calls','replacement_cost_reservations','meal_replacement_requests'):
            if not exists(db,table): continue
            values=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE target_date=? ORDER BY rowid',(target,))]
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
        meal_slot=meal['expected']['target_slot']
        found=[]
        for step in steps:
            if not step['step'].split(':')[0] in ('meal_register','meal_correction') or step['step'].split(':')[1]!=meal_slot or step['state']!='complete': continue
            result=json.loads(step['result'] or '{}').get('result')
            if result and (result.get('result_version'),result.get('image_sha256'),result.get('stored_image_sha256'))==(
                    meal['expected']['target_result_version'],meal['image_sha256'],meal['stored_image_sha256']):
                found.append(step['step'])
        if not found: raise ValueError('rescue_saved_origin_missing')
        matched.append({'slot':meal_slot,'steps':found})
    from .replacement_origin import native_quality_hold
    with store.transaction() as db:
        native = native_quality_hold(db,target,slot,{'preconditions':precondition},steps)
    ordinals=set();starts=[]
    for row in steps:
        if not row['step'].startswith(f'meal_replan:{slot}:'): continue
        number=row['step'].split(':')[-1]
        if number not in ('1','2') or row['state']!='complete' or not 0<row['started']<=now.timestamp():
            raise ValueError('rescue_attempt_origin_invalid')
        result=json.loads(row['result'] or '{}')
        if not result or result.get('reservation_only'): raise ValueError('rescue_attempt_origin_invalid')
        ordinals.add(int(number));starts.append(row['started'])
    for row in ledgers.get('replacement_attempts',[]):
        if row['slot']!=slot: continue
        if row['ordinal'] not in (1,2) or not 0<row['first_started']<=now.timestamp():
            raise ValueError('rescue_attempt_origin_invalid')
        ordinals.add(row['ordinal']);starts.append(row['first_started'])
    by_step={r['step']:r for r in steps}
    known_rounds=set()
    for row in steps:
        if row['step'].startswith((f'automatic_image_repair:{slot}',f'meal_replan:{slot}:')):
            value=json.loads(row['result'] or '{}')
            if value.get('round'):
                from .clients import MealClient
                MealClient.request_key(value['round']);known_rounds.add(value['round'])
    for row in steps:
        if row['step'].startswith(f'image:{slot}:') and row['step'].split(':')[-1] not in known_rounds:
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
        if row['slot']!=slot: continue
        step=by_step.get(f'meal_replan:{slot}:'+str(row['ordinal']))
        receipt=json.loads(row['receipt'])
        if (not step or json.loads(step['result'] or '{}').get('receipt')!=receipt
                or step['digest']!=digest(receipt)):
            raise ValueError('rescue_attempt_origin_invalid')
        from .replacement_origin import verify_receipt
        with store.transaction() as db:
            origin=db.execute('SELECT owner,epoch FROM replacement_origin_receipts WHERE target_date=? AND slot=? AND ordinal=?',
                (target,slot,row['ordinal'])).fetchone()
            if not origin: raise ValueError('rescue_attempt_origin_invalid')
            verify_receipt(db,target,slot,receipt,origin['owner'],origin['epoch'])
    costs=ledgers.get('replacement_waived_calls',[])+ledgers.get('replacement_cost_reservations',[])
    verified_pregrant=set();pregrant_calls=[]
    if case_uuid:
        with store.transaction() as db:
            receipts=[json.loads(r['document']) for r in db.execute("SELECT document FROM scoped_rescue_events WHERE contract='mina-rescue-v3' AND target_date=? AND slot=? AND case_uuid=? AND kind='other_meal_review'",(target,slot,case_uuid))]
            for receipt in receipts:
                for kind in ('food','ogp'):
                    key=receipt[kind+'_call_key']
                    row=db.execute("SELECT * FROM scoped_rescue_calls WHERE contract='mina-rescue-v3' AND target_date=? AND slot=? AND case_uuid=? AND call_key=?",(target,slot,case_uuid,key)).fetchone()
                    if (not row or row['state']!='complete' or row['kind']!='review' or row['input_digest']!=digest(receipt['inputs'])
                            or json.loads(row['value'])!={'status':'publishable','reason_codes':[]}):raise ValueError('rescue_cost_or_call_unresolved')
                    verified_pregrant.add(key);record=dict(row);record['value']={'sha256':hashlib.sha256(record['value']).hexdigest(),'byte_length':len(record['value'])};pregrant_calls.append(record)
            actual_keys={r['call_key'] for r in db.execute("SELECT call_key FROM scoped_rescue_calls WHERE contract='mina-rescue-v3' AND target_date=? AND slot=? AND case_uuid=? AND call_key LIKE ?",(target,slot,case_uuid,case_uuid+':pregrant:%'))}
            if actual_keys!=verified_pregrant:raise ValueError('rescue_cost_or_call_unresolved')
    if not ordinals and (ledgers.get('meal_replacement_requests') or ledgers.get('replacement_external_calls')
            or any(not row['kind'].startswith('collection_') and row['call_key'] not in verified_pregrant for row in costs)):
        raise ValueError('rescue_orphan_generation')
    for event in audit:
        details=json.loads(event['details'])
        if event['stage']=='meal_replan' and event['slot']==slot:
            if event['event']=='observed' and details.get('attempt') not in ordinals: raise ValueError('rescue_orphan_audit')
            if event['event']!='observed' and details.get('step_sha256') not in {r['digest'] for r in steps if r['step'].startswith(f'meal_replan:{slot}:')}:
                raise ValueError('rescue_orphan_audit')
        if event['stage']=='image' and details.get('step_sha256') not in {r['digest'] for r in steps if r['step'].startswith('image:')}:
            raise ValueError('rescue_orphan_audit')
    ordered=sorted(ordinals)
    # APIのused_replan_ordinalsは日内3枠のunion。対象枠の実receiptへ混ぜない。
    if ordered not in ([],[1]):
        raise ValueError('replan_limit_exceeded')
    # 再計画が実証された場合だけ真の初回を採用。画像の同メニュー修復は別履歴のまま保持。
    first=min(starts) if starts else None
    # 前日20時は上限であり、欠けた元期限の補完値ではない。
    import math
    with store.transaction() as db:
        episode=db.execute('SELECT * FROM replacement_episodes WHERE target_date=? AND slot=?',(target,slot)).fetchone()
        if not episode or (episode['owner'],episode['epoch'])!=(owner,epoch) or not isinstance(episode['deadline'],(int,float)) or not math.isfinite(episode['deadline']) or episode['deadline']<=0:
            raise ValueError('rescue_deadline_origin_missing')
        persisted_deadline=episode['deadline']
    maximum=approval_time(date.fromisoformat(target))
    receipts=[json.loads(r['receipt']) for r in ledgers.get('replacement_attempts',[]) if r['slot']==slot]
    prior_deadlines=[timestamp(r['existing_preparation_deadline_at']) for r in receipts]
    from datetime import UTC,datetime,timedelta
    old=min(prior_deadlines) if prior_deadlines else datetime.fromtimestamp(persisted_deadline,UTC)
    if old>maximum:raise ValueError('rescue_deadline_origin_invalid')
    first_time=datetime.fromtimestamp(first,UTC) if first else None
    hard=min(old,first_time+timedelta(minutes=15)) if first_time else old
    if first_time and abs(hard.timestamp()-persisted_deadline)>.001:raise ValueError('rescue_deadline_origin_invalid')
    ledgers['scoped_pregrant_call_receipts']=pregrant_calls
    document={'target_date':target,'owner':owner,'epoch':epoch,'steps':steps,'requests':rows,'matched':matched,'ledgers':ledgers,'generation_audit':audit,'native_quality_hold':native,
        'day_used_replan_ordinals':precondition['policy']['used_replan_ordinals'],'original_context_present':first is not None}
    return {'document':document,'source_ledger_digest':digest(document),'cost_history_digest':digest(ledgers),
            'original_first_replan_started_at':stamp(first_time) if first_time else None,
            'original_preparation_deadline_at':stamp(old),'original_hard_deadline_at':stamp(hard) if first_time else None,'original_context_present':first_time is not None,
            'baseline_consumed_ordinals':ordered}
