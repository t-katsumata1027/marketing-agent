"""登録前の画像保留を、旧判定を残した一度限りの明示復旧で再開する。"""

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from .clients import MealClient
from .daily_pipeline import DeadlineClient, write_artifact
from .domain import Candidate, JST, instant
from .m1_contract import verify_source
from .ogp import capture_ogp
from .preparation import PreparationJournal, fingerprint, prepare_images, save_images_to_drive
from .readiness import build_candidates, check_evidence, read_json, release_commit
from .registration import register_day
from .runtime import import_manifest


def resume_image_hold(store, directory, target, round_key, root, commit, host, ai, drive, meal,
                      history_directory, clock=lambda: datetime.now(UTC), capture=capture_ogp):
    """画像保留だけを修復し、保存・登録済み工程や未知状態があれば拒否する。"""
    MealClient.request_key(round_key)
    local = instant(clock()).astimezone(JST)
    if target != (local.date() + timedelta(days=1)).isoformat() or not time(19) <= local.time() < time(23):
        raise ValueError('翌日画像の明示復旧は当日19時から23時前までです')
    commit = release_commit(root, commit)
    base = Path(directory) / target
    draft = read_json(base / 'draft.json')
    if draft.get('target_date') != target:
        raise ValueError('同じ対象日の元献立が必要です')
    evidence = next((read_json(p) for p in sorted(Path(history_directory).glob('*.json'), reverse=True)
                     if read_json(p).get('target_date') == target), {})
    check_evidence(evidence, target, clock())
    with store.transaction() as db:
        prep = db.execute('SELECT state FROM preparation WHERE target_date=?', (target,)).fetchone()
        rows = list(db.execute('SELECT step,state,result FROM preparation_steps WHERE target_date=?', (target,)))
        has_requests = (db.execute("SELECT 1 FROM sqlite_master WHERE name='mina_requests'").fetchone() and
                        db.execute('SELECT 1 FROM mina_requests WHERE target_date=?', (target,)).fetchone())
        if (not prep or prep['state'] != 'held:images' or not rows or
                any(r['state'] != 'complete' for r in rows) or
                any(not (r['step'] == 'daily_draft' or r['step'].startswith(('image:', 'image_review:'))) for r in rows) or
                has_requests or
                db.execute('SELECT 1 FROM candidates WHERE target_date=?', (target,)).fetchone() or
                (base / 'drive.json').exists()):
            raise ValueError('登録前の画像保留以外は復旧しません。既存記録の照合が必要です')
        holds = [r for r in rows if r['step'].startswith('image_review:') and json.loads(r['result']).get('status') == 'hold']
        if len(holds) != 1 or json.loads(holds[0]['result']).get('reason_codes') != ['image_analysis_mismatch']:
            raise ValueError('構図不一致の画像一件だけを明示復旧できます')
        slot = holds[0]['step'].split(':')[1]
        if not any(r['step'] == 'image:' + slot for r in rows):
            raise ValueError('保留画像の原本記録がありません')
        # 成功済み画像も含め、外部処理より前に全原本の改変を拒否する。
        for row in rows:
            if row['step'].startswith('image:'):
                image = json.loads(row['result'])
                path = Path(image['path'])
                if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != image['sha256'] or path.stat().st_size != image['size']:
                    raise ValueError('保存した原本画像が変更されています')
        db.execute("UPDATE preparation SET state='resuming:images' WHERE target_date=?", (target,))
    deadline = datetime.combine(local.date(), time(23), JST)
    def within_deadline():
        if instant(clock()) >= instant(deadline):
            raise ValueError('本人指定の画像復旧期限を過ぎました')
    ai, drive, meal = (DeadlineClient(c, within_deadline) for c in (ai, drive, meal))
    def checked_capture(url, allowed_host):
        within_deadline()
        return capture(url, allowed_host)
    journal = PreparationJournal(store)
    def recover():
        images = prepare_images(store, base / 'draft.json', base / 'images', ai,
                                replacement_round=round_key, replace_slots=(slot,))
        write_artifact(base / 'images-reviewed.json', images)
        saved = save_images_to_drive(store, images, drive)
        write_artifact(base / 'drive.json', saved)
        reviewed = register_day(store, draft, saved, base / 'ogp', meal, ai, host,
                                capture=checked_capture, allow_correction=True)
        write_artifact(base / 'posts-reviewed.json', reviewed)
        check_evidence(evidence, target, clock())
        candidates = build_candidates(reviewed, evidence, commit, host, clock())
        for payload in candidates:
            within_deadline()
            verify_source(Candidate(**payload), meal, host, checked_capture, clock=clock)
        manifest = base / 'candidates.json'
        write_artifact(manifest, candidates)
        within_deadline()
        import_manifest(store, manifest, host, clock())
        store.preparation_done(target)
        return {'target_date': target, 'state': 'approval_ready', 'post_count': 4}
    try:
        return journal.run(target, 'explicit_image_resume:' + round_key,
                           {'draft': fingerprint(draft), 'commit': commit, 'slot': slot}, recover)
    except Exception as error:
        with store.transaction() as db:
            db.execute("UPDATE preparation SET state='held:image_resume' WHERE target_date=?", (target,))
        store.record_preparation_failure(target, 'images', error, clock())
        raise RuntimeError('画像復旧を保留しました。自動反復しません') from None
