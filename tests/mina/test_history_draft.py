"""履歴下書きが未送信・改変候補を採用せず、人の確認を捏造しないことを確認する。"""

import json
import tempfile
import unittest
from pathlib import Path

from src.mina.history_draft import export_history_draft
from src.mina.readiness import check_evidence
from src.mina.store import Store
from tests.mina.test_daily import NOW, HOST, candidate


class HistoryDraftTest(unittest.TestCase):
    def test_only_published_records_exported_and_never_marked_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            store=Store(root/'db')
            store.save(candidate(),HOST,NOW)
            store.save(candidate('lunch'),HOST,NOW)
            with store.transaction() as db:
                db.execute("UPDATE candidates SET state='published',x_id='123' WHERE slot='breakfast'")
                db.execute("UPDATE candidates SET state='outcome_unknown' WHERE slot='lunch'")
            before=(root/'db').read_bytes()
            result=export_history_draft(root/'db','2026-10-09',root/'history.json')
            self.assertEqual(result['entry_count'],1)
            self.assertEqual(result['unresolved_local_count'],1)
            draft=json.loads((root/'history.json').read_text(encoding='utf-8'))
            self.assertFalse(draft['history_complete'])
            self.assertFalse(draft['legacy_schedule_clear'])
            with self.assertRaises((ValueError,KeyError)):
                check_evidence(draft,'2026-10-09',NOW)
            self.assertEqual((root/'db').read_bytes(),before)
            with self.assertRaises(ValueError):
                export_history_draft(root/'db','2026-10-09',root/'history.json')

    def test_missing_database_and_tampered_published_records_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaises(ValueError): export_history_draft(root/'db','2026-10-09',root/'history.json')
            self.assertFalse((root/'db').exists())
            store=Store(root/'db')
            store.save(candidate(),HOST,NOW)
            with store.transaction() as db:
                db.execute("UPDATE candidates SET state='published',x_id='123',digest='invalid'")
            with self.assertRaises(ValueError): export_history_draft(root/'db','2026-10-09',root/'history.json')
            self.assertFalse((root/'history.json').exists())
