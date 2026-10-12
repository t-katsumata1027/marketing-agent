"""暗号化した実ファイルの復元と、破損・別鍵・上書き拒否を検証する。"""

import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.mina.encrypted_backup import seal_file, unseal_file


class EncryptedBackupTest(unittest.TestCase):
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
