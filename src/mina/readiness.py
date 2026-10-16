"""履歴・旧予約・配布コードの証跡から、点検済み4案を候補へ変換する。"""

import hashlib
import json
import re
import subprocess
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from .domain import ACCOUNT, Candidate, SLOTS, instant


def read_json(path):
    """過大な資料とシンボリックリンクを拒否する。"""
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 131072:
        raise ValueError('証跡ファイルの形式・容量が不正です')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def release_commit(root, commit):
    """実装ファイルを実在commitと比較し、未コミットの版を証跡にしない。"""
    root = Path(root).resolve()
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('実装commitが必要です')
    # root所有のサービス配置を専用ユーザーから読む。この呼出しの正確な配置先だけを信頼する。
    git_command=['git','-c','safe.directory='+root.as_posix(),'-C',str(root)]
    tree = subprocess.run([*git_command, 'ls-tree', '-r', '--name-only', commit],
                          capture_output=True, timeout=10)
    if tree.returncode:
        raise ValueError('実装commitを確認できません')
    protected = lambda name: (name.startswith('src/mina/') and name.endswith('.py')) or name.startswith('deploy/') or name in ('src/__init__.py', 'requirements-mina.txt')
    expected = {name for name in tree.stdout.decode('utf-8').splitlines() if protected(name)}
    actual = {path.relative_to(root).as_posix() for path in (root / 'src' / 'mina').rglob('*.py')}
    actual |= {path.relative_to(root).as_posix() for path in (root / 'deploy').rglob('*') if path.is_file()}
    actual |= {name for name in ('src/__init__.py', 'requirements-mina.txt') if (root / name).is_file()}
    if len(expected) < 2 or actual != expected:
        raise ValueError('配布コードの追加・欠落を検出しました')
    paths = [root / name for name in sorted(expected)]
    if any(parent.is_symlink() for path in paths for parent in (path, *path.parents) if parent != root.parent):
        raise ValueError('配布コードのリンクを拒否しました')
    if len(paths) < 2:
        raise ValueError('配布コードを確認できません')
    for path in paths:
        relative = path.relative_to(root).as_posix()
        result = subprocess.run([*git_command, 'show', f'{commit}:{relative}'],
                                capture_output=True, timeout=10)
        if result.returncode or path.is_symlink() or result.stdout.replace(b'\r\n', b'\n') != path.read_bytes().replace(b'\r\n', b'\n'):
            raise ValueError('配布コードが指定commitと一致しません')
    return commit


def check_evidence(evidence, target, now):
    """履歴の完全性は人の確認証跡を要求する。空一覧を未取得の代用にしない。"""
    day = date.fromisoformat(target)
    if evidence.get('account') != ACCOUNT or evidence.get('target_date') != target:
        raise ValueError('確認証跡の対象が一致しません')
    checked = instant(datetime.fromisoformat(evidence['checked_at']))
    if not timedelta(0) <= instant(now) - checked <= timedelta(hours=24):
        raise ValueError('履歴・予約確認の証跡が期限切れです')
    for key in ('reviewer', 'history_source', 'schedule_source'):
        if not isinstance(evidence.get(key), str) or not evidence[key].strip():
            raise ValueError('確認者と参照元が必要です')
    if evidence.get('history_complete') is not True or evidence.get('legacy_schedule_clear') is not True:
        raise ValueError('履歴の完全性・旧予約の確認が未完了です')
    if evidence.get('history_from') != (day - timedelta(days=14)).isoformat() or evidence.get('history_to') != (day - timedelta(days=1)).isoformat():
        raise ValueError('直前14日間の履歴が必要です')
    entries = evidence.get('entries')
    if not isinstance(entries, list) or len(entries) > 100:
        raise ValueError('履歴一覧が必要です')
    for entry in entries:
        d = date.fromisoformat(entry['date'])
        if not day - timedelta(days=14) <= d < day or any(not isinstance(entry.get(k), str) or not entry[k].strip() for k in ('comment', 'menu')):
            raise ValueError('履歴の範囲・献立・コメントが不正です')
    return entries


def build_candidates(reviewed, evidence, commit, host, now, excluded_slots=(), lunch_after_approval_until=None):
    """APIの完成本文を変更せず、確認証跡を承認対象のハッシュへ含める。"""
    target = reviewed['target_date']
    if excluded_slots:
        from .domain import JST, slot_time
        if (set(excluded_slots) != {'breakfast'} or target != instant(now).astimezone(JST).date().isoformat()
                or instant(now) < slot_time(date.fromisoformat(target), 'breakfast')):
            raise ValueError('当日の時間を過ぎた朝食だけを公開対象外にできます')
    entries = check_evidence(evidence, target, now)
    if reviewed.get('state') != 'posts_reviewed' or set(reviewed.get('posts', {})) != set(SLOTS):
        raise ValueError('点検済みの4投稿が必要です')
    comments = [e['comment'] for e in entries]
    weekly = [e['comment'] for e in entries if date.fromisoformat(e['date']) >= date.fromisoformat(target) - timedelta(days=7)]
    menus = {e['menu'] for e in entries}
    evidence_hash = hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    candidates = []
    fields = ('comment', 'api_text', 'share_url', 'menu', 'nutrition', 'image_sha256',
              'ogp_sha256', 'api_quality', 'secondary_review', 'ogp_verified', 'publish_text')
    for slot in SLOTS:
        post = reviewed['posts'][slot]
        if slot != 'daily' and post['menu'] in menus:
            raise ValueError('直前14日間の献立と重複しています')
        if slot in excluded_slots:
            continue
        source = {**post['approval_source'], 'readiness_sha256': evidence_hash,
                  'ogp_image_url': post['ogp']['image_url'], 'ogp_image_sha256': post['ogp']['image_sha256']}
        if lunch_after_approval_until and slot=='lunch':
            if set(excluded_slots) != {'breakfast'}:
                raise ValueError('当日朝食除外時だけ昼食を臨時公開できます')
            source['publish_after_approval_until'] = lunch_after_approval_until
        candidate = Candidate(target_date=target, slot=slot, **{k: post[k] for k in fields},
                              approval_source=source, source_commit=commit,
                              history_verified=True, legacy_schedule_clear=True,
                              meal_count=3 if slot == 'daily' else 1)
        candidate.validate(host, now, comments, weekly)
        comments.append(candidate.comment)
        weekly.append(candidate.comment)
        candidates.append(asdict(candidate))
    if len({c['approval_source']['state_version'] for c in candidates}) != 1:
        raise ValueError('4投稿の最終状態版が一致しません')
    return candidates
