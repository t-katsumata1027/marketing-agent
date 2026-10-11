"""Drive保存の停止・非再送・内容照合・読取再開を検証する。"""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError

from src.mina.clients import OutcomeUnknown
from src.mina.drive import DriveClient, NoRedirect
from src.mina.preparation import save_images_to_drive
from src.mina.store import Store


class DriveTests(unittest.TestCase):
    def client(self):
        return DriveClient('client', 'secret', 'refresh', 'folder_id_12345')

    def test_disabled_upload_never_refreshes_or_contacts_drive(self):
        client = self.client()
        with patch.dict(os.environ, {'MINA_LIVE_WRITES': 'false'}), patch.object(client.opener, 'open') as send:
            with self.assertRaises(RuntimeError):
                client.upload(b'\xff\xd8\xfftest', '2026-10-08-breakfast.jpg')
        send.assert_not_called()

    def test_disabled_folder_creation_never_contacts_drive(self):
        client = DriveClient('client', 'secret', 'refresh')
        with patch.dict(os.environ, {'MINA_LIVE_WRITES': 'false'}), patch.object(client.opener, 'open') as send:
            with self.assertRaises(RuntimeError):
                client.create_folder('AI広報みな marketing-agent')
        send.assert_not_called()

    def test_missing_folder_prevents_image_upload(self):
        client = DriveClient('client', 'secret', 'refresh')
        with patch.dict(os.environ, {'MINA_LIVE_WRITES': 'true'}), patch.object(client.opener, 'open') as send:
            with self.assertRaises(ValueError):
                client.upload(b'\xff\xd8\xfftest', '2026-10-08-breakfast.jpg')
        send.assert_not_called()

    def test_unknown_upload_is_not_retried_or_logged_raw(self):
        client = self.client()
        with patch.object(client.opener, 'open', side_effect=URLError('private-refresh-token')) as send:
            with self.assertRaises(OutcomeUnknown) as raised:
                client._request('https://www.googleapis.com/upload/drive/v3/files', write=True)
        self.assertEqual(send.call_count, 1)
        self.assertNotIn('private-refresh-token', str(raised.exception))

    def test_wrong_parent_prevents_upload(self):
        client = self.client()
        with patch.dict(os.environ, {'MINA_LIVE_WRITES': 'true'}), patch.object(client, 'metadata', return_value={
                'mimeType': 'application/vnd.google-apps.folder', 'trashed': False, 'capabilities': {'canAddChildren': False}}), patch.object(client, '_request') as send:
            with self.assertRaises(ValueError):
                client.upload(b'\xff\xd8\xfftest', '2026-10-08-breakfast.jpg')
        send.assert_not_called()

    def test_metadata_match_does_not_replace_content_verification(self):
        client = self.client()
        image = b'\xff\xd8\xfforiginal'
        meta = {'id': 'file_id_12345', 'name': '2026-10-08-breakfast.jpg', 'mimeType': 'image/jpeg', 'trashed': False,
                'parents': [client.folder_id], 'size': str(len(image)), 'md5Checksum': hashlib.md5(image).hexdigest()}
        with patch.object(client, 'metadata', return_value=meta), patch.object(client, '_authorization', return_value={}), patch.object(client, '_request', return_value=b'other'):
            with self.assertRaises(ValueError):
                client.verify(meta['id'], image, meta['name'])

    def test_redirect_does_not_forward_authorization(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, None, None, None, 'https://other.example'))

    def test_resume_after_readback_failure_does_not_upload_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            raw = b'\xff\xd8\xffimage'
            path = directory / 'meal.jpg'
            path.write_bytes(raw)
            reviewed = {'target_date': '2026-10-08', 'state': 'images_reviewed', 'images': {slot: {
                'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw),
                'image_review': {'status': 'image_acceptable', 'reason_codes': []}}
                for slot in ('breakfast', 'lunch', 'dinner')}}
            drive = Mock(folder_id='folder_id_12345')
            drive.upload.return_value = {'id': 'file_id_12345'}
            drive.verify.side_effect = [RuntimeError('drive_read_failed'), {'verified': True}, {'verified': True}, {'verified': True}]
            store = Store(str(directory / 'state.sqlite3'))
            with self.assertRaises(RuntimeError):
                save_images_to_drive(store, reviewed, drive)
            result = save_images_to_drive(store, reviewed, drive)
            self.assertEqual(drive.upload.call_count, 3)
            self.assertEqual(result['state'], 'images_drive_verified')
