"""受領記録の鮮度と未解決状態を読取だけで判断する。"""
import json
import hashlib
import tempfile
import unittest
from datetime import UTC,datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from src.mina.backup_health import backup_health
from src.mina.encrypted_backup import MAGIC


class BackupHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.now=datetime(2026,10,12,15,tzinfo=UTC)
        self.disk=patch('src.mina.backup_health.shutil.disk_usage');self.mock_disk=self.disk.start();self.addCleanup(self.disk.stop)
        self.mock_disk.return_value.free=3*1024**3

    def receipt(self,name='job-2026-10-12.json',phase='verified',time=None):
        raw=MAGIC+b'test-only';sha=hashlib.sha256(raw).hexdigest();filename='test.encrypted'
        (self.root/filename).write_bytes(raw)
        (self.root/(filename+'.upload.json')).write_text(json.dumps({'state':'verified','binding':{'sha256':sha,'filename':filename}}))
        (self.root/name).write_text(json.dumps({'phase':phase,'verified_at':(time or self.now).isoformat(),
                                               'filename':filename,'receipt':{'sha256':sha,'bytes':len(raw)}}))

    def test_missing_directory_is_not_created(self):
        path=self.root/'missing'
        self.assertFalse(backup_health(path,self.now)['healthy']);self.assertFalse(path.exists())

    def test_fresh_record_does_not_modify_files_or_claim_live_readback(self):
        self.receipt();before=(self.root/'job-2026-10-12.json').read_bytes()
        result=backup_health(self.root,self.now)
        self.assertTrue(result['healthy']);self.assertFalse(result['drive_readback_performed'])
        self.assertFalse(result['mutations_performed']);self.assertEqual(before,(self.root/'job-2026-10-12.json').read_bytes())

    def test_unknown_job_remains_blocked_even_with_fresh_verified_job(self):
        self.receipt();self.receipt('job-2026-10-11.json','encrypted')
        result=backup_health(self.root,self.now)
        self.assertFalse(result['healthy']);self.assertEqual(result['unresolved_jobs'],1)
        self.assertIn('backup_reconciliation_required',result['reason_codes'])

    def test_stale_and_future_dates_are_not_healthy(self):
        self.receipt(time=self.now-timedelta(hours=37))
        self.assertIn('backup_stale_or_missing',backup_health(self.root,self.now)['reason_codes'])
        self.receipt(time=self.now+timedelta(seconds=1))
        self.assertIn('backup_receipt_invalid',backup_health(self.root,self.now)['reason_codes'])

    def test_low_space_and_retention_warn_without_deletion(self):
        self.receipt();self.mock_disk.return_value.free=1024
        for number in range(31):(self.root/f'{number}.encrypted').write_bytes(b'test')
        report=backup_health(self.root,self.now)
        self.assertIn('backup_disk_space_low',report['reason_codes'])
        self.assertIn('backup_retention_review_required',report['reason_codes'])
        self.assertEqual(len(list(self.root.glob('*.encrypted'))),32)

    def test_lost_or_changed_local_copy_is_not_healthy(self):
        self.receipt();(self.root/'test.encrypted').write_bytes(b'changed')
        self.assertIn('backup_local_copy_invalid',backup_health(self.root,self.now)['reason_codes'])
        (self.root/'test.encrypted').unlink()
        self.assertIn('backup_local_copy_invalid',backup_health(self.root,self.now)['reason_codes'])
