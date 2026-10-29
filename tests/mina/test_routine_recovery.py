"""新規通常日だけの概算点検・照会復旧・実呼出し消費を隔離検証する。"""
import copy,hashlib,json,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.domain import JST
from src.mina.store import Store
from src.mina.routine_recovery import Routine,scope,ReviewedAI,claim_correction,validate_mapping,explicit_grams,provider_started,provider_finished
from src.mina.routine_request_recovery import expected_hash,project,recover,completed_lookup
from src.mina.request_ledger import RequestLedger
from src.mina.generation_receipts import operation
from src.mina.preparation_status import QualityHold
from src.mina.clients import OutcomeUnknown
from test_tolerance_case import positive
import test_registration as registration

KEY='11111111-1111-4111-8111-111111111111'

class RoutineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=Store(self.root/'db');self.now=datetime(2026,12,31,19,tzinfo=JST);self.target='2027-01-01'
        self.store.claim_preparation(self.target,self.now)
        self.run=Routine(self.store,self.target,'a'*40,'www.log-eats.com',lambda:self.now);self.run.admit()
        self.scoped=scope(self.run);self.scoped.__enter__();self.addCleanup(self.scoped.__exit__,None,None,None)
    def test_new_year_policy_not_reapplied_to_existing_work_or_expired(self):
        with self.assertRaises(Exception):self.run.admit()
        self.now+=timedelta(hours=1)
        with self.assertRaises(Exception):self.run.check()
    def test_minor_variation_and_unmeasurable_are_raw_warnings(self):
        meal={'menu':'ご飯','foods':[{'name':'ご飯','amount':'150g'}]};ai=Mock()
        value=positive(meal);value['foods'][0].update(portion='unmeasurable',evidence='cannot_measure_weight')
        value['warnings']=[{'code':'weight_unmeasurable','region':'center','food_index':0}]
        ai.structured.return_value=value;review=ReviewedAI(ai,self.run)
        with operation(self.target,'image_review:breakfast','b'*64):result=review.review_image(b'image',meal)
        self.assertEqual(result,{'status':'image_acceptable','reason_codes':[]})
        with self.store.transaction() as db:self.assertEqual(json.loads(db.execute('SELECT raw FROM routine_quality_reviews').fetchone()[0]),value)
    def test_missing_food_is_hold_not_reclassified_as_warning_or_correction(self):
        meal={'menu':'ご飯','foods':[{'name':'ご飯','amount':'150g'}]};ai=Mock();value=positive(meal,'publishable')
        value.update(status='hold',blocking=[{'code':'food_missing','region':'center','food_index':0}]);value['checks']['food_identity']='hold';value['foods'][0].update(presence='absent',evidence='identity_unknown')
        ai.structured.return_value=value;review=ReviewedAI(ai,self.run)
        response={'result':{'meal':{'meal_type':'breakfast','logged_at':self.target+'T07:30:00+09:00'}}}
        with operation(self.target,'meal_review:breakfast','b'*64):result=review.review_meal(b'image',meal,response,{})
        self.assertEqual(result['status'],'hold');self.assertFalse(review.allow_correction(meal,response,result));self.assertEqual(ai.structured.call_count,1)
    def test_unknown_diagnostic_is_not_retried(self):
        ai=Mock();ai.structured.side_effect=TimeoutError('隔離不明')
        with operation(self.target,'image_review:breakfast','b'*64),self.assertRaises(TimeoutError):ReviewedAI(ai,self.run).review_image(b'image',{'foods':[]})
        self.assertEqual(ai.structured.call_count,1)
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT state FROM routine_quality_reviews').fetchone()[0],'unknown')
    def test_one_correction_across_uuid_variants(self):
        claim_correction(self.run,self.target,'lunch',{'variant':1})
        with self.assertRaises(Exception):claim_correction(self.run,self.target,'lunch',{'variant':2})
    def test_internal_provider_retries_count_and_unknown_never_refunded(self):
        receipt={'preparation':{'target_date':self.target,'step':'meal_review:lunch','input_digest':'b'*64}}
        for n in range(60):provider_started(str(n),receipt);provider_finished(str(n),'unknown' if n%2 else 'complete')
        with self.assertRaises(QualityHold):provider_started('61',receipt)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM routine_provider_calls').fetchone()[0],60)
            self.assertEqual(db.execute("SELECT count(*) FROM routine_provider_calls WHERE state='unknown' AND usage_status='unmeasured'").fetchone()[0],30)
    def test_real_codex_internal_retry_consumes_each_launch(self):
        import test_review_retry as retry_fixture
        from src.mina.codex_client import CodexClient
        retry=retry_fixture.ReviewRetryTests();retry.setUp();self.addCleanup(retry.doCleanups)
        with operation(self.target,'meal_review:lunch','b'*64):
            self.assertEqual(CodexClient().structured('隔離入力','same',{'type':'object'},'mina_routine_quality_review'),retry.output)
        self.assertEqual(len(retry.commands),2)
        with self.store.transaction() as db:
            rows=list(db.execute('SELECT state,usage_status FROM routine_provider_calls'))
        self.assertEqual([tuple(r) for r in rows],[('unknown','unmeasured'),('complete','unmeasured')])
    def test_weight_forms_and_full_mapping(self):
        for amount,w in [('炊飯後220g',220),('80g（千切り）',80),('1/2個（可食部25g）',25),('0.2kg',200)]:self.assertEqual(explicit_grams(amount),w)
        self.assertIsNone(explicit_grams('1杯'))
        for amount in ('80～100g','80g+20g','0g','-20g','−20g','1e2g','約80g'):
            with self.assertRaises(ValueError):explicit_grams(amount)
        meal={'foods':[{'name':'ご飯','amount':'炊飯後220g'},{'name':'塩','amount':'1g'}]}
        foods=[{**f,'calories':0,'protein':0,'fat':0,'carbs':0} for f in meal['foods']]
        value={'foods':foods,'dish_mapping':[{'food_index':0,'shares':[{'analysis_index':0,'fraction':1}]},{'food_index':1,'shares':[{'analysis_index':0,'fraction':.5},{'analysis_index':1,'fraction':.5}]}]}
        original={'result':{'analysis':{'foods':[{},{}]}}};validate_mapping(value,meal,original)
        bad=copy.deepcopy(value);bad['foods'][0].update(protein=300,calories=1200)
        with self.assertRaises(ValueError):validate_mapping(bad,meal,original)
        for change in ('missing','duplicate','fraction','major_split'):
            bad=copy.deepcopy(value)
            if change=='missing':bad['dish_mapping'].pop()
            elif change=='duplicate':bad['dish_mapping'][1]['food_index']=0
            elif change=='fraction':bad['dish_mapping'][1]['shares'][0]['fraction']=.2
            else:bad['dish_mapping'][0]['shares']=bad['dish_mapping'][1]['shares']
            with self.subTest(change=change),self.assertRaises(ValueError):validate_mapping(bad,meal,original)
    def request_fixture(self):
        helper=registration.RegistrationTests();client=helper.client();image=b'\xff\xd8\xfftest'
        payload={'contract_version':'mina-write-v1','meal_type':'breakfast','logged_at':self.target+'T07:30:00+09:00','comment':'朝食です。','expected_state_version':'sv1_'+'0'*64}
        ledger=RequestLedger(self.store);ledger.prepare(KEY,'meal_register',self.target,'breakfast',payload,image)
        response=client.register(image,'image.jpg','breakfast',payload['logged_at'],payload['comment'],request_key=KEY,expected_state_version=payload['expected_state_version'])
        request=ledger.restore(KEY);raw=client.request_state(KEY,'meal_register');raw['input_hash']=expected_hash(request);state=client.day_state(self.target);state['checked_at']=self.now.isoformat();raw['current_state_version']=state['state_version']
        client.request_state.side_effect=None;client.request_state.return_value=raw;client.day_state.side_effect=None;client.day_state.return_value=state
        return ledger,client,raw,state
    def test_official_saved_projection_and_source_mismatch(self):
        ledger,client,raw,state=self.request_fixture();request=ledger.restore(KEY)
        envelope=project(request,raw,state,self.run.host,self.now)
        self.assertEqual(envelope['result'],raw['current_result']);self.assertIn('agent_projection',envelope)
        for key,val in [('input_hash','ih1_'+'0'*64),('current_state_version','sv1_'+'f'*64),('image_sha256','f'*64),('result_status','modified')]:
            bad=copy.deepcopy(raw);bad[key]=val
            with self.subTest(key=key),self.assertRaises(ValueError):project(request,bad,state,self.run.host,self.now)
    def test_lost_response_one_post_one_get_and_persisted_projection(self):
        ledger,client,raw,state=self.request_fixture();client.request_state.reset_mock();send=Mock(side_effect=TimeoutError('隔離応答喪失'))
        response=ledger.execute_once(KEY,client,send)
        self.assertEqual(response['result'],raw['current_result']);self.assertEqual(send.call_count,1);self.assertEqual(client.request_state.call_count,1)
        self.assertEqual(completed_lookup(ledger,KEY,client,'meal_register'),raw);self.assertEqual(client.request_state.call_count,1)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT original_phase FROM routine_request_failures').fetchone()[0],'held')
            self.assertEqual(db.execute('SELECT count(*) FROM routine_request_lookups').fetchone()[0],1)
    def test_unrecognized_or_not_saved_seed_total_gets_three_raw_preserved(self):
        for mode in ('invalid','terminal_then_unknown','404'):
            with self.subTest(mode=mode):
                key=KEY[:-1]+str(('invalid','terminal_then_unknown','404').index(mode)+2);ledger=RequestLedger(self.store)
                payload={'comment':'朝食','logged_at':self.target+'T07:30:00+09:00','meal_type':'breakfast','expected_state_version':'sv1_'+'0'*64}
                ledger.prepare(key,'meal_register',self.target,'breakfast',payload,b'\xff\xd8\xfftest');client=Mock()
                if mode=='404':client.request_state.side_effect=ValueError('隔離404')
                elif mode=='invalid':client.request_state.return_value={'unrecognized':True}
                else:client.request_state.side_effect=[{'contract_version':'mina-write-v1','request_uuid':key,'operation':'meal_register','status':'failed','persistence':'not_saved','stage':'quality','reason':'quality_review_required','result_status':'unresolved','result':None,'current_result':None},ValueError('隔離'),{}]
                send=Mock(side_effect=TimeoutError('隔離不明'))
                with patch('src.mina.routine_request_recovery.time.sleep'),self.assertRaises(OutcomeUnknown):ledger.execute_once(key,client,send)
                self.assertEqual(send.call_count,1);self.assertEqual(client.request_state.call_count,3)
                with self.store.transaction() as db:
                    rows=list(db.execute('SELECT * FROM routine_request_lookups WHERE request_key=?',(key,)));self.assertEqual(len(rows),3)
                    if mode=='invalid':self.assertEqual(json.loads(rows[0]['raw']),{'unrecognized':True})

if __name__=='__main__':unittest.main()
