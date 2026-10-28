"""完了済み修正・日次更新の要求証跡を読み、新しい承認版へ移行する。"""

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from .clients import MealClient
from .domain import Candidate, SLOTS, slot_time
from .generation import validate_plan
from .m1_contract import completed_request, validate_result, validate_state
from .preparation import fingerprint
from .readiness import check_evidence, release_commit
from .rereview import rereview_day


def rebind_day(store, target, round_key, bindings, evidence, images, draft, ai, client, host,
               output, clock, capture, source_commit, root):
    """GETとレビューだけ。新UUIDの発行・POST・PATCH・日次作成はしない。"""
    MealClient.request_key(round_key)
    source_commit = release_commit(root, source_commit)
    entries = check_evidence(evidence, target, clock())
    if bindings.get('target_date') != target or set(bindings.get('requests', {})) != set(SLOTS):
        raise ValueError('明示した同じ対象日の4要求が必要です')
    if draft.get('target_date') != target:
        raise ValueError('新しい正本に対応する献立資料が必要です')
    from .menu_budget import validate_draft_plan
    validate_draft_plan(draft,store)
    if set(images) != set(SLOTS) - {'daily'}:
        raise ValueError('明示した原本画像3枚が必要です')
    rows = store.list_day(target)
    if len(rows) != 4 or any(row['state'] not in ('approved', 'approval_pending', 'changes_requested') for row in rows):
        raise ValueError('送信開始済み・取消済み候補を新証跡へ移行しません')
    expected = {row['slot']: row['digest'] for row in rows}
    # 失敗時にも旧版を送らない。外部の読取開始より先に失効させる。
    store.hold_day(target, expected)
    state = client.day_state(target)
    final_version = validate_state(state, target, clock())
    if state['meal_count'] != 3 or {m['meal']['meal_type'] for m in state['meals']} != set(images):
        raise ValueError('朝昼夕各1件の正本が必要です')
    raw_images = {}
    for slot, value in images.items():
        path = Path(value)
        if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('原本画像の形式・容量が不正です')
        raw_images[slot] = path.read_bytes()
    candidates, meals, keys = {}, {}, set()
    weekly = [e['comment'] for e in entries if date.fromisoformat(e['date']) >= date.fromisoformat(target) - timedelta(days=7)]
    readiness_hash = hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    for slot in SLOTS:
        binding = bindings['requests'][slot]
        key, operation = binding['request_key'], binding['operation']
        MealClient.request_key(key)
        if key in keys or operation not in (('daily_share',) if slot == 'daily' else ('meal_register', 'meal_correction')):
            raise ValueError('要求UUIDの重複または処理種別が不正です')
        keys.add(key)
        lookup = client.request_state(key, operation)
        result = lookup['current_result']
        input_hash = completed_request(lookup, key, operation, result)
        if lookup.get('current_state_version') != final_version:
            raise ValueError('複数照会の正本版が不一致です')
        comment = draft['daily_comment'] if slot == 'daily' else draft['meals'][slot]['comment']
        normalized = validate_result(result, slot, target, host,
                                     logged_at=slot_time(date.fromisoformat(target), slot).isoformat() if slot != 'daily' else None,
                                     input_comment=comment, meals=meals if slot == 'daily' else None, now=clock())
        if slot != 'daily':
            image_sha = hashlib.sha256(raw_images[slot]).hexdigest()
            actual = next(m for m in state['meals'] if m['meal']['meal_type'] == slot)
            if image_sha != result['image_sha256'] or actual['result_version'] != result['result_version']:
                raise ValueError('要求正本と対象日の個別版・原本画像が不一致です')
            meals[slot] = normalized
            menu = draft['meals'][slot]['menu']
        else:
            day_result = state.get('daily_share')
            if not isinstance(day_result, dict) or day_result.get('result_version') != result['result_version'] or result['source_state_version'] != final_version:
                raise ValueError('日次更新の正本版・生成元版が不一致です')
            image_sha = fingerprint({s: hashlib.sha256(raw_images[s]).hexdigest() for s in raw_images})
            menu = ' / '.join(draft['meals'][s]['menu'] for s in raw_images)
        if any(e['comment'] == normalized['comment'] or (slot != 'daily' and e['menu'] == menu) for e in entries):
            raise ValueError('新しい候補が直前14日の履歴と重複しています')
        if '気になります' in normalized['comment'] and any('気になります' in comment for comment in weekly):
            raise ValueError('新しい候補が週次表現上限を超えています')
        weekly.append(normalized['comment'])
        snapshot = capture(normalized['share_url'], host)
        if snapshot['meta']['image_sha256'] != normalized['ogp']['image_sha256'] or snapshot['meta']['og:image'] != normalized['ogp']['image_url']:
            raise ValueError('新しい正本のOGP実画像が不一致です')
        source = {'request_key': key, 'operation': operation, 'input_hash': input_hash,
                  'result_sha256': normalized['result_sha256'], 'state_version': final_version,
                  'result_version': result['result_version'], 'ogp_revision': result['ogp']['revision'],
                  'ogp_source_version': normalized['source_version'], 'ogp_image_url': normalized['ogp']['image_url'],
                  'ogp_image_sha256': normalized['ogp']['image_sha256'], 'readiness_sha256': readiness_hash}
        if slot != 'daily':
            source['stored_image_sha256'] = result['stored_image_sha256']
        # API・実体の検証は完了。AIレビューは未実施として保持し、まだ候補へ保存しない。
        candidates[slot] = Candidate(target, slot, normalized['comment'], normalized['api_text'], normalized['share_url'], menu,
                                     normalized['nutrition'], image_sha, snapshot['digest'], 'pass', 'pending', True, True, True,
                                     source_commit, meal_count=3 if slot == 'daily' else 1,
                                     publish_text=normalized['publish_text'], approval_source=source)
    total = sum(m['nutrition']['calories'] for m in meals.values())
    if not 1800 <= total <= 2000:
        raise ValueError('修正後の3食合計が範囲外です')
    if validate_state(client.day_state(target), target, clock()) != final_version:
        raise ValueError('再点検開始前に日全体版が変更されています')
    return rereview_day(store, target, round_key, images, ai, client, host, output, clock, capture,
                        draft, source_commit, root, replacements=candidates, expected_previous=expected, evidence=evidence)
