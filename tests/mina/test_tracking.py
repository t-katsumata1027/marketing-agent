"""承認・正本照合・環境変更を跨ぐX計測URLの公開境界を検証する。"""
import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from src.mina.domain import Candidate
from src.mina.m1_contract import verify_source
from src.mina.preparation import fingerprint
from src.mina.store import Store
from src.mina.tracking import POLICY, configured_policy, verify_tracked_ogp
from test_daily import NOW, NOTIFY, TARGET, candidate
import test_m1_contract as m1_fixture

HOST = 'www.log-eats.com'


def fixture():
    result = m1_fixture.M1ContractTests().fixture()['result']
    binding = {'request_key': '11111111-1111-4111-8111-111111111111', 'state_version': 'sv1_' + 'c' * 64,
               'input_hash': 'ih1_' + 'd' * 64, 'result_sha256': fingerprint(result)}
    c = replace(candidate(), comment=result['normalized_comment'], api_text=result['x']['fixed_text'],
                share_url=result['share']['url'], publish_text=result['x']['publish_text'], approval_source=binding)
    return c, result


class TrackingTests(unittest.TestCase):
    def tracked(self):
        c, result = fixture()
        return replace(c, approval_source={**c.approval_source, 'url_tracking': dict(POLICY)}), result

    def test_canonical_result_and_legacy_digest_stay_unchanged(self):
        c, _ = fixture()
        old = c.digest
        with patch.dict(os.environ, {'MINA_X_UTM_ENABLED': 'true'}):
            self.assertEqual(c.content, c.publish_text)
            self.assertEqual(c.digest, old)
        tracked, _ = self.tracked()
        self.assertEqual(tracked.source_content, c.content)
        self.assertEqual(tracked.share_url, c.share_url)
        self.assertNotEqual(tracked.digest, old)
        tracked.validate(HOST, NOW)

    def test_fixed_parameters_survive_environment_changes_and_roundtrip(self):
        c, _ = self.tracked()
        expected = c.share_url + '?utm_source=x&utm_medium=social&utm_campaign=mina_daily&utm_content=' + TARGET + '_breakfast'
        with patch.dict(os.environ, {'MINA_X_UTM_ENABLED': 'false'}):
            restored = Candidate(**json.loads(json.dumps(c.__dict__)))
            self.assertEqual(restored.content, c.publish_text[:-len(c.share_url)] + expected)
            self.assertEqual(restored.digest, c.digest)

    def test_unknown_policy_and_existing_queries_fail_closed(self):
        c, _ = self.tracked()
        for source in ({**POLICY, 'source': 'instagram'}, {**POLICY, 'extra': 'x'}, None):
            changed = replace(c, approval_source={**c.approval_source, 'url_tracking': source})
            with self.subTest(source=source), self.assertRaises(ValueError):
                changed.validate(HOST, NOW)
        for suffix in ('?a=1', '#x', '?utm_source=x'):
            url = c.share_url + suffix
            changed = replace(c, share_url=url, publish_text=c.publish_text[:-len(c.share_url)] + url)
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                changed.validate(HOST, NOW)

    def test_configuration_only_enables_explicit_true(self):
        for value, enabled in (('false', False), ('true', True)):
            with patch.dict(os.environ, {'MINA_X_UTM_ENABLED': value}):
                self.assertEqual(configured_policy() is not None, enabled)
        with patch.dict(os.environ, {'MINA_X_UTM_ENABLED': 'yes'}), self.assertRaises(ValueError):
            configured_policy()

    def test_discord_card_contains_exact_approved_tracking_url(self):
        c, _ = self.tracked()
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'test.sqlite3')
            store.save(c, HOST, NOW)
            card = store.issue_cards(TARGET, NOTIFY)[0]
            self.assertIn(c.published_share_url, json.dumps(card, ensure_ascii=False))
            token = card['components'][0]['components'][0]['custom_id']
            store.decide(token, 'user', NOTIFY)
            persisted = Candidate(**json.loads(store.get(TARGET, c.slot)['payload']))
            self.assertEqual(persisted.content, c.content)
            changed = replace(c, approval_source={**c.approval_source, 'url_tracking': {**POLICY, 'campaign': 'other'}})
            with self.assertRaises(ValueError):
                store.save(changed, HOST, NOW)

    def test_tracked_destination_and_pixels_must_match_canonical(self):
        c, result = self.tracked()
        canonical = {'meta': {'page_url': c.share_url, 'og:image': result['ogp']['image_url'], 'image_sha256': 'b' * 64}}
        capture = Mock(return_value={'meta': {**canonical['meta'], 'page_url': c.published_share_url}})
        verify_tracked_ogp(c, HOST, capture, canonical)
        for changes in ({'image_sha256': 'f' * 64}, {'page_url': 'https://evil.test/s/test'},
                        {'page_url': 'https://' + HOST + '/s/other'},
                        {'page_url': c.share_url + '?utm_source=other'}):
            capture.return_value = {'meta': {**canonical['meta'], **changes}}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                verify_tracked_ogp(c, HOST, capture, canonical)

    def test_source_verification_compares_api_canonical_and_checks_tracking_url(self):
        c, result = self.tracked()
        binding = c.approval_source
        client = Mock()
        client.day_state.return_value = {'contract_version': 'mina-write-v1', 'account': c.account, 'date': TARGET,
            'checked_at': NOW.isoformat(), 'complete': True, 'meal_count': 3, 'meals': [{}, {}, {}], 'state_version': binding['state_version']}
        request = {'contract_version': 'mina-write-v1', 'request_uuid': binding['request_key'], 'operation': 'meal_register',
            'status': 'succeeded', 'persistence': 'saved', 'result_status': 'unchanged', 'current_state_version': binding['state_version'],
            'input_hash': binding['input_hash'], 'result': result, 'current_result': copy.deepcopy(result), 'stage': 'completed', 'result_hold_reason': None}
        client.request_state.return_value = request
        def capture(url, host):
            return {'digest': c.ogp_sha256, 'meta': {'page_url': url, 'og:image': result['ogp']['image_url'], 'image_sha256': result['ogp']['image_sha256']}}
        spy = Mock(side_effect=capture)
        verify_source(c, client, HOST, spy, NOW)
        self.assertEqual([a.args[0] for a in spy.call_args_list], [c.share_url, c.published_share_url])
        request['current_result']['x']['publish_text'] = c.content
        with self.assertRaises(ValueError):
            verify_source(c, client, HOST, spy, NOW)

    def test_daily_preparation_binds_tracking_for_all_four_slots(self):
        import test_readiness as readiness_fixture
        from src.mina.readiness import build_candidates
        reviewed = readiness_fixture.reviewed()
        with patch.dict(os.environ, {'MINA_X_UTM_ENABLED': 'true'}):
            entries = build_candidates(reviewed, readiness_fixture.evidence(), 'a' * 40, readiness_fixture.HOST, NOW)
        for entry in entries:
            c = Candidate(**entry)
            self.assertEqual(c.publish_text, reviewed['posts'][c.slot]['publish_text'])
            self.assertIn('utm_content=' + TARGET + '_' + c.slot, c.content)
            self.assertEqual(c.approval_source['url_tracking'], POLICY)

    def test_publication_sends_exact_approved_tracking_body_once(self):
        from src.mina.runtime import publish_due
        c, _ = self.tracked()
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'test.sqlite3')
            store.save(c, HOST, NOW)
            card = store.issue_cards(TARGET, NOTIFY)[0]
            store.decide(card['components'][0]['components'][0]['custom_id'], 'user', NOTIFY)
            client = Mock()
            client.publish.return_value = '12345'
            proof = {key: c.scheduled_at.isoformat() for key in ('started_at', 'checked_at', 'observed_at')}
            proof.update(expires_at='2099-01-01T00:00:00Z', started_monotonic=0, observed_monotonic=0)
            preflight = Mock(return_value=proof)
            with patch.dict(os.environ, {'MINA_X_UTM_ENABLED': 'false'}):
                self.assertEqual(publish_due(store, client, c.scheduled_at, HOST, preflight, clock=lambda: c.scheduled_at, timer=lambda: 0), 1)
                self.assertEqual(publish_due(store, client, c.scheduled_at, HOST, preflight, clock=lambda: c.scheduled_at, timer=lambda: 0), 0)
            client.publish.assert_called_once_with(c.content)
