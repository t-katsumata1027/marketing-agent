"""翌日品質保留で通常巡回と別日の承認を止めず、直接の承認禁止は保持する。"""
import tempfile,unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from src.mina.store import Store
from src.mina.publication_hold import acquire,PublicationHeld
from src.mina.runtime import notify_day
from tests.mina.test_daily import NOW,NOTIFY,TARGET,HOST,candidate

class HeldNotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'db')
        self.flags=patch.dict('os.environ',{'MINA_MEAL_REPLACEMENT_ENABLED':'true','MINA_LIVE_WRITES':'false'})
        self.flags.start();self.addCleanup(self.flags.stop)
        acquire(self.store,TARGET,'11111111-1111-4111-8111-111111111111',NOW)

    def test_direct_approval_fence_still_rejects_but_scheduler_retains_hold(self):
        with self.assertRaises(PublicationHeld):self.store.issue_cards(TARGET,NOTIFY)
        notify_day(self.store,TARGET,NOTIFY)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT state FROM publication_holds').fetchone()[0],'held')
            self.assertEqual(db.execute('SELECT count(*) FROM notices').fetchone()[0],0)

    def test_held_tomorrow_does_not_prevent_current_day_approval(self):
        today=(NOW.date()).isoformat()
        self.store.save(replace(candidate('daily'),target_date=today),HOST,NOW)
        notify_day(self.store,TARGET,NOTIFY);notify_day(self.store,today,NOTIFY)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM notices WHERE target_date=? AND kind='approval'",(today,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM notices WHERE target_date=?',(TARGET,)).fetchone()[0],0)

    def test_cycle_reports_normal_dry_run_for_known_preparation_hold(self):
        from src.mina.__main__ import cycle
        result=cycle(self.store,NOTIFY)
        self.assertEqual(result['mode'],'dry_run');self.assertEqual(result['target_date'],TARGET)

    def test_unexpected_failure_is_not_suppressed(self):
        with patch.object(self.store,'issue_cards',side_effect=ValueError('isolated-invalid-record')):
            with self.assertRaisesRegex(ValueError,'isolated-invalid-record'):notify_day(self.store,TARGET,NOTIFY)
