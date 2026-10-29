"""料理/材料の対応と、部分日入口の固定境界を隔離する。"""
import copy,unittest
from unittest.mock import Mock,patch
from src.mina.partial_day_continuation import validate_mapping,PartialGate
from src.mina.current_day_continuation import ContinuationGate

class PartialTests(unittest.TestCase):
    def setUp(self):
        names=['食パン','卵','牛乳','砂糖','バター','プレーンヨーグルト','アーモンド','オレンジ']
        self.draft={'meals':{'breakfast':{'foods':[{'name':n,'amount':'50g'} for n in names]}}}
        self.response={'result':{'analysis':{'foods':[{'name':n} for n in ['フレンチトースト','オレンジ','ヨーグルト（アーモンドトッピング）']]}}}
        self.review={'status':'hold','checks':dict.fromkeys(('composition','style','food_identity','visible_portions'),'pass'),'blocking':[{'code':'nutrition_inconsistent'}]}
        self.review['checks']['nutrition']='hold'
    def test_complete_mapping_without_added_or_missing_ingredients(self):
        result=validate_mapping(self.draft,self.response,self.review)
        self.assertEqual(sorted(i for r in result for i in r['planned_indices']),list(range(8)))
    def test_other_dish_is_not_accepted(self):
        self.response['result']['analysis']['foods'][0]['name']='別料理'
        with self.assertRaises(ValueError):validate_mapping(self.draft,self.response,self.review)
    def test_image_hold_is_not_accepted(self):
        self.review['checks']['visible_portions']='hold'
        with self.assertRaises(ValueError):validate_mapping(self.draft,self.response,self.review)
    def test_different_materials_are_not_accepted(self):
        self.draft['meals']['breakfast']['foods'].pop()
        with self.assertRaises(ValueError):validate_mapping(self.draft,self.response,self.review)
    def test_original_zero_meal_gate_still_rejects_saved_day(self):
        with self.assertRaises(ValueError):ContinuationGate.validate_initial_state(Mock(),{'meal_count':1,'daily_share':None})
    def test_partial_gate_rejects_two_meals_and_existing_daily(self):
        for state in ({'meal_count':2,'daily_share':None},{'meal_count':1,'daily_share':{}}):
            with self.assertRaises(ValueError):PartialGate.validate_initial_state(Mock(),state)
