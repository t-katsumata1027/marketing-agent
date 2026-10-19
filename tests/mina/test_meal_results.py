"""固定文・栄養値・対象時刻を別の値へすり替えて通せないことを検証する。"""

import copy
import unittest

from src.mina.meal_results import validate_daily_response, validate_meal_response


HOST = 'www.log-eats.com'
LOGGED = '2026-10-08T07:15:00+09:00'
COMMENT = '朝の組み合わせは納豆と卵🥚'
MARKERS = '#AI食事解析 #LogEats #食事記録 @EatsLog88161'


def response():
    return {'meal': {'meal_type': 'breakfast', 'logged_at': LOGGED},
            'analysis': {'is_ambiguous': False,
                         'foods': [{'name': 'ごはんと納豆と卵', 'amount': '1人前', 'calories': 500.5, 'protein': 20, 'fat': 10.5, 'carbs': 81.5}],
                         'summary': {'totalCalories': 500.5, 'totalProtein': 20, 'totalFat': 10.5, 'totalCarbs': 81.5}},
            'quality': {'status': 'pass', 'warnings': []}, 'share': {'url': 'https://www.log-eats.com/s/abcd1234'},
            'x': {'text': COMMENT + '\n\n今日の食事解析結果 🔥\n501kcal (P:20g F:11g C:82g)\n' + MARKERS}}


class MealResultTests(unittest.TestCase):
    def validate(self, result):
        return validate_meal_response(result, 'breakfast', LOGGED, COMMENT, HOST)

    def test_comment_only_removed_from_exact_prefix_and_half_rounds_like_api(self):
        result = self.validate(response())
        self.assertEqual(result['comment'], COMMENT)
        self.assertNotIn(COMMENT, result['api_text'])
        self.assertIn('501kcal (P:20g F:11g C:82g)', result['api_text'])

    def test_utc_representation_is_same_logged_instant(self):
        result = response()
        result['meal']['logged_at'] = '2026-10-07T22:15:00Z'
        self.validate(result)

    def test_pass_flag_does_not_hide_mismatched_nutrition_or_text(self):
        for edit in ('summary', 'foods', 'text', 'time', 'ambiguous', 'host', 'prefix'):
            with self.subTest(edit=edit):
                result = response()
                if edit == 'summary':
                    result['analysis']['summary']['totalProtein'] = True
                elif edit == 'foods':
                    result['analysis']['foods'][0]['carbs'] = 1
                elif edit == 'text':
                    result['x']['text'] = result['x']['text'].replace('501kcal', '900kcal')
                elif edit == 'time':
                    result['meal']['logged_at'] = '2026-10-09T07:15:00+09:00'
                elif edit == 'ambiguous':
                    result['analysis']['is_ambiguous'] = None
                elif edit == 'host':
                    result['share']['url'] = 'https://other.example/s/abcd1234'
                else:
                    result['x']['text'] = '別のコメント\n\n' + result['x']['text']
                with self.assertRaises(ValueError):
                    self.validate(result)

    def test_daily_requires_exactly_three_meals_and_matching_aggregate(self):
        meal = self.validate(response())
        meals = {slot: copy.deepcopy(meal) for slot in ('breakfast', 'lunch', 'dinner')}
        daily = {'date': '2026-10-08', 'meal_count': 3,
                 'summary': {'totalCalories': 1501.5, 'totalProtein': 60, 'totalFat': 31.5, 'totalCarbs': 244.5},
                 'share': {'url': 'https://www.log-eats.com/s/daily123'},
                 'x': {'text': '今日1日の食事まとめ 🔥\n3食 1502kcal (P:60g F:32g C:245g)\n' + MARKERS}}
        validate_daily_response(daily, '2026-10-08', '', HOST, meals)
        for field, value in (('meal_count', 4), ('meal_count', True), ('date', '2026-10-07')):
            changed = copy.deepcopy(daily)
            changed[field] = value
            with self.assertRaises(ValueError):
                validate_daily_response(changed, '2026-10-08', '', HOST, meals)
        daily['summary']['totalCalories'] += 30
        with self.assertRaises(ValueError):
            validate_daily_response(daily, '2026-10-08', '', HOST, meals)
