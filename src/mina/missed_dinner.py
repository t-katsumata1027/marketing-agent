"""公開前に停止した当日夕食を、独立再点検後の新しい本人承認へ戻す。"""
import hashlib
import json
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path

from .clients import MealClient
from .domain import Candidate, JST, slot_time
from .m1_contract import completed_request, verify_source
from .ogp import capture_ogp
from .preparation import PreparationJournal, fingerprint
from .readiness import check_evidence, read_json, release_commit


def recover_missed_dinner(store, target, round_key, draft_path, images_path, evidence, root, commit,
                          host, ai, client, clock, capture=capture_ogp):
    """他の枠を変えず、未送信の夕食だけを本日21:15までの再承認へ渡す。"""
    MealClient.request_key(round_key)
    now = clock()
    day = date.fromisoformat(target)
    until = slot_time(day,'daily') - timedelta(minutes=15)
    if now.astimezone(JST).date() != day or not slot_time(day,'dinner') <= now < until:
        raise ValueError('本日の夕食だけを21:15前までに復旧できます')
    entries = check_evidence(evidence,target,now)
    commit = release_commit(root,commit)
    row = store.get(target,'dinner')
    if not row or row['state'] != 'changes_requested' or row['reason'] != 'preflight_failed' or row['x_id']:
        raise ValueError('公開前停止の夕食以外は復旧しません')
    original = Candidate(**json.loads(row['payload']))
    draft, saved = read_json(draft_path), read_json(images_path)
    meal = draft['meals']['dinner']
    if (draft.get('target_date') != target or saved.get('target_date') != target or
            meal['menu'] != original.menu or meal['comment'] != original.comment or
            any(e['comment'] == original.comment or e['menu'] == original.menu for e in entries)):
        raise ValueError('夕食の元資料または履歴が一致しません')
    image = saved['images']['dinner']
    path = Path(image['path'])
    raw = path.read_bytes()
    if path.is_symlink() or hashlib.sha256(raw).hexdigest() != original.image_sha256:
        raise ValueError('夕食の原本画像が一致しません')
    verify_source(original,client,host,capture,clock=clock)
    binding = original.approval_source
    lookup = client.request_state(binding['request_key'],binding['operation'])
    result = lookup['current_result']
    completed_request(lookup,binding['request_key'],binding['operation'],result)
    snapshot = capture(original.share_url,host)
    if snapshot['digest'] != original.ogp_sha256:
        raise ValueError('夕食のOGPが変更されています')
    def review():
        food = ai.review_meal(raw,meal,result,snapshot['meta'])
        if food != {'status':'publishable','reason_codes':[]}:
            raise ValueError('夕食の食品再点検が保留です')
        ogp = ai.review_ogp([snapshot['review_image'],raw],{**asdict(original),'ogp':result['ogp']},snapshot['meta'])
        if ogp != {'status':'publishable','reason_codes':[]}:
            raise ValueError('夕食の実OGP再点検が保留です')
        return {'status':'publishable','reason_codes':[]}
    PreparationJournal(store).run(target,'missed_dinner_review:'+round_key,
        {'candidate_digest':original.digest,'result_sha256':fingerprint(result),'ogp_digest':snapshot['digest'],'source_commit':commit},review)
    verify_source(original,client,host,capture,clock=clock)
    check_evidence(evidence,target,clock())
    replacement = replace(original,source_commit=commit,approval_source={**binding,'review_round':round_key,
        'readiness_sha256':fingerprint(evidence),'publish_after_approval_until':until.isoformat()})
    revision = store.requeue_failed_dinner(replacement,host,clock(),original.digest,round_key)
    store.issue_cards(target,clock(),slots=('dinner',))
    return {'target_date':target,'slot':'dinner','revision':revision,'state':'approval_pending','approval_deadline_jst':until.astimezone(JST).isoformat()}
