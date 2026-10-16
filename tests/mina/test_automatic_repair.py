"""通常CLIで品質保留を限定修復し、重複送信・判定のすり抜けを防ぐ。"""

import json
import tempfile
import unittest
from pathlib import Path

from src.mina.clients import MealWriteFailure, OutcomeUnknown
from src.mina.runtime import notify_day, send_notifications
from unittest.mock import Mock
import test_nightly_flow as nightly

NOW, TARGET = nightly.NOW, nightly.TARGET


class AutomaticRepairTests(unittest.TestCase):
    def setup_flow(self, root):
        fixture = nightly.NightlyFlowTests()
        flow = fixture.setup_flow(root, 'plain')
        return flow, lambda: fixture.prepare(root, flow, 'plain')

    def image_holds(self, ai, slots, permanent=False, reason='image_analysis_mismatch'):
        seen = set()
        def review(raw, meal):
            slot = next(slot for slot, value in ai.plan['meals'].items() if value == meal)
            hold = slot in slots and (permanent or slot not in seen)
            seen.add(slot)
            return {'status':'hold','reason_codes':[reason]} if hold else {'status':'image_acceptable','reason_codes':[]}
        ai.review_image = review

    def terminal_failure(self, meal, slots, permanent=False, lookup_mode='terminal'):
        register, lookup = meal.register, meal.request_state
        failures, failed_slots = set(), set()
        def send(raw, filename, slot, logged, comment, request_key, expected_state_version):
            if slot in slots and (permanent or slot not in failed_slots):
                meal.calls.append(('POST',slot,request_key))
                failures.add(request_key); failed_slots.add(slot)
                raise MealWriteFailure(422,{'persistence':'not_saved','stage':'quality','reason':'quality_review_required'})
            return register(raw,filename,slot,logged,comment,request_key,expected_state_version)
        def read(key, operation):
            if key not in failures:
                return lookup(key,operation)
            if lookup_mode == '404':
                raise RuntimeError('404')
            return {'contract_version':'mina-write-v1','request_uuid':key,'operation':operation,
                'status':'processing' if lookup_mode=='processing' else 'failed',
                'persistence': 'saved' if lookup_mode=='saved' else 'not_saved',
                'stage':'quality','reason':'quality_review_required','result_status':'unresolved',
                'result':None,'current_result':None}
        meal.register, meal.request_state = send, read
        return failures

    def test_three_image_holds_recover_once_each_and_deliver_four_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); flow, prepare=self.setup_flow(root)
            store,clock,ai,drive,meal,_=flow
            self.image_holds(ai, {'breakfast','lunch','dinner'})
            prepare()
            self.assertEqual((ai.images,len(drive.uploads),len(meal.calls)),(6,3,4))
            notify_day(store,TARGET,NOW.replace(hour=20));discord=Mock()
            self.assertEqual(send_notifications(store,discord),4)
            self.assertEqual(len(store.list_day(TARGET)),4)
            with store.transaction() as db:
                reviews=[json.loads(row[0]) for row in db.execute("SELECT result FROM preparation_steps WHERE step IN ('image_review:breakfast','image_review:lunch','image_review:dinner')")]
                self.assertEqual([value['status'] for value in reviews],['hold']*3)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM preparation_steps WHERE step LIKE 'automatic_image_repair:%'").fetchone()[0],3)
            prepare()
            self.assertEqual((ai.images,len(meal.calls)),(6,4))


    def test_repeated_image_hold_or_non_image_reason_never_passes(self):
        for reason in ('image_analysis_mismatch','context_mismatch','health_claim'):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);flow,prepare=self.setup_flow(root)
                self.image_holds(flow[2], {'breakfast'}, permanent=True, reason=reason)
                for _ in range(2):
                    with self.assertRaises((RuntimeError,ValueError)): prepare()
                self.assertEqual(flow[2].images,2 if reason=='image_analysis_mismatch' else 1)
                self.assertEqual(flow[4].calls,[])
                self.assertEqual(flow[0].list_day(TARGET),[])


    def test_unknown_processing_saved_and_missing_get_never_generate_replacement(self):
        for mode in ('processing','saved','404'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);flow,prepare=self.setup_flow(root)
                self.terminal_failure(flow[4], {'breakfast'}, lookup_mode=mode)
                for _ in range(2):
                    with self.assertRaises((RuntimeError,ValueError)):prepare()
                self.assertEqual((flow[2].images,len(flow[4].calls)),(3,1))
                self.assertEqual(flow[0].list_day(TARGET),[])

    def test_unknown_generation_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);flow,prepare=self.setup_flow(root)
            def image(description):
                flow[2].images+=1
                raise OutcomeUnknown('生成の応答不明')
            flow[2].image=image
            for _ in range(2):
                with self.assertRaises((RuntimeError,ValueError)):prepare()
            self.assertEqual(flow[2].images,1)
            self.assertEqual(flow[4].calls,[])




    def test_deadline_during_repair_prevents_new_review_or_registration(self):
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);flow,prepare=self.setup_flow(root)
            self.image_holds(flow[2], {'breakfast'})
            image=flow[2].image
            def generate(description):
                result=image(description)
                if flow[2].images==2:flow[1][0]+=timedelta(hours=1)
                return result
            flow[2].image=generate
            with self.assertRaises(RuntimeError):prepare()
            self.assertEqual(flow[2].images,2)
            self.assertEqual(flow[4].calls,[])

    def test_quality_rejection_is_held_without_regeneration_or_new_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);flow,prepare=self.setup_flow(root)
            self.terminal_failure(flow[4], {'breakfast'}, permanent=True)
            for _ in range(2):
                with self.assertRaises((RuntimeError,ValueError)):prepare()
            self.assertEqual((flow[2].images,len(flow[3].uploads),len(flow[4].calls)),(3,3,1))
            with flow[0].transaction() as db:
                self.assertEqual(db.execute('SELECT status FROM preparation_progress').fetchone()[0],'quality_hold')
                self.assertIn('api_quality_rejected_not_saved',db.execute('SELECT reasons FROM preparation_progress').fetchone()[0])
            self.assertEqual(flow[0].list_day(TARGET),[])
