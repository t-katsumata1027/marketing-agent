"""v3の構造・所有・原本・専用Drive実Clientを隔離して検証する。"""
import base64
import copy
import hashlib
import io
import json
import tempfile
import unittest
import uuid
from datetime import UTC,datetime
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
from src.mina.scoped_rescue_contract import ScopedRescueContract,ScopedRescueClient,SCHEMA_SHA
from src.mina.scoped_rescue_storage import ScopedRescueJournal
from src.mina.scoped_rescue_runtime import acceptance
from src.mina.replacement_contract import canonical,digest
from src.mina.replacement_ports import PreparationPorts
from src.mina.replacement_daily import daily_comment
from src.mina.drive import DriveClient
from src.mina.store import Store
from src.mina.publication_hold import acquire

NOW=datetime(2026,10,28,tzinfo=UTC)
OWNER='99999999-9999-4999-8999-999999999999'
ROOT=Path(__file__).resolve().parents[2]


class ScopedContractTests(unittest.TestCase):
    def setUp(self):
        self.contract=ScopedRescueContract(lambda:NOW,'2026-10-28','lunch')
        self.fixtures=json.loads((ROOT/'investigations/preparation-recovery-20261028/rescue-v3/structural-fixtures.json').read_text(encoding='utf-8'))['cases']
        self.issue=copy.deepcopy(next(c['value'] for c in self.fixtures if c['name']=='合成昼食権限の構造'))

    def test_fixed_structural_vectors(self):
        from jsonschema.exceptions import ValidationError
        for c in self.fixtures:
            with self.subTest(c['name']):
                if c['valid']:self.contract.validate(c['definition'],c['value'])
                else:
                    with self.assertRaises(ValidationError):self.contract.validate(c['definition'],c['value'])

    def test_semantic_other_slot_digest_and_freshness_not_schema_only(self):
        b=self.issue['basis'];self.contract.other_meals(b,issuing=True)
        for mutate in (lambda v:v['other_meals_reviews'][0].update(evidence_digest='f'*64),
                       lambda v:v['other_meals_reviews'][0].update(reviewed_at='2026-10-27T23:57:59.000Z'),
                       lambda v:v['other_meals_reviews'][1].update(target_slot='breakfast'),
                       lambda v:v['other_meals_expected'][0].update(ogp_input_digest='f'*24)):
            value=copy.deepcopy(b);mutate(value)
            with self.assertRaises(ValueError):self.contract.other_meals(value,issuing=True)

    def test_v3_routes_never_fallback(self):
        c=ScopedRescueClient('https://www.log-eats.com','synthetic-token-never-a-secret-'+40*'x',decoder=self.contract.decode)
        self.assertEqual(c.route('/api/internal/mina/requests/'+str(uuid.uuid4())+'?operation=meal_replace').split('/')[5],'commits')
        with self.assertRaises(ValueError):c.route('/api/internal/mina/meal-rescue-candidates')

    def test_acceptance_does_not_infer_true_from_schema_or_flag(self):
        settings={'target_date':'2026-10-28','target_slot':'lunch','case_uuid':str(uuid.uuid4()),'contract_version':'mina-rescue-v3',
                  'schema_sha256':SCHEMA_SHA,'agent_commit':'a'*40,'body_commit':'b'*40}
        with self.assertRaises(ValueError):acceptance(settings,'a'*40)


