"""朝昼訂正を終えた実通常経路の停止点から、昼点検だけを追加する。"""
import copy,json,hashlib,unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path
import test_meal_review_recovery as fixtures
from src.mina.ogp_finish_recovery import resume_review,TARGET,END,STEP,PARENTS
from src.mina.preparation import fingerprint


class OgpFinishRecoveryTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.MealReviewRecoveryTests();f.setUp();self.addCleanup(f.doCleanups)
        self.f=f;self.c=c=f.c;self.ai=f.ai;self.meal=f.meal
        old_ogp=self.ai.review_ogp
        def failed(images,expected,meta):
            if expected['slot']=='dinner':raise RuntimeError('failed_dinner_ogp')
            return old_ogp(images,expected,meta)
        self.ai.review_ogp=failed
        # 夕食は保存済み訂正を含む本番形状に揃える。
        old_meal=self.ai.review_meal
        def dinner(raw,meal,response,meta):
            if response['result']['meal']['meal_type']=='dinner' and not any(m=='PATCH' and s=='dinner' for m,s,k in self.meal.calls):return {'status':'hold','reason_codes':['nutrition_semantic_mismatch']}
            return old_meal(raw,meal,response,meta)
        self.ai.review_meal=dinner
        old_write=self.meal.json_write
        def write(path,payload,method='POST',request_key=None):
            if method!='PATCH' or not payload['share_url'].endswith('dinner'):return old_write(path,payload,method,request_key)
            self.meal.calls.append(('PATCH','dinner',request_key));self.meal.version+=1
            result=copy.deepcopy(self.meal.meals['dinner']);result['analysis']['foods']=copy.deepcopy(payload['foods'])
            response=self.meal.wrap(result,request_key);self.meal.meals['dinner']=copy.deepcopy(response['result']);return response
        self.meal.json_write=write
        with self.assertRaisesRegex(RuntimeError,'failed_dinner_ogp'):f.invoke()
        self.ai.review_ogp=old_ogp
        with c.store.transaction() as db:
            self.rows={r['step']:dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=?',(TARGET,))}
            self.parents={t:dict(db.execute('SELECT * FROM '+t).fetchone()) for t in PARENTS}
        job=c.jobroot/'dinner-ogp-failed';job.mkdir()
        saved=json.loads((c.base/'drive.json').read_text())
        (job/'state.json').write_text(json.dumps({'state':'failed','reason':'execution'}))
        (job/'diagnostic.json').write_text(json.dumps({'exit_code':1,'input_image_sha256':[saved['images']['dinner']['sha256']]}))
        current={m['meal']['meal_type']:m for m in self.meal.day_state(TARGET)['meals']}
        self.approval={'target_date':TARGET,'deadline':END.isoformat(),'commit':fixtures.fixtures.COMMIT,'recovery_id':'55555555-5555-4555-8555-555555555555',
            'scope':'independent_ogp_then_daily_four_candidates','approval_reference':'本人の追加許可',
            'original_digest':self.rows[STEP]['digest'],'failed_job':str(job),
            'draft_sha256':hashlib.sha256(c.draft_path.read_bytes()).hexdigest(),'saved_sha256':hashlib.sha256((c.base/'drive.json').read_bytes()).hexdigest(),
            'steps_sha256':fingerprint(self.rows),'parents_sha256':fingerprint(self.parents),'state_version':self.meal.sv,'current_results_sha256':fingerprint(current)}
        self.path=c.root/'lunch-review-approval.json';self.save()
        for p in (patch('src.mina.ogp_finish_recovery.release_commit',return_value=fixtures.fixtures.COMMIT),patch('src.mina.ogp_finish_recovery.JOB_ROOT',c.jobroot)):
            p.start();self.addCleanup(p.stop)
        self.before=list(self.meal.calls)

    def save(self):self.path.write_text(json.dumps(self.approval))
    def invoke(self):
        c=self.c
        return resume_review(c.store,c.root/'daily',self.path,c.history,c.root,fixtures.fixtures.COMMIT,'logeats.test',self.ai,self.meal,clock=lambda:c.now[0],capture=self.f.f.capture)

    def test_real_pipeline_only_dinner_and_daily_new_posts_four_candidates(self):
        self.assertEqual(self.invoke()['post_count'],4)
        self.assertEqual([(m,s) for m,s,k in self.meal.calls[len(self.before):]],[('POST','daily')])
        with self.c.store.transaction() as db:
            for k,v in self.rows.items():self.assertEqual(dict(db.execute('SELECT * FROM preparation_steps WHERE step=?',(k,)).fetchone()),v)
            for t,v in self.parents.items():self.assertEqual(dict(db.execute('SELECT * FROM '+t).fetchone()),v)
            self.assertEqual(db.execute('SELECT count(*) FROM candidates').fetchone()[0],4)

    def test_unknown_review_is_not_repeated_with_another_uuid(self):
        self.ai.review_ogp=lambda *a:(_ for _ in ()).throw(RuntimeError('unknown_again'))
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
        self.ai.review_ogp=lambda *a:{'status':'hold','reason_codes':['nutrition_semantic_mismatch']}
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(self.meal.calls,self.before)
        with self.c.store.transaction() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM preparation_steps WHERE step LIKE '%:adoption:%' AND step LIKE 'ogp_review:%'").fetchone())
            self.assertEqual(db.execute('SELECT state FROM preparation_steps WHERE step=?',(STEP,)).fetchone()[0],'outcome_unknown')

    def test_late_result_preserved_without_adoption_or_post(self):
        def late(*args):self.c.now[0]=END;return {'status':'publishable','reason_codes':[]}
        self.ai.review_ogp=late
        with self.assertRaisesRegex(ValueError,'deadline'):self.invoke()
        self.assertEqual(self.meal.calls,self.before)
