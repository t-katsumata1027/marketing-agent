"""保存済み朝食を再登録せず、未知の提案だけ別採用して一度訂正する。"""
import json,hashlib,unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
import test_review_recovery as fixtures
COMMIT=fixtures.COMMIT
from nightly_fakes import NightlyAI,NightlyDrive,NightlyMeal
from src.mina.review_recovery import repair_review
from src.mina.correction_recovery import resume_correction,TARGET,END


class CorrectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.RepairReviewTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.c=self.fixture.c;c=self.c
        for p in c.patches[2:]:p.stop()
        self.ai=NightlyAI('corrected',c.now);self.ai.plan={'meals':c.draft['meals'],'daily_comment':c.draft['daily_comment']}
        self.drive=NightlyDrive();self.meal=NightlyMeal('corrected',c.now,'logeats.test')
        self.structured=self.ai.structured
        def failed(*args,**kwargs):raise RuntimeError('failed_proposal')
        self.ai.structured=failed
        self.capture=lambda url,host:{'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':b'\xff\xd8\xffpreview'}
        self.api_time=patch('src.mina.registration.datetime',wraps=datetime);self.clock_module=self.api_time.start();self.addCleanup(self.api_time.stop)
        self.clock_module.now.side_effect=lambda *a:c.now[0]
        with self.assertRaises(RuntimeError):repair_review(c.store,c.root/'daily',c.approval_path,c.history,c.root,COMMIT,'logeats.test',self.ai,self.drive,self.meal,clock=lambda:c.now[0],capture=self.capture)
        self.ai.structured=self.structured
        with c.store.transaction() as db:
            self.old=dict(db.execute("SELECT * FROM preparation_steps WHERE step='correction_plan:breakfast'").fetchone())
            parent=dict(db.execute('SELECT * FROM interrupted_review_recoveries').fetchone());repair=dict(db.execute('SELECT * FROM interrupted_image_repairs').fetchone())
            self.parents={'interrupted_review_recoveries':parent,'interrupted_image_repairs':repair}
            request=dict(db.execute("SELECT * FROM mina_requests WHERE slot='breakfast'").fetchone())
        current=self.meal.day_state(TARGET)['meals'][0];saved=json.loads((c.base/'drive.json').read_text())
        job=c.jobroot/'correction-failed';job.mkdir()
        (job/'state.json').write_text(json.dumps({'state':'failed','reason':'execution'}))
        (job/'diagnostic.json').write_text(json.dumps({'exit_code':1,'input_image_sha256':[current['image_sha256']]}))
        (job/'input-0.jpg').write_bytes(Path(saved['images']['breakfast']['path']).read_bytes())
        self.approval={'target_date':TARGET,'deadline':END.isoformat(),'commit':COMMIT,'recovery_id':'44444444-4444-4444-8444-444444444444',
            'scope':'one_breakfast_correction_then_remaining_candidates','approval_reference':'追加の本人承認',
            'parent_id':parent['recovery_id'],'repair_id':repair['recovery_id'],'original_digest':self.old['digest'],
            'failed_job':str(job),'draft_sha256':hashlib.sha256(c.draft_path.read_bytes()).hexdigest(),
            'saved_sha256':hashlib.sha256((c.base/'drive.json').read_bytes()).hexdigest(),'state_version':self.meal.sv,
            'saved_request_uuid':request['request_key'],**{k:current[k] for k in ('result_version','image_sha256','stored_image_sha256')}}
        self.path=c.root/'correction-approval.json';self.save()
        for p in [patch('src.mina.correction_recovery.release_commit',return_value=COMMIT),patch('src.mina.correction_recovery.JOB_ROOT',c.jobroot)]:p.start();self.addCleanup(p.stop)

    def save(self):self.path.write_text(json.dumps(self.approval))
    def invoke(self):
        c=self.c
        return resume_correction(c.store,c.root/'daily',self.path,c.history,c.root,COMMIT,'logeats.test',self.ai,self.meal,clock=lambda:c.now[0],capture=self.capture)

    def test_real_correction_remaining_registration_and_four_candidates(self):
        self.assertEqual(self.invoke()['post_count'],4)
        self.assertEqual([(m,s) for m,s,k in self.meal.calls],[('POST','breakfast'),('PATCH','breakfast'),('POST','lunch'),('POST','dinner'),('POST','daily')])
        self.assertEqual(self.ai.images,2)
        with self.c.store.transaction() as db:
            self.assertEqual(dict(db.execute("SELECT * FROM preparation_steps WHERE step='correction_plan:breakfast'").fetchone()),self.old)
            for table,parent in self.parents.items():self.assertEqual(dict(db.execute('SELECT * FROM '+table).fetchone()),parent)
            self.assertEqual(db.execute('SELECT count(*) FROM candidates').fetchone()[0],4)

    def test_real_state_aliases_preserve_result_comparison(self):
        previous=self.meal.day_state
        def state(target):
            value=previous(target)
            for m in value['meals']:
                m.update(meal_type=m['meal']['meal_type'],logged_at=m['meal']['logged_at'],share_url=m['share']['url'])
            return value
        self.meal.day_state=state
        self.assertEqual(self.invoke()['post_count'],4)

    def test_inconsistent_state_alias_stops_before_new_model_or_patch(self):
        previous=self.meal.day_state;calls=list(self.meal.calls)
        def state(target):
            value=previous(target);value['meals'][0]['meal_type']='dinner';return value
        self.meal.day_state=state
        with self.assertRaisesRegex(ValueError,'state_alias_changed'):self.invoke()
        self.assertEqual(self.meal.calls,calls)

    def test_expiry_before_start_has_no_new_model_or_patch(self):
        self.c.now[0]=END;calls=list(self.meal.calls)
        with self.assertRaisesRegex(ValueError,'deadline'):self.invoke()
        self.assertEqual(self.meal.calls,calls)

    def test_wrong_saved_version_is_rejected(self):
        self.approval['state_version']='sv1_'+'f'*64;self.save()
        with self.assertRaisesRegex(ValueError,'state_changed'):self.invoke()
        self.assertEqual(len(self.meal.calls),1)

    def test_failure_does_not_replay_under_new_uuid(self):
        self.ai.structured=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('second_failure'))
        with self.assertRaises(RuntimeError):self.invoke()
        self.approval['recovery_id']='55555555-5555-4555-8555-555555555555';self.save()
        with self.assertRaisesRegex(ValueError,'already_claimed'):self.invoke()
        self.assertEqual(len(self.meal.calls),1)

    def test_late_proposal_saved_but_no_patch(self):
        def delayed(*args,**kwargs):self.c.now[0]=END;return self.structured(*args,**kwargs)
        self.ai.structured=delayed
        with self.assertRaisesRegex(ValueError,'deadline'):self.invoke()
        self.assertEqual(len(self.meal.calls),1)
        with self.c.store.transaction() as db:self.assertEqual(db.execute("SELECT state FROM preparation_steps WHERE step LIKE 'correction_plan:breakfast:recovery:%'").fetchone()[0],'complete')

    def test_existing_correction_key_blocks_retry(self):
        with self.c.store.transaction() as db:db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'started',NULL,?)",(TARGET,'correction_key:breakfast','f'*64,self.c.now[0].timestamp()))
        with self.assertRaisesRegex(ValueError,'original_unresolved'):self.invoke()
        self.assertEqual(len(self.meal.calls),1)

    def test_parent_approval_change_blocks_before_ai(self):
        with self.c.store.transaction() as db:db.execute("UPDATE interrupted_image_repairs SET approval_json='{}'")
        with self.assertRaisesRegex(ValueError,'parent_approval'):self.invoke()
        self.assertEqual(len(self.meal.calls),1)

    def test_unexpected_unknown_stage_blocks_before_ai(self):
        with self.c.store.transaction() as db:db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'outcome_unknown',NULL,?)",(TARGET,'drive_upload:extra','e'*64,self.c.now[0].timestamp()))
        with self.assertRaisesRegex(ValueError,'original_unresolved'):self.invoke()
        self.assertEqual(len(self.meal.calls),1)

    def test_foreign_request_added_during_proposal_blocks_patch(self):
        from src.mina.request_ledger import RequestLedger
        def foreign(*a,**kw):
            RequestLedger(self.c.store).prepare('66666666-6666-4666-8666-666666666666','meal_register',TARGET,'lunch',{},b'foreign')
            return self.structured(*a,**kw)
        self.ai.structured=foreign
        with self.assertRaisesRegex(ValueError,'request_binding'):self.invoke()
        self.assertEqual(len(self.meal.calls),1)

    def test_patch_response_unknown_is_not_retried(self):
        self.meal.mode='patch_unknown'
        with self.assertRaises(RuntimeError):self.invoke()
        self.assertEqual([(m,s) for m,s,k in self.meal.calls],[('POST','breakfast'),('PATCH','breakfast')])
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(len(self.meal.calls),2)
        with self.c.store.transaction() as db:
            self.assertEqual(db.execute("SELECT phase FROM mina_requests WHERE operation='meal_correction'").fetchone()[0],'held')

    def test_atomic_candidate_deadline_rolls_back_all_four(self):
        actual=self.c.store.save_many
        def late(*a,**kw):self.c.now[0]=END;return actual(*a,**kw)
        with patch.object(self.c.store,'save_many',side_effect=late):
            with self.assertRaisesRegex(ValueError,'deadline'):self.invoke()
        with self.c.store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM candidates').fetchone()[0],0)

    def test_saved_request_bytes_mutation_blocks_admission(self):
        with self.c.store.transaction() as db:db.execute("UPDATE mina_requests SET image=?",(b'changed',))
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(len(self.meal.calls),1)
