"""翌日分の明示再開で、未知要求・再実行・期限外の書込みを防ぐ。"""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.domain import JST
from src.mina.store import Store
from src.mina.preparation import PreparationJournal
from src.mina.request_ledger import RequestLedger
from src.mina.resume_preparation import resume_preparation
from test_readiness import evidence,reviewed
from test_daily import TARGET,HOST

NOW=datetime(2026,10,7,20,5,tzinfo=JST)
KEY='11111111-1111-4111-8111-111111111111'

class ResumePreparationTests(unittest.TestCase):
    def setup(self,root):
        store=Store(root/'db');journal=PreparationJournal(store)
        with store.transaction() as db:
            db.execute("INSERT INTO preparation VALUES(?,'held:registration',?)",(TARGET,NOW.timestamp()))
        requests=RequestLedger(store);requests.prepare(KEY,'meal_register',TARGET,'breakfast',{})
        requests.record(KEY,'complete',{'persistence':'saved'})
        journal.run(TARGET,'meal_review:breakfast',{},lambda:{'status':'hold','reason_codes':['nutrition_semantic_mismatch']})
        base=root/'daily'/TARGET;base.mkdir(parents=True)
        for name in ('draft.json','drive.json'):(base/name).write_text(json.dumps({'target_date':TARGET}))
        history=root/'history';history.mkdir();(history/'1.json').write_text(json.dumps(evidence()))
        return store,history

    def invoke(self,root,store,history,register,now=NOW):
        with patch('src.mina.resume_preparation.release_commit',return_value='a'*40), \
             patch('src.mina.resume_preparation.register_day',register), \
             patch('src.mina.resume_preparation.verify_source'):
            return resume_preparation(store,root/'daily',TARGET,KEY,root,'a'*40,HOST,Mock(),Mock(),history,clock=lambda:now)

    def test_completed_breakfast_uses_existing_directory_and_four_atomic_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store,history=self.setup(root);register=Mock(return_value=reviewed())
            result=self.invoke(root,store,history,register)
            self.assertEqual(result['post_count'],4)
            self.assertTrue(register.call_args.kwargs['allow_correction'])
            self.assertEqual(register.call_args.args[3],root/'daily'/TARGET/'ogp')
            self.assertEqual(len(store.list_day(TARGET)),4)
            cards=store.issue_cards(TARGET,NOW)
            self.assertEqual(len(cards),4)
            self.assertIn('朝食',cards[0]['content']);self.assertIn('1日まとめ',cards[-1]['content'])
            with self.assertRaises(ValueError):self.invoke(root,store,history,register)
            self.assertEqual(register.call_count,1)

    def test_unknown_write_stops_before_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store,history=self.setup(root);register=Mock()
            with store.transaction() as db:db.execute("UPDATE mina_requests SET phase='held'")
            with self.assertRaisesRegex(ValueError,'未知状態'):self.invoke(root,store,history,register)
            register.assert_not_called()

    def test_review_failure_does_not_loop_or_create_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store,history=self.setup(root);register=Mock(side_effect=ValueError('品質保留'))
            with self.assertRaises(RuntimeError):self.invoke(root,store,history,register)
            with self.assertRaises(ValueError):self.invoke(root,store,history,register)
            self.assertEqual(register.call_count,1)
            self.assertEqual(store.list_day(TARGET),[])

    def test_expired_window_stops_before_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store,history=self.setup(root);register=Mock()
            with self.assertRaises(ValueError):self.invoke(root,store,history,register,datetime(2026,10,7,23,tzinfo=JST))
            register.assert_not_called()

    def test_known_saved_corrections_allow_one_context_review_without_resending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store,history=self.setup(root);register=Mock(return_value=reviewed())
            requests=RequestLedger(store);journal=PreparationJournal(store)
            for index,(slot,operation) in enumerate((('breakfast','meal_correction'),('lunch','meal_register'),('lunch','meal_correction')),2):
                key=f'{index:08d}-1111-4111-8111-111111111111'
                requests.prepare(key,operation,TARGET,slot,{})
                requests.record(key,'complete',{'persistence':'saved'})
            journal.run(TARGET,'meal_review:lunch:corrected',{},lambda:{'status':'hold','reason_codes':['context_mismatch']})
            with self.assertRaises(ValueError):
                journal.run(TARGET,'explicit_preparation_resume',{},lambda:(_ for _ in ()).throw(ValueError('文脈保留')))
            with store.transaction() as db:db.execute("UPDATE preparation SET state='held:explicit_resume'")
            self.assertEqual(self.invoke(root,store,history,register)['post_count'],4)
            self.assertEqual(register.call_args.kwargs['capture_round'],KEY)
            with store.transaction() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM mina_requests').fetchone()[0],4)
                self.assertEqual(db.execute("SELECT state FROM preparation_steps WHERE step='explicit_preparation_resume'").fetchone()[0],'outcome_unknown')
