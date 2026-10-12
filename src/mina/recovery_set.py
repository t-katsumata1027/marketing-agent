"""台帳・成果物・明示指定の認証を暗号化し、稼働先へ自動復元しない。"""

import hashlib
import json
import os
import re
import stat
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path
from pathlib import PurePosixPath

from .artifact_backup import checked_path, inventory
from .encrypted_backup import LIMIT, read_regular, seal_file, protect_directory


def create_recovery_set(database, artifacts, files, output, public_key, recipient, commit):
    """コピー中の成果物変更を拒否し、平文の一時コピーを公開領域へ残さない。"""
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('復旧コードの固定commitが必要です')
    database, output = checked_path(database), checked_path(output)
    if not database.is_file() or output.exists() or not output.parent.is_dir():
        raise ValueError('既存の台帳と新規の保護された保存先が必要です')
    if os.name == 'posix' and output.parent.stat().st_mode & 0o077:
        raise ValueError('復旧セットの保存先に第三者のアクセス権があります')
    if set(files) != {'service-env', 'doppler-env', 'codex-auth', 'history-bootstrap', 'cutover'}:
        raise ValueError('運用設定・認証・移行証跡の5ファイルが必要です')
    if not isinstance(artifacts, dict) or set(artifacts)-{'daily', 'history'}:
        raise ValueError('復旧成果物の対象が不正です')
    roots = {name: checked_path(path) for name, path in artifacts.items()}
    # 私有ファイルを読む前にも受信先の公開鍵を照合する。
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = serialization.load_pem_public_key(read_regular(public_key, 16384))
    if (not isinstance(key, rsa.RSAPublicKey) or key.key_size<3072 or
            hashlib.sha256(key.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()!=recipient):
        raise ValueError('復旧セットの受信先公開鍵が不一致です')
    before = {name: inventory(path) for name, path in roots.items()}
    protected = {name: checked_path(path) for name, path in files.items()}
    initial = {name: read_regular(path, LIMIT) for name, path in protected.items()}
    manifest = {'format': 'mina-recovery-set-v1', 'source_commit': commit,
                'live_restore_allowed': False, 'external_reconciliation_required': True,
                'files': [], 'artifact_roots': sorted(roots)}
    total = 0
    with tempfile.TemporaryDirectory(prefix='.mina-recovery-', dir=output.parent) as temporary:
        root = Path(temporary)
        protect_directory(root)
        snapshot = root/'database.sqlite3'
        deadline = time.monotonic()+60
        def progress(*_):
            if time.monotonic() > deadline:
                raise ValueError('台帳コピーの制限時間を超えました')
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=10)) as source, \
             closing(sqlite3.connect(snapshot)) as destination:
            if source.execute('PRAGMA page_count').fetchone()[0]*source.execute('PRAGMA page_size').fetchone()[0] > LIMIT:
                raise ValueError('台帳の復旧容量上限を超えました')
            source.backup(destination, pages=256, progress=progress, sleep=0.05)
            if destination.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('復旧台帳の整合性を確認できません')
        snapshot.chmod(0o600)
        archive = root/'recovery.zip'
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as writer:
            def add(name, raw):
                nonlocal total
                total += len(raw)
                if total > LIMIT or len(manifest['files']) >= 10000:
                    raise ValueError('復旧セットの容量・件数上限を超えました')
                writer.writestr(name, raw)
                manifest['files'].append({'path': name, 'bytes': len(raw),
                                          'sha256': hashlib.sha256(raw).hexdigest()})
            add('database.sqlite3', read_regular(snapshot, LIMIT))
            for name, raw in initial.items():
                add('protected/'+name, raw)
            for name, entries in before.items():
                for relative in sorted(entries):
                    add('artifacts/'+name+'/'+relative, read_regular(roots[name]/relative, LIMIT))
            writer.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
        for name, path in protected.items():
            if read_regular(path, LIMIT) != initial[name]:
                raise ValueError('運用設定・移行証跡がコピー中に変わりました')
        if {name: inventory(path) for name, path in roots.items()} != before:
            raise ValueError('成果物がコピー中に変わりました')
        report = seal_file(archive, output, public_key, recipient)
    return {**report, 'recovery_set_created': True, 'file_count': len(manifest['files']),
            'live_restore_performed': False, 'external_reconciliation_required': True}