class ScopedJournalTests(unittest.TestCase):
    def setUp(self):
        ScopedContractTests.setUp(self);self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        flags=patch.dict('os.environ',{'MINA_MEAL_REPLACEMENT_ENABLED':'true'});flags.start();self.addCleanup(flags.stop)
        self.store=Store(Path(self.tmp.name)/'db');acquire(self.store,'2026-10-28',OWNER,NOW)
        self.j=ScopedRescueJournal(self.store,lambda:NOW,'2026-10-28','lunch',self.issue['case_uuid'])
        self.j.issue(self.issue,OWNER,1,{'synthetic':True})

    def test_other_day_and_v1_saved_do_not_affect_stage(self):
        from src.mina.rescue_storage import RescueJournal
        RescueJournal(self.store,lambda:NOW)
        with self.store.transaction() as db:
            db.execute("INSERT INTO rescue_events(kind,reference,document,observed_at) VALUES('saved','old','{}','old')")
        self.j.stage('ready');self.assertFalse(self.j.saved());self.assertEqual(self.j.job()['stage'],'ready')
        other=ScopedRescueJournal(self.store,lambda:NOW,'2026-10-29','lunch',str(uuid.uuid4()))
        self.assertEqual(other.rows('events'),[])

    def test_no_case_reissue_or_uuid_reassignment(self):
        value=copy.deepcopy(self.issue);value['case_uuid']=str(uuid.uuid4());value['planned_prepare_uuid']=str(uuid.uuid4())
        other=ScopedRescueJournal(self.store,lambda:NOW,'2026-10-28','lunch',value['case_uuid'])
        with self.assertRaises(ValueError):other.issue(value,OWNER,1,{})
        from src.mina.rescue_storage import reserve_uuid
        with self.store.transaction() as db:
            with self.assertRaises(ValueError):reserve_uuid(db,self.issue['planned_prepare_uuid'],'meal_replace','another-owner')

    def observation(self,persistence,status,key=None):
        if key is None:
            key=str(uuid.uuid4());meta={'request_uuid':key,'rescue':{'case_uuid':self.issue['case_uuid']}}
            self.j.prepare(meta,'meal_replace',canonical(meta),'application/json')
        meta=json.loads(self.j.request(key)['metadata'])
        request={'request_uuid':key,'operation':'meal_replace','input_hash':digest({'operation':'meal_replace','rescue':meta['rescue']},'ih1_'),
                 'persistence':persistence,'status':status,'stage':'reserved' if status=='processing' else 'completed' if status=='succeeded' else 'failed'}
        return key,self.j.observe(key,{'request':request})

    def test_processing_not_saved_to_saved_is_not_a_conflict(self):
        key,first=self.observation('not_saved','processing');self.assertFalse(first['terminal'])
        key,saved=self.observation('saved','succeeded',key);self.assertTrue(saved['terminal']);self.assertNotIn('conflict',saved)
        key,late=self.observation('not_saved','processing',key);self.assertEqual(late['persistence'],'saved');self.assertNotIn('conflict',late)

    def test_processing_to_terminal_not_saved_and_real_conflict(self):
        key,first=self.observation('not_saved','processing')
        key,failed=self.observation('not_saved','failed',key);self.assertTrue(failed['terminal']);self.assertNotIn('conflict',failed)
        key,conflict=self.observation('saved','succeeded',key);self.assertTrue(conflict['conflict']);self.assertEqual(conflict['persistence'],'saved')


