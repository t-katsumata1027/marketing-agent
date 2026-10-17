"""公開停止・承認・送信確保の競合と、再起動後の停止保持を実SQLiteで検証する。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import Mock, patch

from src.mina.buffer_trial import BufferTrialClient, CREATE
from src.mina.instagram_buffer_client import InstagramBufferClient
from src.mina.instagram_publisher import draft_once
from src.mina.instagram_store import InstagramStore
from src.mina.publication_hold import acquire, mutation_lease, PublicationHeld, require_claim, status
from src.mina.store import Store
from tests.mina.test_daily import candidate, HOST, NOW, NOTIFY, TARGET
from tests.mina.test_instagram import candidate as ig_candidate

OWNER = '11111111-1111-4111-8111-111111111111'
FLAGS = {'MINA_MEAL_REPLACEMENT_ENABLED':'true'}


class PublicationHoldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'state.sqlite3'
        self.store = Store(self.path)
        self.ig = InstagramStore(self.path)

    def hold(self):
        with patch.dict('os.environ', FLAGS):
            return acquire(self.store, TARGET, OWNER, NOW)

    def x_card(self, approve=False):
        self.store.save(candidate(), HOST, NOW)
        card = self.store.issue_cards(TARGET, NOTIFY)[0]
        key = card['components'][0]['components'][0]['custom_id']
        if approve:
            self.store.decide(key, 'human', NOTIFY)
        return key

    def ig_card(self, approve=False):
        from src.mina.instagram_media import HOSTS
        c = replace(ig_candidate(), target_date=TARGET)
        self.ig.save(c, NOW, HOSTS)
        self.ig.queue_approval(self.ig.rows()[0])
        with self.store.transaction() as db:
            key = json.loads(db.execute('SELECT payload FROM instagram_notices').fetchone()[0])['components'][0]['components'][0]['custom_id']
        if approve:
            self.ig.decide(key, 'human', NOTIFY)
        return key

    def test_no_hold_table_or_rows_are_created_by_legacy_publication(self):
        self.x_card(approve=True)
        self.assertEqual(len(self.store.claim_due(candidate().scheduled_at)), 1)
        self.assertEqual(status(self.path), [])
        with self.store.transaction() as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='publication_holds'").fetchone())

    def test_feature_disabled_and_readonly_status_never_create_database(self):
        with patch.dict('os.environ', {'MINA_MEAL_REPLACEMENT_ENABLED':'false'}):
            with self.assertRaisesRegex(ValueError, 'disabled'):
                acquire(self.store, TARGET, OWNER, NOW)
        missing = self.path.parent/'missing.sqlite3'
        self.assertEqual(status(missing), [])
        self.assertFalse(missing.exists())

    def test_hold_rejects_both_old_approvals_even_after_feature_is_disabled(self):
        keys = self.x_card(), self.ig_card()
        self.assertEqual(self.hold()['state'], 'held')
        with patch.dict('os.environ', {'MINA_MEAL_REPLACEMENT_ENABLED':'false'}):
            with self.assertRaises(PublicationHeld):
                Store(self.path).decide(keys[0], 'human', NOTIFY)
            with self.assertRaises(PublicationHeld):
                InstagramStore(self.path).decide(keys[1], 'human', NOTIFY)
        self.assertEqual(status(self.path)[0]['state'], 'held')

    def test_hold_skips_claims_and_pending_approval_notifications_on_both_channels(self):
        self.x_card(approve=True)
        self.ig_card(approve=True)
        self.hold()
        due = candidate().scheduled_at
        self.assertEqual(self.store.claim_due(due), [])
        self.assertFalse(self.store.has_due(due))
        self.assertIsNone(self.ig.claim_due(due))
        self.assertIsNone(self.store.claim_notice())
        client = Mock()
        self.assertEqual(self.ig.send_notices(client), 0)
        client.send.assert_not_called()

    def test_hold_does_not_block_a_different_day(self):
        self.hold()
        c = replace(candidate(), target_date='2026-10-09')
        self.store.save(c, HOST, NOW)
        key = self.store.issue_cards(c.target_date, NOTIFY+timedelta(days=1))[0]['components'][0]['components'][0]['custom_id']
        self.store.decide(key, 'human', NOTIFY+timedelta(days=1))
        self.assertEqual(len(self.store.claim_due(c.scheduled_at)), 1)

    def test_claim_winning_race_makes_replacement_ineligible(self):
        self.x_card(approve=True)
        row = self.store.claim_due(candidate().scheduled_at)[0]
        self.assertEqual(self.hold()['state'], 'blocked_external')
        with self.assertRaises(PublicationHeld):
            require_claim(self.store, row)

    def test_instagram_claim_or_existing_publication_blocks_replacement(self):
        self.ig_card(approve=True)
        self.assertIsNotNone(self.ig.claim_due(candidate().scheduled_at))
        self.assertEqual(self.hold()['state'], 'blocked_external')

    def test_previously_published_x_day_is_never_automatically_replaced(self):
        self.x_card(approve=True)
        row = self.store.claim_due(candidate().scheduled_at)[0]
        self.store.finish(row, '12345', candidate().scheduled_at)
        self.assertEqual(self.hold()['state'], 'blocked_external')

    def test_unmapped_legacy_draft_is_not_assumed_to_belong_to_another_day(self):
        with self.store.transaction() as db:
            db.execute('CREATE TABLE instagram_drafts(digest TEXT PRIMARY KEY,state TEXT,post_id TEXT)')
            db.execute("INSERT INTO instagram_drafts VALUES('old-digest','attempting',NULL)")
        self.assertEqual(self.hold()['state'], 'blocked_external')

    def test_acquire_and_claim_are_serialized_on_same_write_transaction(self):
        self.x_card(approve=True)
        barrier = Barrier(2)
        def hold():
            barrier.wait()
            return acquire(self.store, TARGET, OWNER, NOW)
        def claim():
            barrier.wait()
            return self.store.claim_due(candidate().scheduled_at)
        with patch.dict('os.environ', FLAGS), ThreadPoolExecutor(max_workers=2) as pool:
            h, c = pool.submit(hold), pool.submit(claim)
            result, rows = h.result(), c.result()
        self.assertEqual(result['state'], 'blocked_external' if rows else 'held')
        self.assertEqual(self.store.claim_due(candidate().scheduled_at), [])

    def test_stale_owner_cannot_take_over_even_after_deadline(self):
        first = self.hold()
        with patch.dict('os.environ', FLAGS):
            self.assertEqual(acquire(self.store,TARGET,OWNER,NOW+timedelta(days=1))['epoch'], first['epoch'])
            with self.assertRaises(ValueError):
                acquire(self.store,TARGET,'22222222-2222-4222-8222-222222222222',NOW+timedelta(days=2))

    def test_draft_inflight_or_unknown_blocks_replacement_without_resend(self):
        with mutation_lease(self.store, TARGET, 'instagram_draft'):
            self.assertEqual(self.hold()['state'], 'blocked_external')
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT state FROM publication_mutation_leases').fetchone()[0], 'complete')
        self.assertEqual(status(self.path)[0]['state'], 'blocked_external')

    def test_unknown_draft_lease_survives_restart(self):
        with self.assertRaises(TimeoutError):
            with mutation_lease(self.store, TARGET, 'instagram_draft'):
                raise TimeoutError()
        self.store = Store(self.path)
        self.assertEqual(self.hold()['state'], 'blocked_external')

    def test_hold_stops_low_level_buffer_mutations_before_network(self):
        self.hold()
        transport = Mock()
        client = BufferTrialClient('test-only-token', transport=transport)
        client.publication_store, client.publication_target = self.store, TARGET
        with self.assertRaises(PublicationHeld):
            client._request(CREATE, {})
        ig = InstagramBufferClient(self.ig, token='test-only-token', transport=transport)
        ig.publication_target = TARGET
        with self.assertRaises(PublicationHeld):
            ig._request('mutation { createPost }', {})
        transport.assert_not_called()

    def test_standalone_buffer_writer_has_no_bypass_when_feature_enabled(self):
        transport = Mock()
        client = BufferTrialClient('test-only-token', transport=transport)
        with patch.dict('os.environ', FLAGS), self.assertRaisesRegex(ValueError, 'fence_unavailable'):
            client._request(CREATE, {})
        transport.assert_not_called()

    def test_hold_stops_new_private_draft_before_createPost(self):
        self.hold()
        c = replace(ig_candidate(), target_date=TARGET)
        client = Mock()
        client._request.side_effect = AssertionError('書込み不可')
        # 実時刻での有効性や対象照会を通る前に停止検査する。
        with patch.dict('os.environ', {'MINA_LIVE_WRITES':'true'}), \
                patch('src.mina.instagram_publisher.instagram_channel', return_value={
                    'id':c.channel_id,'serviceId':c.account_id}), self.assertRaises(PublicationHeld):
            draft_once(self.ig, c, client)
        client._request.assert_not_called()
