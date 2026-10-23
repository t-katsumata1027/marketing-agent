"""画像・工程成果物を保護された別ディレクトリへ固定し、復元候補を検査する。"""

import hashlib
import json
import os
import shutil
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

MAX_FILES = 10000
MAX_BYTES = 256 * 1024 * 1024
FORBIDDEN = {'.env', 'auth.json', 'mina.env', 'doppler.env'}


def checked_path(path):
    """元のパスでリンクを確認し、解決によるリンク検査の回避を防ぐ。"""
    path = Path(path).absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('成果物のリンクを拒否しました')
    return path.resolve()


def inventory(source):
    """特殊ファイル・リンク・過大な成果物・認証ファイルを拒否する。"""
    source = checked_path(source)
    if not source.is_dir():
        raise ValueError('成果物ディレクトリを確認できません')
    entries = {}
    for directory, dirs, files in os.walk(source, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink() or name in FORBIDDEN or name.startswith('.env.') or name in ('.codex', '.doppler'):
                raise ValueError('成果物以外の保護対象を拒否しました')
        for name in files:
            path = Path(directory) / name
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('通常ファイル以外を拒否しました')
            entries[path.relative_to(source).as_posix()] = (info.st_dev, info.st_ino, info.st_size,
                                                          info.st_mtime_ns, info.st_ctime_ns)
    if not entries or len(entries) > MAX_FILES or sum(info[2] for info in entries.values()) > MAX_BYTES:
        raise ValueError('成果物の件数・容量を確認できません')
    return entries


def backup_artifacts(source, directory):
    """全ファイルの前後状態を照合し、完成したコピーだけを復元候補にする。"""
    source, directory = checked_path(source), checked_path(directory)
    if not source.is_dir() or source == directory or source in directory.parents:
        raise ValueError('成果物の元・保存先が不正です')
    before = inventory(source)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    from .encrypted_backup import check_private_parent,protect_directory
    check_private_parent(directory)
    destination = directory / datetime.now(UTC).strftime('artifacts-%Y%m%dT%H%M%S%fZ')
    with tempfile.TemporaryDirectory(prefix='.incomplete-', dir=directory) as tmp:
        pending = Path(tmp) / 'snapshot'
        (pending / 'files').mkdir(parents=True, mode=0o700)
        protect_directory(pending)
        files = []
        for name in sorted(before):
            path = source / name
            copy = pending / 'files' / name
            copy.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as reader, copy.open('xb') as writer:
                os.chmod(copy, 0o600)
                digest = hashlib.sha256()
                count = 0
                while raw := reader.read(1024 * 1024):
                    count += len(raw)
                    if count > before[name][2]:
                        raise ValueError('成果物がコピー中に変化しました')
                    digest.update(raw)
                    writer.write(raw)
            if count != before[name][2]:
                raise ValueError('成果物がコピー中に変化しました')
            files.append({'path': name, 'bytes': count, 'sha256': digest.hexdigest()})
        if inventory(source) != before:
            raise ValueError('成果物一覧がコピー中に変化しました')
        manifest = pending / 'manifest.json'
        with manifest.open('x', encoding='utf-8') as writer:
            os.chmod(manifest, 0o600)
            json.dump({'format': 'mina-artifacts-v1', 'files': files}, writer, ensure_ascii=False)
        verify_artifacts(pending)
        # 新規ディレクトリだけを移動し、既存バックアップを置き換えない。
        if destination.exists():
            raise ValueError('既存バックアップを上書きしません')
        shutil.move(str(pending), str(destination))
    return {'artifact_backup_complete': True, 'file_count': len(files),
            'total_bytes': sum(item['bytes'] for item in files), 'authentication_files_excluded': True}


def verify_artifacts(snapshot):
    """自動復元せず、保存した実体とmanifestを照合する。"""
    snapshot = checked_path(snapshot)
    manifest_path = snapshot / 'manifest.json'
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('復元候補のmanifestが不正です')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('format') != 'mina-artifacts-v1' or not isinstance(manifest.get('files'), list):
        raise ValueError('復元候補の形式が不正です')
    entries = inventory(snapshot / 'files')
    expected = {}
    for item in manifest['files']:
        name = item['path']
        if not isinstance(name, str):
            raise ValueError('復元候補のパスが不正です')
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or path.as_posix() != name or name in expected:
            raise ValueError('復元候補のパスが不正です')
        expected[name] = item
    if set(entries) != set(expected):
        raise ValueError('復元候補の追加・欠落を検出しました')
    for name, info in entries.items():
        item = expected[name]
        if type(item['bytes']) is not int or item['bytes'] != info[2] or hashlib.sha256((snapshot/'files'/name).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('復元候補のハッシュが不一致です')
    return {'artifact_snapshot_verified': True, 'file_count': len(entries), 'live_restore_performed': False}
