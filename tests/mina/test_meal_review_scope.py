"""元画像の食品点検と実OGP点検を混同せず、点検指示の版を保持する。"""
import unittest,json
from unittest.mock import Mock,patch
from src.mina.generation import OpenAIClient,REVIEW_INSTRUCTIONS,MEAL_REVIEW_SCOPE,meal_review_policy_sha256,image_review_policy_sha256,publication_context
class MealScopeTest(unittest.TestCase):
 def test_image_scope_stays_unchanged_and_meal_scope_requires_following_ogp_review(self):
  client=OpenAIClient.__new__(OpenAIClient);client.structured=Mock(return_value={'status':'image_acceptable','reason_codes':[]})
  client.review_image(b'\xff\xd8\xffraw',{})
  self.assertTrue(client.structured.call_args.args[0].startswith(REVIEW_INSTRUCTIONS))
  self.assertNotIn(MEAL_REVIEW_SCOPE,client.structured.call_args.args[0])
  self.assertIn('findings',client.structured.call_args.args[2]['properties'])
  image_policy=image_review_policy_sha256();meal_policy=meal_review_policy_sha256()
  client.structured.return_value={'status':'publishable','reason_codes':[]}
  client.review_meal(b'\xff\xd8\xffraw',{}, {'result':{'meal':{'logged_at':'2026-10-16T03:15:00.000Z','meal_type':'lunch'}}},{})
  self.assertIn(MEAL_REVIEW_SCOPE,client.structured.call_args.args[0])
  context=json.loads(client.structured.call_args.args[1][0]['text'])['publication_context']
  self.assertEqual(context['meal_logged_at_jst'],'2026-10-16T12:15:00+09:00')
  self.assertEqual(context['publication_date_jst'],'2026-10-16')
  with patch('src.mina.generation.MEAL_REVIEW_SCOPE',MEAL_REVIEW_SCOPE+'担当範囲の変更'):
   self.assertEqual(image_review_policy_sha256(),image_policy)
   self.assertNotEqual(meal_review_policy_sha256(),meal_policy)
 def test_utc_previous_date_is_next_day_breakfast_in_jst(self):
  context=publication_context({'meal':{'logged_at':'2026-10-15T22:30:00.000Z','meal_type':'breakfast'}})
  self.assertEqual(context['meal_logged_at_jst'],'2026-10-16T07:30:00+09:00')
  self.assertEqual(context['weekday_jst'],'金')
