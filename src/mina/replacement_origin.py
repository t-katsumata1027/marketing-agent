"""初回Pの移行に用いるAgent原本を、同じSQLite読取版と所有情報へ固定する。"""
import hashlib
import json
from pathlib import Path
from .replacement_contract import digest
from .preparation import fingerprint


def native_quality_hold(db,target,slot,binding,steps):
    """通常点検のcomplete+holdを、現在正本と同じ入力の事実として読む。書換えはしない。"""
    from .generation import meal_review_policy_sha256
    from .replacement_contract import Contract
    from jsonschema.exceptions import ValidationError
    from .m1_contract import validate_write
    from .domain import slot_time,date
    from .readiness import read_json
    by_step={r['step']:r for r in steps}
    corrected='meal_correction:'+slot in by_step
    review=by_step.get('meal_review:'+slot+(':corrected' if corrected else ''))
    capture=by_step.get('ogp_capture:'+slot+('-corrected' if corrected else ''))
    saved_step=by_step.get(('meal_correction:' if corrected else 'meal_register:')+slot)
    if not review or not capture or not saved_step or any(r['state']!='complete' for r in (review,capture,saved_step)):
        raise ValueError('replacement_quality_hold_required')
    verdict=json.loads(review['result'])
    if (verdict.get('status')!='hold' or not verdict.get('reason_codes')
            or not set(verdict['reason_codes'])<={'image_analysis_mismatch','nutrition_semantic_mismatch'}):
        raise ValueError('replacement_quality_hold_required')
    context_row=db.execute('SELECT context FROM replacement_runtime_context WHERE target_date=?',(target,)).fetchone()
    if not context_row:raise ValueError('migration_unverified')
    context=json.loads(context_row[0]);base=Path(context['directory'])
    for name,key in (('draft.json','draft'),('drive.json','saved')):
        path=base/name
        if path.is_symlink() or read_json(path)!=context[key]:raise ValueError('migration_unverified')
    draft=context['draft'];image=context['saved']['images'][slot]
    if draft['target_date']!=target or context['saved']['target_date']!=target:raise ValueError('migration_unverified')
    path=Path(image['path'])
    if path.is_symlink():raise ValueError('migration_unverified')
    raw=path.read_bytes()
    if not 0<len(raw)<=4194304 or len(raw)!=image['size'] or hashlib.sha256(raw).hexdigest()!=image['sha256']:
        raise ValueError('migration_unverified')
    response=json.loads(saved_step['result']);captured=json.loads(capture['result'])
    try:
        validate_write(response,slot,target,context['host'],operation='meal_correction' if corrected else 'meal_register',
                       logged_at=slot_time(date.fromisoformat(target),slot).isoformat(),input_comment=draft['meals'][slot]['comment'])
    except ValueError:raise ValueError('replacement_quality_hold_required') from None
    own=next((m for m in binding['preconditions']['meals'] if m['expected']['target_slot']==slot),None)
    result=response['result']
    descriptor=own['analysis'] if own else None
    try:descriptor_digest=Contract(lambda:None).analysis(descriptor)
    except (ValidationError,ValueError,KeyError,TypeError):raise ValueError('replacement_quality_hold_required') from None
    # 保存済みdescriptorのprovenanceはM1とは異なる。nullをfalseへ補完せず、共有する実食品・栄養・品質を照合する。
    def foods(value):
        return [{k:f.get(k) for k in ('name','amount','calories','protein','fat','carbs')} for f in value['foods']]
    if (not own or own['expected']['target_result_version']!=result['result_version']
            or own['expected']['target_share_url']!=result['share']['url']
            or own['image_sha256']!=image['sha256'] or result['image_sha256']!=image['sha256']
            or own['stored_image_sha256']!=result['stored_image_sha256']
            or descriptor_digest!=own['analysis_digest'] or descriptor['analysis_source']!='stored_canonical'
            or descriptor['is_ambiguous'] not in (None,False) or result['analysis'].get('is_ambiguous') is not False
            or foods(descriptor)!=foods(result['analysis']) or descriptor['summary']!=result['analysis']['summary']
            or descriptor['quality']!=result['quality']):
        raise ValueError('replacement_quality_hold_required')
    expected={'image_sha256':image['sha256'],'meal':draft['meals'][slot],'response':response,
              'ogp_digest':captured['digest'],'policy_sha256':meal_review_policy_sha256()}
    if review['digest']!=fingerprint(expected):raise ValueError('replacement_quality_hold_required')
    return {'format':'native-complete-hold-v1','step':review['step'],'input_digest':review['digest'],
            'result_version':result['result_version'],'image_sha256':image['sha256'],'verdict_digest':fingerprint(verdict),
            'stored_descriptor_digest':descriptor_digest,'stored_descriptor_ambiguity':descriptor['is_ambiguous'],
            'm1_ambiguity':result['analysis']['is_ambiguous']}


