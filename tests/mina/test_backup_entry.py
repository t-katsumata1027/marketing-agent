"""専用入口が業務コマンドと生のエラー本文を拒否する。"""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from src.mina.backup_entry import run


class BackupEntryTests(unittest.TestCase):
    def test_business_command_is_rejected_before_main(self):
        with patch('sys.argv',['backup_entry','register']),patch('src.mina.__main__.main') as main,redirect_stdout(io.StringIO()):
            self.assertEqual(run(),2)
        main.assert_not_called()

    def test_raw_secret_error_is_not_printed(self):
        output=io.StringIO()
        with patch('sys.argv',['backup_entry','backup-run']),patch('src.mina.__main__.main',side_effect=ValueError('試験用の秘密値')),redirect_stdout(output):
            self.assertEqual(run(),1)
        self.assertIn('backup_validation_failed',output.getvalue());self.assertNotIn('試験用の秘密値',output.getvalue())

    def test_unknown_failure_requires_reconciliation(self):
        from src.mina.clients import OutcomeUnknown
        output=io.StringIO()
        with patch('sys.argv',['backup_entry','backup-upload']),patch('src.mina.__main__.main',side_effect=OutcomeUnknown('秘密のURI')),redirect_stdout(output):
            self.assertEqual(run(),1)
        self.assertIn('backup_reconciliation_required',output.getvalue());self.assertNotIn('秘密のURI',output.getvalue())
