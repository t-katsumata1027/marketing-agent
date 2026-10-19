"""新しい要求証跡への移行が再登録・旧承認の再利用を伴わないことを検証する。"""

import copy
import json
from unittest.mock import patch

import test_rereview as previous
import test_readiness as readiness
from test_daily import NOW, NOTIFY, TARGET
from src.mina.rebinding import rebind_day


class RebindingTests(previous.RereviewTests):
    # 継承元のケースはこのクラスで再実行しない。
    test_review_returns_all_four_to_new_approval_and_rejects_old_button = None
    test_changed_source_stops_before_review_or_reimport = None
    test_started_dispatch_is_never_reactivated = None
    test_approval_race_aborts_whole_import = None

    def setUp(self):
        super().setUp()
        self.rebind_release = patch('src.mina.rebinding.release_commit', return_value='b' * 40)
        self.rebind_release.start()
        self.addCleanup(self.rebind_release.stop)
        self.version = 'sv1_' + '9' * 64
        self.results, self.bindings = {}, {'target_date': TARGET, 'requests': {}}
        for i, row in enumerate(self.store.list_day(TARGET)):
            slot = row['slot']
            old = json.loads(row['payload'])
            operation = 'daily_share' if slot == 'daily' else ('meal_correction' if slot == 'breakfast' else 'meal_register')
            old_lookup = self.client.request_state(old['approval_source']['request_key'], old['approval_source']['operation'])
            result = copy.deepcopy(old_lookup['current_result'])
            result['result_version'] = 'rv1_' + str(i + 5) * 64
            if slot == 'daily':
                result['source_state_version'] = self.version
                result['ogp']['source_state_version'] = self.version
            else:
                result['ogp']['source_result_version'] = result['result_version']
            key = f'33333333-3333-4333-8333-33333333333{i}'
            self.results[key] = result
            self.bindings['requests'][slot] = {'request_key': key, 'operation': operation}
        def lookup(key, operation):
            result = self.results[key]
            return {'contract_version': 'mina-write-v1', 'request_uuid': key, 'operation': operation, 'status': 'succeeded',
                    'persistence': 'saved', 'stage': 'completed', 'reason': None, 'result_status': 'unchanged',
                    'result_hold_reason': None, 'current_state_version': self.version, 'input_hash': 'ih1_' + 'e' * 64,
                    'current_result': copy.deepcopy(result), 'result': copy.deepcopy(result)}
        self.client.request_state.side_effect = lookup
        self.state = {'contract_version': 'mina-write-v1', 'account': 'EatsLog88161', 'date': TARGET,
                      'checked_at': NOW.isoformat(), 'complete': True, 'state_version': self.version, 'meal_count': 3,
                      'meals': [copy.deepcopy(self.results[self.bindings['requests'][slot]['request_key']]) for slot in self.images],
                      'daily_share': copy.deepcopy(self.results[self.bindings['requests']['daily']['request_key']])}
        self.client.day_state.side_effect = lambda target: copy.deepcopy(self.state)

    def rebind(self):
        return rebind_day(self.store, TARGET, '44444444-4444-4444-8444-444444444444', self.bindings,
                          readiness.evidence(), self.images, self.draft, self.ai, self.client, 'www.log-eats.com',
                          self.root / 'replacement.json', lambda: NOW, self.capture, 'b' * 40, self.root)

    def test_completed_new_requests_are_reviewed_and_imported_without_writes(self):
        with self.store.transaction() as db:
            db.execute("UPDATE candidates SET state='approved',actor='old',approved=1")
        with patch('src.mina.rereview.verify_source'):
            self.assertEqual(self.rebind(), TARGET)
        rows = self.store.list_day(TARGET)
        self.assertEqual({row['state'] for row in rows}, {'approval_pending'})
        self.assertEqual({row['revision'] for row in rows}, {2})
        breakfast = json.loads(next(row['payload'] for row in rows if row['slot'] == 'breakfast'))
        self.assertEqual(breakfast['approval_source']['operation'], 'meal_correction')
        self.assertEqual(breakfast['approval_source']['state_version'], self.version)
        self.assertEqual(self.client.register.call_count, 3)
        self.assertEqual(self.client.json_write.call_count, 1)
        self.assertEqual(self.ai.review_meal.call_count, 3)

    def test_failed_or_modified_request_keeps_old_approval_revoked(self):
        original = self.client.request_state.side_effect
        def modified(key, operation):
            return {**original(key, operation), 'result_status': 'modified'}
        self.client.request_state.side_effect = modified
        with self.assertRaises(ValueError):
            self.rebind()
        self.assertEqual({row['state'] for row in self.store.list_day(TARGET)}, {'changes_requested'})
        self.ai.review_meal.assert_not_called()

    def test_wrong_original_stops_before_ai_and_import(self):
        key = self.bindings['requests']['breakfast']['request_key']
        self.results[key]['image_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            self.rebind()
        self.ai.review_ogp.assert_not_called()

    def test_terminal_x_state_prevents_even_reads(self):
        with self.store.transaction() as db:
            db.execute("UPDATE candidates SET state='published' WHERE slot='breakfast'")
        self.client.day_state.reset_mock()
        with self.assertRaises(ValueError):
            self.rebind()
        self.client.day_state.assert_not_called()

    def test_candidate_change_during_reads_is_not_overwritten(self):
        from dataclasses import replace
        from src.mina.domain import Candidate
        original = self.client.request_state.side_effect
        def changed(key, operation):
            row = self.store.get(TARGET, 'breakfast')
            candidate = Candidate(**json.loads(row['payload']))
            self.store.save(replace(candidate, menu='別の候補へ変更'), 'www.log-eats.com', NOW)
            self.client.request_state.side_effect = original
            return original(key, operation)
        self.client.request_state.side_effect = changed
        with self.assertRaises(ValueError):
            self.rebind()
        row = self.store.get(TARGET, 'breakfast')
        self.assertEqual(json.loads(row['payload'])['menu'], '別の候補へ変更')
        self.assertEqual(self.ai.review_meal.call_count, 0)

    def test_independent_review_hold_does_not_import_any_new_candidate(self):
        self.ai.review_meal.return_value = {'status': 'hold', 'reason_codes': ['nutrition_semantic_mismatch']}
        with self.assertRaises(ValueError):
            self.rebind()
        self.assertEqual({row['revision'] for row in self.store.list_day(TARGET)}, {1})
        self.assertEqual({row['state'] for row in self.store.list_day(TARGET)}, {'changes_requested'})
