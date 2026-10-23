"""要求原本の復元と保存状態の照会が、再送を伴わないことを検証する。"""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from src.mina.clients import MealClient, MealWriteFailure, OutcomeUnknown
from src.mina.request_ledger import RequestLedger
from src.mina.store import Store


KEY = '11111111-1111-4111-8111-111111111111'


class RequestLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'ledger.db')
        self.ledger = RequestLedger(self.store)
        self.payload = {'contract_version': 'mina-write-v1', 'comment': '朝食の候補', 'logged_at': '2026-10-08T07:30:00+09:00',
                        'meal_type': 'breakfast', 'expected_state_version': 'sv1_' + 'a' * 64}
        self.image = b'\xff\xd8\xfforiginal'
        self.ledger.prepare(KEY, 'meal_register', '2026-10-08', 'breakfast', self.payload, self.image)

    def lookup(self, persistence):
        return {'contract_version': 'mina-write-v1', 'request_uuid': KEY, 'operation': 'meal_register',
                'status': 'failed', 'persistence': persistence, 'stage': 'ogp_failed',
                'reason': 'og_image_preparation_failed', 'result_status': 'unresolved', 'result': None}

    def test_restart_restores_exact_input_and_original_without_files(self):
        restored = RequestLedger(Store(self.store.path)).restore(KEY)
        self.assertEqual(restored['payload'], self.payload)
        self.assertEqual(restored['image'], self.image)
        self.assertEqual(restored['image_sha256'], hashlib.sha256(self.image).hexdigest())
        self.assertEqual(restored['operation'], 'meal_register')

    def test_same_uuid_changed_input_and_tampered_original_rejected(self):
        with self.assertRaises(ValueError):
            self.ledger.prepare(KEY, 'meal_register', '2026-10-08', 'breakfast', {**self.payload, 'comment': '変更'}, self.image)
        with self.store.transaction() as db:
            db.execute("UPDATE mina_requests SET image=? WHERE request_key=?", (b'changed', KEY))
        with self.assertRaises(ValueError):
            self.ledger.restore(KEY)

    def test_error_lookup_distinguishes_three_states_without_resending(self):
        for state in ('not_saved', 'saved', 'unknown'):
            with self.subTest(state=state):
                # ケースごとに別のローカル要求を使用する。
                key = KEY[:-1] + str(('not_saved', 'saved', 'unknown').index(state) + 2)
                self.ledger.prepare(key, 'meal_register', '2026-10-08', 'breakfast', self.payload, self.image)
                client = Mock()
                client.request_state.return_value = {**self.lookup(state), 'request_uuid': key}
                send = Mock(side_effect=TimeoutError('private-token'))
                with self.assertRaises(OutcomeUnknown):
                    self.ledger.execute_once(key, client, send)
                with self.assertRaises(OutcomeUnknown):
                    self.ledger.execute_once(key, client, send)
                self.assertEqual(send.call_count, 1)
                self.assertEqual(client.request_state.call_count, 1)
                observation = json.loads(self.ledger.restore(key)['observation'])
                self.assertEqual(observation['persistence'], state)
                self.assertFalse(observation['automatic_retry_allowed'])
                self.assertNotIn('private', str(observation))

    def test_404_lookup_remains_unknown_and_saved_is_not_downgraded(self):
        client = Mock()
        client.request_state.side_effect = RuntimeError('meal_request_http_404')
        self.assertEqual(self.ledger.reconcile(KEY, client)['persistence'], 'unknown')
        self.ledger.record(KEY, 'held', {'persistence': 'saved'})
        self.assertEqual(self.ledger.reconcile(KEY, client)['persistence'], 'saved')

    def test_write_requires_persisted_uuid_before_http(self):
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true'}), patch('src.mina.clients.urlopen') as send:
            with self.assertRaises(ValueError):
                MealClient('https://logeats.test', 'a' * 32).json_write('/api/internal/mina/daily-share', self.payload)
        send.assert_not_called()

    def test_saved_409_and_503_preserve_safe_metadata_only(self):
        for code in (409, 503):
            payload = {'persistence': 'saved', 'stage': 'ogp_source_check', 'reason': 'source_changed', 'error': 'private-db-error'}
            error = HTTPError('https://logeats.test', code, 'private', {}, io.BytesIO(json.dumps(payload).encode()))
            with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true'}), patch('src.mina.clients.urlopen', side_effect=error):
                with self.assertRaises(MealWriteFailure) as raised:
                    MealClient('https://logeats.test', 'a' * 32).json_write('/api/internal/mina/daily-share', self.payload, request_key=KEY)
            self.assertEqual(raised.exception.write_state['persistence'], 'saved')
            self.assertEqual(raised.exception.write_state['stage'], 'ogp_source_check')
            self.assertNotIn('private', str(raised.exception.write_state) + str(raised.exception))
