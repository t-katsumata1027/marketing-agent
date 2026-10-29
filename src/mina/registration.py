"""画像保存後の登録・正本照合・独立レビューを順番に実行する。"""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from .clients import OutcomeUnknown, MealClient
from .domain import instant, slot_time
from .generation import MEAL_SLOTS, validate_plan, meal_review_policy_sha256
from .m1_contract import CONTRACT, completed_request, request_report, validate_state, validate_write
from .ogp import capture_ogp, verify_image_reference
from .preparation import PreparationJournal, fingerprint
from .request_ledger import RequestLedger
from .preparation_status import QualityHold


def assert_day_state(state, target, completed, now):
    """サーバー正本に今回以外の食事があれば、新規登録を止める。"""
    try:
        current_version = validate_state(state, target, now)
        meals = state['meals']
        if not isinstance(meals, list) or len(meals) != len(completed):
            raise ValueError()
        expected = {(slot, instant(datetime.fromisoformat(value['result']['meal']['logged_at'])), value['result']['share']['url'])
                    for slot, value in completed.items()}
        actual = {(value['meal']['meal_type'], instant(datetime.fromisoformat(value['meal']['logged_at'])), value['share']['url'])
                  for value in meals}
        if len(actual) != len(meals) or actual != expected:
            raise ValueError()
        for value in meals:
            original = completed[value['meal']['meal_type']]['result']
            if any(value[key] != original[key] for key in ('analysis', 'meal', 'normalized_comment', 'share', 'x', 'result_version')):
                raise ValueError()
        return current_version
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError('対象日の既存記録・登録成否を照合できません') from None


