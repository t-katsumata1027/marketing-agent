"""元画像の食品点検と実OGP点検を混同せず、点検指示の版を保持する。"""
import unittest
from unittest.mock import Mock,patch
from src.mina.generation import OpenAIClient,REVIEW_INSTRUCTIONS,MEAL_REVIEW_SCOPE,meal_review_policy_sha256,image_review_policy_sha256
class MealScopeTest(unittest.TestCase):
 def test_image_scope_stays_unchanged_and_meal_scope_requires_following_ogp_review(self):
  client=OpenAIClient.__new__(OpenAIClient);client.structured=Mock(return_value={'status':'image_acceptable','reason_codes':[]})
  client.review_image(b'\xff\xd8\xffraw',{})
  self.assertEqual(client.structured.call_args.args[0],REVIEW_INSTRUCTIONS)
  image_policy=image_review_policy_sha256();meal_policy=meal_review_policy_sha256()
  client.structured.return_value={'status':'publishable','reason_codes':[]}
  client.review_meal(b'\xff\xd8\xffraw',{}, {'result':{}},{})
  self.assertIn(MEAL_REVIEW_SCOPE,client.structured.call_args.args[0])
  with patch('src.mina.generation.MEAL_REVIEW_SCOPE',MEAL_REVIEW_SCOPE+'担当範囲の変更'):
   self.assertEqual(image_review_policy_sha256(),image_policy)
   self.assertNotEqual(meal_review_policy_sha256(),meal_policy)
