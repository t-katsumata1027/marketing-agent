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
            result['quality']={'status':'pass','atwater_calories':600,'calorie_difference':0,'calorie_difference_ratio':0,'warnings':[]}
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
        from tests.mina.test_replacement_integration import fresh
        self.pre.update(date=TARGET,state_version='sv1_'+'3'*64,freshness=fresh(NOW),daily_share=None)
        self.pre['policy'].update(policy_version=self.policy.policy['policy_version'],budget_policy_digest=self.policy.policy_digest,
            budget_configured=False,analysis_provider='gemini',cost_control={'mode':'waived','approval_reference_digest':self.policy.policy['approval_reference_digest']},
            existing_preparation_deadline_verified=False,blocking_reasons=['deadline_unverified'])
        current={v['meal']['meal_type']:v for v in state(TARGET)['meals']}
        for m in self.pre['meals']:
            slot=m['expected']['target_slot'];value=current[slot]
            m['expected'].update(target_date=TARGET,state_version=self.pre['state_version'],target_result_version=value['result_version'],target_share_url=value['share']['url'])
            analysis={**copy.deepcopy(value['analysis']),'spec':'mina-candidate-analysis-v1','analyzer_spec':'isolated-m1-v1',
                      'quality_spec':'isolated-quality-v1','analysis_source':'stored_canonical','is_ambiguous':None,
                      'quality':{**value['quality'],'atwater_calories':600,'calorie_difference':0,'calorie_difference_ratio':0}}
            analysis['foods']=[{**f,'amount':f.get('amount')} for f in analysis['foods']]
            m.update(image_sha256=value['image_sha256'],stored_image_sha256=value['stored_image_sha256'],analysis=analysis)
            m['precondition_digest']=digest(m['expected'],'pc1_');m['analysis_digest']=Contract(lambda:NOW).analysis(analysis)
        self.pre['summary']={k:sum(m['analysis']['summary'][k] for m in self.pre['meals']) for k in self.pre['summary']}
        Contract(lambda:NOW).preconditions(self.pre,TARGET)
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
            self.assertIsNone(origin['quality_hold_evidence']['stored_descriptor_ambiguity'])
            self.assertIs(origin['quality_hold_evidence']['m1_ambiguity'],False)
            self.assertFalse(db.execute("SELECT 1 FROM preparation_steps WHERE state='held'").fetchone())
        self.assertEqual(self.meal.register.call_count,3);self.assertEqual(self.meal.json_write.call_count,1)
        self.assertEqual(receipt['attempt_ordinal'],1);self.assertEqual(receipt['prior_consumed_ordinals'],[])

    def test_real_flow_sends_one_prepare_from_native_normal_registration(self):
        from tests.mina.test_replacement_integration import Server
        from src.mina.replacement_client import ReplacementClient
        from src.mina.replacement_inventory import ExternalInventory,REQUIRED_STATES
        from src.mina.domain import ACCOUNT
        from PIL import Image
        from io import BytesIO
        server=Server(lambda:self.now,self.policy);server.pre=self.pre
        self.flow.client=ReplacementClient('https://www.log-eats.com','isolated-token-not-a-real-secret',decoder=self.flow.contract.decode,opener=server,clock=lambda:self.now,write_authorized=True)
        self.flow.inventory=ExternalInventory(self.store,lambda ch,b,t,c:{'account':ACCOUNT,'channel':b,'date':t,'covered_states':sorted(REQUIRED_STATES),'complete':True,'items':[],'has_next':False},lambda:self.now,{'x':'x','instagram':'ig'})
        self.flow.collect_missing=Collection(self.store,self.base,self.draft,self.saved,self.meal,self.ai,'www.log-eats.com',self.policy,self.flow.inventory,lambda:self.now,Mock())
        self.flow.ports.previous_image_hashes.return_value={s:i['sha256'] for s,i in self.saved['images'].items()}
        self.flow.ports.plan.return_value={'menu':'隔離の別献立','comment':'隔離の候補です。','estimated_calories':750,
            'foods':[{'name':'別の主食','amount':'150g'},{'name':'別の主菜','amount':'100g'}],'image_description':'隔離画像'}
        raw=BytesIO();Image.new('RGB',(4,4),'white').save(raw,format='PNG');self.flow.ports.image.return_value=raw.getvalue()
        self.flow._complete_preflight(self.target,self.e)
        self.assertEqual(self.flow.episode(self.target)['stage'],'ready')
        self.assertEqual(sum(method=='POST' for method,path in server.calls),1)
        self.assertEqual(self.meal.register.call_count,3);self.assertEqual(self.meal.json_write.call_count,1)
        with self.store.transaction() as db:
            metadata=json.loads(db.execute('SELECT metadata FROM replacement_wire').fetchone()[0])
            self.assertEqual(metadata['expected']['target_slot'],'lunch');self.assertEqual(metadata['migration']['attempt_ordinal'],1)
            self.assertFalse(db.execute("SELECT 1 FROM preparation_steps WHERE state='held'").fetchone())

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

    def test_descriptor_ambiguity_and_wrong_provenance_are_not_normalized_away(self):
        own=next(m for m in self.pre['meals'] if m['expected']['target_slot']=='lunch')
        own['analysis']['is_ambiguous']=True
        with self.assertRaisesRegex(ValueError,'quality_hold_required'):self.attempt()
        self.no_attempt();own['analysis']['is_ambiguous']=None;own['analysis']['analysis_source']='candidate_analysis'
        with self.assertRaisesRegex(ValueError,'quality_hold_required'):self.attempt()
        self.no_attempt()

    def test_valid_descriptor_food_order_amount_nutrients_and_quality_must_match(self):
        own=next(m for m in self.pre['meals'] if m['expected']['target_slot']=='lunch');original=copy.deepcopy(own['analysis'])
        def order(a):a['foods'].reverse()
        def amount(a):a['foods'][0]['amount']='999g'
        def nutrients(a):a['foods'][0]['protein']+=1;a['summary']['totalProtein']+=1
        def quality(a):a['quality']['calorie_difference']=1
        for change in (order,amount,nutrients,quality):
            own['analysis']=copy.deepcopy(original);change(own['analysis'])
            own['analysis_digest']=Contract(lambda:self.now).analysis(own['analysis'])
            with self.assertRaisesRegex(ValueError,'quality_hold_required'):self.attempt()
            self.no_attempt()

    def test_missing_descriptor_amount_and_ambiguous_m1_are_rejected(self):
        own=next(m for m in self.pre['meals'] if m['expected']['target_slot']=='lunch');original=copy.deepcopy(own['analysis'])
        del own['analysis']['foods'][0]['amount']
        with self.assertRaisesRegex(ValueError,'quality_hold_required'):self.attempt()
        self.no_attempt();own['analysis']=original
        with self.store.transaction() as db:
            row=db.execute("SELECT result FROM preparation_steps WHERE step='meal_correction:lunch'").fetchone()
            value=json.loads(row[0]);value['result']['analysis']['is_ambiguous']=True;value['analysis']['is_ambiguous']=True
            db.execute("UPDATE preparation_steps SET result=? WHERE step='meal_correction:lunch'",(json.dumps(value),))
        # M1自身の品質・完成result照合で先に拒否される場合も、置換予約0を要求する。
        with self.assertRaises(ValueError):self.attempt()
        self.no_attempt()

    def no_attempt(self):
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM replacement_attempts').fetchone()[0],0)
