"""既存記録・成否不明・レビュー保留で新規登録が増えないことを確認する。"""

import copy
import hashlib
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from src.mina.clients import OutcomeUnknown
from src.mina.domain import ACCOUNT, FIXED_MARKERS
from src.mina.registration import assert_day_state, register_day
from src.mina.store import Store


class RegistrationTests(unittest.TestCase):
    def setup_inputs(self, directory):
        meals, images = {}, {}
        for slot in ('breakfast', 'lunch', 'dinner'):
            meals[slot] = {'menu': 'ご飯と魚', 'comment': slot + 'の献立です。', 'estimated_calories': 600,
                           'foods': [{'name': 'ご飯', 'amount': '150g'}, {'name': '魚', 'amount': '100g'}], 'image_description': '別皿に盛ったご飯と魚'}
            raw = b'\xff\xd8\xff' + slot.encode()
            path = directory / (slot + '.jpg')
            path.write_bytes(raw)
            sha = hashlib.sha256(raw).hexdigest()
            images[slot] = {'path': str(path), 'size': len(raw), 'sha256': sha,
                            'drive': {'verified': True, 'sha256': sha},
                            'image_review': {'status': 'image_acceptable', 'reason_codes': []}}
        return ({'target_date': '2026-10-08', 'state': 'draft_unreviewed', 'meals': meals, 'daily_comment': '1日の献立です。'},
                {'target_date': '2026-10-08', 'state': 'images_drive_verified', 'images': images})

    def client(self):
        client = Mock()
        records = []
        requests = {}
        client.day_state.side_effect = lambda target: {'contract_version': 'mina-write-v1', 'account': ACCOUNT,
            'date': target, 'checked_at': datetime.now(UTC).isoformat(), 'complete': True,
            'state_version': 'sv1_' + str(len(records)) * 64, 'meal_count': len(records), 'meals': copy.deepcopy(records)}
        def wrap(result, raw=None, daily=False):
            result['normalized_comment'] = result['x']['text'].split('\n\n')[0]
            result['x']['intent_url'] = 'https://twitter.com/intent/tweet?text=example'
            result['x']['fixed_text'] = result['x']['text'].split('\n\n')[1]
            result['x']['publish_text'] = result['x']['text'] + '\n' + result['share']['url']
            source = 'sv1_' + '3' * 64 if daily else 'rv1_' + str(len(records) + 1) * 64
            result['source_state_version' if daily else 'result_version'] = source
            result['ogp'] = {'status': 'ready', 'revision': 'example', 'image_sha256': 'e' * 64,
                'source_state_version' if daily else 'source_result_version': source,
                'image_url': result['share']['url'] + '/og-image', 'expires_at': '2099-01-01T00:00:00Z'}
            if raw:
                result['image_sha256'] = hashlib.sha256(raw).hexdigest()
                result['stored_image_sha256'] = 'a' * 64
            else:
                result['result_version'] = 'rv1_' + 'd' * 64
            return {**copy.deepcopy(result), 'result': result, 'request': {'input_hash': 'ih1_' + 'f' * 64,
                'image_sha256': hashlib.sha256(raw).hexdigest() if raw else None}}
        def register(raw, filename, slot, logged, comment, request_key=None, expected_state_version=None):
            self.assertEqual(expected_state_version, 'sv1_' + str(len(records)) * 64)
            url = 'https://www.log-eats.com/s/abcd_' + slot
            response = wrap({'meal': {'meal_type': slot, 'logged_at': logged}, 'analysis': {
                'foods': [{'name': 'ご飯と魚', 'calories': 600, 'protein': 30, 'fat': 20, 'carbs': 75}],
                'summary': {'totalCalories': 600, 'totalProtein': 30, 'totalFat': 20, 'totalCarbs': 75}, 'is_ambiguous': False},
                'quality': {'status': 'pass', 'warnings': []}, 'share': {'url': url},
                'x': {'text': comment + '\n\n今日の食事解析結果 🔥\n600kcal (P:30g F:20g C:75g)\n' + ' '.join(FIXED_MARKERS)}}, raw)
            records.append(copy.deepcopy(response['result']))
            requests[request_key] = response['result']
            return response
        client.register.side_effect = register
        def daily(path, body, request_key=None):
            response = wrap({'date': body['date'], 'meal_count': 3,
            'summary': {'totalCalories': 1800, 'totalProtein': 90, 'totalFat': 60, 'totalCarbs': 225},
            'share': {'url': 'https://www.log-eats.com/s/abcd_daily'},
                'x': {'text': body['comment'] + '\n\n今日1日の食事まとめ 🔥\n3食 1800kcal (P:90g F:60g C:225g)\n' + ' '.join(FIXED_MARKERS)}}, daily=True)
            requests[request_key] = response['result']
            return response
        client.json_write.side_effect = daily
        client.request_state.side_effect = lambda key, operation: {'contract_version': 'mina-write-v1', 'request_uuid': key,
            'operation': operation, 'status': 'succeeded', 'persistence': 'saved', 'stage': 'completed', 'result_status': 'unchanged',
            'result_hold_reason': None, 'input_hash': 'ih1_' + 'f' * 64, 'image_sha256': requests[key].get('image_sha256'),
            'result': copy.deepcopy(requests[key]), 'current_result': copy.deepcopy(requests[key])}
        return client

    def run_pipeline(self, store, draft, saved, directory, client, ai):
        capture = lambda url, host: {'meta': {'og:image': url + '/og-image', 'image_sha256': 'e' * 64}, 'digest': 'd' * 64, 'review_image': b'\xff\xd8\xffpreview'}
        return register_day(store, draft, saved, directory / 'ogp', client, ai, 'www.log-eats.com', capture)

    def test_success_and_resume_do_not_repeat_registration_or_review(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            draft, saved = self.setup_inputs(directory)
            store, client, ai = Store(directory / 'state.db'), self.client(), Mock()
            ai.review_meal.return_value = ai.review_ogp.return_value = {'status': 'publishable', 'reason_codes': []}
            result = self.run_pipeline(store, draft, saved, directory, client, ai)
            again = self.run_pipeline(store, draft, saved, directory, client, ai)
            self.assertEqual(result, again)
            self.assertEqual(client.register.call_count, 3)
            self.assertEqual(client.json_write.call_count, 1)
            self.assertEqual(ai.review_meal.call_count, 3)
            self.assertEqual(ai.review_ogp.call_count, 4)
            self.assertEqual(result['state'], 'posts_reviewed')
            self.assertFalse(result['history_verified'])

    def test_missing_state_api_stops_before_first_write(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            draft, saved = self.setup_inputs(directory)
            client = self.client()
            client.day_state.side_effect = RuntimeError('meal_state_http_404')
            with self.assertRaises(RuntimeError):
                self.run_pipeline(Store(directory / 'state.db'), draft, saved, directory, client, Mock())
            client.register.assert_not_called()

    def test_unknown_registration_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            draft, saved = self.setup_inputs(directory)
            store, client = Store(directory / 'state.db'), self.client()
            client.register.side_effect = OutcomeUnknown('meal_write_unknown')
            for _ in range(2):
                with self.assertRaises(OutcomeUnknown):
                    self.run_pipeline(store, draft, saved, directory, client, Mock())
            self.assertEqual(client.register.call_count, 1)
            with store.transaction() as db:
                row = db.execute("SELECT result FROM preparation_steps WHERE step='meal_request_key:breakfast'").fetchone()
            import json
            self.assertEqual(client.register.call_args.kwargs['request_key'], json.loads(row['result'])['key'])

    def test_review_hold_does_not_register_remaining_meals(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            draft, saved = self.setup_inputs(directory)
            client, ai = self.client(), Mock()
            ai.review_meal.return_value = {'status': 'hold', 'reason_codes': ['nutrition_semantic_mismatch']}
            with self.assertRaises(ValueError):
                self.run_pipeline(Store(directory / 'state.db'), draft, saved, directory, client, ai)
            self.assertEqual(client.register.call_count, 1)
            client.json_write.assert_not_called()

    def test_unrelated_existing_record_is_rejected(self):
        state = {'date': '2026-10-08', 'account': ACCOUNT, 'checked_at': datetime.now(UTC).isoformat(),
                 'meals': [{'meal_type': 'snack', 'logged_at': datetime.now(UTC).isoformat(), 'share_url': 'existing'}]}
        with self.assertRaises(ValueError):
            assert_day_state(state, '2026-10-08', {}, datetime.now(UTC))
