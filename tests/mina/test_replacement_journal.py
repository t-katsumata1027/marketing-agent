"""P/Dの候補操作とCの保存結果を分離し、送信前入力と不明時の再送禁止を検証する。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.mina.publication_hold import acquire
from src.mina.replacement_journal import ReplacementJournal
from src.mina.store import Store
from tests.mina.test_daily import NOW, TARGET
from tests.mina.test_publication_hold import OWNER, FLAGS

KEY = '33333333-3333-4333-8333-333333333333'


class ReplacementJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name)/'db.sqlite3')
        with patch.dict('os.environ', FLAGS):
            acquire(self.store, TARGET, OWNER, NOW)
        self.journal = ReplacementJournal(self.store)

    def prepare(self, operation='candidate_prepare', body=b'contract-encoder-output'):
        self.journal.prepare(KEY, operation, TARGET, 'dinner', 1, OWNER, body)

    def test_uuid_and_wire_bytes_are_fixed_before_only_send_claim(self):
        self.prepare()
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'input_changed'):
            self.prepare(body=b'changed')
        self.assertEqual(self.journal.start(KEY), b'contract-encoder-output')
        restarted = ReplacementJournal(self.store)
        with self.assertRaisesRegex(ValueError, 'lookup_only'):
            restarted.start(KEY)

    def test_candidate_applied_never_means_meal_saved(self):
        for operation in ('candidate_prepare','candidate_discard'):
            with self.subTest(operation=operation):
                key = KEY if operation == 'candidate_prepare' else OWNER
                self.journal.prepare(key,operation,TARGET,'dinner',1,OWNER,b'fixed')
                self.journal.start(key)
                with self.assertRaisesRegex(ValueError, 'kind_invalid'):
                    self.journal.observe(key, persistence='saved')
                self.assertEqual(self.journal.observe(key,outcome='applied'), {'outcome':'applied'})
                self.assertEqual(self.journal.observe(key,outcome='unknown'), {'outcome':'applied'})

    def test_saved_is_preserved_after_read_failure_or_conflicting_not_saved(self):
        self.prepare('meal_replace')
        self.journal.start(KEY)
        self.assertEqual(self.journal.observe(KEY,persistence='saved'), {'persistence':'saved'})
        self.assertEqual(self.journal.observe(KEY,persistence='unknown'), {'persistence':'saved'})
        self.assertEqual(self.journal.observe(KEY,persistence='not_saved'), {'persistence':'saved','conflict':True})
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM meal_replacement_observations').fetchone()[0], 3)
        with self.assertRaises(ValueError):
            self.journal.start(KEY)

    def test_late_saved_observation_overrides_not_saved_and_keeps_conflict_paused(self):
        self.prepare('meal_replace')
        self.journal.start(KEY)
        self.journal.observe(KEY,persistence='not_saved')
        self.assertEqual(self.journal.observe(KEY,persistence='saved'), {'persistence':'saved','conflict':True})
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT state FROM publication_holds').fetchone()[0], 'saved_pending_review')
        with self.assertRaises(ValueError):
            self.journal.start(KEY)

    def test_other_owner_or_epoch_cannot_use_hold(self):
        for owner,epoch in ((KEY,1),(OWNER,2)):
            with self.assertRaisesRegex(ValueError, 'hold_not_owned'):
                self.journal.prepare(KEY,'candidate_prepare',TARGET,'dinner',epoch,owner,b'fixed')

    def test_wire_corruption_never_reaches_send_claim(self):
        self.prepare()
        with self.store.transaction() as db:
            db.execute('UPDATE meal_replacement_requests SET body=? WHERE request_key=?', (b'changed',KEY))
        with self.assertRaisesRegex(ValueError, 'lookup_only'):
            self.journal.start(KEY)
