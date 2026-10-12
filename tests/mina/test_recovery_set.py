"""実SQLite・成果物・合成認証の暗号化往復と、隔離以外への復元拒否を確認する。"""
import hashlib
import json
import sqlite3
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from contextlib import closing
from pathlib import Path
from src.mina.encrypted_backup import create_recovery_keys,unseal_file
from src.mina.recovery_set import create_recovery_set,restore_recovery_set
from src.mina.store import Store


class RecoverySetTest(unittest.TestCase):
    def test_encrypted_set_restores_database_artifacts_and_credentials_only_in_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            keys=create_recovery_keys(root/'keys',b'test-only-long-password')
            db=root/'database'
            with closing(sqlite3.connect(db)) as connection:
                connection.execute('CREATE TABLE requests(uuid TEXT, image BLOB)')
                connection.execute('INSERT INTO requests VALUES(?,?)',('test-only-uuid',b'original-image'))
                connection.execute('CREATE TABLE candidates(state TEXT,reason TEXT,actor TEXT,approved REAL,nonce_hash TEXT)')
                connection.executemany('INSERT INTO candidates VALUES(?,NULL,?,1,?)',
                    [(state,'test-actor','test-nonce') for state in ('approved','approval_pending','published','dispatching','outcome_unknown')])
                connection.execute('CREATE TABLE notices(state TEXT)')
                connection.executemany('INSERT INTO notices VALUES(?)',[('pending',),('sending',),('outcome_unknown',)])
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
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM candidates WHERE state IN ('approved','approval_pending')").fetchone()[0],0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM candidates WHERE state IN ('published','dispatching','outcome_unknown')").fetchone()[0],3)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM notices WHERE state='pending'").fetchone()[0],0)
            with self.assertRaisesRegex(ValueError,'復旧台帳'):
                Store(root/'keys/restored/database.sqlite3')
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
            content={'database.sqlite3':b'fake',**{f'protected/{name}':b'x' for name in
                     ('service-env','doppler-env','codex-auth','history-bootstrap','cutover')}}
            manifest={'format':'mina-recovery-set-v1','live_restore_allowed':False,
                      'external_reconciliation_required':True,
                      'files':[{'path':name,'bytes':len(raw),'sha256':'0'*64 if name=='database.sqlite3' else hashlib.sha256(raw).hexdigest()} for name,raw in content.items()]}
            with zipfile.ZipFile(root/'bad.zip','x') as writer:
                writer.writestr('manifest.json',json.dumps(manifest))
                for name,raw in content.items():writer.writestr(name,raw)
            with self.assertRaisesRegex(ValueError,'ハッシュ'):
                restore_recovery_set(root/'bad.zip',root/'restore')
            self.assertFalse((root/'restore').exists())

    def test_invalid_database_leaves_no_completed_or_partial_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            content={'database.sqlite3':b'not a database',**{f'protected/{name}':b'test-only' for name in
                     ('service-env','doppler-env','codex-auth','history-bootstrap','cutover')}}
            manifest={'format':'mina-recovery-set-v1','live_restore_allowed':False,
                      'external_reconciliation_required':True,
                      'files':[{'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in content.items()]}
            with zipfile.ZipFile(root/'bad.zip','x') as writer:
                writer.writestr('manifest.json',json.dumps(manifest))
                for name,raw in content.items():writer.writestr(name,raw)
            with self.assertRaises(sqlite3.DatabaseError):restore_recovery_set(root/'bad.zip',root/'restore')
            self.assertFalse((root/'restore').exists())
            self.assertFalse(list(root.glob('.mina-restore-*')))

    def test_create_cli_does_not_initialize_live_store_or_clients(self):
        from src.mina.__main__ import main
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=root/'paths.json'
            config.write_text(json.dumps({'artifacts':{},'files':{}}))
            with patch('sys.argv',['mina','backup-create-set','--manifest',str(config),'--output',str(root/'sealed'),
                 '--backup-key',str(root/'public'),'--backup-recipient','0'*64,'--source-commit','a'*40]), \
                 patch('src.mina.readiness.release_commit') as commit, \
                 patch('src.mina.recovery_set.create_recovery_set',return_value={'test_only':True}) as create, \
                 patch('src.mina.__main__.Store') as store,patch('src.mina.__main__.XClient') as client,patch('builtins.print'):
                main()
            commit.assert_called_once();create.assert_called_once();store.assert_not_called();client.assert_not_called()
