"""実SQLite・成果物・合成認証の暗号化往復と、隔離以外への復元拒否を確認する。"""
import hashlib
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from src.mina.encrypted_backup import create_recovery_keys,unseal_file
from src.mina.recovery_set import create_recovery_set,restore_recovery_set


class RecoverySetTest(unittest.TestCase):
    def test_encrypted_set_restores_database_artifacts_and_credentials_only_in_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            keys=create_recovery_keys(root/'keys',b'test-only-long-password')
            db=root/'database'
            with closing(sqlite3.connect(db)) as connection:
                connection.execute('CREATE TABLE requests(uuid TEXT, image BLOB)')
                connection.execute('INSERT INTO requests VALUES(?,?)',('test-only-uuid',b'original-image'))
                connection.commit()
            files={name:root/name for name in ('service-env','doppler-env','codex-auth','history-bootstrap','cutover')}
            for name,path in files.items():path.write_bytes(('test-only-'+name).encode())
            artifacts=root/'daily';artifacts.mkdir();(artifacts/'image.jpg').write_bytes(b'original-image')
            report=create_recovery_set(db,{'daily':artifacts},files,root/'keys/sealed',root/'keys/public.pem',keys['recipient_sha256'],'a'*40)
            self.assertFalse(report['live_restore_performed'])
            self.assertNotIn(b'test-only-doppler', (root/'keys/sealed').read_bytes())
            unseal_file(root/'keys/sealed',root/'keys/plain.zip',root/'keys/private-encrypted.pem',b'test-only-long-password')
            restored=restore_recovery_set(root/'keys/plain.zip',root/'keys/restored')
            self.assertFalse(restored['production_start_allowed'])
            self.assertEqual((root/'keys/restored/protected/doppler-env').read_bytes(),files['doppler-env'].read_bytes())
            self.assertEqual((root/'keys/restored/artifacts/daily/image.jpg').read_bytes(),b'original-image')
            with closing(sqlite3.connect(root/'keys/restored/database.sqlite3')) as connection:
                self.assertEqual(connection.execute('SELECT image FROM requests').fetchone()[0],b'original-image')
            with self.assertRaises(ValueError):restore_recovery_set(root/'keys/plain.zip',root/'keys/restored')

    def test_traversal_archive_creates_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with zipfile.ZipFile(root/'bad.zip','x') as writer:writer.writestr('../outside',b'unsafe')
            with self.assertRaises(ValueError):restore_recovery_set(root/'bad.zip',root/'restore')
            self.assertFalse((root/'restore').exists())

    def test_wrong_recipient_stops_before_reading_protected_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            create_recovery_keys(root/'keys',b'test-only-long-password')
            with closing(sqlite3.connect(root/'db')) as db:
                db.execute('CREATE TABLE state(value TEXT)')
            missing={name:root/('missing-'+name) for name in
                     ('service-env','doppler-env','codex-auth','history-bootstrap','cutover')}
            with self.assertRaisesRegex(ValueError,'受信先公開鍵'):
                create_recovery_set(root/'db',{},missing,root/'keys/sealed',root/'keys/public.pem','0'*64,'a'*40)
            self.assertFalse((root/'keys/sealed').exists())

    def test_hash_mismatch_is_rejected_before_restore_directory_is_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            manifest={'format':'mina-recovery-set-v1','live_restore_allowed':False,
                      'external_reconciliation_required':True,
                      'files':[{'path':'database.sqlite3','bytes':4,'sha256':'0'*64}]}
            with zipfile.ZipFile(root/'bad.zip','x') as writer:
                writer.writestr('manifest.json',json.dumps(manifest))
                writer.writestr('database.sqlite3',b'fake')
            with self.assertRaisesRegex(ValueError,'ハッシュ'):
                restore_recovery_set(root/'bad.zip',root/'restore')
            self.assertFalse((root/'restore').exists())
