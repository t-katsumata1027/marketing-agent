"""送信前に停止した未変更の正本を再点検し、新しい承認版へ戻す。"""

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

from .clients import MealClient
from .domain import Candidate, SLOTS
from .m1_contract import completed_request, verify_source, normalized_comment
from .generation import validate_plan
from .readiness import check_evidence, release_commit
from .preparation import PreparationJournal, fingerprint
from .runtime import import_manifest


def rereview_day(store, target, round_key, images, ai, client, host, output, clock, capture, draft, source_commit, root, replacements=None, expected_previous=None, evidence=None):
    """変更済み正本・送信開始後は拒否。正本へのPATCHや再登録は行わない。"""
    MealClient.request_key(round_key)
    if evidence is None:
        raise ValueError('再点検時の履歴・旧予約確認証跡が必要です')
    entries = check_evidence(evidence, target, clock())
    readiness_hash = hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    source_commit = release_commit(root, source_commit)
    if draft.get('target_date') != target:
        raise ValueError('同じ対象日の献立資料が必要です')
    validate_plan({'meals': draft['meals'], 'daily_comment': draft['daily_comment']})
    rows = store.list_day(target)
    if len(rows) != 4 or {row['slot'] for row in rows} != set(SLOTS):
        raise ValueError('同じ対象日の4候補が必要です')
    if any(row['state'] not in ('changes_requested', 'approval_pending') for row in rows):
        raise ValueError('送信開始済み・承認済み・取消済み候補を自動復帰しません')
    if expected_previous is not None and any(row['digest'] != expected_previous.get(row['slot']) for row in rows):
        raise ValueError('正本取得中に旧候補が差し替えられました')
    previous = {row['slot']: Candidate(**json.loads(row['payload'])) for row in rows}
    candidates = previous if replacements is None else replacements
    if set(candidates) != set(SLOTS) or any(c.target_date != target or c.slot != slot for slot, c in candidates.items()):
        raise ValueError('新しい正本の4枠が不一致です')
    for slot, c in candidates.items():
        comment = draft['daily_comment'] if slot == 'daily' else draft['meals'][slot]['comment']
        if normalized_comment(comment) != c.comment or (slot != 'daily' and draft['meals'][slot]['menu'] != c.menu):
            raise ValueError('献立資料と再点検候補が不一致です')
        if any(e['comment'] == c.comment or (slot != 'daily' and e['menu'] == c.menu) for e in entries):
            raise ValueError('再点検候補が履歴と重複しています')
    if set(images) != set(SLOTS) - {'daily'}:
        raise ValueError('原本画像3枚が必要です')
    raw_images = {}
    for slot, path in images.items():
        path = Path(path)
        if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('原本画像の形式・容量が不正です')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != candidates[slot].image_sha256:
            raise ValueError('承認対象の原本画像が不一致です')
        raw_images[slot] = raw
    if candidates['daily'].image_sha256 != fingerprint({slot: candidates[slot].image_sha256 for slot in raw_images}):
        raise ValueError('日次の原本3画像が不一致です')
    journal = PreparationJournal(store)
    outputs = []
    for slot in SLOTS:
        c = candidates[slot]
        verify_source(c, client, host, capture, clock=clock)
        operation = c.approval_source.get('operation', 'daily_share' if slot == 'daily' else 'meal_register')
        lookup = client.request_state(c.approval_source['request_key'], operation)
        result = lookup['current_result']
        completed_request(lookup, c.approval_source['request_key'], operation, result)
        if fingerprint(result) != c.approval_source['result_sha256']:
            raise ValueError('再点検対象の正本が変更されています')
        snapshot = capture(c.share_url, host)
        if snapshot['digest'] != c.ogp_sha256:
            raise ValueError('再点検中にOGP実体が変更されています')
        def review():
            if slot != 'daily':
                outcome = ai.review_meal(raw_images[slot], draft['meals'][slot], result, snapshot['meta'])
                if outcome != {'status': 'publishable', 'reason_codes': []}:
                    raise ValueError('再点検の解析レビューが保留です')
            originals = list(raw_images.values()) if slot == 'daily' else [raw_images[slot]]
            outcome = ai.review_ogp([snapshot['review_image'], *originals],
                                    {**asdict(c), 'ogp': result['ogp']}, snapshot['meta'])
            if outcome != {'status': 'publishable', 'reason_codes': []}:
                raise ValueError('再点検のOGPレビューが保留です')
            return {'status': 'publishable'}
        journal.run(target, 'rereview:' + round_key + ':' + slot,
                    {'candidate_digest': c.digest, 'result': result, 'ogp_digest': snapshot['digest'],
                     'draft': draft, 'source_commit': source_commit}, review)
        verify_source(c, client, host, capture, clock=clock)
        outputs.append(asdict(replace(c, secondary_review='publishable', source_commit=source_commit,
                                     history_verified=True, legacy_schedule_clear=True,
                                     approval_source={**c.approval_source, 'review_round': round_key, 'readiness_sha256': readiness_hash})))
    # 長時間レビューで証跡が期限切れになった場合も、承認対象へ進めない。
    check_evidence(evidence, target, clock())
    from datetime import date, timedelta
    comments = [e['comment'] for e in entries]
    weekly = [e['comment'] for e in entries if date.fromisoformat(e['date']) >= date.fromisoformat(target) - timedelta(days=7)]
    for output_entry in outputs:
        c = Candidate(**output_entry)
        c.validate(host, clock(), comments, weekly)
        comments.append(c.comment)
        weekly.append(c.comment)
    # 点検中に元候補が差し替えられた場合も、全件の取り込みを中止する。
    with store.transaction() as db:
        current = {row['slot']: row for row in db.execute('SELECT * FROM candidates WHERE target_date=?', (target,))}
        if any(current[slot]['digest'] != previous[slot].digest or current[slot]['state'] not in ('changes_requested', 'approval_pending') for slot in SLOTS):
            raise ValueError('再点検中に候補または判断が変更されています')
        # 取り込みは別トランザクションのため、Store側でも期待版を検査する。
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as file:
        json.dump(outputs, file, ensure_ascii=False, indent=2)
    return import_manifest(store, output, host, clock(), expected_digests={slot: previous[slot].digest for slot in SLOTS})