def restore_recovery_set(archive, output):
    """全実体を検証してから、新規隔離先へ復元する。稼働台帳へは反映しない。"""
    archive, output = checked_path(archive), checked_path(output)
    if output.exists() or not output.parent.is_dir():
        raise ValueError('新規の隔離復元先が必要です')
    if os.name == 'posix' and output.parent.stat().st_mode & 0o077:
        raise ValueError('隔離復元先の親に第三者のアクセス権があります')
    raw = read_regular(archive, LIMIT)
    import io
    with zipfile.ZipFile(io.BytesIO(raw)) as reader:
        infos = reader.infolist()
        if not infos or len(infos)>10001 or sum(item.file_size for item in infos)>LIMIT+4*1024*1024:
            raise ValueError('復旧セットの展開容量・件数が不正です')
        names = set()
        for item in infos:
            name = item.filename
            path = PurePosixPath(name)
            mode = stat.S_IFMT(item.external_attr >> 16)
            if (path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or
                    path.as_posix()!=name or name in names or item.is_dir() or mode not in (0, stat.S_IFREG)):
                raise ValueError('復旧セットのパス・形式が不正です')
            names.add(name)
        if 'manifest.json' not in names or reader.getinfo('manifest.json').file_size>4*1024*1024:
            raise ValueError('復旧セットのmanifestが不正です')
        manifest = json.loads(reader.read('manifest.json'))
        if (manifest.get('format')!='mina-recovery-set-v1' or manifest.get('live_restore_allowed') is not False
                or manifest.get('external_reconciliation_required') is not True):
            raise ValueError('復旧セットの契約が不正です')
        required = {'database.sqlite3', *(f'protected/{name}' for name in
                   ('service-env','doppler-env','codex-auth','history-bootstrap','cutover'))}
        entries = manifest.get('files')
        if not isinstance(entries,list) or len(entries)>10000:
            raise ValueError('復旧セットの実体一覧が不正です')
        expected = set()
        for entry in entries:
            name = entry['path']
            if name in expected or name not in names or (name not in required and not name.startswith(('artifacts/daily/','artifacts/history/'))):
                raise ValueError('復旧セットの保存範囲が不正です')
            if type(entry['bytes']) is not int or not 0<entry['bytes']<=LIMIT or reader.getinfo(name).file_size!=entry['bytes']:
                raise ValueError('復旧セットの容量が不一致です')
            if hashlib.sha256(reader.read(name)).hexdigest()!=entry['sha256']:
                raise ValueError('復旧セットのハッシュが不一致です')
            expected.add(name)
        if names != expected|{'manifest.json'} or not required<=expected:
            raise ValueError('復旧セットの不足・余分な実体を検出しました')
        output.mkdir(mode=0o700)
        try:
            protect_directory(output)
            for name in sorted(names):
                path = output/name
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as writer:
                    writer.write(reader.read(name))
                    writer.flush();os.fsync(writer.fileno())
            with closing(sqlite3.connect((output/'database.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
                if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
                    raise ValueError('隔離復元した台帳の整合性が不正です')
        except Exception:
            # 途中の復元も隔離先に残し、成功や稼働可能とは通知しない。
            raise ValueError('隔離復元は未完了です。途中の実体を稼働へ使用しないでください') from None
    return {'isolated_recovery_complete': True, 'file_count': len(entries),
            'live_restore_performed': False, 'production_start_allowed': False,
            'external_reconciliation_required': True}
