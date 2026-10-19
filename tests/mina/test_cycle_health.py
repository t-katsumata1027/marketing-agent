"""生存中の処理失敗と、公開枠なしでの不要なX照会を検証する。"""

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

from src.mina.diagnostics import diagnose
from src.mina.runtime import publish_due
from src.mina.store import Store
from tests.mina.test_daily import NOW, HOST, TARGET, candidate


class CycleHealthTest(unittest.TestCase):
    def test_fresh_heartbeat_does_not_hide_failure_and_restart_keeps_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, heartbeat = Path(tmp)/'db', Path(tmp)/'heartbeat'
            store = Store(path)
            store.record_cycle_health(True, NOW, 'dry_run')
            store.record_cycle_health(False, NOW)
            Store(path).record_cycle_health(False, NOW)
            heartbeat.write_text(str(NOW.timestamp()))
            report = diagnose(path,heartbeat,NOW,{})
            self.assertTrue(report['heartbeat']['fresh'])
            self.assertFalse(report['cycle_health']['healthy'])
            self.assertEqual(report['cycle_health']['consecutive_failures'],2)
            store.record_cycle_health(True,NOW,'dry_run')
            self.assertTrue(diagnose(path,heartbeat,NOW,{})['cycle_health']['healthy'])
            self.assertFalse(diagnose(path,heartbeat,NOW+timedelta(seconds=91),{})['cycle_health']['healthy'])

    def test_no_due_post_does_not_contact_x_and_expired_post_is_local_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'db')
            x = Mock()
            self.assertEqual(publish_due(store,x,NOW,HOST,Mock()),0)
            x.verify_account.assert_not_called()
            c = candidate()
            store.save(c,HOST,NOW)
            with store.transaction() as db:
                db.execute("UPDATE candidates SET state='approved'")
            self.assertEqual(publish_due(store,x,c.scheduled_at+timedelta(seconds=121),HOST,Mock()),0)
            self.assertEqual(store.get(TARGET,'breakfast')['state'],'expired')
            x.verify_account.assert_not_called()
            x.publish.assert_not_called()
