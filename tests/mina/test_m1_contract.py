"""新契約の欠落・本文改変・承認後変更を実際の台帳と組み合わせて検証する。"""

import copy
import io
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from src.mina.clients import MealClient
from src.mina.m1_contract import normalized_comment, request_report, validate_state, validate_write, verify_source
from src.mina.preparation import fingerprint
from src.mina.runtime import publish_due
import test_daily as daily
from test_daily import HOST, NOW, TARGET, candidate
import test_meal_results as meals_fixture
from test_meal_results import LOGGED


class M1ContractTests(unittest.TestCase):
    def fixture(self):
        response = meals_fixture.response()
        result = copy.deepcopy(response)
        comment = result['x']['text'].split('\n\n')[0]
        result.update(normalized_comment=comment, result_version='rv1_' + 'a' * 64,
                      image_sha256='a' * 64, stored_image_sha256='c' * 64)
        result['x'].update(intent_url='https://twitter.com/intent/tweet',
                           fixed_text=result['x']['text'].split('\n\n')[1],
                           publish_text=result['x']['text'] + '\n' + result['share']['url'])
        result['ogp'] = {'status': 'ready', 'revision': 'meal-example', 'image_sha256': 'b' * 64,
                         'source_result_version': result['result_version'], 'expires_at': '2099-01-01T00:00:00Z',
                         'image_url': 'https://www.log-eats.com/share/example/og-image?v=meal-example'}
        response['x']['intent_url'] = result['x']['intent_url']
        response['result'] = result
        return response

    def test_comment_normalization_and_completion(self):
        self.assertEqual(normalized_comment('\ufeff  か\u3099  '), 'が')
        with self.assertRaises(ValueError):
            normalized_comment('朝\r\n昼')
        response = self.fixture()
        output = validate_write(response, 'breakfast', TARGET, 'www.log-eats.com', logged_at=LOGGED)
        self.assertEqual(output['publish_text'], response['result']['x']['publish_text'])

    def test_legacy_response_and_modified_completion_are_rejected(self):
        for mutation in ('legacy', 'publish', 'copy', 'source', 'pending'):
            response = self.fixture()
            if mutation == 'legacy':
                del response['result']
            elif mutation == 'publish':
                response['result']['x']['publish_text'] += '\n余計な広告'
            elif mutation == 'copy':
                response['analysis']['summary']['totalCalories'] += 1
            elif mutation == 'source':
                response['result']['ogp']['source_result_version'] = 'rv1_' + 'c' * 64
            else:
                response['result']['ogp']['status'] = 'pending'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_write(response, 'breakfast', TARGET, 'www.log-eats.com', logged_at=LOGGED)

    def test_incomplete_state_is_not_empty_success(self):
        state = {'contract_version': 'mina-write-v1', 'account': 'EatsLog88161', 'date': TARGET,
                 'checked_at': NOW.isoformat(), 'complete': True, 'meal_count': 0, 'meals': [], 'state_version': 'sv1_' + 'a' * 64}
        self.assertEqual(validate_state(state, TARGET, NOW), state['state_version'])
        for field, value in [('complete', 1), ('meal_count', True), ('state_version', 'sv1_invalid'), ('account', {'scope': 'other'})]:
            altered = {**state, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_state(altered, TARGET, NOW)

    def test_uuid_not_found_does_not_become_not_saved(self):
        opener = Mock()
        opener.open.side_effect = HTTPError('https://www.log-eats.com', 404, 'private', {}, io.BytesIO(b'private'))
        with patch('src.mina.clients.build_opener', return_value=opener):
            with self.assertRaisesRegex(RuntimeError, '^meal_request_http_404$'):
                MealClient('https://www.log-eats.com', 'x' * 32).request_state('11111111-1111-4111-8111-111111111111', 'meal_register')
        self.assertEqual(opener.open.call_count, 1)

    def test_preflight_compares_day_uuid_body_and_actual_ogp(self):
        result = self.fixture()['result']
        binding = {'request_key': '11111111-1111-4111-8111-111111111111', 'state_version': 'sv1_' + 'c' * 64,
                   'input_hash': 'ih1_' + 'd' * 64, 'result_sha256': fingerprint(result)}
        c = replace(candidate(), comment=result['normalized_comment'], api_text=result['x']['fixed_text'],
                    share_url=result['share']['url'], publish_text=result['x']['publish_text'], approval_source=binding)
        state = {'contract_version': 'mina-write-v1', 'account': 'EatsLog88161', 'date': TARGET,
                 'checked_at': NOW.isoformat(), 'complete': True, 'meal_count': 3, 'meals': [{}, {}, {}], 'state_version': binding['state_version']}
        request = {'contract_version': 'mina-write-v1', 'request_uuid': binding['request_key'], 'operation': 'meal_register',
                   'status': 'succeeded', 'persistence': 'saved', 'result_status': 'unchanged', 'current_state_version': binding['state_version'],
                   'input_hash': binding['input_hash'], 'result': result, 'current_result': copy.deepcopy(result),
                   'stage': 'completed', 'result_hold_reason': None}
        capture = Mock(return_value={'digest': c.ogp_sha256, 'meta': {'og:image': result['ogp']['image_url'], 'image_sha256': result['ogp']['image_sha256']}})
        client = Mock()
        client.day_state.return_value = state
        client.request_state.return_value = request
        verify_source(c, client, 'www.log-eats.com', capture, NOW)
        for altered in ({**request, 'result_status': 'modified'}, {**request, 'persistence': 'unknown'}, {**request, 'input_hash': 'ih1_' + 'e' * 64}):
            client.request_state.return_value = altered
            with self.assertRaises(ValueError):
                verify_source(c, client, 'www.log-eats.com', capture, NOW)
        client.request_state.return_value = request
        stale = copy.deepcopy(request)
        stale['current_result']['x']['publish_text'] += '変更'
        client.request_state.return_value = stale
        with self.assertRaises(ValueError):
            verify_source(c, client, 'www.log-eats.com', capture, NOW)
        client.request_state.return_value = request
        capture.return_value['meta']['image_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            verify_source(c, client, 'www.log-eats.com', capture, NOW)

    def test_request_report_never_exposes_result_or_authorizes_retry(self):
        key = '11111111-1111-4111-8111-111111111111'
        response = {'contract_version': 'mina-write-v1', 'request_uuid': key, 'operation': 'meal_register',
                    'status': 'failed', 'persistence': 'saved', 'result_status': 'unresolved',
                    'result': {'private': 'sensitive-body'}, 'reason': 'sensitive-error'}
        report = request_report(response, key, 'meal_register')
        self.assertEqual(report['persistence'], 'saved')
        self.assertFalse(report['automatic_retry_allowed'])
        self.assertNotIn('sensitive', str(report))
        with self.assertRaises(ValueError):
            request_report(response, key, 'daily_share')


class BeforePublishTests(unittest.TestCase):
    setUp = daily.DailyTest.setUp
    approve = daily.DailyTest.approve

    def test_preflight_failure_revokes_approval_without_sending(self):
        c = candidate()
        self.approve(c)
        client = Mock()
        preflight = Mock(side_effect=ValueError('changed'))
        self.assertEqual(publish_due(self.store, client, c.scheduled_at, HOST, preflight), 0)
        client.publish.assert_not_called()
        row = self.store.get(TARGET, 'breakfast')
        self.assertEqual(row['state'], 'changes_requested')
        self.assertIsNone(row['approved'])
        self.assertEqual(publish_due(self.store, client, c.scheduled_at, HOST, preflight), 0)
        self.assertEqual(preflight.call_count, 1)
