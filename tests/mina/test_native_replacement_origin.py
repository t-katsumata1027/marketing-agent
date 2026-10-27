"""実通常登録・訂正のcomplete保留から、原本予約とP入力生成までを隔離して通す。"""
import copy,json,tempfile,unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.store import Store
from src.mina.registration import register_day
from src.mina.preparation_status import QualityHold
from src.mina.replacement_policy import ReplacementPolicy
from src.mina.replacement_flow import ReplacementFlow
from src.mina.replacement_collection import Collection
from src.mina.replacement_origin import verify_receipt
from src.mina.replacement_contract import Contract,digest,stamp
from src.mina.publication_hold import acquire

class NativeOriginTests(unittest.TestCase):
    def setUp(self):
        from tests.mina.test_registration import RegistrationTests
        from tests.mina.test_replacement_control import waived_policy
        from tests.mina.test_daily import NOW,TARGET
        from tests.mina.test_replacement_integration import SAMPLES
        self.now,self.target,self.samples=NOW,TARGET,SAMPLES
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
        fixture=RegistrationTests();self.draft,self.saved=fixture.setup_inputs(self.base)
        self.store=Store(self.base/'db');self.meal=fixture.client();self.ai=Mock();self.corrected={};self.patch_requests={}
        flags=patch.dict('os.environ',{'MINA_MEAL_REPLACEMENT_ENABLED':'true','MINA_LIVE_WRITES':'true','MINA_REPLACEMENT_COLLECTION_ENABLED':'true'})
        flags.start();self.addCleanup(flags.stop)
        clock=patch('src.mina.registration.datetime',wraps=datetime);dt=clock.start();dt.now.return_value=NOW;self.addCleanup(clock.stop)
        self.capture=lambda url,host:{'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':b'\xff\xd8\xffpreview'}
        capture=patch('src.mina.ogp.capture_ogp',self.capture);capture.start();self.addCleanup(capture.stop)
        original_state=self.meal.day_state.side_effect;original_lookup=self.meal.request_state.side_effect
        def state(target):
            result=original_state(target);result['checked_at']=NOW.isoformat()
            result['meals']=[copy.deepcopy(self.corrected.get(m['meal']['meal_type'],m)) for m in result['meals']]
            return result
        def lookup(key,operation):
            if key not in self.patch_requests:return original_lookup(key,operation)
            result=self.patch_requests[key]
            return {'contract_version':'mina-write-v1','request_uuid':key,'operation':operation,'status':'succeeded','persistence':'saved',
                    'stage':'completed','result_status':'unchanged','result_hold_reason':None,'input_hash':'ih1_'+'f'*64,
                    'image_sha256':None,'result':copy.deepcopy(result),'current_result':copy.deepcopy(result)}
        def correction(path,body,method,request_key):
            self.assertEqual((path,method),('/api/internal/mina/meals','PATCH'))
            with self.store.transaction() as db:old=json.loads(db.execute("SELECT result FROM preparation_steps WHERE step='meal_register:lunch'").fetchone()[0])
            result=copy.deepcopy(old['result']);result['result_version']='rv1_'+'c'*64
            result['ogp']['source_result_version']=result['result_version'];result['analysis']['foods']=copy.deepcopy(body['foods'])
            self.corrected['lunch']=result;self.patch_requests[request_key]=result
            return {**copy.deepcopy(result),'result':copy.deepcopy(result),'request':{'input_hash':'ih1_'+'f'*64}}
        self.meal.day_state.side_effect=state;self.meal.request_state.side_effect=lookup;self.meal.json_write.side_effect=correction
        self.ai.structured.return_value={'foods':[{**f,'calories':300,'protein':15,'fat':10,'carbs':37.5} for f in self.draft['meals']['lunch']['foods']]}
        self.ai.review_meal.side_effect=[{'status':'publishable','reason_codes':[]},{'status':'hold','reason_codes':['nutrition_semantic_mismatch']},
                                         {'status':'hold','reason_codes':['image_analysis_mismatch']}]
        self.ai.review_ogp.return_value={'status':'publishable','reason_codes':[]}
        with self.assertRaises(QualityHold):register_day(self.store,self.draft,self.saved,self.base/'ogp',self.meal,self.ai,'www.log-eats.com',self.capture,allow_correction=True)
        self.ai.review_meal.side_effect=None;self.ai.review_meal.return_value={'status':'publishable','reason_codes':[]}
        for name,value in [('draft.json',self.draft),('drive.json',self.saved)]: (self.base/name).write_text(json.dumps(value),encoding='utf-8')
        self.policy=ReplacementPolicy(self.store,waived_policy(),lambda:NOW)
        self.flow=ReplacementFlow(self.store,Mock(),self.policy,Mock(),Mock(),lambda:NOW)
        owner='11111111-1111-4111-8111-111111111111';h=acquire(self.store,TARGET,owner,NOW)
        self.e={'target_date':TARGET,'slot':'lunch','owner':owner,'epoch':h['epoch'],'deadline':NOW.timestamp()+3600}
        context={'directory':str(self.base),'draft':self.draft,'saved':self.saved,'host':'www.log-eats.com'}
        with self.store.transaction() as db:
            db.execute("INSERT INTO replacement_episodes VALUES(?,?,?,?,'checking','{}',NULL,NULL,NULL,?,NULL)",(TARGET,'lunch',owner,h['epoch'],self.e['deadline']))
            db.execute('CREATE TABLE replacement_runtime_context(target_date TEXT PRIMARY KEY,context TEXT)')
            db.execute('INSERT INTO replacement_runtime_context VALUES(?,?)',(TARGET,json.dumps(context)))
        Collection(self.store,self.base,self.draft,self.saved,self.meal,self.ai,'www.log-eats.com',self.policy,Mock(),lambda:NOW,Mock())(self.e)
        self.pre=copy.deepcopy(SAMPLES['precondition_success']);self.pre['policy']['used_replan_ordinals']=[]
        current={v['meal']['meal_type']:v for v in state(TARGET)['meals']}
        for m in self.pre['meals']:
            slot=m['expected']['target_slot'];value=current[slot]
            m['expected'].update(target_date=TARGET,target_result_version=value['result_version'],target_share_url=value['share']['url'])
            m.update(image_sha256=value['image_sha256'],stored_image_sha256=value['stored_image_sha256'],analysis=copy.deepcopy(value['analysis']))
        self.binding={'owner':owner,'epoch':h['epoch'],'preconditions':self.pre}

    def attempt(self):return self.policy.attempt(self.target,'lunch',binding=self.binding)

    def test_native_corrected_hold_reaches_real_attempt_and_schema_valid_prepare(self):
        receipt,deadline=self.attempt();own=next(m for m in self.pre['meals'] if m['expected']['target_slot']=='lunch')
        metadata=copy.deepcopy(self.samples['prepare_input']);metadata.update(expected=own['expected'],precondition_digest=digest(own['expected'],'pc1_'),migration=receipt)
        metadata['publication_hold']['provider_checked_at']=stamp(self.now)
        Contract(lambda:self.now).validate('PrepareInput',metadata)
        with self.store.transaction() as db:
            origin=verify_receipt(db,self.target,'lunch',receipt,self.e['owner'],self.e['epoch'])
            self.assertEqual(origin['quality_hold_evidence']['step'],'meal_review:lunch:corrected')
            self.assertFalse(db.execute("SELECT 1 FROM preparation_steps WHERE state='held'").fetchone())
        self.assertEqual(self.meal.register.call_count,3);self.assertEqual(self.meal.json_write.call_count,1)
        self.assertEqual(receipt['attempt_ordinal'],1);self.assertEqual(receipt['prior_consumed_ordinals'],[])

    def test_old_initial_hold_cannot_replace_latest_corrected_pass(self):
        with self.store.transaction() as db:db.execute("UPDATE preparation_steps SET result=? WHERE step='meal_review:lunch:corrected'",(json.dumps({'status':'publishable','reason_codes':[]}),))
        with self.assertRaisesRegex(ValueError,'quality_hold_required'):self.attempt()
        self.no_attempt()

    def test_same_image_changed_current_version_is_rejected(self):
        next(m for m in self.pre['meals'] if m['expected']['target_slot']=='lunch')['expected']['target_result_version']='rv1_'+'d'*64
        with self.assertRaisesRegex(ValueError,'quality_hold_required'):self.attempt()
        self.no_attempt()

    def test_changed_review_input_and_unknown_cannot_start(self):
        with self.store.transaction() as db:db.execute("UPDATE preparation_steps SET digest=? WHERE step='meal_review:lunch:corrected'",('a'*64,))
        with self.assertRaisesRegex(ValueError,'quality_hold_required'):self.attempt()
        self.no_attempt()
        with self.store.transaction() as db:db.execute("UPDATE preparation_steps SET state='outcome_unknown' WHERE step='meal_review:lunch:corrected'")
        with self.assertRaisesRegex(ValueError,'migration_unverified'):self.attempt()
        self.no_attempt()

    def test_changed_current_analysis_image_and_ogp_binding_are_rejected(self):
        own=next(m for m in self.pre['meals'] if m['expected']['target_slot']=='lunch')
        for name,value in [('image_sha256','b'*64),('stored_image_sha256','b'*64),('analysis',{})]:
            before=copy.deepcopy(own[name]);own[name]=value
            with self.assertRaisesRegex(ValueError,'quality_hold_required'):self.attempt()
            self.no_attempt();own[name]=before
        with self.store.transaction() as db:
            row=db.execute("SELECT result FROM preparation_steps WHERE step='ogp_capture:lunch-corrected'").fetchone()
            value=json.loads(row[0]);value['digest']='a'*64
            db.execute("UPDATE preparation_steps SET result=? WHERE step='ogp_capture:lunch-corrected'",(json.dumps(value),))
        with self.assertRaisesRegex(ValueError,'quality_hold_required'):self.attempt()
        self.no_attempt()

    def no_attempt(self):
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM replacement_attempts').fetchone()[0],0)
