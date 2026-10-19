"""一食の再計画から通常準備へ復帰し、他枠や保存済み要求を壊さないことを確認する。"""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
import test_automatic_repair as fixtures
from src.mina.meal_replanning import MealReplanner
from src.mina.preparation_status import QualityHold
from src.mina.request_ledger import RequestLedger


class MealReplanningTests(unittest.TestCase):
    def test_food_level_budget_shortfall_is_replanned_before_any_image_or_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture=fixtures.AutomaticRepairTests();flow,prepare=fixture.setup_flow(root)
            structured=flow[2].structured
            calls=[]
            def estimate(instructions,content,schema,name):
                if name=='mina_menu_nutrition_check':
                    context=json.loads(content)
                    changed=context['foods']['dinner'][1]['name']!='魚'
                    calls.append((flow[2].images,len(flow[4].calls)))
                    return {'breakfast':[300,300],'lunch':[300,300],'dinner':[300,300] if changed else [200,200]}
                return structured(instructions,content,schema,name)
            flow[2].structured=estimate
            prepare()
            self.assertEqual(calls,[(0,0),(0,0)])
            self.assertEqual(flow[2].images,3)
            self.assertEqual(len(flow[0].list_day('2026-10-08')),4)

    def test_two_image_failures_replan_only_lunch_and_finish_four_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture=fixtures.AutomaticRepairTests();flow,prepare=fixture.setup_flow(root)
            original=json.loads(json.dumps(flow[2].plan))
            def review(raw,meal):
                if meal==original['meals']['lunch']:
                    return {'status':'hold','reason_codes':['image_analysis_mismatch']}
                return {'status':'image_acceptable','reason_codes':[]}
            flow[2].review_image=review
            prepare()
            draft=json.loads((root/'daily/2026-10-08/draft.json').read_text(encoding='utf-8'))
            self.assertEqual(draft['meals']['breakfast'],original['meals']['breakfast'])
            self.assertEqual(draft['meals']['dinner'],original['meals']['dinner'])
            self.assertNotEqual(draft['meals']['lunch']['foods'],original['meals']['lunch']['foods'])
            self.assertEqual(draft['daily_comment'],original['daily_comment'])
            self.assertEqual((flow[2].images,len(flow[3].uploads),len(flow[4].calls)),(5,3,4))
            self.assertEqual(len(flow[0].list_day('2026-10-08')),4)
            archived=list((root/'daily/2026-10-08/plan-revisions').glob('*/previous-draft.json'))
            self.assertEqual(len(archived),1)
            self.assertEqual(json.loads(archived[0].read_text(encoding='utf-8'))['meals'],original['meals'])

    def test_unsaved_lunch_and_dinner_replan_without_reposting_saved_breakfast(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture=fixtures.AutomaticRepairTests();flow,prepare=fixture.setup_flow(root)
            fixture.terminal_failure(flow[4],{'lunch','dinner'})
            prepare()
            calls=[(method,slot) for method,slot,key in flow[4].calls]
            self.assertEqual(calls.count(('POST','breakfast')),1)
            self.assertEqual(calls.count(('POST','lunch')),2)
            self.assertEqual(calls.count(('POST','dinner')),2)
            self.assertEqual(len({key for method,slot,key in flow[4].calls}),len(flow[4].calls))
            self.assertEqual(len(flow[0].list_day('2026-10-08')),4)
            self.assertEqual(len(flow[3].uploads),5)
            prepare()
            self.assertEqual(len(flow[4].calls),6)

    def test_existing_saved_or_unknown_request_is_never_replanned(self):
        for phase,persistence in (('complete','saved'),('dispatching','unknown'),('held','unknown')):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);fixture=fixtures.AutomaticRepairTests();flow,_=fixture.setup_flow(root)
                draft={'target_date':'2026-10-08',**flow[2].plan}
                path=root/'draft.json';path.write_text(json.dumps(draft))
                ledger=RequestLedger(flow[0]);key='11111111-1111-4111-8111-111111111111'
                ledger.prepare(key,'meal_register','2026-10-08','lunch',{},b'original')
                ledger.record(key,phase,{'persistence':persistence})
                ai=Mock();planner=MealReplanner(flow[0],draft,path,ai)
                with self.assertRaises(ValueError):planner.replan('lunch',QualityHold('保留','image_review:lunch',('image_analysis_mismatch',)),Mock())
                ai.structured.assert_not_called()
                self.assertEqual(json.loads(path.read_text()),draft)

    def test_wrong_day_or_slot_in_positive_get_does_not_allow_replanning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture=fixtures.AutomaticRepairTests();flow,_=fixture.setup_flow(root)
            draft={'target_date':'2026-10-08',**flow[2].plan};path=root/'draft.json';path.write_text(json.dumps(draft))
            ledger=RequestLedger(flow[0]);key='11111111-1111-4111-8111-111111111111'
            ledger.prepare(key,'meal_register','2026-10-08','lunch',{},b'original')
            ledger.record(key,'held',{'persistence':'not_saved'})
            client=Mock();client.request_state.side_effect=RuntimeError('照会不能')
            ai=Mock()
            with self.assertRaises(RuntimeError):MealReplanner(flow[0],draft,path,ai).replan('lunch',QualityHold('保留','meal_register:lunch',('api_quality_rejected_not_saved',)),client)
            ai.structured.assert_not_called()


if __name__=='__main__':unittest.main()
