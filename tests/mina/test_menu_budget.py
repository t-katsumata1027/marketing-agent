"""概算の二重基準、根拠の漂流、生成拒否の誤再送を外部呼出しなしで再現する。"""
import copy
import json
import os
import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock,patch

from nightly_fakes import NightlyAI
from src.mina.codex_client import CodexClient
from src.mina.generation import validate_plan
from src.mina.generation_receipts import GeneratedPlanRejected,rejection
from src.mina.meal_replanning import MealReplanner
from src.mina.menu_budget import MenuBudget,PlanBudget,foods,validate_draft_plan
from src.mina.preparation import PreparationJournal,fingerprint
from src.mina.store import Store


class MenuBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root/'db')
        self.draft={'target_date':'2026-10-29','state':'draft_unreviewed',**NightlyAI('plain',[]).plan}
        for slot,amount in zip(self.draft['meals'],(590,680,660)):
            self.draft['meals'][slot]['estimated_calories']=amount
        self.path=self.root/'draft.json'
        self.original=json.dumps(self.draft,ensure_ascii=False,indent=2).encode()
        self.path.write_bytes(self.original)
        self.journal=PreparationJournal(self.store)
        self.budget=MenuBudget(self.store,self.draft['target_date'],self.journal)
        self.calls=[];self.ai=Mock();self.ai.structured.side_effect=self.estimate

    def estimate(self,instructions,content,schema,name):
        context=json.loads(content)
        if name=='mina_menu_nutrition_check':
            self.calls.append(tuple(context['foods']))
            amounts={'breakfast':483,'lunch':578,'dinner':621.4}
            return {s:[amounts[s]/2]*2 if fs[1]['name']=='魚' else [419.5,419.5] for s,fs in context['foods'].items()}
        if name=='mina_meal_replan':
            self.assertEqual(context['nutrition_budget']['verified_estimates']['breakfast'],'483.0')
            candidate=copy.deepcopy(context['previous_meal']);candidate['foods'][1]['name']='鶏肉'
            candidate['menu']='鶏肉の献立';candidate['estimated_calories']=839
            return candidate
        raise AssertionError('未定義の生成')

    def test_conflicting_old_total_uses_fixed_food_basis_and_keeps_other_meals(self):
        before=copy.deepcopy(self.draft)
        planner=MealReplanner(self.store,self.draft,self.path,self.ai)
        planner.check_budget()
        self.assertEqual(self.calls,[('breakfast','lunch','dinner'),('dinner',)])
        self.assertEqual(self.draft['meals']['breakfast'],before['meals']['breakfast'])
        self.assertEqual(self.draft['meals']['lunch'],before['meals']['lunch'])
        self.assertEqual(sum(m['estimated_calories'] for m in self.draft['meals'].values()),2109)
        validate_draft_plan(self.draft,self.store)
        with self.assertRaises(ValueError):validate_plan({k:self.draft[k] for k in ('meals','daily_comment')})
        old=list((self.root/'plan-revisions').glob('*/previous-draft.json'))
        self.assertEqual(old[0].read_bytes(),self.original)
        planner.check_budget();self.assertEqual(len(self.calls),2)

    def test_precise_boundaries_do_not_round_failure_to_success(self):
        for total,accepted in (('1799.4',False),('1799.99',False),('1800',True),('2000',True),('2000.01',False)):
            with self.subTest(total=total):
                basis=PlanBudget(self.draft['target_date'],fingerprint(foods(self.draft)),(Decimal(600),Decimal(600),Decimal(total)-1200))
                def check():validate_plan({k:self.draft[k] for k in ('meals','daily_comment')},calorie_basis=basis,target_date=self.draft['target_date'])
                if accepted:check()
                else:
                    with self.assertRaises(ValueError):check()

    def test_changed_food_requires_new_admission_and_tampered_source_stops(self):
        self.budget.collect(self.draft,self.ai)
        changed=copy.deepcopy(self.draft);changed['meals']['dinner']['foods'][0]['amount']='160g'
        with self.assertRaises(ValueError):validate_draft_plan(changed,self.store)
        with self.store.transaction() as db:
            db.execute("UPDATE preparation_steps SET result=? WHERE step LIKE 'menu_nutrition:%'",(json.dumps({'breakfast':[900,900],'lunch':[900,900],'dinner':[900,900]}),))
        with self.assertRaises(ValueError):self.budget.collect(self.draft,self.ai)
        self.assertEqual(len(self.calls),1)

    def test_old_unknown_is_not_reclassified_or_bypassed(self):
        with self.store.transaction() as db:
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'outcome_unknown',NULL,1)",(self.draft['target_date'],'meal_replan:dinner:1','a'*64))
        with self.assertRaises(ValueError):self.budget.collect(self.draft,self.ai)
        self.ai.structured.assert_not_called()
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT state FROM preparation_steps").fetchone()[0],'outcome_unknown')

    def test_saved_analysis_is_persistent_and_cannot_disappear_after_restart(self):
        from datetime import datetime,UTC
        from test_registration import RegistrationTests
        client=RegistrationTests().client()
        client.register(b'original','meal.jpg','breakfast','2026-10-29T07:00:00+09:00','朝の献立です。',
            request_key='11111111-1111-4111-8111-111111111111',expected_state_version='sv1_'+'0'*64)
        state=client.day_state(self.draft['target_date'])
        now=datetime.now(UTC)
        result=self.budget.collect(self.draft,self.ai,state,now)
        self.assertEqual(result.amounts[0],Decimal(600));self.assertEqual(self.calls,[('lunch','dinner')])
        for changed in ('version','missing','without_current_read'):
            with self.subTest(changed=changed):
                other=copy.deepcopy(state)
                if changed=='version':
                    other['meals'][0]['result_version']='rv1_'+'b'*64
                    other['meals'][0]['ogp']['source_result_version']='rv1_'+'b'*64
                elif changed=='missing':other['meals']=[];other['meal_count']=0
                else:other=None
                restarted=MenuBudget(self.store,self.draft['target_date'],self.journal)
                with self.assertRaises(ValueError):restarted.collect(self.draft,self.ai,other,now)
        self.assertEqual(len(self.calls),1)

    def test_empty_target_interval_stops_before_generation_or_attempt(self):
        # 他2食だけで上限を超えるケースでは対象一食を足しても成立しない。
        def estimate(instructions,content,schema,name):
            self.assertEqual(name,'mina_menu_nutrition_check')
            amounts={'breakfast':[125,125],'lunch':[125,125],'dinner':[300,300]}
            return {slot:amounts[slot] for slot in json.loads(content)['foods']}
        self.ai.structured.side_effect=estimate
        with self.assertRaises(ValueError):MealReplanner(self.store,self.draft,self.path,self.ai).check_budget(only_slot='dinner')
        self.assertEqual(self.ai.structured.call_count,1)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM preparation_steps WHERE step LIKE 'meal_replan:%'").fetchone()[0],0)

    def test_daily_total_does_not_hide_abnormal_food_portions(self):
        def estimate(instructions,content,schema,name):
            self.assertEqual(name,'mina_menu_nutrition_check')
            return {'breakfast':[50,50],'lunch':[50,50],'dinner':[850,850]}
        self.ai.structured.side_effect=estimate
        with self.assertRaises(ValueError):MealReplanner(self.store,self.draft,self.path,self.ai).check_budget()
        with self.assertRaises(ValueError):validate_draft_plan(self.draft,self.store)
        self.assertEqual(self.ai.structured.call_count,1)

    def test_basis_failure_after_cli_completion_does_not_consume_second_attempt(self):
        # 完了receiptがあっても、DB根拠異常は生成案の純粋な拒否ではない。
        from src.mina.generation_receipts import completed,invocation
        def generate(instructions,content,schema,name):
            if name!='mina_meal_replan':return self.estimate(instructions,content,schema,name)
            value=self.estimate(instructions,content,schema,name)
            root=self.root/('f'*32);root.mkdir()
            (root/'result.json').write_text(json.dumps(value),encoding='utf-8')
            (root/'state.json').write_text(json.dumps({'state':'complete','reason':None}),encoding='utf-8')
            completed(root,schema,value,invocation(root.name,schema,content),0,False)
            with self.store.transaction() as db:db.execute('DELETE FROM menu_nutrition_admissions')
            return value
        self.ai.structured.side_effect=generate
        with self.assertRaises(ValueError):MealReplanner(self.store,self.draft,self.path,self.ai).check_budget()
        with self.store.transaction() as db:
            rows=list(db.execute("SELECT state FROM preparation_steps WHERE step LIKE 'meal_replan:%'"))
        self.assertEqual([r[0] for r in rows],['outcome_unknown'])

    def adoption_failure(self,after_replace):
        planner=MealReplanner(self.store,self.draft,self.path,self.ai)
        replace=planner.replace_artifact
        def fail(*args):
            if after_replace:replace(*args)
            raise OSError('採用途中の停止')
        with patch.object(planner,'replace_artifact',side_effect=fail):
            with self.assertRaises(OSError):planner.check_budget()
        calls=self.ai.structured.call_count
        current=json.loads(self.path.read_bytes())
        restarted=MealReplanner(self.store,current,self.path,self.ai)
        with self.assertRaises(ValueError):restarted.check_budget()
        self.assertEqual(self.ai.structured.call_count,calls)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT state FROM meal_plan_adoptions').fetchone()[0],'pending')
            rows=list(db.execute("SELECT step,state FROM preparation_steps WHERE step LIKE 'meal_replan:%'"))
        self.assertEqual([tuple(r) for r in rows],[('meal_replan:dinner:1','complete')])
        self.assertEqual(current==json.loads(self.original),not after_replace)

    def test_completed_generation_without_file_adoption_stops_after_restart(self):
        self.adoption_failure(False)

    def test_partial_file_adoption_without_db_commit_stops_after_restart(self):
        self.adoption_failure(True)

    def test_pending_before_journal_blocks_direct_image_and_registration_entries(self):
        from src.mina.preparation import prepare_images
        from src.mina.registration import register_day
        self.ai.structured.side_effect=lambda instructions,content,schema,name:{s:[300,300] for s in json.loads(content)['foods']}
        self.budget.collect(self.draft,self.ai)
        MealReplanner(self.store,self.draft,self.path,self.ai)
        validate_draft_plan(self.draft,self.store)
        with self.store.transaction() as db:
            db.execute("INSERT INTO meal_plan_adoptions VALUES(?,?,?,?,NULL,NULL,NULL,'pending')",
                (self.draft['target_date'],'meal_replan:dinner:1','a'*64,fingerprint(self.draft)))
        api=Mock();self.ai.reset_mock()
        with self.assertRaises(ValueError):prepare_images(self.store,self.path,self.root/'images',self.ai)
        with self.assertRaises(ValueError):register_day(self.store,self.draft,{},self.root/'ogp',api,self.ai,'www.log-eats.com')
        self.ai.assert_not_called();self.assertEqual(self.ai.mock_calls,[]);self.assertEqual(api.mock_calls,[])
        # 完了工程だけが残った場合も、採用行の欠損を旧互換として扱わない。
        with self.store.transaction() as db:
            db.execute('DELETE FROM meal_plan_adoptions')
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,1)",
                (self.draft['target_date'],'meal_replan:dinner:1','a'*64,'{}'))
        with self.assertRaises(ValueError):prepare_images(self.store,self.path,self.root/'images',self.ai)
        self.assertEqual(self.ai.mock_calls,[])

    def completed_cli_rejection(self,amounts,exhausted=False):
        candidates=[]
        for amount in amounts:
            value=copy.deepcopy(self.draft['meals']['dinner']);value['menu']='鶏肉の献立'
            value['foods'][1]['name']='鶏肉';value['estimated_calories']=amount;candidates.append(value)
        launches=[]
        def launch(command,**kwargs):
            result=Path(command[command.index('--output-last-message')+1]);launches.append(result)
            process=Mock(returncode=0)
            def communicate(*args,**kwargs):result.write_text(json.dumps(candidates[len(launches)-1]),encoding='utf-8');return ('','')
            process.communicate.side_effect=communicate
            return process
        original=self.ai.structured.side_effect
        with patch.dict(os.environ,{'MINA_CODEX_BIN':'codex-test','MINA_CODEX_WORKDIR':str(self.root/'jobs'),'MINA_AI_ENABLED':'true'}),patch('src.mina.codex_client.subprocess.Popen',side_effect=launch):
            client=CodexClient()
            self.ai.structured.side_effect=lambda instructions,content,schema,name:client.execute(content,schema) if name=='mina_meal_replan' else original(instructions,content,schema,name)
            if exhausted:
                with self.assertRaises(GeneratedPlanRejected):MealReplanner(self.store,self.draft,self.path,self.ai).check_budget()
            else:MealReplanner(self.store,self.draft,self.path,self.ai).check_budget()
        with self.store.transaction() as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM preparation_steps WHERE step LIKE 'meal_replan:%' ORDER BY step")]
        self.assertEqual([r['state'] for r in rows],['known_rejected','known_rejected' if exhausted else 'complete'])
        self.assertEqual(len(launches),2);receipt=rejection(rows[0]);self.assertNotIn('round',receipt)
        before=len(launches)
        with self.assertRaises(GeneratedPlanRejected):self.journal.run(self.draft['target_date'],rows[0]['step'],
            {'draft_sha256':fingerprint(json.loads(self.original)),'slot':'dinner','reason_codes':['daily_energy_out_of_range'],'attempt':1},lambda:self.fail('再送禁止'))
        self.assertEqual(len(launches),before)

    def test_completed_cli_rejection_consumes_once_then_uses_second_ordinal(self):
        self.completed_cli_rejection((600,839))

    def test_two_completed_rejections_never_generate_a_third_candidate(self):
        self.completed_cli_rejection((600,600),exhausted=True)

    def test_invalid_unproven_ai_result_does_not_allow_second_generation(self):
        original=self.ai.structured.side_effect
        calls=[]
        def generate(instructions,content,schema,name):
            if name!='mina_meal_replan':return original(instructions,content,schema,name)
            calls.append(name);value=copy.deepcopy(self.draft['meals']['dinner'])
            value['estimated_calories']=600;return value
        self.ai.structured.side_effect=generate
        with self.assertRaises(ValueError):MealReplanner(self.store,self.draft,self.path,self.ai).check_budget()
        self.assertEqual(calls,['mina_meal_replan'])
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT state FROM preparation_steps WHERE step='meal_replan:dinner:1'").fetchone()[0],'outcome_unknown')


if __name__=='__main__':unittest.main()
