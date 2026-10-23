"""公開の遅延・照会障害・再起動でも、受付IDの読取だけで確定する境界検証。"""

from datetime import timedelta
import json
import unittest
from unittest.mock import patch

import test_instagram as legacy
from src.mina.audit import OperationalFailure
from src.mina.instagram_domain import InstagramCandidate
from src.mina.instagram_media import HOSTS
from src.mina.instagram_publisher import publish_once, reconcile_known, request_for
from src.mina.instagram_reconcile import MAX_READS
from src.mina.instagram_runtime import InstagramRuntime
from src.mina.instagram_store import InstagramStore


class ReadOnly:
    def __init__(self, c, result=None, error=None):
        self.c, self.result, self.error, self.calls = c, result, error, 0

    def _request(self, query, variables):
        if 'createPost' in query or not query.startswith('query'):
            raise AssertionError('照会で投稿を作成してはいけません')
        self.calls += 1
        if self.error:
            raise self.error
        return {'post': self.result or legacy.post(self.c, 'sent')}


class InstagramReconciliationTests(unittest.TestCase):
    setUp = legacy.InstagramTests.setUp
    approve = legacy.InstagramTests.approve
    accepted = legacy.InstagramTests.accepted

    def unknown(self, slot='daily', known=True):
        c = legacy.candidate(slot)
        self.approve(c)
        row = self.store.claim_due(c.scheduled_at)
        self.store.begin(row, request_for(c), c.scheduled_at)
        if known:
            self.store.mark(row, 'accepted', legacy.post(c, 'sending'))
        self.store.mark(row, 'outcome_unknown')
        return c, row

    def test_delayed_publication_moves_from_pending_to_confirmed_without_second_post(self):
        c = legacy.candidate('daily')
        self.approve(c)
        self.accepted(c)
        row = self.store.claim_due(c.scheduled_at)
        class Delayed(legacy.API):
            def _request(self, query, variables=None):
                result = super()._request(query, variables)
                if 'createPost(' in query:
                    result['createPost']['post'] = legacy.post(c, 'sending')
                if query.startswith('query($input:PostInput!)'):
                    return {'post': legacy.post(c, 'sending')}
                return result
        api = Delayed(c)
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true', 'MINA_INSTAGRAM_PUBLISH_ENABLED': 'true'}):
            with self.assertRaises(OperationalFailure) as caught:
                publish_once(self.store, row, lambda _: None, HOSTS, api, clock=lambda: c.scheduled_at,
                             sleep=lambda _: None, verify_media=lambda _: None)
        self.assertEqual(caught.exception.audit_reason, 'outcome_unknown')
        self.assertEqual(len(api.mutations), 1)
        with self.store.transaction() as db:
            notice = json.loads(db.execute("SELECT payload FROM instagram_notices WHERE kind='outcome_unknown'").fetchone()[0])
        self.assertIn('確認待ち', notice['content'])
        self.assertNotIn('buffer_unavailable', notice['content'])
        result = legacy.post(c, 'sent')
        result['sentAt'] = (c.scheduled_at + timedelta(seconds=113)).isoformat()
        reader = ReadOnly(c, result)
        self.assertEqual(reconcile_known(self.store, reader, scheduled=True, clock=lambda: c.scheduled_at + timedelta(minutes=3)), 1)
        self.assertEqual(self.store.rows()[0]['state'], 'published')
        self.assertEqual(len(api.mutations), 1)
        self.assertEqual(reconcile_known(self.store, reader, scheduled=True), 0)
        self.assertEqual(reader.calls, 1)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM instagram_attempts").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT state FROM instagram_notices WHERE kind='outcome_unknown'").fetchone()[0], 'obsolete')
            self.assertIn('解消', json.loads(db.execute("SELECT payload FROM instagram_notices WHERE kind='published'").fetchone()[0])['content'])

    def test_read_failure_backoff_persists_across_store_restart(self):
        c, _ = self.unknown()
        failing = ReadOnly(c, error=TimeoutError())
        self.assertEqual(reconcile_known(self.store, failing, scheduled=True, clock=lambda: c.scheduled_at), 0)
        restarted = InstagramStore(self.store.path)
        self.assertEqual(reconcile_known(restarted, failing, scheduled=True, clock=lambda: c.scheduled_at + timedelta(seconds=1)), 0)
        self.assertEqual(failing.calls, 1)
        self.assertEqual(restarted.rows()[0]['state'], 'outcome_unknown')
        self.assertIsNone(restarted.claim_due(c.scheduled_at + timedelta(seconds=15)))
        reader = ReadOnly(c)
        self.assertEqual(reconcile_known(restarted, reader, scheduled=True, clock=lambda: c.scheduled_at + timedelta(seconds=31)), 1)
        self.assertEqual(reader.calls, 1)

    def test_runtime_reconciles_existing_id_even_when_new_publication_is_disabled(self):
        c, _ = self.unknown()
        runtime = InstagramRuntime.__new__(InstagramRuntime)
        runtime.store, runtime.api = self.store, ReadOnly(c)
        runtime.send_notices = lambda: 0
        with patch.dict('os.environ', {'MINA_INSTAGRAM_PUBLISH_ENABLED': 'false'}):
            result = runtime.tick()
        self.assertEqual(result['reconciled'], 1)
        self.assertEqual(runtime.api.calls, 1)
        self.assertEqual(self.store.rows()[0]['state'], 'published')

    def test_missing_id_never_triggers_a_remote_request_or_releases_hold(self):
        c, _ = self.unknown(known=False)
        factory = unittest.mock.Mock(side_effect=AssertionError('受付IDなしで照会してはいけません'))
        self.assertEqual(reconcile_known(self.store, scheduled=True, client_factory=factory), 0)
        factory.assert_not_called()
        self.assertEqual(self.store.rows()[0]['state'], 'outcome_unknown')
        self.assertIsNone(self.store.claim_due(c.scheduled_at))

    def test_bad_id_or_changed_post_never_confirms_publication(self):
        c, _ = self.unknown()
        for index, (field, value) in enumerate((('id', 'other'), ('text', '本文変更'), ('sentAt', (c.scheduled_at - timedelta(days=1)).isoformat()))):
            with self.subTest(field=field):
                result = {**legacy.post(c, 'sent'), field: value}
                self.assertEqual(reconcile_known(self.store, ReadOnly(c, result), scheduled=True,
                                                clock=lambda: c.scheduled_at + timedelta(hours=index + 1)), 0)
                self.assertEqual(self.store.rows()[0]['state'], 'outcome_unknown')

    def test_changed_stored_request_is_rejected_before_network(self):
        c, _ = self.unknown()
        with self.store.transaction() as db:
            db.execute("UPDATE instagram_attempts SET request_json='{}'")
        reader = ReadOnly(c)
        self.assertEqual(reconcile_known(self.store, reader, scheduled=True), 0)
        self.assertEqual(reader.calls, 0)
        self.assertEqual(self.store.rows()[0]['state'], 'outcome_unknown')

    def test_read_limit_keeps_unknown_and_queues_only_one_manual_notice(self):
        c, _ = self.unknown()
        reader = ReadOnly(c, legacy.post(c, 'sending'))
        reconcile_known(self.store, reader, scheduled=True, clock=lambda: c.scheduled_at)
        with self.store.transaction() as db:
            db.execute('UPDATE instagram_reconciliation SET attempts=?,next_at=0', (MAX_READS,))
        for _ in range(2):
            self.assertEqual(reconcile_known(self.store, reader, scheduled=True), 0)
        self.assertEqual(reader.calls, 1)
        self.assertEqual(self.store.rows()[0]['state'], 'outcome_unknown')
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM instagram_notices WHERE kind='reconciliation_exhausted'").fetchone()[0], 1)
        self.assertEqual(reconcile_known(self.store, ReadOnly(c)), 1)

    def test_confirming_late_delivery_does_not_apply_the_new_post_deadline(self):
        c, _ = self.unknown()
        result = legacy.post(c, 'sent')
        result['sentAt'] = (c.scheduled_at + timedelta(minutes=10)).isoformat()
        self.assertEqual(reconcile_known(self.store, ReadOnly(c, result), scheduled=True,
                                        clock=lambda: c.scheduled_at + timedelta(minutes=11)), 1)


if __name__ == '__main__':
    unittest.main()
