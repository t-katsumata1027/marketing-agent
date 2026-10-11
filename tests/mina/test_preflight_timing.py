"""GET完了後の鮮度と、照合中に期限を跨いだ場合の送信停止を検証する。"""

import copy
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import test_daily as daily
import test_m1_contract as fixtures
from src.mina.m1_contract import verify_source
from src.mina.preparation import fingerprint
from src.mina.runtime import publish_due


class TimingTests(unittest.TestCase):
    setUp = daily.DailyTest.setUp
    approve = daily.DailyTest.approve

    def setup_preflight(self):
        result = fixtures.M1ContractTests().fixture()['result']
        binding = {'request_key': '11111111-1111-4111-8111-111111111111', 'state_version': 'sv1_' + 'a' * 64,
                   'input_hash': 'ih1_' + 'b' * 64, 'result_sha256': fingerprint(result)}
        c = replace(daily.candidate(), comment=result['normalized_comment'], api_text=result['x']['fixed_text'],
                    share_url=result['share']['url'], publish_text=result['x']['publish_text'], approval_source=binding)
        self.time = daily.NOW
        client = Mock()
        def state(target):
            self.time += timedelta(seconds=2)
            return {'contract_version': 'mina-write-v1', 'account': 'EatsLog88161', 'date': target,
                    'checked_at': self.time.isoformat(), 'complete': True, 'meal_count': 3, 'meals': [{}, {}, {}],
                    'state_version': binding['state_version']}
        client.day_state.side_effect = state
        client.request_state.return_value = {'contract_version': 'mina-write-v1', 'request_uuid': binding['request_key'],
            'operation': 'meal_register', 'status': 'succeeded', 'persistence': 'saved', 'stage': 'completed',
            'reason': None, 'result_status': 'unchanged', 'result_hold_reason': None, 'input_hash': binding['input_hash'],
            'current_state_version': binding['state_version'], 'current_result': copy.deepcopy(result)}
        capture = Mock(return_value={'digest': c.ogp_sha256, 'meta': {'og:image': result['ogp']['image_url'], 'image_sha256': result['ogp']['image_sha256']}})
        return c, client, capture

    def test_checked_at_after_request_start_is_accepted(self):
        c, client, capture = self.setup_preflight()
        proof = verify_source(c, client, 'www.log-eats.com', capture, clock=lambda: self.time)
        self.assertEqual(proof['checked_at'], self.time.isoformat())

    def test_slow_capture_invalidates_old_get(self):
        c, client, capture = self.setup_preflight()
        snapshot = capture.return_value
        def delayed(*args):
            self.time += timedelta(seconds=121)
            return snapshot
        capture.side_effect = delayed
        with self.assertRaises(ValueError):
            verify_source(c, client, 'www.log-eats.com', capture, clock=lambda: self.time)

    def test_preflight_crosses_publish_grace_and_revokes_approval(self):
        c, _ = self.approve()
        client = Mock()
        self.assertEqual(publish_due(self.store, client, c.scheduled_at, daily.HOST, Mock(),
                                    clock=lambda: c.scheduled_at + timedelta(seconds=121)), 0)
        client.publish.assert_not_called()
        self.assertEqual(self.store.get(c.target_date, c.slot)['state'], 'changes_requested')

    def test_send_rejects_expired_ogp_proof_without_calling_x(self):
        c, _ = self.approve()
        c = replace(c, publish_text=c.content, approval_source={
            'request_key': '11111111-1111-4111-8111-111111111111', 'state_version': 'sv1_' + 'a' * 64,
            'input_hash': 'ih1_' + 'b' * 64, 'result_sha256': 'c' * 64})
        self.store.save(c, daily.HOST, daily.NOW)
        card = self.store.issue_cards(c.target_date, daily.NOTIFY)[0]
        self.store.decide(card['components'][0]['components'][0]['custom_id'], '試験承認者', daily.NOTIFY)
        proof = {key: c.scheduled_at.isoformat() for key in ('started_at', 'checked_at', 'observed_at', 'expires_at')}
        proof.update(started_monotonic=100, observed_monotonic=101)
        client = Mock()
        self.assertEqual(publish_due(self.store, client, c.scheduled_at, daily.HOST, Mock(return_value=proof),
                                    clock=lambda: c.scheduled_at, timer=lambda: 102), 0)
        client.publish.assert_not_called()

    def test_slow_get_cannot_reset_monotonic_deadline_at_receipt(self):
        c, client, capture = self.setup_preflight()
        values = iter((100, 221))
        with self.assertRaises(ValueError):
            verify_source(c, client, 'www.log-eats.com', capture, clock=lambda: self.time, timer=lambda: next(values))

    def test_monotonic_deadline_is_checked_again_just_before_send(self):
        c, _ = self.approve()
        c = replace(c, publish_text=c.content, approval_source={
            'request_key': '11111111-1111-4111-8111-111111111111', 'state_version': 'sv1_' + 'a' * 64,
            'input_hash': 'ih1_' + 'b' * 64, 'result_sha256': 'c' * 64})
        self.store.save(c, daily.HOST, daily.NOW)
        card = self.store.issue_cards(c.target_date, daily.NOTIFY)[0]
        self.store.decide(card['components'][0]['components'][0]['custom_id'], '試験承認者', daily.NOTIFY)
        proof = {key: c.scheduled_at.isoformat() for key in ('started_at', 'checked_at', 'observed_at')}
        proof.update(started_monotonic=100, observed_monotonic=219, expires_at='2099-01-01T00:00:00Z')
        client = Mock()
        self.assertEqual(publish_due(self.store, client, c.scheduled_at, daily.HOST, Mock(return_value=proof),
                                    clock=lambda: c.scheduled_at, timer=lambda: 221), 0)
        client.publish.assert_not_called()
