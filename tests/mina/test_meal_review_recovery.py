"""朝昼訂正を終えた実通常経路の停止点から、昼点検だけを追加する。"""
import copy,json,hashlib,unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path
import test_correction_recovery as fixtures
from src.mina.meal_review_recovery import resume_review,TARGET,END,STEP,PARENTS
from src.mina.preparation import fingerprint


class MealReviewRecoveryTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.CorrectionRecoveryTests();f.setUp();self.addCleanup(f.doCleanups)
        self.f=f;self.c=c=f.c;self.ai=f.ai;self.meal=f.meal
        old_structured=self.ai.structured
        def structured(instructions,content,schema,name):
            if name!='mina_meal_correction':return old_structured(instructions,content,schema,name)
            meal=json.loads(content[0]['text'])['meal']
            return {'foods':[{**food,'calories':300,'protein':15,'fat':10,'carbs':37.5} for food in meal['foods']]}
        self.ai.structured=structured
        old_write=self.meal.json_write
        def write(path,payload,method='POST',request_key=None):
            if method!='PATCH' or not payload['share_url'].endswith('lunch'):return old_write(path,payload,method,request_key)
            self.assertEqual(payload['expected_state_version'],self.meal.sv)
            self.assertNotIn(request_key,self.meal.requests)
            self.meal.calls.append(('PATCH','lunch',request_key));self.meal.version+=1
            result=copy.deepcopy(self.meal.meals['lunch']);result['analysis']['foods']=copy.deepcopy(payload['foods'])
            response=self.meal.wrap(result,request_key);self.meal.meals['lunch']=copy.deepcopy(response['result']);return response
        self.meal.json_write=write;old_review=self.ai.review_meal
        def failed(raw,meal,response,meta):
            if response['result']['meal']['meal_type']!='lunch':return old_review(raw,meal,response,meta)
            if not any(m=='PATCH' and s=='lunch' for m,s,k in self.meal.calls):return {'status':'hold','reason_codes':['nutrition_semantic_mismatch']}
            raise RuntimeError('failed_lunch_review')
        self.ai.review_meal=failed
        with self.assertRaisesRegex(RuntimeError,'failed_lunch_review'):f.invoke()
        self.ai.review_meal=lambda *a:{'status':'publishable','reason_codes':[]}
        with c.store.transaction() as db:
            self.rows={r['step']:dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=?',(TARGET,))}
            self.parents={t:dict(db.execute('SELECT * FROM '+t).fetchone()) for t in PARENTS}
        job=c.jobroot/'lunch-failed';job.mkdir()
        saved=json.loads((c.base/'drive.json').read_text())
        (job/'state.json').write_text(json.dumps({'state':'failed','reason':'authentication'}))
        (job/'diagnostic.json').write_text(json.dumps({'exit_code':1,'input_image_sha256':[saved['images']['lunch']['sha256']]}))
        current={m['meal']['meal_type']:m for m in self.meal.day_state(TARGET)['meals']}
        self.approval={'target_date':TARGET,'deadline':END.isoformat(),'commit':fixtures.COMMIT,'recovery_id':'55555555-5555-4555-8555-555555555555',
            'scope':'one_lunch_corrected_review_then_remaining_candidates','approval_reference':'本人の追加許可',
            'original_digest':self.rows[STEP]['digest'],'failed_job':str(job),
            'draft_sha256':hashlib.sha256(c.draft_path.read_bytes()).hexdigest(),'saved_sha256':hashlib.sha256((c.base/'drive.json').read_bytes()).hexdigest(),
            'steps_sha256':fingerprint(self.rows),'parents_sha256':fingerprint(self.parents),'state_version':self.meal.sv,'current_results_sha256':fingerprint(current)}
        self.path=c.root/'lunch-review-approval.json';self.save()
        for p in (patch('src.mina.meal_review_recovery.release_commit',return_value=fixtures.COMMIT),patch('src.mina.meal_review_recovery.JOB_ROOT',c.jobroot)):
            p.start();self.addCleanup(p.stop)
        self.before=list(self.meal.calls)

    def save(self):self.path.write_text(json.dumps(self.approval))
    def invoke(self):
        c=self.c
        return resume_review(c.store,c.root/'daily',self.path,c.history,c.root,fixtures.COMMIT,'logeats.test',self.ai,self.meal,clock=lambda:c.now[0],capture=self.f.capture)

    def test_real_pipeline_only_dinner_and_daily_new_posts_four_candidates(self):
        self.assertEqual(self.invoke()['post_count'],4)
        self.assertEqual([(m,s) for m,s,k in self.meal.calls[len(self.before):]],[('POST','dinner'),('POST','daily')])
        with self.c.store.transaction() as db:
            for k,v in self.rows.items():self.assertEqual(dict(db.execute('SELECT * FROM preparation_steps WHERE step=?',(k,)).fetchone()),v)
            for t,v in self.parents.items():self.assertEqual(dict(db.execute('SELECT * FROM '+t).fetchone()),v)
            self.assertEqual(db.execute('SELECT count(*) FROM candidates').fetchone()[0],4)

    def test_unknown_review_is_not_repeated_with_another_uuid(self):
        self.ai.review_meal=lambda *a:(_ for _ in ()).throw(RuntimeError('unknown_again'))
        with self.assertRaisesRegex(RuntimeError,'unknown_again'):self.invoke()
        self.approval['recovery_id']='66666666-6666-4666-8666-666666666666';self.save()
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(self.meal.calls,self.before)

    def test_deadline_stops_before_extra_review_or_post(self):
        self.c.now[0]=END
        with self.assertRaisesRegex(ValueError,'deadline'):self.invoke()
        self.assertEqual(self.meal.calls,self.before)

    def test_changed_current_version_stops(self):
        self.approval['state_version']='sv1_'+'f'*64;self.save()
        with self.assertRaisesRegex(ValueError,'current_state_changed'):self.invoke()
        self.assertEqual(self.meal.calls,self.before)

    def test_changed_parent_stops(self):
        with self.c.store.transaction() as db:db.execute("UPDATE correction_recovery_claims SET state='complete'")
        with self.assertRaisesRegex(ValueError,'baseline_changed'):self.invoke()

    def test_quality_hold_preserves_unknown_and_stops_dinner(self):
        self.ai.review_meal=lambda *a:{'status':'hold','reason_codes':['nutrition_semantic_mismatch']}
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(self.meal.calls,self.before)
        with self.c.store.transaction() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM preparation_steps WHERE step LIKE '%:adoption:%' AND step LIKE 'meal_review:%'").fetchone())
            self.assertEqual(db.execute('SELECT state FROM preparation_steps WHERE step=?',(STEP,)).fetchone()[0],'outcome_unknown')

    def test_late_result_preserved_without_adoption_or_post(self):
        def late(*args):self.c.now[0]=END;return {'status':'publishable','reason_codes':[]}
        self.ai.review_meal=late
        with self.assertRaisesRegex(ValueError,'deadline'):self.invoke()
        self.assertEqual(self.meal.calls,self.before)