def originals(db, target, slot, binding):
    """空の台帳を初回と推測しない。保存済み3食と登録前工程の実在を照合する。"""
    owner, epoch, pre = binding['owner'], binding['epoch'], binding['preconditions']
    hold = db.execute('SELECT * FROM publication_holds WHERE target_date=?', (target,)).fetchone()
    if not hold or (hold['owner'], hold['epoch'], hold['state']) != (owner, epoch, 'held'):
        raise ValueError('replacement_hold_changed')
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'mina_requests', 'preparation_steps'} <= names:
        raise ValueError('migration_unverified')
    steps = [dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=? ORDER BY step', (target,))]
    requests = [dict(r) for r in db.execute('SELECT * FROM mina_requests WHERE target_date=? ORDER BY request_key', (target,))]
    if any(r['state'] in ('started', 'outcome_unknown') for r in steps):
        raise ValueError('migration_unverified')
    holds=[json.loads(r['result'] or '{}') for r in steps
           if r['step'].split(':')[:2]==['meal_review',slot] and r['state']=='held']
    native=[r for r in steps if r['step'].split(':')[:2]==['meal_review',slot] and r['state']=='complete']
    evidence=native_quality_hold(db,target,slot,binding,steps) if native else None
    if evidence is None and not any(h.get('classification')=='quality_hold' and h.get('reason_codes')
               and set(h['reason_codes'])<={'image_analysis_mismatch','nutrition_semantic_mismatch'} for h in holds):
        raise ValueError('replacement_quality_hold_required')
    by_step={r['step']:r for r in steps}
    local_ordinals={int(r['step'].split(':')[-1]) for r in steps if r['step'].startswith('meal_replan:') and r['step'].split(':')[-1] in ('1','2')}
    if not set(pre['policy']['used_replan_ordinals'])<=local_ordinals:
        raise ValueError('migration_unverified')
    for step in steps:
        if step['step'].startswith('meal_replan:' + slot + ':'):
            if step['state']=='known_rejected':
                from .generation_receipts import rejection
                rejection(step)
                continue
            value=json.loads(step['result'] or '{}')
            if not value.get('reservation_only'):
                # 旧再計画は提案原本と対象日を保持する。工程名だけの復元を認めない。
                import uuid
                try:
                    uuid.UUID(value['round'])
                    if value['draft']['target_date'] != target:raise ValueError()
                    from .menu_budget import validate_draft_plan
                    validate_draft_plan(value['draft'],db=db)
                    image_step=by_step['image:'+slot+':'+value['round']]
                    key_step=by_step['meal_request_key:'+slot+':'+value['round']]
                    registered=by_step['meal_register:'+slot+':'+value['round']]
                    if any(r['state']!='complete' for r in (image_step,key_step,registered)):raise ValueError()
                    image=json.loads(image_step['result']);path=Path(image['path'])
                    if path.is_symlink():raise ValueError()
                    raw=path.read_bytes()
                    if len(raw)!=image['size'] or hashlib.sha256(raw).hexdigest()!=image['sha256']:raise ValueError()
                    key=json.loads(key_step['result'])['key']
                    request=next(r for r in requests if r['request_key']==key)
                    payload=json.loads(request['input_json'])
                    inputs={'sha256':image['sha256'],'logged_at':payload['logged_at'],'comment':value['draft']['meals'][slot]['comment']}
                    if (request['image']!=raw or payload['meal_type']!=slot or payload['comment']!=inputs['comment']
                            or key_step['digest']!=fingerprint(inputs) or registered['digest']!=fingerprint(inputs)
                            or json.loads(registered['result'])['result']['image_sha256']!=image['sha256']):raise ValueError()
                except (KeyError, TypeError, ValueError, OSError, StopIteration):raise ValueError('migration_unverified') from None
    # 要求原本のバイトと入力digestを再計算する。UUIDの形式だけでは保存済みと認めない。
    for r in requests:
        if r['phase'] != 'complete':
            raise ValueError('replacement_existing_request_unresolved')
        sha = hashlib.sha256(r['image']).hexdigest() if r['image'] is not None else None
        envelope = {'operation': r['operation'], 'target_date': target, 'slot': r['slot'],
                    'payload': json.loads(r['input_json']), 'image_sha256': r['image_sha256']}
        if sha != r['image_sha256'] or fingerprint(envelope) != r['input_digest']:
            raise ValueError('migration_unverified')
    for meal in pre['meals']:
        expected = meal['expected']; meal_slot = expected['target_slot']
        saved = []
        for step in steps:
            if step['step'].startswith(('meal_register:' + meal_slot, 'meal_correction:' + meal_slot)) and step['state'] == 'complete':
                result = json.loads(step['result']).get('result', {})
                if (result.get('result_version') == expected['target_result_version']
                        and result.get('image_sha256') == meal['image_sha256']
                        and result.get('stored_image_sha256') == meal['stored_image_sha256']
                        and result.get('share', {}).get('url') == expected['target_share_url']):
                    matching=[]
                    for request in requests:
                        payload=json.loads(request['input_json'])
                        if request['slot']!=meal_slot:continue
                        if step['step'].split(':')[0]=='meal_correction':
                            key_step=by_step.get('correction_key:'+meal_slot)
                            if (request['operation']=='meal_correction' and key_step and key_step['state']=='complete'
                                    and json.loads(key_step['result'])['key']==request['request_key']
                                    and payload.get('share_url')==expected['target_share_url']
                                    and payload.get('comment')==result.get('normalized_comment')
                                    and payload.get('foods')==result['analysis']['foods']
                                    and step['digest']==fingerprint(payload)):
                                matching.append(request)
                            continue
                        if request['operation']!='meal_register':continue
                        if (request['image_sha256']==meal['image_sha256'] and payload.get('meal_type')==meal_slot
                                and payload.get('comment')==result.get('normalized_comment')
                                and payload.get('logged_at') and result.get('meal',{}).get('meal_type')==meal_slot):
                            from datetime import datetime
                            if datetime.fromisoformat(payload['logged_at'])!=datetime.fromisoformat(result['meal']['logged_at']):continue
                            inputs={'sha256':request['image_sha256'],'logged_at':payload['logged_at'],'comment':payload['comment']}
                            if step['digest']!=fingerprint(inputs):continue
                            key_step=by_step.get(step['step'].replace('meal_register:','meal_request_key:',1))
                            if (not key_step or key_step['state']!='complete' or key_step['digest']!=fingerprint(inputs)
                                    or json.loads(key_step['result'])['key']!=request['request_key']):continue
                            matching.append(request)
                    if matching:saved.append(result)
        if not saved or not any(r['slot'] == meal_slot and r['image_sha256'] == meal['image_sha256']
                                and json.loads(r['observation'] or '{}').get('persistence') == 'saved' for r in requests):
            raise ValueError('migration_unverified')
    # 既存の生成・費用予約・置換要求も原本へ含める。不明な外部処理を別試行で迂回しない。
    ledgers = {}
    for table in ('replacement_external_calls', 'replacement_waived_calls', 'replacement_cost_reservations', 'meal_replacement_requests'):
        if table not in names:
            continue
        rows = [dict(r) for r in db.execute('SELECT * FROM ' + table + ' WHERE target_date=? ORDER BY rowid', (target,))]
        for r in rows:
            if 'state' in r and r['state'] != 'complete':
                raise ValueError('replacement_external_lookup_required')
            if table == 'meal_replacement_requests':
                observed = json.loads(r['observation'] or '{}')
                if observed.get('outcome') not in ('applied', 'rejected') or observed.get('conflict'):
                    raise ValueError('replacement_lookup_required')
            for key, value in list(r.items()):
                if isinstance(value, bytes):
                    r[key] = {'sha256': hashlib.sha256(value).hexdigest(), 'byte_length': len(value)}
        ledgers[table] = rows
    for r in requests:
        r.pop('image')
    return {'version': 'agent-initial-prepare-origin-v1', 'target_date': target, 'slot': slot,
            'owner': owner, 'hold_epoch': epoch, 'preconditions': pre, 'preparation_steps': steps,
            'requests': requests, 'generation_and_requests': ledgers,
            **({'quality_hold_evidence':evidence} if evidence is not None else {})}


def verify_receipt(db, target, slot, receipt, owner, epoch):
    """送信入力が永続化した原本・所有版と一致することをP直前にも点検する。"""
    row = db.execute('SELECT * FROM replacement_origin_receipts WHERE target_date=? AND slot=? AND ordinal=?',
                     (target, slot, receipt['attempt_ordinal'])).fetchone()
    if not row or (row['owner'], row['epoch'], row['receipt']) != (owner, epoch, json.dumps(receipt, sort_keys=True)):
        raise ValueError('migration_unverified')
    origin = json.loads(row['origin'])
    if digest(origin) != row['origin_digest'] or receipt['receipt_digest'] != digest({k: v for k, v in receipt.items() if k != 'receipt_digest'}):
        raise ValueError('migration_unverified')
    reservation=digest({'origin_digest':row['origin_digest'],'policy':receipt['budget_policy_digest'],
        'target':target,'slot':slot,'ordinal':receipt['attempt_ordinal'],'owner':owner,'epoch':epoch,
        'allocation':origin['server_allocation_units']})
    if receipt['fee_reservation_digest']!=reservation:
        raise ValueError('migration_unverified')
    if (origin['target_date'],origin['slot'],origin['owner'],origin['hold_epoch'])!=(target,slot,owner,epoch):
        raise ValueError('migration_unverified')
    # 予約後も参照先の実原本を再読取する。スナップショットだけ残した台帳喪失を通さない。
    for step in origin['preparation_steps']:
        current=db.execute('SELECT * FROM preparation_steps WHERE target_date=? AND step=?',(target,step['step'])).fetchone()
        if not current or dict(current)!=step:raise ValueError('migration_unverified')
    for request in origin['requests']:
        current=db.execute('SELECT * FROM mina_requests WHERE request_key=?',(request['request_key'],)).fetchone()
        if not current:raise ValueError('migration_unverified')
        current=dict(current);raw=current.pop('image')
        sha=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if current!=request or sha!=request['image_sha256']:raise ValueError('migration_unverified')
    return origin
