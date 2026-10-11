"""19時の翌日準備をまとめ、完成した4案だけを20時承認へ渡す。"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .domain import JST, approval_time, date, instant
from .generation import draft_day
from .preparation import PreparationJournal, prepare_images, save_images_to_drive
from .readiness import build_candidates, check_evidence, read_json, release_commit
from .registration import register_day
from .runtime import import_manifest


class DeadlineClient:
    """各外部工程の開始時に期限を確認する。期限後の追加登録を防ぐ。"""

    def __init__(self, client, check):
        self.client, self.check = client, check

    def __getattr__(self, name):
        value = getattr(self.client, name)
        if not callable(value):
            return value
        def invoke(*args, **kwargs):
            self.check()
            return value(*args, **kwargs)
        return invoke


def write_artifact(path, value):
    """完成成果物を上書きせず保存する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if read_json(path) != value:
            raise ValueError('既存成果物の版が変わっています')
        return
    with path.open('x', encoding='utf-8') as file:
        json.dump(value, file, ensure_ascii=False, indent=2)


def prepare_day(store, directory, history, evidence_path, commit, root, host, ai, drive, meal,
                clock=lambda: datetime.now(UTC)):
    """翌日だけを時間窓内で処理。再起動・競合実行では外部工程を重ねない。"""
    now = instant(clock())
    target = (now.astimezone(JST).date() + timedelta(days=1)).isoformat()
    deadline = approval_time(date.fromisoformat(target))
    if not deadline - timedelta(hours=1) <= now < deadline:
        raise ValueError('翌日準備の開始可能時間は19時から20時直前です')
    evidence = read_json(evidence_path)
    check_evidence(evidence, target, now)
    commit = release_commit(root, commit)
    history = Path(history)
    if history.is_symlink() or history.stat().st_size > 65536:
        raise ValueError('履歴資料の形式・容量が不正です')
    # 生成と照合は同じ構造化履歴を参照する。別資料の取り違えを防ぐ。
    if read_json(history) != evidence:
        raise ValueError('生成資料と確認証跡は同じ履歴JSONを指定してください')
    if not store.claim_preparation(target, now):
        with store.transaction() as db:
            state = db.execute('SELECT state FROM preparation WHERE target_date=?', (target,)).fetchone()['state']
        if state == 'complete':
            return {'target_date': target, 'state': 'already_complete'}
        raise ValueError('準備の成否照合が必要です。自動再実行しません')
    directory = Path(directory) / target
    directory.mkdir(parents=True, exist_ok=True)
    stage = 'draft'
    try:
        def within_deadline():
            if instant(clock()) >= deadline:
                raise ValueError('20時の準備期限を超過しました')
        ai, drive, meal = (DeadlineClient(client, within_deadline) for client in (ai, drive, meal))
        journal = PreparationJournal(store)
        draft_path = directory / 'draft.json'
        journal.run(target, 'daily_draft', {'history': evidence, 'commit': commit},
                    lambda: draft_day(target, history, draft_path, ai))
        draft = read_json(draft_path)
        within_deadline()
        stage = 'images'
        images = prepare_images(store, draft_path, directory / 'images', ai)
        write_artifact(directory / 'images-reviewed.json', images)
        within_deadline()
        stage = 'drive'
        saved = save_images_to_drive(store, images, drive)
        write_artifact(directory / 'drive.json', saved)
        within_deadline()
        stage = 'registration'
        from .ogp import capture_ogp
        def capture(url, allowed_host):
            within_deadline()
            return capture_ogp(url, allowed_host)
        reviewed = register_day(store, draft, saved, directory / 'ogp', meal, ai, host, capture=capture)
        write_artifact(directory / 'posts-reviewed.json', reviewed)
        within_deadline()
        stage = 'candidates'
        candidates = build_candidates(reviewed, evidence, commit, host, clock())
        # 全4件に対する正本・実OGP照合を取り込みの直前にも行う。
        from .domain import Candidate
        from .m1_contract import verify_source
        for payload in candidates:
            within_deadline()
            verify_source(Candidate(**payload), meal, host, capture_ogp, clock=clock)
        manifest = directory / 'candidates.json'
        write_artifact(manifest, candidates)
        within_deadline()
        import_manifest(store, manifest, host, clock())
        store.preparation_done(target)
        return {'target_date': target, 'state': 'approval_ready', 'post_count': 4}
    except Exception:
        # 秘密値や外部応答を保存せず、停止した工程だけを残す。
        with store.transaction() as db:
            db.execute("UPDATE preparation SET state=? WHERE target_date=?", ('held:' + stage, target))
        raise RuntimeError('daily_preparation_held:' + stage) from None
