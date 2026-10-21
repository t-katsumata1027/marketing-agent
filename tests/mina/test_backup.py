"""バックアップが整合したコピーを作り、元台帳を変更しないことを確認する。"""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.mina.backup import backup_database


class BackupTest(unittest.TestCase):
    def test_live_database_copy_preserves_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'state.sqlite3'
            with closing(sqlite3.connect(source)) as db:
                db.execute('CREATE TABLE sample(value TEXT)')
                db.execute("INSERT INTO sample VALUES('確認済み')")
                db.commit()
            before = source.read_bytes()
            self.assertTrue(backup_database(source, root / 'copies')['backup_complete'])
            copy = next((root / 'copies').glob('*.sqlite3'))
            with closing(sqlite3.connect(copy)) as db:
                self.assertEqual(db.execute('SELECT value FROM sample').fetchone()[0], '確認済み')
            self.assertEqual(source.read_bytes(), before)

    def test_missing_source_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'missing.sqlite3'
            with self.assertRaises(ValueError):
                backup_database(path, Path(tmp) / 'copies')
            self.assertFalse(path.exists())
