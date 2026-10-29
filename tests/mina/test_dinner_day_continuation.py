"""一次資料の決定論的換算と、変更2行だけの固定を検証する。"""
import copy,json,tempfile,unittest
from pathlib import Path
from src.mina.dinner_day_continuation import proposal

class DinnerTests(unittest.TestCase):
    def test_reference_data_only_for_dinner_and_not_a_policy_change(self):
        from unittest.mock import Mock
        from src.mina.dinner_day_continuation import DinnerGate
        from src.mina.tolerance_case import ReviewedAI
        gate=Mock();gate.snapshot={'dinner':{'proposal':{'foods':[]}}};gate.review_evidence=lambda r:DinnerGate.review_evidence(gate,r)
        ai=ReviewedAI(gate,None)
        breakfast={'result':{'meal':{'meal_type':'breakfast'}}}
        self.assertIs(ai.review_response(breakfast),breakfast)
        dinner={'result':{'meal':{'meal_type':'dinner'}}};reviewed=ai.review_response(dinner)
        self.assertNotIn('secondary_review_evidence',dinner)
        self.assertFalse(reviewed['secondary_review_evidence']['measured_from_image'])
        self.assertEqual(reviewed['result'],dinner['result'])
    def setUp(self):
        self.meal={'foods':[{'name':'固定','amount':'1g'} for _ in range(16)]}
        self.meal['foods'][7]={'name':'キャベツ','amount':'80g（千切り）'};self.meal['foods'][8]={'name':'ごはん','amount':'炊飯後220g'}
        self.foods=[{'name':n,'amount':'1人前','calories':e,'protein':p,'fat':f,'carbs':c} for n,e,p,f,c in [
            ('豚の生姜焼き（玉ねぎ入り）',379,26.1,23.8,15.3),('千切りキャベツ',63,3,.5,11.7),('白米',242,3.8,.5,55.7),('きゅうりの浅漬け',13,.5,.1,2.7),('味噌汁（豆腐とわかめ）',43,3,1.2,5)]]
        self.original={'result':{'analysis':{'foods':self.foods}}}
    def test_exact_two_entries_and_decimal_reference(self):
        result=proposal(self.meal,self.original)['foods']
        self.assertEqual([i for i,(old,new) in enumerate(zip(self.foods,result)) if old!=new],[1,2])
        self.assertEqual(result[1]['calories'],18.4);self.assertEqual(result[2]['calories'],343.2)
        self.assertEqual(result[1]['carbs'],4.16);self.assertEqual(result[2]['carbs'],81.62)
        self.assertAlmostEqual(sum(f['calories'] for f in result),796.6)
        self.assertEqual(self.original['result']['analysis']['foods'],self.foods)
    def test_changed_plan_or_food_refused(self):
        self.meal['foods'][8]['amount']='炊飯後200g'
        with self.assertRaises(ValueError):proposal(self.meal,self.original)
        self.meal['foods'][8]['amount']='炊飯後220g';self.foods[2]['name']='別食品'
        with self.assertRaises(ValueError):proposal(self.meal,self.original)
    def test_large_evidence_is_only_explicit(self):
        from src.mina.current_day_cli import protected_json
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'proof.json';path.write_text(json.dumps({'evidence':'x'*300000}),encoding='utf-8')
            with self.assertRaises(ValueError):protected_json(path)
            with self.assertRaises(ValueError):protected_json(path,max_bytes=262144)
            self.assertEqual(len(protected_json(path,max_bytes=524288)[0]['evidence']),300000)