def register_day(store, draft, saved, directory, meal_client, ai, host, capture=capture_ogp, capture_round=None,
                 allow_correction=False, replacement_rounds=None, collection_slots=None, meal_policy_sha256=None):
    """3食と日次を点検する。履歴・旧予約が未照合なら承認候補へ進めない。"""
    from .tolerance_review import scoped_policy
    selected_policy=scoped_policy(meal_review_policy_sha256(),meal_policy_sha256)
    target = draft['target_date']
    from .current_day_gate import require_stage
    require_stage(store,target)
    if collection_slots is not None:
        from .rescue_collection import _scope
        from .replacement_collection import _scope as future_scope
        future=future_scope.get()
        valid_future=(future is not None and future.e['target_date']==target and tuple(collection_slots)==future.missing)
        if not valid_future and (tuple(collection_slots) != ('lunch','dinner') or target != '2026-10-24' or _scope.get() is None):
            raise ValueError('rescue_collection_scope_required')
    if capture_round is not None:
        MealClient.request_key(capture_round)
    day = date.fromisoformat(target)
    from .menu_budget import validate_draft_plan
    validate_draft_plan(draft,store)
    if draft.get('state') != 'draft_unreviewed' or saved.get('state') != 'images_drive_verified' or saved.get('target_date') != target or set(saved.get('images', {})) != set(MEAL_SLOTS):
        raise ValueError('同じ対象日の下書きとDrive確認済み3画像が必要です')
    # どの枠の書き込みも始める前に、全画像の版と再読取証跡を確認する。
    images = {}
    for slot in MEAL_SLOTS:
        info = saved['images'][slot]
        path = Path(info['path'])
        if path.is_symlink():
            raise ValueError('画像実体が必要です')
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if (sha != info['sha256'] or len(raw) != info['size'] or info['drive'].get('verified') is not True or
                info['drive'].get('sha256') != sha or info['image_review']['status'] != 'image_acceptable' or info['image_review']['reason_codes']):
            raise ValueError('画像・事前点検・Drive保存の版が不一致です')
        images[slot] = raw
    directory.mkdir(parents=True, exist_ok=True)
    journal = PreparationJournal(store)
    requests = RequestLedger(store)
    replacement_rounds = replacement_rounds or {}
    if not set(replacement_rounds) <= set(MEAL_SLOTS):
        raise ValueError('未保存要求の差替え枠が不正です')
    for slot, round_key in replacement_rounds.items():
        MealClient.request_key(round_key)
        with store.transaction() as db:
            adopted=db.execute('SELECT state,result FROM preparation_steps WHERE target_date=? AND step=?',
                               (target,'meal_register:'+slot+':'+round_key)).fetchone()
            if adopted and adopted['state']=='complete':
                if json.loads(adopted['result'])['result']['image_sha256']!=saved['images'][slot]['sha256']:
                    raise ValueError('採用済み再計画画像の版が違います')
                # 保存済み採用版のUUID・現在正本は後続で通常どおり照合する。
                continue
            failed = list(db.execute("SELECT request_key FROM mina_requests WHERE target_date=? AND slot=? AND operation='meal_register'", (target, slot)))
            candidates_exist = db.execute('SELECT 1 FROM candidates WHERE target_date=?', (target,)).fetchone()
        if candidates_exist or not failed:
            raise ValueError('候補のない日の終端未保存要求だけを差替えできます')
        for row in failed:
            request = requests.restore(row['request_key'])
            lookup = meal_client.request_state(row['request_key'], 'meal_register')
            request_report(lookup, row['request_key'], 'meal_register')
            if (request['phase'] != 'held' or lookup.get('status') != 'failed' or lookup.get('persistence') != 'not_saved' or
                    lookup.get('stage') != 'quality' or lookup.get('reason') != 'quality_review_required' or
                    lookup.get('current_result') is not None or request['image_sha256'] == saved['images'][slot]['sha256']):
                raise ValueError('保存済み・処理中・不明の要求は差替えしません')
    completed = {}
    with store.transaction() as db:
        for slot in MEAL_SLOTS:
            suffix = ':' + replacement_rounds[slot] if slot in replacement_rounds else ''
            row = db.execute('SELECT state,result FROM preparation_steps WHERE target_date=? AND step=?', (target, 'meal_register:' + slot + suffix)).fetchone()
            if row:
                if row['state'] != 'complete':
                    raise OutcomeUnknown('preparation_reconciliation_required')
                completed[slot] = json.loads(row['result'])
    corrections = {}
    if allow_correction:
        for slot in MEAL_SLOTS:
            with store.transaction() as db:
                row = db.execute('SELECT state,result FROM preparation_steps WHERE target_date=? AND step=?', (target, 'meal_correction:' + slot)).fetchone()
                key_row = db.execute('SELECT result FROM preparation_steps WHERE target_date=? AND step=?', (target, 'correction_key:' + slot)).fetchone()
            if not row:
                continue
            if row['state'] != 'complete' or not key_row or slot not in completed:
                raise OutcomeUnknown('訂正要求の成否確認が必要です')
            response, key = json.loads(row['result']), json.loads(key_row['result'])['key']
            record = requests.restore(key)
            if record['phase'] not in ('response_received','complete'):
                raise OutcomeUnknown('成否不明の訂正は再開しません')
            validate_write(response, slot, target, host, operation='meal_correction', logged_at=slot_time(day,slot).isoformat(), input_comment=draft['meals'][slot]['comment'])
            completed_request(meal_client.request_state(key,'meal_correction'), key,'meal_correction',response['result'])
            if (response['result']['image_sha256'] != hashlib.sha256(images[slot]).hexdigest() or
                    response['result']['stored_image_sha256'] != completed[slot]['result']['stored_image_sha256']):
                raise ValueError('訂正済み結果の画像証跡が変わっています')
            requests.record(key,'complete',{'persistence':'saved','stage':'completed'})
            corrections[slot] = (response,key)
            completed[slot] = response

    checking_slot = None
    def check_state():
        # 最初の正本GETも、直前に完了したDrive工程とは別の処理として記録する。
        with store.transaction() as db:
            previous = db.execute('SELECT step,status,reasons FROM preparation_progress WHERE target_date=?', (target,)).fetchone()
        step = 'source_state' + (':' + checking_slot if checking_slot else '')
        store.record_preparation_progress(target, step, 'reviewing')
        with store.audit.operation('creation', target, checking_slot, None, 'source_state'):
            result = assert_day_state(meal_client.day_state(target), target, completed, datetime.now(UTC))
        # 読取が成功した後のPOST/PATCHを、読取中の進捗として誤表示しない。
        if previous:
            store.record_preparation_progress(target, previous['step'], previous['status'], json.loads(previous['reasons']))
        else:
            store.record_preparation_progress(target, 'registration', 'running')
        return result

    def ogp(slot, normalized, variant=''):
        def collect():
            captured = capture(normalized['share_url'], host)
            if captured['meta']['image_sha256'] != normalized['ogp']['image_sha256']:
                raise ValueError('共有カードとサーバー検証済みOGPが不一致です')
            verify_image_reference(captured, normalized['ogp'], normalized['share_url'], host)
            path = directory / (slot + variant + '-ogp.jpg')
            with path.open('xb') as file:
                file.write(captured['review_image'])
            return {'meta': captured['meta'], 'digest': captured['digest'], 'path': str(path.resolve()),
                    'preview_sha256': hashlib.sha256(captured['review_image']).hexdigest()}
        step = 'ogp_capture:' + slot + (':' + capture_round if capture_round else '') + variant
        result = journal.run(target, step, {'share_url': normalized['share_url'], 'directory': str(directory.resolve())}, collect)
        path = Path(result['path'])
        if path.is_symlink():
            raise ValueError('OGP点検画像の実体が必要です')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != result['preview_sha256']:
            raise ValueError('OGP点検画像の版が不一致です')
        return result, raw

    outputs = {}
    check_state()
    for slot in MEAL_SLOTS:
        if collection_slots is not None and slot not in collection_slots:
            if slot not in completed: raise ValueError("rescue_saved_breakfast_missing")
            continue
        checking_slot = slot
        meal, info, raw = draft['meals'][slot], saved['images'][slot], images[slot]
        logged = slot_time(day, slot).isoformat()
        inputs = {'sha256': info['sha256'], 'logged_at': logged, 'comment': meal['comment']}
        operation = 'meal_correction' if slot in corrections else 'meal_register'
        if slot in corrections:
            response, key = corrections[slot]
        else:
            suffix = ':' + replacement_rounds[slot] if slot in replacement_rounds else ''
            request = journal.run(target, 'meal_request_key:' + slot + suffix, inputs,
                                  lambda: {'key': str(uuid.uuid4()), 'expected_state_version': check_state()})
            key = request['key']
            payload = {'contract_version': CONTRACT, 'meal_type': slot, 'logged_at': logged, 'comment': meal['comment'],
                       'expected_state_version': request['expected_state_version'], 'filename': f'{target}-{slot}.jpg'}
            requests.prepare(key, 'meal_register', target, slot, payload, raw)
            def register():
                if check_state() != request['expected_state_version']:
                    raise ValueError('送信前に期待する正本版が変更されています')
                return meal_client.register(raw, f'{target}-{slot}.jpg', slot, logged, meal['comment'], request_key=key,
                                            expected_state_version=request['expected_state_version'])
            response = journal.run(target, 'meal_register:' + slot + suffix,
                                   inputs, lambda: requests.execute_once(key, meal_client, register))
        completed[slot] = response
        normalized = validate_write(response, slot, target, host, operation=operation, logged_at=logged, input_comment=meal['comment'])
        if response['result']['image_sha256'] != info['sha256']:
            raise ValueError('登録入力画像の版が一致しません')
        lookup = meal_client.request_state(key, operation)
        input_hash = completed_request(lookup, key, operation, response['result'])
        requests.record(key, 'complete', {'persistence': 'saved', 'stage': 'completed'})
        if operation == 'meal_register' and lookup.get('image_sha256') != info['sha256']:
            raise ValueError('要求照会の入力画像版が不一致です')
        variant = ':corrected' if operation == 'meal_correction' else ''
        captured, preview = ogp(slot, normalized, '-corrected' if variant else '')
        review_suffix = ':' + capture_round if capture_round else ''
        from .meal_review_recovery import review_once
        review = review_once(journal,target, 'meal_review:' + slot + variant + review_suffix, {'image_sha256': info['sha256'], 'meal': meal, 'response': response, 'ogp_digest': captured['digest'], 'policy_sha256':selected_policy},
                             lambda: ai.review_meal(raw, meal, response, captured['meta']))
        from .review_diagnostics import record_findings,record_verdict
        record_findings(store,ai,target,slot,'meal_review',info['sha256'])
        record_verdict(store,target,slot,'meal_review',review)
        if (allow_correction and operation == 'meal_register' and review.get('status') == 'hold' and review.get('reason_codes') and
                set(review['reason_codes']) <= {'image_analysis_mismatch','nutrition_semantic_mismatch'} and
                (meal_policy_sha256 is None or ai.allow_correction(meal,response,review))):
            from .correction import correct_meal_once
            original = response
            response, key = correct_meal_once(journal, requests, meal_client, ai, check_state, target, slot, meal, raw, original, host)
            operation = 'meal_correction'
            completed[slot] = response
            normalized = validate_write(response, slot, target, host, operation=operation, logged_at=logged, input_comment=meal['comment'])
            input_hash = completed_request(meal_client.request_state(key, operation), key, operation, response['result'])
            captured, preview = ogp(slot, normalized, '-corrected')
            review = review_once(journal,target, 'meal_review:' + slot + ':corrected' + review_suffix,
                {'image_sha256':info['sha256'],'meal':meal,'response':response,'ogp_digest':captured['digest'],'policy_sha256':selected_policy},
                lambda:ai.review_meal(raw, meal, response, captured['meta']))
            record_findings(store,ai,target,slot,'meal_review',info['sha256'])
            record_verdict(store,target,slot,'meal_review',review)
        if review != {'status': 'publishable', 'reason_codes': []}:
            raise QualityHold('解析結果の独立レビューが保留です','meal_review:'+slot,review.get('reason_codes',()))
        from .ogp_finish_recovery import review_once as ogp_review_once
        ogp_review = ogp_review_once(journal,target, 'ogp_review:' + slot, {'image_sha256': info['sha256'], 'normalized': normalized, 'ogp_digest': captured['digest']},
                                 lambda: ai.review_ogp([preview, raw], {**normalized, 'target_date': target, 'slot': slot}, captured['meta']))
        record_findings(store,ai,target,slot,'ogp_review',hashlib.sha256(preview).hexdigest())
        record_verdict(store,target,slot,'ogp_review',ogp_review)
        if ogp_review != {'status': 'publishable', 'reason_codes': []}:
            raise QualityHold('OGPの実画像レビューが保留です','ogp_review:'+slot,ogp_review.get('reason_codes',()))
        outputs[slot] = {**normalized, 'menu': meal['menu'], 'image_sha256': info['sha256'],
                         'ogp_sha256': captured['digest'], 'api_quality': 'pass', 'secondary_review': 'publishable', 'ogp_verified': True,
                         'approval_source': {'request_key': key, 'operation': operation, 'input_hash': input_hash,
                            'result_sha256': normalized['result_sha256'], 'result_version': response['result']['result_version'],
                            'stored_image_sha256': response['result']['stored_image_sha256'],
                            'ogp_revision': normalized['ogp']['revision'], 'ogp_source_version': normalized['source_version']}}
    if collection_slots is not None:
        # 保存済み朝食の保留を解除せず、昼夕点検の結果だけを返す。日次生成・公開なし。
        check_state()
        return outputs
    total = sum(value['nutrition']['calories'] for value in outputs.values())
    if not 1800 <= total <= 2000:
        raise QualityHold('実解析の3食合計が1800〜2000kcalから外れています','meal_review:daily',('daily_energy_out_of_range',))
    checking_slot = 'daily'
    check_state()
    daily_inputs = {'date': target, 'comment': draft['daily_comment'], 'meals': completed}
    request = journal.run(target, 'daily_request_key', daily_inputs,
                          lambda: {'key': str(uuid.uuid4()), 'expected_state_version': check_state()})
    key = request['key']
    daily_payload = {'contract_version': CONTRACT, 'date': target, 'comment': draft['daily_comment'],
                     'expected_meal_count': 3, 'expected_state_version': request['expected_state_version']}
    requests.prepare(key, 'daily_share', target, 'daily', daily_payload)
    daily_response = journal.run(target, 'daily_share', daily_inputs,
                                 lambda: requests.execute_once(key, meal_client,
                                     lambda: meal_client.json_write('/api/internal/mina/daily-share', daily_payload, request_key=key)))
    daily = validate_write(daily_response, 'daily', target, host, input_comment=draft['daily_comment'], meals=outputs)
    daily_hash = completed_request(meal_client.request_state(key, 'daily_share'), key, 'daily_share', daily_response['result'])
    requests.record(key, 'complete', {'persistence': 'saved', 'stage': 'completed'})
    if daily['source_version'] != request['expected_state_version']:
        raise ValueError('日次集計の生成元版が不一致です')
    captured, preview = ogp('daily', daily)
    review = journal.run(target, 'ogp_review:daily', {'normalized': daily, 'ogp_digest': captured['digest'], 'images': {slot: saved['images'][slot]['sha256'] for slot in MEAL_SLOTS}},
                         lambda: ai.review_ogp([preview, *(images[slot] for slot in MEAL_SLOTS)], {**daily, 'target_date': target, 'slot': 'daily', 'meal_count': 3}, captured['meta']))
    record_findings(store,ai,target,'daily','ogp_review',hashlib.sha256(preview).hexdigest())
    record_verdict(store,target,'daily','ogp_review',review)
    if review != {'status': 'publishable', 'reason_codes': []}:
        raise QualityHold('日次OGPの実画像レビューが保留です','ogp_review:daily',review.get('reason_codes',()))
    outputs['daily'] = {**daily, 'menu': ' / '.join(outputs[slot]['menu'] for slot in MEAL_SLOTS),
                        'api_quality': 'pass', 'secondary_review': 'publishable',
                        'ogp_sha256': captured['digest'], 'ogp_verified': True, 'meal_count': 3,
                        'image_sha256': fingerprint({slot: saved['images'][slot]['sha256'] for slot in MEAL_SLOTS}),
                        'approval_source': {'request_key': key, 'operation': 'daily_share', 'input_hash': daily_hash,
                            'result_sha256': daily['result_sha256'], 'result_version': daily_response['result']['result_version'],
                            'ogp_revision': daily['ogp']['revision'], 'ogp_source_version': daily['source_version']}}
    final_version = check_state()
    if final_version != daily['source_version']:
        raise ValueError('レビュー中に日全体の正本が変更されています')
    for post in outputs.values():
        post['approval_source']['state_version'] = final_version
    return {'target_date': target, 'state': 'posts_reviewed', 'posts': outputs,
            'history_verified': False, 'legacy_schedule_clear': False,
            'remaining': ['履歴・既存予約照合', '実装commit証跡', '承認候補への取り込み', 'Discord承認']}