class ScopedOriginTests(unittest.TestCase):
    def setUp(self):
        from tests.mina.test_native_replacement_origin import NativeOriginTests
        from scripts.build_replacement_waived_policy import build
        policy=patch('tests.mina.test_replacement_control.waived_policy',return_value=build()['agent_policy'])
        policy.start();self.addCleanup(policy.stop)
        self.native=NativeOriginTests();self.native.setUp();self.addCleanup(self.native.doCleanups)

    def test_real_registration_correction_native_hold_keeps_absent_first(self):
        from src.mina.scoped_rescue_origin import capture
        n=self.native
        result=capture(n.store,n.pre,n.e['owner'],n.e['epoch'],n.now,n.target,'lunch')
        self.assertFalse(result['original_context_present']);self.assertIsNone(result['original_first_replan_started_at'])
        self.assertIsNone(result['original_hard_deadline_at']);self.assertEqual(result['baseline_consumed_ordinals'],[])
        self.assertIsNone(result['document']['native_quality_hold']['stored_descriptor_ambiguity'])
        self.assertIs(result['document']['native_quality_hold']['m1_ambiguity'],False)
        self.assertEqual(result['document']['native_quality_hold']['step'],'meal_review:lunch:corrected')
        self.assertEqual(n.meal.register.call_count,3);self.assertEqual(n.meal.json_write.call_count,1)

    def test_other_slots_union_does_not_consume_target_ordinal(self):
        from src.mina.scoped_rescue_origin import capture
        n=self.native;pre=copy.deepcopy(n.pre);pre['policy']['used_replan_ordinals']=[1]
        result=capture(n.store,pre,n.e['owner'],1,n.now,n.target,'lunch')
        self.assertEqual(result['baseline_consumed_ordinals'],[]);self.assertEqual(result['document']['day_used_replan_ordinals'],[1])

    def test_actual_native_reviews_bind_operator_bundle_without_issuing(self):
        from datetime import timedelta
        from src.mina.scoped_rescue_flow import ScopedRescueFlow
        from src.mina.scoped_rescue_origin import capture
        from src.mina.replacement_inventory import ExternalInventory,REQUIRED_STATES
        from src.mina.domain import ACCOUNT
        from src.mina.rescue_contract import POLICY,BUDGET,APPROVAL
        from src.mina.replacement_contract import stamp
        n=self.native;key=str(uuid.uuid4());normal=Mock();normal.preconditions.return_value=n.pre
        inventory=ExternalInventory(n.store,lambda ch,b,t,c:{'account':ACCOUNT,'channel':b,'date':t,'covered_states':sorted(REQUIRED_STATES),
            'complete':True,'items':[],'has_next':False},lambda:n.now,{'x':'x','instagram':'ig'})
        flow=ScopedRescueFlow(n.store,Mock(),normal,n.policy,inventory,Mock(),lambda:n.now,n.target,'lunch',key)
        anchors=[{'target_slot':m['expected']['target_slot'],'target_result_version':m['expected']['target_result_version'],
            'original_sha256':m['image_sha256'],'stored_sha256':m['stored_image_sha256'],'analysis_digest':m['analysis_digest'],
            'ogp_input_digest':'d'*24} for m in n.pre['meals'] if m['expected']['target_slot']!='lunch']
        with patch.dict('os.environ',{'MINA_SCOPED_RESCUE_ENABLED':'true'}):
            reviews=flow.review_other_meals(n.meal,n.ai,n.capture,n.draft,n.saved,'www.log-eats.com',anchors,'synthetic-review-v3',stamp(n.now+timedelta(minutes=15)),n.e['owner'],1)
        origin=capture(n.store,n.pre,n.e['owner'],1,n.now,n.target,'lunch',key);receipt=inventory.check(n.target)
        publication={'checked_at':receipt['checked_at'],'all_pages_checked':True,'all_providers_checked':True,'hold_epoch':1,
            'pause_receipt_digest':digest({'target_date':n.target,'owner':n.e['owner'],'epoch':1}),
            'dispatching_count':0,'unknown_count':0,'external_accepted_count':0,'published_count':0,'receipt':receipt}
        ledger={'native_origin':origin,'all_sources_enumerated':True,'all_native_meal_requests_terminal':True,'unfinished_native_meal_request_count':0,
            'same_menu_repair_history_preserved':True,'consumed_ordinals':origin['baseline_consumed_ordinals'],'other_meals_reviews':reviews,
            'publication_evidence_sha256':digest(publication),**{k:origin[k] for k in ('original_context_present','original_first_replan_started_at','original_preparation_deadline_at','original_hard_deadline_at')}}
        costs={'history_complete':True,'unresolved_cost_count':0,'waived_approval_digest':APPROVAL,'ledgers':origin['document']['ledgers']}
        own=next(m for m in n.pre['meals'] if m['expected']['target_slot']=='lunch')
        basis={'target_date':n.target,'target_slot':'lunch','initial_expected':own['expected'],'original_image_sha256':own['image_sha256'],
            'original_stored_image_sha256':own['stored_image_sha256'],'target_analysis_digest':own['analysis_digest'],
            **{k:origin[k] for k in ('original_context_present','original_first_replan_started_at','original_preparation_deadline_at','original_hard_deadline_at','baseline_consumed_ordinals')},
            'source_ledger_digest':digest(ledger),'cost_history_digest':digest(costs),'source_requests_terminal':True,'hold_epoch':1,
            'pause_receipt_digest':publication['pause_receipt_digest'],'policy_version':POLICY,'budget_policy_digest':BUDGET,'cost_approval_digest':APPROVAL,
            'not_after':stamp(n.now+timedelta(minutes=15)),'exception_kind':'operator_scoped_meal_replacement',
            'other_meals_reviews':reviews,'other_meals_expected':[{**a,'independent_review_digest':digest(next(r for r in reviews if r['target_slot']==a['target_slot']))} for a in anchors],
            'review_policy_version':'synthetic-review-v3'}
        prepare=str(uuid.uuid4());body='858e2bbb4f62a26ec8dc6e0d5310944541d0ec27'
        approval={'action':'issue','approved':True,'case_uuid':key,'planned_prepare_uuid':prepare,'basis_sha256':digest(basis),'not_after':basis['not_after'],
            'expected_review_policy_version':basis['review_policy_version'],'expected_agent_commit':'a'*40,'expected_body_commit':body,'expected_schema_sha256':SCHEMA_SHA}
        basis['operator_approval_digest']=digest(approval)
        issue={'contract_version':'mina-rescue-v3','case_uuid':key,'planned_prepare_uuid':prepare,'attempt_ordinal':1,'basis':basis}
        proof={'source_ledger':ledger,'cost_history':costs,'publication':publication,'integration':{'agent_commit':'a'*40,'body_commit':body,'schema_sha256':SCHEMA_SHA}}
        flow.stage_issue(issue,approval,proof,owner=n.e['owner'],epoch=1,agent_commit='a'*40,body_commit=body)
        packet=flow.operator_bundle();self.assertEqual(packet['input'],issue);self.assertFalse(packet['agent_issues_authority'])
        self.assertEqual(len(flow.journal.rows('calls')),4);self.assertEqual(flow.journal.rows('requests'),[])
        flow.client.case.assert_not_called()


