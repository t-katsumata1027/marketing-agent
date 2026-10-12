"""実暗号化ファイルの分割保存・成否不明停止・受領後の読取再開を検証する。"""
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from urllib.error import HTTPError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from src.mina.clients import OutcomeUnknown
from src.mina.drive import DriveClient
from src.mina.drive_backup import BackupDriveClient
from src.mina.encrypted_backup import seal_file


class DriveBackupTest(unittest.TestCase):
    def test_progress_response_closes_transport_without_logging_body(self):
        error=HTTPError('https://www.googleapis.com/upload/drive/v3/files',308,'progress',{'Range':'bytes=0-42'},io.BytesIO(b'test-only-private'))
        with patch.object(self.drive.opener,'open',side_effect=error):
            response=self.client._send('PUT',error.url,b'test-only',{})
        self.assertEqual(response,(308,{'Range':'bytes=0-42'},b''))
        self.assertTrue(error.closed)

    def test_paused_cli_never_constructs_store_or_google_client(self):
        from src.mina.__main__ import main
        with patch.dict(os.environ,{'MINA_BACKUP_UPLOAD_ENABLED':'false'}), \
             patch('sys.argv',['mina','backup-upload','--manifest','cipher','--output','journal',
                '--backup-sha256','0'*64,'--backup-filename',self.filename,'--source-commit','a'*40]), \
             patch('src.mina.drive.DriveClient.from_env') as client,patch('src.mina.__main__.Store') as store:
            with self.assertRaises(RuntimeError):main()
        client.assert_not_called();store.assert_not_called()

    @classmethod
    def setUpClass(cls):
        cls.key=rsa.generate_private_key(public_exponent=65537,key_size=3072)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'public').write_bytes(self.key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
        (self.root/'plain').write_bytes(b'test-only'*40000)
        seal_file(self.root/'plain',self.root/'sealed',self.root/'public')
        self.raw=(self.root/'sealed').read_bytes();self.sha=hashlib.sha256(self.raw).hexdigest()
        self.filename='mina-recovery-20261012-aaaaaaaa.encrypted'
        self.drive=DriveClient('test-client','test-secret','test-refresh','folder_id_12345')
        folder={'mimeType':'application/vnd.google-apps.folder','trashed':False,'shared':False,
                'owners':[{'me':True}],'capabilities':{'canAddChildren':True}}
        file={'name':self.filename,'mimeType':'application/octet-stream','parents':[self.drive.folder_id],
              'trashed':False,'shared':False,'size':str(len(self.raw)),
              'md5Checksum':hashlib.md5(self.raw).hexdigest(),
              'appProperties':{'mina_role':'encrypted_recovery','mina_sha256':self.sha}}
        self.drive.metadata=Mock(side_effect=lambda id,*_:folder if id==self.drive.folder_id else file)
        self.drive._authorization=Mock(return_value={});self.drive._request=Mock(return_value=self.raw)
        self.client=BackupDriveClient(self.drive)
        self.env=patch.dict(os.environ,{'MINA_BACKUP_UPLOAD_ENABLED':'true','MINA_LIVE_WRITES':'false','MINA_PUBLISH_ENABLED':'false'})
        self.env.start();self.addCleanup(self.env.stop)

    def upload(self):
        return self.client.upload_once(self.root/'sealed',self.root/'journal',self.filename,self.sha,'a'*40)

    def success(self):
        self.client._send=Mock(side_effect=[(200,{'Location':'https://www.googleapis.com/upload/drive/v3/files?upload_id=test-only'},b''),
            (308,{'Range':'bytes=0-262143'},b''),(201,{},b'{"id":"file_id_12345"}')])

    def test_stopped_backup_does_not_read_source_or_connect(self):
        with patch.dict(os.environ,{'MINA_BACKUP_UPLOAD_ENABLED':'false'}):
            with self.assertRaises(RuntimeError):self.upload()
        self.drive.metadata.assert_not_called();self.drive._authorization.assert_not_called()
        self.assertFalse((self.root/'journal').exists())

    def test_split_upload_and_repeat_only_reverify_without_business_activation(self):
        self.success()
        with patch('src.mina.drive_backup.CHUNK',262144):
            self.assertTrue(self.upload()['verified'])
            self.assertTrue(self.upload()['verified'])
        self.assertEqual(self.client._send.call_count,3)
        ranges=[call.args[3].get('Content-Range') for call in self.client._send.call_args_list[1:]]
        self.assertEqual(ranges,[f'bytes 0-262143/{len(self.raw)}',f'bytes 262144-{len(self.raw)-1}/{len(self.raw)}'])
        self.assertEqual(json.loads((self.root/'journal').read_text())['state'],'verified')
        self.assertEqual(os.environ['MINA_LIVE_WRITES'],'false')
        self.assertEqual(os.environ['MINA_PUBLISH_ENABLED'],'false')

    def test_response_loss_is_redacted_and_new_upload_is_not_repeated(self):
        self.client._send=Mock(side_effect=TimeoutError('test-only-private-response'))
        with self.assertRaises(OutcomeUnknown) as raised:self.upload()
        self.assertNotIn('test-only-private-response',str(raised.exception))
        with self.assertRaises(OutcomeUnknown):self.upload()
        self.assertEqual(self.client._send.call_count,1)

    def test_redirected_session_never_receives_the_encrypted_body(self):
        self.client._send=Mock(return_value=(200,{'Location':'https://other.example/upload'},b''))
        with self.assertRaises(OutcomeUnknown):self.upload()
        self.assertEqual(self.client._send.call_count,1)

    def test_readback_failure_keeps_received_id_and_never_reuploads(self):
        self.success();self.drive._request.side_effect=[RuntimeError('read_failed'),self.raw]
        with patch('src.mina.drive_backup.CHUNK',262144):
            with self.assertRaises(RuntimeError):self.upload()
            self.assertEqual(json.loads((self.root/'journal').read_text())['state'],'uploaded')
            self.assertTrue(self.upload()['verified'])
        self.assertEqual(self.client._send.call_count,3)

    def test_plaintext_or_changed_source_is_not_sent(self):
        self.client._send=Mock()
        (self.root/'sealed').write_bytes(b'plain-credentials')
        with self.assertRaises(ValueError):self.upload()
        self.client._send.assert_not_called()

    def test_uploaded_record_failure_stays_unknown_and_never_reposts(self):
        self.success()
        original=self.client._update
        def fail_uploaded(path,state):
            if state['state']=='uploaded':raise OSError('試験用の保存失敗')
            return original(path,state)
        with patch.object(self.client,'_update',side_effect=fail_uploaded),patch('src.mina.drive_backup.CHUNK',262144):
            with self.assertRaises(OutcomeUnknown):self.upload()
        self.assertEqual(json.loads((self.root/'journal').read_text())['state'],'outcome_unknown')
        calls=self.client._send.call_count
        with self.assertRaises(OutcomeUnknown):self.upload()
        self.assertEqual(self.client._send.call_count,calls)

    def test_secondary_journal_failure_does_not_expose_raw_exception(self):
        self.client._send=Mock(side_effect=TimeoutError('試験用'))
        with patch.object(self.client,'_update',side_effect=OSError('秘密を含む可能性のある生の例外')):
            with self.assertRaisesRegex(OutcomeUnknown,'^backup_upload_unknown$'):self.upload()
        self.assertEqual(json.loads((self.root/'journal').read_text())['state'],'started')
