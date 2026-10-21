"""稼働中SQLiteの整合したバックアップを秘密領域へ保存する。"""

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def backup_database(source, directory):
    """存在しないDBを作らず、失敗したコピーは復元候補にしない。"""
    source, directory = Path(source).resolve(), Path(directory)
    if not source.is_file() or directory.is_symlink():
        raise ValueError('バックアップ元・保存先が不正です')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (datetime.now(UTC).strftime('mina-%Y%m%dT%H%M%S%fZ') + '.sqlite3')
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=10)) as src, \
             closing(sqlite3.connect(path)) as dst:
            src.backup(dst, pages=256, sleep=0.05)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('バックアップの整合性を確認できません')
        return {'backup_complete': True}
    except Exception:
        path.unlink(missing_ok=True)
        raise RuntimeError('backup_failed') from None
