"""未確認点検の別採用・回数固定・各境界の拒否を実SQLiteで検証する。"""
import json,hashlib,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.domain import JST
from src.mina.store import Store
from src.mina.request_ledger import RequestLedger
from src.mina.preparation import PreparationJournal,prepare_images,fingerprint
from src.mina.generation import image_review_policy_sha256
from src.mina.review_recovery import resume_review,TARGET,END,PASS
from test_generation import plan
from test_readiness import evidence,reviewed

KEY='11111111-1111-4111-8111-111111111111'
COMMIT='a'*40


class ReviewRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=Store(self.root/'db');RequestLedger(self.store);self.journal=PreparationJournal(self.store)
        self.now=[datetime(2026,10,26,22,tzinfo=JST)]
        self.base=self.root/'daily'/TARGET;self.base.mkdir(parents=True)
        self.draft={'state':'draft_unreviewed','target_date':TARGET,**plan()}
        self.draft_path=self.base/'draft.json';self.draft_path.write_text(json.dumps(self.draft),encoding='utf-8')
        with self.store.transaction() as db:db.execute('INSERT INTO preparation VALUES(?,?,?)',(TARGET,'held:images',self.now[0].timestamp()))
        self.journal.run(TARGET,'daily_draft',{},lambda:self.draft)
        foods={'foods':{s:self.draft['meals'][s]['foods'] for s in ('breakfast','lunch','dinner')}}
        self.journal.run(TARGET,'menu_nutrition:'+fingerprint(foods),foods,lambda:{s:[650/len(v)]*len(v) for s,v in foods['foods'].items()})
        ai=Mock();ai.image.side_effect=[b'\xff\xd8\xffbreakfast',b'\xff\xd8\xfflunch'];ai.review_image.side_effect=[PASS,RuntimeError('cli_failed')]
        with self.assertRaises(RuntimeError):prepare_images(self.store,self.draft_path,self.base/'images',ai)
        with self.store.transaction() as db:
            unknown=dict(db.execute("SELECT * FROM preparation_steps WHERE step='image_review:lunch'").fetchone())
        self.original_digest=unknown['digest'];self.lunch=self.base/'images'/f'{TARGET}-lunch.jpg'
        self.jobroot=self.root/'jobs';self.job=self.jobroot/'fixed';self.job.mkdir(parents=True)
        (self.job/'state.json').write_text(json.dumps({'state':'failed','reason':'execution'}));(self.job/'input-0.jpg').write_bytes(self.lunch.read_bytes())
        self.approval={'target_date':TARGET,'deadline':END.isoformat(),'commit':COMMIT,'recovery_id':KEY,
            'original_digest':self.original_digest,'image_sha256':hashlib.sha256(self.lunch.read_bytes()).hexdigest(),
            'draft_sha256':hashlib.sha256(self.draft_path.read_bytes()).hexdigest(),'policy_sha256':image_review_policy_sha256(),
            'cli_job':str(self.job),'approval_reference':'本人の限定承認','scope':'one_interrupted_lunch_review_then_four_candidates'}
        self.approval_path=self.root/'approval.json';self.save_approval()
        self.history=self.root/'history.json';self.history.write_text(json.dumps({**evidence(),'target_date':TARGET,
            'checked_at':self.now[0].isoformat(),'history_from':'2026-10-13','history_to':'2026-10-26'}))
        self.ai=Mock();self.ai.review_image.return_value=PASS;self.ai.image.return_value=b'\xff\xd8\xffdinner'
        self.drive=Mock();self.meal=Mock();self.meal.day_state.side_effect=lambda _: {'account':'EatsLog88161','contract_version':'mina-write-v1',
            'complete':True,'date':TARGET,'checked_at':self.now[0].isoformat(),'state_version':'sv1_'+'a'*64,'meal_count':0,'meals':[],'daily_share':None}
        posts=reviewed();posts['target_date']=TARGET;self.register=Mock(return_value=posts)
        self.patches=[patch('src.mina.review_recovery.release_commit',return_value=COMMIT),patch('src.mina.review_recovery.JOB_ROOT',self.jobroot),
            patch('src.mina.review_recovery.save_images_to_drive',return_value={'saved':True}),
            patch('src.mina.review_recovery.register_day',self.register),patch('src.mina.review_recovery.verify_source')]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def save_approval(self):self.approval_path.write_text(json.dumps(self.approval))
    def invoke(self):return resume_review(self.store,self.root/'daily',self.approval_path,self.history,self.root,COMMIT,'logeats.test',self.ai,self.drive,self.meal,clock=lambda:self.now[0])
    def original_unknown(self):
        with self.store.transaction() as db:return db.execute("SELECT state FROM preparation_steps WHERE step='image_review:lunch'").fetchone()[0]

    def test_success_reuses_both_images_adopts_separately_and_imports_four(self):
        original=self.lunch.read_bytes();result=self.invoke()
        self.assertEqual(result['post_count'],4);self.assertEqual(self.ai.image.call_count,1);self.assertEqual(self.ai.review_image.call_count,2)
        self.assertEqual(self.original_unknown(),'outcome_unknown');self.assertEqual(self.lunch.read_bytes(),original)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM candidates').fetchone()[0],4)
            self.assertEqual(db.execute("SELECT count(*) FROM preparation_steps WHERE step LIKE 'review_adoption:%' AND state='complete'").fetchone()[0],1)
        with self.assertRaisesRegex(ValueError,'already_claimed'):self.invoke()
        self.assertEqual(self.ai.image.call_count,1)

    def test_failed_new_review_is_fixed_stop_even_with_new_uuid(self):
        self.ai.review_image.side_effect=RuntimeError('new_failure')
        with self.assertRaises(RuntimeError):self.invoke()
        self.assertEqual(self.original_unknown(),'outcome_unknown');self.ai.image.assert_not_called();self.register.assert_not_called()
        self.approval['recovery_id']='22222222-2222-4222-8222-222222222222';self.save_approval()
        with self.assertRaisesRegex(ValueError,'already_claimed'):self.invoke()
        self.assertEqual(self.ai.review_image.call_count,1)

    def test_quality_hold_does_not_generate_or_register(self):
        self.ai.review_image.return_value={'status':'hold','reason_codes':['image_analysis_mismatch']}
        with self.assertRaisesRegex(ValueError,'quality_hold'):self.invoke()
        self.ai.image.assert_not_called();self.register.assert_not_called()

    def test_delayed_review_result_is_retained_but_next_step_stops(self):
        def delayed(*_):self.now[0]=END;return PASS
        self.ai.review_image.side_effect=delayed
        with self.assertRaisesRegex(ValueError,'deadline'):self.invoke()
        self.ai.image.assert_not_called()
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT state FROM preparation_steps WHERE step LIKE 'image_review:lunch:interrupted:%'").fetchone()[0],'complete')
        self.assertEqual(self.original_unknown(),'outcome_unknown')

    def test_expired_before_start_has_no_external_read_or_ai(self):
        self.now[0]=END
        with self.assertRaisesRegex(ValueError,'deadline'):self.invoke()
        self.meal.day_state.assert_not_called();self.ai.review_image.assert_not_called()

    def test_changed_lunch_image_is_rejected(self):
        self.lunch.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.invoke()
        self.ai.review_image.assert_not_called()

    def test_changed_draft_is_rejected(self):
        self.draft_path.write_text('{}')
        with self.assertRaises(ValueError):self.invoke()
        self.ai.review_image.assert_not_called()

    def test_foreign_date_and_policy_and_deadline_are_rejected(self):
        for key,value in [('target_date','2026-10-28'),('policy_sha256','0'*64),('deadline',(END+timedelta(minutes=1)).isoformat())]:
            with self.subTest(key=key):
                old=self.approval[key];self.approval[key]=value;self.save_approval()
                with self.assertRaises(ValueError):self.invoke()
                self.approval[key]=old
        self.ai.review_image.assert_not_called()

    def test_existing_cli_result_is_not_assumed_failed(self):
        (self.job/'result.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'cli_evidence'):self.invoke()
        self.ai.review_image.assert_not_called()

    def test_stale_history_is_rejected_before_ai(self):
        v=json.loads(self.history.read_text());v['checked_at']=(self.now[0]-timedelta(minutes=3)).isoformat();self.history.write_text(json.dumps(v))
        with self.assertRaisesRegex(ValueError,'history_stale'):self.invoke()
        self.ai.review_image.assert_not_called()

    def test_existing_server_meal_is_rejected_before_ai(self):
        original=self.meal.day_state.side_effect
        def occupied(t):v=original(t);v['meals']=[{}];v['meal_count']=1;return v
        self.meal.day_state.side_effect=occupied
        with self.assertRaisesRegex(ValueError,'server_not_empty'):self.invoke()
        self.ai.review_image.assert_not_called()

    def test_parallel_claim_is_rejected_while_first_review_runs(self):
        def review(*_):
            with self.assertRaisesRegex(ValueError,'already_claimed'):self.invoke()
            return PASS
        self.ai.review_image.side_effect=review
        self.invoke();self.assertEqual(self.ai.image.call_count,1)

    def test_downstream_failure_does_not_replay_completed_images(self):
        self.register.side_effect=RuntimeError('registration_unknown')
        with self.assertRaises(RuntimeError):self.invoke()
        with self.assertRaisesRegex(ValueError,'already_claimed'):self.invoke()
        self.assertEqual(self.ai.image.call_count,1);self.assertEqual(self.register.call_count,1)

    def test_old_step_and_approval_mutation_are_detected_before_next_call(self):
        def changed(*_):
            with self.store.transaction() as db:db.execute("UPDATE preparation_steps SET digest=? WHERE step='daily_draft'",('0'*64,))
            return PASS
        self.ai.review_image.side_effect=changed
        with self.assertRaisesRegex(ValueError,'old_step_changed'):self.invoke()
        self.ai.image.assert_not_called()

    def test_claim_and_deadline_checked_inside_candidate_transaction(self):
        actual=self.store.save_many
        def delayed(*args,**kwargs):self.now[0]=END;return actual(*args,**kwargs)
        with patch.object(self.store,'save_many',side_effect=delayed):
            with self.assertRaisesRegex(ValueError,'deadline'):self.invoke()
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM candidates').fetchone()[0],0)

    def test_real_drive_registration_correction_and_four_candidates(self):
        from nightly_fakes import NightlyAI,NightlyDrive,NightlyMeal
        for p in self.patches[2:]:p.stop()
        ai=NightlyAI('corrected',self.now);ai.plan={'meals':self.draft['meals'],'daily_comment':self.draft['daily_comment']}
        drive=NightlyDrive();meal=NightlyMeal('corrected',self.now,'logeats.test')
        def capture(url,host):return {'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':b'\xff\xd8\xffpreview'}
        with patch('src.mina.registration.datetime',wraps=datetime) as api_time:
            api_time.now.side_effect=lambda *a:self.now[0]
            result=resume_review(self.store,self.root/'daily',self.approval_path,self.history,self.root,COMMIT,
                'logeats.test',ai,drive,meal,clock=lambda:self.now[0],capture=capture)
        self.assertEqual(result['post_count'],4);self.assertEqual(ai.images,1);self.assertEqual(len(drive.uploads),3)
        self.assertEqual([(m,s) for m,s,k in meal.calls],[('POST','breakfast'),('PATCH','breakfast'),('POST','lunch'),('POST','dinner'),('POST','daily')])
        self.assertEqual(self.original_unknown(),'outcome_unknown')
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM candidates').fetchone()[0],4)
