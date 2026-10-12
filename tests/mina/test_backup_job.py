"""日次の暗号化保存が停止・中断・再実行で重複しないことを確認する。"""
import json
import os
import hashlib
import sqlite3
import tempfile
import unittest
from datetime import UTC,datetime
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock,patch

from src.mina.backup_job import run_backup_job
from src.mina.encrypted_backup import protect_directory
from src.mina.drive import DriveClient
from src.mina.drive_backup import BackupDriveClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


class BackupJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);protect_directory(self.root)
        self.config={'database':str(self.root/'db'),'artifacts':{},
                     'files':{name:str(self.root/name) for name in
                              ('service-env','doppler-env','codex-auth','history-bootstrap','cutover')},
                     'directory':str(self.root/'backups'),'public_key':str(self.root/'public'),
                     'recipient_sha256':'a'*64}
        self.now=datetime(2026,10,12,14,10,tzinfo=UTC)
        self.env=patch.dict(os.environ,{'MINA_BACKUP_UPLOAD_ENABLED':'true'});self.env.start();self.addCleanup(self.env.stop)

    def run_job(self):
        return run_backup_job(self.config,Mock(),'a'*40,self.now)

    def test_stopped_does_not_create_job_or_call_google(self):
        with patch.dict(os.environ,{'MINA_BACKUP_UPLOAD_ENABLED':'false'}),patch('src.mina.backup_job.BackupDriveClient') as client:
            with self.assertRaises(RuntimeError):self.run_job()
        client.assert_not_called();self.assertFalse((self.root/'backups').exists())

    def test_invalid_configuration_is_rejected_before_record_creation(self):
        self.config['artifacts']={'unknown':str(self.root/'absent')}
        with self.assertRaises(ValueError):self.run_job()
        self.assertFalse((self.root/'backups').exists())

    def test_invalid_commit_is_rejected_before_record_creation(self):
        with self.assertRaises(ValueError):run_backup_job(self.config,Mock(),'invalid',self.now)
        self.assertFalse((self.root/'backups').exists())

    def test_repeat_reuses_cipher_and_upload_record_without_reencrypting(self):
        receipt={'sha256':'b'*64}
        with patch('src.mina.backup_job.create_recovery_set',return_value=receipt) as create,patch('src.mina.backup_job.BackupDriveClient.upload_once',return_value={'verified':True}) as upload:
            self.assertTrue(self.run_job()['daily_backup_verified'])
            self.assertTrue(self.run_job()['daily_backup_verified'])
        self.assertEqual(create.call_count,1);self.assertEqual(upload.call_count,2)
        self.assertEqual(upload.call_args_list[0],upload.call_args_list[1])
        self.assertFalse(self.run_result_business_flag())

    def run_result_business_flag(self):
        state=json.loads((self.root/'backups/job-2026-10-12.json').read_text())
        self.assertEqual(state['phase'],'verified')
        return 'business_enabled' in state

    def test_creation_failure_does_not_automatically_create_again(self):
        with patch('src.mina.backup_job.create_recovery_set',side_effect=ValueError('試験用の停止')) as create,patch('src.mina.backup_job.BackupDriveClient.upload_once') as upload:
            with self.assertRaises(ValueError):self.run_job()
            with self.assertRaisesRegex(ValueError,'自動再作成'):self.run_job()
        self.assertEqual(create.call_count,1);upload.assert_not_called()

    def test_upload_unknown_reuses_existing_encrypted_source(self):
        with patch('src.mina.backup_job.create_recovery_set',return_value={'sha256':'b'*64}) as create,patch('src.mina.backup_job.BackupDriveClient.upload_once',side_effect=[RuntimeError('成否不明'),RuntimeError('照合必須')]) as upload:
            with self.assertRaises(RuntimeError):self.run_job()
            with self.assertRaises(RuntimeError):self.run_job()
        self.assertEqual(create.call_count,1)
        self.assertEqual(upload.call_args_list[0],upload.call_args_list[1])

    def test_changed_same_day_configuration_stops(self):
        with patch('src.mina.backup_job.create_recovery_set',return_value={'sha256':'b'*64}),patch('src.mina.backup_job.BackupDriveClient.upload_once',return_value={'verified':True}):
            self.run_job()
            self.config['database']=str(self.root/'different-db')
            with self.assertRaisesRegex(ValueError,'コード版が変わ'):self.run_job()

    def test_dedicated_wrapper_excludes_business_credentials(self):
        wrapper=Path('deploy/run-mina-backup-doppler.sh').read_text(encoding='utf-8')
        self.assertIn('backup-upload|backup-run',wrapper)
        self.assertIn('--preserve-env=MINA_BACKUP_UPLOAD_ENABLED',wrapper)
        selected=wrapper.split('--only-secrets ',1)[1].split()[0].split(',')
        self.assertEqual(set(selected),{'MINA_GOOGLE_CLIENT_ID','MINA_GOOGLE_CLIENT_SECRET','MINA_GOOGLE_REFRESH_TOKEN','MINA_DRIVE_FOLDER_ID'})

    def test_real_sqlite_encryption_and_upload_protocol_join(self):
        # 台帳・暗号化は実処理、Googleの境界だけを代替する。
        with closing(sqlite3.connect(self.config['database'])) as connection, connection:
            connection.execute('CREATE TABLE sample(value TEXT)')
            connection.execute("INSERT INTO sample VALUES ('試験専用')")
        for path in self.config['files'].values():Path(path).write_text('試験専用',encoding='utf-8')
        key=rsa.generate_private_key(public_exponent=65537,key_size=3072).public_key()
        Path(self.config['public_key']).write_bytes(key.public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
        self.config['recipient_sha256']=hashlib.sha256(key.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
        drive=DriveClient('test','test','test','folder_id_12345')
        drive._authorization=Mock(return_value={})
        folder={'mimeType':'application/vnd.google-apps.folder','trashed':False,'shared':False,
                'owners':[{'me':True}],'capabilities':{'canAddChildren':True}}
        received={}
        def send(method,url,body,headers):
            if method=='POST':
                received['metadata']=json.loads(body)
                return 200,{'Location':'https://www.googleapis.com/upload/drive/v3/files?upload_id=test'},b''
            received['raw']=body
            return 201,{},b'{"id":"file_id_12345"}'
        def metadata(identifier,*args):
            if identifier==drive.folder_id:return folder
            return {**received['metadata'],'trashed':False,'shared':False,'size':str(len(received['raw'])),
                    'md5Checksum':hashlib.md5(received['raw']).hexdigest()}
        drive.metadata=Mock(side_effect=metadata)
        drive._request=Mock(side_effect=lambda *args,**kwargs:received['raw'])
        with patch.object(BackupDriveClient,'_send',side_effect=send) as transport:
            report=run_backup_job(self.config,drive,'a'*40,self.now)
            self.assertTrue(report['daily_backup_verified']);self.assertFalse(report['business_calls_performed'])
            run_backup_job(self.config,drive,'a'*40,self.now)
        self.assertEqual(transport.call_count,2)
