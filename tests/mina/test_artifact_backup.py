"""工程成果物の固定コピーと、破損・追加・欠落時の停止を検証する。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.mina.artifact_backup import backup_artifacts, verify_artifacts, inventory


class ArtifactBackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'daily'
        (self.source/'day/images').mkdir(parents=True)
        (self.source/'day/images/breakfast.jpg').write_bytes('合成画像'.encode())
        (self.source/'day/request.json').write_text('{"operation":"meal_register"}')

    def snapshot(self):
        report = backup_artifacts(self.source, self.root/'copies')
        self.assertEqual(report['file_count'], 2)
        self.assertTrue(report['authentication_files_excluded'])
        return next((self.root/'copies').glob('artifacts-*'))

    def test_copy_and_verification_do_not_restore_or_modify_source(self):
        before = inventory(self.source)
        snapshot = self.snapshot()
        self.assertTrue(verify_artifacts(snapshot)['artifact_snapshot_verified'])
        self.assertFalse(verify_artifacts(snapshot)['live_restore_performed'])
        self.assertEqual(inventory(self.source), before)
        if os.name == 'posix':
            self.assertEqual((snapshot/'manifest.json').stat().st_mode & 0o777, 0o600)

    def test_changed_missing_added_and_escape_paths_are_rejected(self):
        snapshot = self.snapshot()
        image = snapshot/'files/day/images/breakfast.jpg'
        raw = image.read_bytes()
        image.write_bytes('改変'.encode())
        with self.assertRaises(ValueError): verify_artifacts(snapshot)
        image.unlink()
        with self.assertRaises(ValueError): verify_artifacts(snapshot)
        image.write_bytes(raw)
        extra = snapshot/'files/extra.json'
        extra.write_text('{}')
        with self.assertRaises(ValueError): verify_artifacts(snapshot)
        extra.unlink()
        manifest = json.loads((snapshot/'manifest.json').read_text())
        manifest['files'][0]['path'] = '../escape'
        (snapshot/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError): verify_artifacts(snapshot)

    def test_credentials_nested_destination_empty_source_and_links_rejected(self):
        with self.assertRaises(ValueError): backup_artifacts(self.source, self.source/'copies')
        (self.source/'auth.json').write_text('秘密の認証')
        with self.assertRaises(ValueError): self.snapshot()
        (self.source/'auth.json').unlink()
        empty = self.root/'empty'
        empty.mkdir()
        with self.assertRaises(ValueError): backup_artifacts(empty, self.root/'copies')
        if os.name == 'posix':
            (self.source/'linked').symlink_to(self.root/'outside')
            with self.assertRaises(ValueError): self.snapshot()

    def test_mutation_during_copy_never_leaves_completed_snapshot(self):
        original = inventory
        calls = 0
        def changed(source):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.source/'day/added.json').write_text('{}')
            return original(source)
        with patch('src.mina.artifact_backup.inventory', side_effect=changed):
            with self.assertRaises(ValueError): self.snapshot()
        self.assertEqual(list((self.root/'copies').iterdir()), [])
