"""暗号化した実ファイルの復元と、破損・別鍵・上書き拒否を検証する。"""

import tempfile
import unittest
import io
from unittest.mock import patch
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.mina.encrypted_backup import create_recovery_keys, seal_file, unseal_file
from src.mina.encrypted_backup import save_new


class EncryptedBackupTest(unittest.TestCase):
    def test_parent_permission_failure_prevents_plaintext_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'plaintext'
            with patch('src.mina.encrypted_backup.check_private_parent',side_effect=ValueError('拒否')):
                with self.assertRaises(ValueError):save_new(output,b'test-only-private')
            self.assertFalse(output.exists())

    def test_mismatched_passwords_have_safe_message_and_no_keys(self):
        from src.mina.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'keys';error=io.StringIO()
            with patch('sys.argv',['mina','backup-keygen','--output',str(output)]), \
                 patch('sys.stdin.isatty',return_value=True), \
                 patch('getpass.getpass',side_effect=['test-password-first','test-password-second']), \
                 patch('sys.stderr',error),self.assertRaises(SystemExit) as stopped:
                main()
            self.assertEqual(stopped.exception.code,2)
            self.assertIn('2回の入力が一致しません',error.getvalue())
            self.assertNotIn('test-password-',error.getvalue())
            self.assertNotIn('Traceback',error.getvalue())
            self.assertFalse(output.exists())

    def test_short_password_has_safe_cli_message_without_creating_keys(self):
        from src.mina.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'keys'
            error = io.StringIO()
            with patch('sys.argv', ['mina', 'backup-keygen', '--output', str(output)]), \
                 patch('sys.stdin.isatty', return_value=True), \
                 patch('getpass.getpass', return_value='test-only-short'), \
                 patch('sys.stderr', error), self.assertRaises(SystemExit) as stopped:
                main()
            self.assertEqual(stopped.exception.code, 2)
            self.assertIn('鍵は作成していません', error.getvalue())
            self.assertNotIn('test-only-short', error.getvalue())
            self.assertNotIn('Traceback', error.getvalue())
            self.assertFalse(output.exists())

    def test_key_creation_roundtrip_and_recipient_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = create_recovery_keys(root/'keys', b'test-only-password-long')
            (root/'source').write_bytes(b'recovery test')
            with self.assertRaises(ValueError):
                seal_file(root/'source', root/'wrong', root/'keys/public.pem', '0'*64)
            self.assertFalse((root/'wrong').exists())
            seal_file(root/'source', root/'sealed', root/'keys/public.pem', report['recipient_sha256'])
            unseal_file(root/'sealed', root/'restored', root/'keys/private-encrypted.pem', b'test-only-password-long')
            self.assertEqual((root/'restored').read_bytes(), b'recovery test')
            with self.assertRaises(ValueError):
                create_recovery_keys(root/'keys', b'test-only-password-long')
            with self.assertRaises(ValueError):
                create_recovery_keys(root/'invalid', b'short')
            self.assertFalse((root/'invalid').exists())
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)

    def setup_files(self, root):
        (root/'public.pem').write_bytes(self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        (root/'private.pem').write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(b'test-only-password')))
        (root/'snapshot').write_bytes(b'SQLite/artifacts test-only private content')

    def test_roundtrip_and_no_plaintext_in_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.setup_files(root)
            report = seal_file(root/'snapshot', root/'sealed', root/'public.pem')
            self.assertNotIn((root/'snapshot').read_bytes(), (root/'sealed').read_bytes())
            self.assertFalse(report['drive_upload_performed'])
            restored = unseal_file(root/'sealed', root/'restored', root/'private.pem', b'test-only-password')
            self.assertEqual((root/'snapshot').read_bytes(), (root/'restored').read_bytes())
            self.assertFalse(restored['live_restore_performed'])
            with self.assertRaises(ValueError): seal_file(root/'snapshot', root/'sealed', root/'public.pem')
            with self.assertRaises(ValueError): unseal_file(root/'sealed', root/'restored', root/'private.pem', b'test-only-password')

    def test_tampering_password_and_truncation_leave_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.setup_files(root)
            seal_file(root/'snapshot', root/'sealed', root/'public.pem')
            original = (root/'sealed').read_bytes()
            for value in (original[:-1], b'wrong-format', original[:-1] + bytes([original[-1] ^ 1])):
                (root/'broken').write_bytes(value)
                with self.assertRaises(ValueError): unseal_file(root/'broken', root/'restored', root/'private.pem', b'test-only-password')
                self.assertFalse((root/'restored').exists())
            with self.assertRaises(ValueError): unseal_file(root/'sealed', root/'restored', root/'private.pem', b'wrong')
            self.assertFalse((root/'restored').exists())

    def test_another_recipient_cannot_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.setup_files(root)
            seal_file(root/'snapshot', root/'sealed', root/'public.pem')
            other = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            (root/'other.pem').write_bytes(other.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(b'test-only-password')))
            with self.assertRaises(ValueError):
                unseal_file(root/'sealed', root/'restored', root/'other.pem', b'test-only-password')
            self.assertFalse((root/'restored').exists())