class DriveBoundaryTests(unittest.TestCase):
    def test_actual_port_and_client_upload_verify_without_old_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp,patch('src.mina.drive.require_live'):
            old=b'old-original';raw=io.BytesIO();Image.new('RGB',(32,32),'red').save(raw,format='JPEG');raw=raw.getvalue()
            path=Path(tmp)/'old.jpg';path.write_bytes(old)
            key=str(uuid.uuid4());target='2026-10-28';name=f'{target}-lunch-replacement-{key}.jpg'
            folder='synthetic_folder_123';newid='synthetic_new_id_123';oldid='synthetic_old_id_123';files={oldid:(old,target+'-lunch.jpg',{})};calls=[]
            client=DriveClient('synthetic','synthetic','synthetic',folder)
            def transport(url,data=None,headers=None,method='GET',**kwargs):
                calls.append((method,url))
                if 'upload/drive' in url:
                    meta=json.loads(data.split(b'\r\n\r\n',1)[1].split(b'\r\n--',1)[0]);files[newid]=(raw,meta['name'],meta['appProperties']);return json.dumps({'id':newid}).encode()
                fid=url.split('/files/')[1].split('?')[0]
                if fid==folder:return json.dumps({'id':folder,'mimeType':'application/vnd.google-apps.folder','trashed':False,'capabilities':{'canAddChildren':True}}).encode()
                image,filename,props=files[fid]
                if 'alt=media' in url:return image
                return json.dumps({'id':fid,'name':filename,'mimeType':'image/jpeg','parents':[folder],'trashed':False,'size':str(len(image)),
                    'md5Checksum':hashlib.md5(image).hexdigest(),'appProperties':props}).encode()
            client._request=transport;client._authorization=lambda:{}
            saved={'images':{'lunch':{'path':str(path),'sha256':hashlib.sha256(old).hexdigest(),'drive':{'id':oldid}}}}
            ports=PreparationPorts(Mock(),client,{},saved,Path(tmp)/'ports',lambda:NOW)
            c={'prepared_preconditions':{'target_date':target,'target_slot':'lunch'},'prepare_uuid':key,'candidate_version':'cv1_'+'c'*64,'original':{'sha256':hashlib.sha256(raw).hexdigest()}}
            proof=ports.drive(raw,c)
            self.assertEqual(proof['previous_download_sha256'],hashlib.sha256(old).hexdigest());self.assertEqual(files[newid][1],name)
            self.assertEqual(files[oldid][0],old);self.assertEqual(sum(m=='POST' for m,u in calls),1)
            with self.assertRaises(ValueError):client.upload(raw,key+'.jpg')

    def test_daily_comment_uses_real_total_and_rejects_1799_point4(self):
        def state(kcal):return {'date':'2026-10-28','meals':[{'meal':{'meal_type':slot},'analysis':{'summary':{'totalCalories':value,'totalProtein':20,'totalFat':10,'totalCarbs':60}}} for slot,value in zip(('breakfast','lunch','dinner'),kcal)]}
        text=daily_comment(state([455,750,716]));self.assertIn('1921',text);self.assertNotIn('パスタ',text)
        with self.assertRaises(ValueError):daily_comment(state([455,628.4,716]))


if __name__=='__main__':unittest.main()
