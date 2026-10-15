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
                clock=lambda: datetime.now(UTC), same_day=False):
    """翌日だけを時間窓内で処理。再起動・競合実行では外部工程を重ねない。"""
    now = instant(clock())
    target = (now.astimezone(JST).date() + timedelta(days=0 if same_day else 1)).isoformat()
    from .domain import slot_time
    excluded_slots = ('breakfast',) if same_day else ()
    deadline = slot_time(date.fromisoformat(target), 'lunch') if same_day else approval_time(date.fromisoformat(target))
    start = slot_time(date.fromisoformat(target), 'breakfast') if same_day else deadline - timedelta(hours=1)
    if not start <= now < deadline:
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
    if same_day:
        # 当日朝食は登録・日次集計だけに使い、後から候補として追加することも拒否する。
        with store.transaction() as db:
            if db.execute('SELECT 1 FROM candidates WHERE target_date=?', (target,)).fetchone():
                raise ValueError('既存候補のある日は当日試行を開始しません')
            db.execute("INSERT INTO publication_exclusions VALUES(?,'breakfast','本人指定・時刻経過のためX非公開')", (target,))
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
        candidates = build_candidates(reviewed, evidence, commit, host, clock(), excluded_slots=excluded_slots)
        # 全4件に対する正本・実OGP照合を取り込みの直前にも行う。
        from .domain import Candidate
        from .m1_contract import verify_source
        for payload in candidates:
            within_deadline()
            verify_source(Candidate(**payload), meal, host, capture_ogp, clock=clock)
        manifest = directory / 'candidates.json'
        write_artifact(manifest, candidates)
        within_deadline()
        import_manifest(store, manifest, host, clock(), excluded_slots=excluded_slots)
        store.preparation_done(target)
        return {'target_date': target, 'state': 'approval_ready', 'post_count': len(candidates)}
    except Exception:
        # 秘密値や外部応答を保存せず、停止した工程だけを残す。
        with store.transaction() as db:
            db.execute("UPDATE preparation SET state=? WHERE target_date=?", ('held:' + stage, target))
        raise RuntimeError('daily_preparation_held:' + stage) from None


def resume_today_read_failure(store, directory, commit, root, host, ai, meal, round_key,
                              clock=lambda: datetime.now(UTC), history_directory=None, allow_correction=False):
    """朝食保存完了後のOGP読取失敗だけを、原記録を残した明示的な別工程で再照合する。"""
    from .clients import MealClient
    from .domain import Candidate, slot_time
    from .m1_contract import verify_source
    from .ogp import capture_ogp
    from .preparation import fingerprint
    MealClient.request_key(round_key)
    now = instant(clock())
    day = now.astimezone(JST).date()
    target = day.isoformat()
    deadline = slot_time(day, 'lunch')
    if not slot_time(day, 'breakfast') <= now < deadline:
        raise ValueError('当日再照合は朝食時刻経過後から昼食前までです')
    commit = release_commit(root, commit)
    base = Path(directory) / target
    draft, saved = read_json(base/'draft.json'), read_json(base/'drive.json')
    history_files = list(Path(history_directory or '/var/lib/mina-agent/history').glob('*.json'))
    evidence = None
    for path in sorted(history_files, reverse=True):
        value = read_json(path)
        if value.get('target_date') == target:
            evidence = value
            break
    check_evidence(evidence or {}, target, now)
    with store.transaction() as db:
        row = db.execute('SELECT state FROM preparation WHERE target_date=?', (target,)).fetchone()
        if (not row or row['state'] not in ('held:registration', 'held:read_recovery') or
                db.execute('SELECT 1 FROM candidates WHERE target_date=?', (target,)).fetchone() or
                not db.execute("SELECT 1 FROM publication_exclusions WHERE target_date=? AND slot='breakfast'", (target,)).fetchone()):
            raise ValueError('当日朝食のOGP読取停止からだけ再照合できます')
        pending = list(db.execute("SELECT step FROM preparation_steps WHERE target_date=? AND state<>'complete'", (target,)))
        requests = list(db.execute('SELECT slot,operation,phase FROM mina_requests WHERE target_date=?', (target,)))
        review_started = db.execute("SELECT 1 FROM preparation_steps WHERE target_date=? AND (step LIKE 'meal_review:%' OR step LIKE 'ogp_review:%')", (target,)).fetchone()
        correction_allowed = False
        request_rows = [tuple(r) for r in requests]
        original_only = request_rows == [('breakfast','meal_register','complete')]
        known_correction = (len(request_rows) == 2 and ('breakfast','meal_register','complete') in request_rows and
            any(row in request_rows for row in [('breakfast','meal_correction','response_received'),('breakfast','meal_correction','complete')]) and
            db.execute("SELECT 1 FROM preparation_steps WHERE target_date=? AND step='meal_correction:breakfast' AND state='complete'", (target,)).fetchone())
        if allow_correction:
            held = db.execute("SELECT result FROM preparation_steps WHERE target_date=? AND step='meal_review:breakfast' AND state='complete'", (target,)).fetchone()
            outcome = json.loads(held[0]) if held else {}
            correction_allowed = (outcome.get('status') == 'hold' and bool(outcome.get('reason_codes')) and
                set(outcome['reason_codes']) <= {'image_analysis_mismatch','nutrition_semantic_mismatch'} and
                not db.execute("SELECT 1 FROM preparation_steps WHERE target_date=? AND (step LIKE 'ogp_review:%' OR step='meal_review:breakfast:corrected')", (target,)).fetchone() and
                (original_only or known_correction))
            if not correction_allowed:
                raise ValueError('食品・量の誤認による保留だけを一度訂正できます')
        if (not pending or any(not r[0].startswith(('ogp_capture:breakfast','read_recovery:')) for r in pending) or
                (review_started and not correction_allowed) or
                not (original_only or (allow_correction and known_correction))):
            raise ValueError('保存成否不明や未完了の書込みを再開しません')
        db.execute("UPDATE preparation SET state='read_recovery_started' WHERE target_date=?", (target,))
    out = base / ('read-recovery-' + round_key)
    out.mkdir(mode=0o700)
    def within_deadline():
        if instant(clock()) >= deadline:
            raise ValueError('昼食前の準備期限を超過しました')
    try:
        # 新しい画像・朝食UUIDは生成せず、完了済みの同一入力・原本を既存台帳から再使用する。
        journal = PreparationJournal(store)
        reviewed = journal.run(target, 'read_recovery:' + round_key,
            {'draft': fingerprint(draft), 'saved': fingerprint(saved), 'commit': commit, 'allow_correction':allow_correction},
            lambda: register_day(store, draft, saved, out/'ogp', DeadlineClient(meal, within_deadline),
                                 DeadlineClient(ai, within_deadline), host, capture_round=round_key, allow_correction=allow_correction))
        write_artifact(out/'posts-reviewed.json', reviewed)
        candidates = build_candidates(reviewed, evidence, commit, host, clock(), excluded_slots=('breakfast',))
        for payload in candidates:
            within_deadline()
            verify_source(Candidate(**payload), meal, host, capture_ogp, clock=clock)
        manifest = out/'candidates.json'
        write_artifact(manifest, candidates)
        within_deadline()
        import_manifest(store, manifest, host, clock(), excluded_slots=('breakfast',))
        store.preparation_done(target)
        return {'target_date':target, 'state':'approval_ready', 'post_count':3}
    except Exception:
        with store.transaction() as db:
            db.execute("UPDATE preparation SET state='held:read_recovery' WHERE target_date=?", (target,))
        raise RuntimeError('当日の読取再照合を保留しました。自動再実行しません') from None
