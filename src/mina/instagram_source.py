"""既存X用の確認済み成果物を読取だけで再利用する。食事登録・解析は行わない。"""

from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from .domain import Candidate, SLOTS
from .instagram_domain import digest, nutrition
from .m1_contract import validate_state


def load_meal_bundle(database, directory, target, state, now=None,artifact_directory=None):
    """現在API・X候補・検査済み原本画像が一致した日だけ取り出す。"""
    now = now or datetime.now(UTC)
    version = validate_state(state, target, now)
    base = Path(directory).resolve() / target
    from .replacement_artifacts import resolve
    if artifact_directory is None:base = resolve(base)
    else:
        selected=Path(artifact_directory)
        if selected.is_symlink() or not selected.resolve().is_relative_to(base.resolve()):raise ValueError('replacement_artifact_escape')
        base=selected.resolve()
    saved_file = base / 'drive-final.json'
    if not saved_file.exists():
        saved_file = base / 'drive.json'
    saved = json.loads(saved_file.read_text(encoding='utf-8'))
    if saved.get('target_date') != target:
        raise ValueError('対象日の画像正本がありません')
    db = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute('SELECT slot,payload,digest,state FROM candidates WHERE target_date=?', (target,)).fetchall()
        steps = {}
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='preparation_steps'").fetchone():
            steps = {r['step']: json.loads(r['result']) for r in db.execute(
                "SELECT step,result FROM preparation_steps WHERE target_date=? AND state='complete' AND (step LIKE 'image:%' OR step LIKE 'image_review:%')", (target,))}
    finally:
        db.close()
    if {r['slot'] for r in rows} != set(SLOTS) or len(rows) != 4 or state.get('meal_count') != 3:
        raise ValueError('同じ日の確認済み3食と日次が必要です')
    current = {}
    for value in state['meals']:
        slot = value.get('meal', {}).get('meal_type')
        if slot in current:
            raise ValueError('同じ食事区分が重複しています')
        current[slot] = value
    current['daily'] = state.get('daily_share')
    bundle = {'target_date': target, 'state_version': version, 'meals': {}, 'x_candidates': {}, 'x_digests': {}}
    for row in rows:
        c = Candidate(**json.loads(row['payload']))
        if (c.digest != row['digest'] or row['state'] not in ('approval_pending', 'approved', 'published')
                or c.api_quality != 'pass' or c.secondary_review != 'publishable' or
                not all((c.ogp_verified, c.history_verified, c.legacy_schedule_clear)) or
                not c.approval_source or c.approval_source.get('state_version') != version):
            raise ValueError('X用候補と現在の日版・品質状態が一致しません')
        result = current.get(c.slot)
        if not isinstance(result, dict) or digest(result) != c.approval_source['result_sha256']:
            # 状態APIの互換用追加キーは完成resultのハッシュへ含めない。
            if not isinstance(result, dict):
                raise ValueError('現在の食事正本がありません')
            expected = {k: result[k] for k in ('normalized_comment', 'image_sha256', 'stored_image_sha256',
                         'analysis', 'meal', 'quality', 'share', 'x', 'result_version', 'ogp') if k in result}
            if c.slot == 'daily':
                expected = {k: result[k] for k in ('normalized_comment', 'date', 'meal_count', 'summary',
                            'share', 'x', 'result_version', 'source_state_version', 'ogp') if k in result}
            if digest(expected) != c.approval_source['result_sha256']:
                raise ValueError('X用候補と現在のAPI結果が一致しません')
        bundle['x_candidates'][c.slot] = asdict(c)
        bundle['x_digests'][c.slot] = c.digest
        if c.slot != 'daily':
            info = saved['images'][c.slot]
            if info.get('sha256') != c.image_sha256:
                # 修復後も元のdrive.jsonを保持する運用では、採用画像に一致する完成工程を読む。
                # 未採用画像を最新時刻やファイル名だけで選ばない。
                matches = []
                prefix = 'image:' + c.slot
                for step, image in steps.items():
                    if (step == prefix or step.startswith(prefix + ':')) and image.get('sha256') == c.image_sha256:
                        review = steps.get('image_review:' + step[len('image:'):])
                        if review == {'status': 'image_acceptable', 'reason_codes': []}:
                            matches.append({**image, 'image_review': review, 'proof': 'completed_image_journal'})
                if not matches:
                    raise ValueError('採用済み画像と完成・点検工程が対応していません')
                info = matches[0]
            path = Path(info['path'])
            if path.is_symlink() or not path.resolve().is_relative_to(base):
                raise ValueError('画像の参照先が対象日の保存領域外です')
            raw = path.read_bytes()
            if (hashlib.sha256(raw).hexdigest() != c.image_sha256 or info['sha256'] != c.image_sha256
                    or (info.get('proof') != 'completed_image_journal' and
                        (info['drive'].get('verified') is not True or info['drive']['sha256'] != c.image_sha256))
                    or info['image_review'] != {'status': 'image_acceptable', 'reason_codes': []}):
                raise ValueError('既存の料理画像と点検・保存証跡が一致しません')
            bundle['meals'][c.slot] = {'menu': c.menu, 'comment': c.comment, 'nutrition': nutrition(c.nutrition),
                                      'image_path': str(path), 'image_sha256': c.image_sha256}
        else:
            bundle['total'] = nutrition(c.nutrition)
    for key in bundle['total']:
        if abs(sum(v['nutrition'][key] for v in bundle['meals'].values()) - bundle['total'][key]) > 1:
            raise ValueError('APIの日次合計と同じ3食が対応していません')
    summary = state.get('summary', {})
    totals = nutrition({key: summary.get(api_key) for key, api_key in
                        (('calories', 'totalCalories'), ('protein', 'totalProtein'),
                         ('fat', 'totalFat'), ('carbs', 'totalCarbs'))})
    if any(abs(totals[key] - bundle['total'][key]) > 0.000001 for key in totals):
        raise ValueError('現在APIの集計値とX用日次の正本が一致しません')
    bundle['total'] = totals
    return bundle


def load_bundle(database, directory, target, state, now=None,artifact_directory=None):
    """目標も含めた公開用正本。API未提供と未設定を取り違えない。"""
    bundle = load_meal_bundle(database, directory, target, state, now,artifact_directory=artifact_directory)
    targets = state.get('nutrition_targets')
    if not isinstance(targets, dict) or set(targets) != {'calories', 'protein', 'fat', 'carbs'}:
        raise ValueError('logeats_nutrition_targets_not_exposed')
    if any(v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0)
           for v in targets.values()):
        raise ValueError('APIの目標値が不正です')
    bundle['targets'] = targets
    bundle['bundle_sha256'] = digest(bundle)
    return bundle


def verify_bundle(bundle, database, directory, state, now):
    current = load_bundle(database, directory, bundle['target_date'], state, now)
    if current['bundle_sha256'] != bundle['bundle_sha256']:
        raise ValueError('Instagram承認後にX用の正本が変わっています')
    return current
