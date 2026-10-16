"""再点検後の承認版更新と、送信済み・競合時の復帰拒否を確認する。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_registration as fixture
import test_readiness as readiness
from test_daily import NOW, NOTIFY, TARGET
from src.mina.readiness import build_candidates
from src.mina.rereview import rereview_day
from src.mina.domain import Candidate
from src.mina.store import Store


class RereviewTests(unittest.TestCase):
    def setUp(self):
        self.release_patch = patch('src.mina.rereview.release_commit', return_value='b' * 40)
        self.release_patch.start()
        self.addCleanup(self.release_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = fixture.RegistrationTests()
        self.draft, self.saved = self.fixture.setup_inputs(self.root)
        self.store, self.client, self.ai = Store(self.root / 'state.db'), self.fixture.client(), Mock()
        self.ai.review_meal.return_value = self.ai.review_ogp.return_value = {'status': 'publishable', 'reason_codes': []}
        result = self.fixture.run_pipeline(self.store, self.draft, self.saved, self.root, self.client, self.ai)
        payloads = build_candidates(result, readiness.evidence(), 'a' * 40, 'www.log-eats.com', NOW)
        self.store.save_many([Candidate(**value) for value in payloads], 'www.log-eats.com', NOW)
        self.old_cards = self.store.issue_cards(TARGET, NOTIFY)
        with self.store.transaction() as db:
            db.execute("UPDATE candidates SET state='changes_requested'")
        self.images = {slot: value['path'] for slot, value in self.saved['images'].items()}
        self.capture = lambda url, host: {'meta': {'og:image': url + '/og-image', 'image_sha256': 'e' * 64},
                                         'digest': 'd' * 64, 'review_image': b'preview'}
        self.ai.reset_mock()

    def run_review(self):
        return rereview_day(self.store, TARGET, '22222222-2222-4222-8222-222222222222', self.images,
                            self.ai, self.client, 'www.log-eats.com', self.root / 'new.json', lambda: NOW, self.capture,
                            self.draft, 'b' * 40, self.root, evidence=readiness.evidence())

    def test_review_returns_all_four_to_new_approval_and_rejects_old_button(self):
        with patch('src.mina.rereview.verify_source') as preflight:
            self.assertEqual(self.run_review(), TARGET)
        self.assertEqual(preflight.call_count, 8)
        self.assertEqual(self.ai.review_meal.call_count, 3)
        self.assertEqual(self.ai.review_ogp.call_count, 4)
        self.assertEqual({row['revision'] for row in self.store.list_day(TARGET)}, {2})
        self.assertEqual({row['state'] for row in self.store.list_day(TARGET)}, {'approval_pending'})
        old = self.old_cards[0]['components'][0]['components'][0]['custom_id']
        with self.assertRaises(ValueError):
            self.store.decide(old, '試験承認者', NOTIFY)
        self.client.register.assert_called()
        self.assertEqual(self.client.register.call_count, 3)
        self.assertEqual(self.client.json_write.call_count, 1)

    def test_changed_source_stops_before_review_or_reimport(self):
        with patch('src.mina.rereview.verify_source', side_effect=ValueError('modified')):
            with self.assertRaises(ValueError):
                self.run_review()
        self.ai.review_ogp.assert_not_called()
        self.assertEqual({row['revision'] for row in self.store.list_day(TARGET)}, {1})

    def test_started_dispatch_is_never_reactivated(self):
        with self.store.transaction() as db:
            db.execute("UPDATE candidates SET state='outcome_unknown' WHERE slot='breakfast'")
        with self.assertRaises(ValueError):
            self.run_review()
        self.ai.review_meal.assert_not_called()

    def test_approval_race_aborts_whole_import(self):
        def reviewed(*args):
            with self.store.transaction() as db:
                db.execute("UPDATE candidates SET state='approved' WHERE slot='breakfast'")
            return {'status': 'publishable', 'reason_codes': []}
        self.ai.review_ogp.side_effect = reviewed
        with patch('src.mina.rereview.verify_source'), self.assertRaises(ValueError):
            self.run_review()
        self.assertEqual({row['revision'] for row in self.store.list_day(TARGET)}, {1})
