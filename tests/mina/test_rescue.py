"""救済の期限・対象束縛・不明保持を実SQLiteと合成HTTPで検証する。"""
import copy
import hashlib
import json
import tempfile
import unittest
import uuid
from datetime import UTC,datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from jsonschema import ValidationError
from src.mina.store import Store
from src.mina.request_ledger import RequestLedger
from src.mina.publication_hold import acquire,PublicationHeld
from src.mina.rescue_collection import CollectionPermit
from src.mina.rescue_storage import RescueJournal,reserve_uuid
from src.mina.rescue_contract import RescueContract,RescueClient,TARGET,SCHEMA_SHA
from src.mina.replacement_contract import canonical,digest,stamp
from src.mina.replacement_policy import ReplacementPolicy
from scripts.build_replacement_waived_policy import build
from src.mina.rescue_flow import RescueFlow

FLAGS={'MINA_MEAL_REPLACEMENT_ENABLED':'true','MINA_SAME_DAY_RESCUE_ENABLED':'true','MINA_LIVE_WRITES':'true'}
OWNER='99999999-9999-4999-8999-999999999999'
FIXTURES=json.loads(Path(__file__).with_name('rescue_schema_fixtures.json').read_text(encoding='utf-8'))['cases']

def sample(name):return copy.deepcopy(next(c['value'] for c in FIXTURES if c['definition']==name and c['valid']))

class RescueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'db')
        self.now=datetime(2026,10,24,tzinfo=UTC);self.clock=lambda:self.now
        self.flags=patch.dict('os.environ',FLAGS);self.flags.start();self.addCleanup(self.flags.stop)
        self.hold=acquire(self.store,TARGET,OWNER,self.now)
        self.contract=RescueContract(self.clock);self.permit=CollectionPermit(self.store,self.clock)
        self.policy=ReplacementPolicy(self.store,build()['agent_policy'],self.clock)
        self.journal=RescueJournal(self.store,self.clock)
        self.ledger=RequestLedger(self.store)
        self.approval={'target_date':TARGET,'operations':{'lunch':['meal_register','meal_correction'],'dinner':['meal_register','meal_correction']},
            'not_after':stamp(self.now+timedelta(hours=1)),'operator_approval_reference':'隔離合成本人承認','schema_sha256':SCHEMA_SHA}
        self.images={'lunch':hashlib.sha256(b'lunch').hexdigest(),'dinner':hashlib.sha256(b'dinner').hexdigest()}
        self.approval.update(hold_owner=OWNER,hold_epoch=1,code_commit='a'*40,images=self.images,scope='same_day_lunch_dinner_collection')
        self.permit.issue(self.approval,OWNER,1,'a'*40,self.images)

    def prepared(self, slot='lunch', operation='meal_register', **updates):
        from src.mina.domain import date,slot_time
        key=str(uuid.uuid4());payload={'contract_version':'mina-write-v1','meal_type':slot,
            'logged_at':slot_time(date.fromisoformat(TARGET),slot).isoformat() if slot!='daily' else '',
            'filename':f'{TARGET}-{slot}.jpg','expected_state_version':'sv1_'+'a'*64,**updates}
        self.ledger.prepare(key,operation,TARGET,slot,payload,slot.encode() if operation=='meal_register' else None)
        return key

    def test_structural_contract_rejects_completed_null_and_active_saved(self):
        for case in FIXTURES:
            with self.subTest(case=case['name']):
                if case['valid']:self.contract.validate(case['definition'],case['value'])
                else:
                    with self.assertRaises(ValidationError):self.contract.validate(case['definition'],case['value'])

    def test_hold_blocks_without_narrow_scope_and_allows_one_lunch_claim(self):
        key=self.prepared();operation=Mock(return_value={'request':{}})
        with self.assertRaises(PublicationHeld):self.ledger.execute_once(key,Mock(),operation)
        with self.permit.scope():self.ledger.execute_once(key,Mock(),operation)
        operation.assert_called_once()
        with self.permit.scope(),self.assertRaises(Exception):self.ledger.execute_once(self.prepared(),Mock(),operation)
        operation.assert_called_once()
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT state FROM publication_holds').fetchone()[0],'held')

    def test_breakfast_and_daily_never_admitted(self):
        for slot,operation in [('breakfast','meal_register'),('daily','daily_share')]:
            key=self.prepared(slot,operation)
            with self.permit.scope(),self.assertRaises(PublicationHeld):self.ledger.execute_once(key,Mock(),Mock())

    def test_claim_expiry_is_checked_inside_write_transaction(self):
        key=self.prepared();operation=Mock()
        with self.permit.scope():
            self.now+=timedelta(hours=2)
            with self.assertRaisesRegex(ValueError,'deadline'):self.ledger.execute_once(key,Mock(),operation)
        operation.assert_not_called()
        self.assertEqual(self.ledger.restore(key)['phase'],'prepared')

    def test_permit_reissue_does_not_extend_deadline(self):
        altered=copy.deepcopy(self.approval);altered['not_after']=stamp(self.now+timedelta(hours=2))
        with self.assertRaisesRegex(ValueError,'immutable'):self.permit.issue(altered,OWNER,1,'a'*40,self.images)

    def test_other_hold_epoch_rejected(self):
        with self.store.transaction() as db:db.execute('UPDATE publication_holds SET epoch=2')
        with self.assertRaises(PublicationHeld):self.permit.checkpoint()

    def test_correction_cannot_point_to_breakfast_share(self):
        key=self.prepared()
        with self.permit.scope():self.ledger.execute_once(key,Mock(),Mock(return_value={'request':{}}))
        self.ledger.record(key,'complete',{'persistence':'saved'})
        with self.store.transaction() as db:
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",(TARGET,'meal_register:lunch','a'*64,
                json.dumps({'result':{'meal':{'meal_type':'lunch'},'image_sha256':self.images['lunch'],
                    'share':{'url':'https://www.log-eats.com/s/lunch'},'normalized_comment':'昼'}}),self.now.timestamp()))
        correction=self.prepared(operation='meal_correction',share_url='https://www.log-eats.com/s/breakfast',reason='codex_secondary_review',comment='昼')
        operation=Mock()
        with self.permit.scope(),self.assertRaisesRegex(ValueError,'target_changed'):
            self.ledger.execute_once(correction,Mock(),operation)
        operation.assert_not_called()

    def test_flag_off_stops_unclaimed_lunch_inside_scope(self):
        key=self.prepared();operation=Mock()
        with self.permit.scope(),patch.dict('os.environ',{'MINA_SAME_DAY_RESCUE_ENABLED':'false'}):
            with self.assertRaisesRegex(ValueError,'disabled'):self.ledger.execute_once(key,Mock(),operation)
        operation.assert_not_called()

    def test_declared_lunch_cannot_register_breakfast_or_another_date(self):
        for update in ({'meal_type':'breakfast'},{'logged_at':'2026-10-25T12:00:00+09:00'}):
            key=self.prepared(**update);operation=Mock()
            with self.permit.scope(),self.assertRaisesRegex(ValueError,'target_changed'):
                self.ledger.execute_once(key,Mock(),operation)
            operation.assert_not_called()

    def origin_fixture(self):
        pre={'meals':[],'policy':{'used_replan_ordinals':[]}}
        for slot in ('breakfast','lunch','dinner'):
            raw=slot.encode();sha=hashlib.sha256(raw).hexdigest();key=str(uuid.uuid4())
            self.ledger.prepare(key,'meal_register',TARGET,slot,{},raw)
            self.ledger.record(key,'complete',{'persistence':'saved'})
            result={'result_version':'rv1_'+sha,'image_sha256':sha,'stored_image_sha256':'b'*64}
            path=Path(self.tmp.name)/(slot+'.jpg');path.write_bytes(raw)
            image={'path':str(path),'size':len(raw),'sha256':sha}
            pre['meals'].append({'expected':{'target_slot':slot,'target_result_version':result['result_version']},'image_sha256':sha,'stored_image_sha256':'b'*64})
            with self.store.transaction() as db:
                for step,value in [('meal_register:'+slot,{'result':result}),('image:'+slot,image)]:
                    db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",(TARGET,step,'c'*64,json.dumps(value),self.now.timestamp()-60))
        with self.store.transaction() as db:
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",(TARGET,'meal_review:breakfast','c'*64,
                json.dumps({'status':'hold','reason_codes':['image_analysis_mismatch']}),self.now.timestamp()-10))
        return pre

    def test_collection_pipeline_skips_held_breakfast_and_registers_only_lunch_dinner(self):
        from tests.mina.test_registration import RegistrationTests
        from src.mina.registration import register_day
        from src.mina.preparation import fingerprint
        from src.mina.domain import slot_time,date
        helper=RegistrationTests();directory=Path(self.tmp.name)
        draft,saved=helper.setup_inputs(directory);draft['target_date']=saved['target_date']=TARGET
        # このfixtureの実画像SHAへ限定許可を固定し直すため、新しい隔離台帳を使う。
        store=Store(directory/'pipeline.db');acquire(store,TARGET,OWNER,self.now)
        policy=ReplacementPolicy(store,build()['agent_policy'],self.clock)
        permit=CollectionPermit(store,self.clock)
        images={s:saved['images'][s]['sha256'] for s in ('lunch','dinner')}
        approval={**self.approval,'images':images}
        permit.issue(approval,OWNER,1,'a'*40,images)
        client=helper.client();raw=Path(saved['images']['breakfast']['path']).read_bytes()
        response=client.register(raw,TARGET+'-breakfast.jpg','breakfast',slot_time(date.fromisoformat(TARGET),'breakfast').isoformat(),
            draft['meals']['breakfast']['comment'],request_key='old-breakfast',expected_state_version='sv1_'+'0'*64)
        ledger=RequestLedger(store);old=str(uuid.uuid4());ledger.prepare(old,'meal_register',TARGET,'breakfast',{},raw);ledger.record(old,'complete',{'persistence':'saved'})
        with store.transaction() as db:
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",(TARGET,'meal_register:breakfast','a'*64,json.dumps(response),self.now.timestamp()))
        ai=Mock();ai.review_meal.return_value=ai.review_ogp.return_value={'status':'publishable','reason_codes':[]}
        capture=lambda url,host:{'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':b'preview'}
        from src.mina.rescue_collection import GuardedPort
        with permit.scope():
            result=register_day(store,draft,saved,directory/'ogp',GuardedPort(client,permit,policy),GuardedPort(ai,permit,policy),
                'www.log-eats.com',capture=capture,allow_correction=True,collection_slots=('lunch','dinner'))
        self.assertEqual(set(result),{'lunch','dinner'});self.assertEqual(client.register.call_count,3)
        client.json_write.assert_not_called();self.assertEqual(ai.review_meal.call_count,2)
        with store.transaction() as db:self.assertEqual(db.execute('SELECT state FROM publication_holds').fetchone()[0],'held')

    def test_origin_preserves_real_images_and_zero_replans_only_when_proven(self):
        from src.mina.rescue_origin import capture
        pre=self.origin_fixture();origin=capture(self.store,pre,OWNER,1,self.now)
        self.assertIsNone(origin['original_first_replan_started_at']);self.assertEqual(origin['baseline_consumed_ordinals'],[])
        (Path(self.tmp.name)/'breakfast.jpg').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'image_origin_changed'):capture(self.store,pre,OWNER,1,self.now)

    def test_orphan_generation_and_cost_cannot_become_zero_consumption(self):
        from src.mina.rescue_origin import capture
        pre=self.origin_fixture()
        with self.store.transaction() as db:
            db.execute("INSERT INTO replacement_waived_calls VALUES(?,?,?,NULL,'complete')",('orphan',TARGET,'image'))
        with self.assertRaisesRegex(ValueError,'orphan_generation'):capture(self.store,pre,OWNER,1,self.now)

    def test_orphan_replan_audit_cannot_reset_first_or_ordinal(self):
        from src.mina.rescue_origin import capture
        pre=self.origin_fixture()
        self.store.audit.emit('creation',TARGET,'breakfast',None,'meal_replan','observed',details={'attempt':1})
        with self.assertRaisesRegex(ValueError,'orphan_audit'):capture(self.store,pre,OWNER,1,self.now)

    def test_terminal_conflict_retains_saved_but_stops_new_actions(self):
        case=self.seed_job();metadata=sample('CommitInput');metadata['rescue']['case_uuid']=case['case_uuid']
        key=metadata['request_uuid'];self.journal.prepare(metadata,'meal_replace',canonical(metadata),'application/json');self.journal.start(key,lambda db:None)
        request={'request_uuid':key,'operation':'meal_replace','input_hash':digest({'operation':'meal_replace',**{k:v for k,v in metadata.items() if k!='request_uuid'}},'ih1_'),'persistence':'saved'}
        self.journal.observe(key,{'request':request})
        result=self.journal.observe(key,{'request':{**request,'persistence':'not_saved'}})
        self.assertTrue(result['conflict']);self.assertEqual(result['persistence'],'saved')
        flow=RescueFlow(self.store,Mock(),Mock(),self.policy,Mock(),Mock(),self.clock)
        with self.assertRaisesRegex(ValueError,'reconciliation_required'):flow.checkpoint()

    def test_origin_recheck_rejects_added_cost_or_request(self):
        from src.mina.rescue_origin import capture
        pre=self.origin_fixture();origin=capture(self.store,pre,OWNER,1,self.now)
        case=self.seed_job();self.journal.event('origin',case['case_uuid'],origin['document']);self.journal.event('precondition',case['case_uuid'],pre)
        flow=RescueFlow(self.store,Mock(),Mock(),self.policy,Mock(),Mock(),self.clock)
        flow.verify_origin()
        with self.store.transaction() as db:
            db.execute("INSERT INTO replacement_waived_calls VALUES(?,?,?,NULL,'complete')",('late-cost',TARGET,'collection_read'))
        with self.assertRaisesRegex(ValueError,'origin_changed'):flow.verify_origin()

    def test_refresh_review_does_not_recharge_or_forge_new_timestamp(self):
        flow=RescueFlow(self.store,Mock(),Mock(),self.policy,Mock(),Mock(),self.clock);operation=Mock(return_value={'status':'publishable','reason_codes':[]})
        evidence={'sha256':'a'*64}
        value,first=flow.collection_review('lunch','meal',evidence,operation)
        self.now+=timedelta(minutes=3)
        again,old=flow.collection_review('lunch','meal',evidence,operation)
        self.assertEqual(old,first);self.assertEqual(value,again);operation.assert_called_once()
        with self.assertRaisesRegex(ValueError,'lookup_only'):flow.collection_review('lunch','meal',{'sha256':'b'*64},operation)

    def test_pending_draft_latest_evidence_is_used_without_new_case_or_prepare_uuid(self):
        from src.mina.rescue_origin import capture
        pre=self.origin_fixture();old=capture(self.store,pre,OWNER,1,self.now)
        case=self.seed_job();issue=json.loads(self.journal.job()['issue_json'])
        self.journal.event('origin',case['case_uuid'],old['document']);self.journal.event('precondition',case['case_uuid'],pre)
        self.journal.event('operator_evidence',case['case_uuid'],{'source_ledger':{'old':True},'cost_history':{}})
        with self.store.transaction() as db:
            db.execute("INSERT INTO replacement_waived_calls VALUES(?,?,?,NULL,'complete')",('refreshed-review',TARGET,'collection_review'))
            db.execute("UPDATE rescue_jobs SET case_observation=NULL,stage='awaiting_approval'")
        updated=capture(self.store,pre,OWNER,1,self.now)
        evidence={'source_ledger':updated['document'],'cost_history':updated['document']['ledgers']}
        issue['basis'].update(source_ledger_digest=digest(evidence['source_ledger']),cost_history_digest=digest(evidence['cost_history']))
        issue['basis'].pop('operator_approval_digest',None)
        with self.store.transaction() as db:db.execute('UPDATE rescue_jobs SET issue_json=?',(canonical(issue).decode(),))
        self.journal.event('origin',case['case_uuid'],updated['document']);self.journal.event('precondition',case['case_uuid'],pre)
        self.journal.event('operator_evidence',case['case_uuid'],evidence)
        artifact={'action':'issue','approved':True,'case_uuid':case['case_uuid'],'planned_prepare_uuid':case['planned_prepare_uuid'],
            'basis_sha256':digest(issue['basis']),'not_after':issue['basis']['not_after'],'target_date':TARGET,'target_slot':'breakfast',
            'scope':'same_day_breakfast_single_rescue','code_commit':'a'*40,'schema_sha256':SCHEMA_SHA,'hold_owner':OWNER,'hold_epoch':1,
            'original_image_sha256':issue['basis']['original_image_sha256'],'operator_approval_reference':'隔離合成承認'}
        flow=RescueFlow(self.store,Mock(),Mock(),self.policy,Mock(),Mock(),self.clock)
        flow.authorize_issue(artifact);bundle=flow.operator_bundle()
        self.assertEqual(bundle['evidence'],evidence)
        self.assertEqual(bundle['input']['case_uuid'],case['case_uuid'])
        self.assertEqual(bundle['input']['planned_prepare_uuid'],case['planned_prepare_uuid'])
        self.assertEqual(bundle['input']['basis']['operator_approval_digest'],digest(artifact))

    def test_wrong_scope_artifact_is_not_accepted_by_runtime(self):
        from src.mina.rescue_runtime import acceptance
        settings={'contract_schema_sha256':SCHEMA_SHA,'agent_commit':'a'*40,'provider_commit':'b'*40,'target_date':TARGET}
        for role in ('provider_acceptance','agent_acceptance','implementation_review','deployment_approval','execution_approval'):
            document={'role':role,'agent_commit':'a'*40,'provider_commit':'b'*40,'target_date':TARGET,
                'schema_sha256':SCHEMA_SHA,'scope':'same_day_rescue','verdict':'approved' if role.endswith('_approval') else 'Go'}
            path=Path(self.tmp.name)/(role+'.json');path.write_bytes(canonical(document))
            settings[role]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        acceptance(settings,'a'*40)
        path=Path(settings['execution_approval']['path']);document=json.loads(path.read_bytes());document['target_date']='2026-10-25';path.write_bytes(canonical(document))
        settings['execution_approval']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'scope_changed'):acceptance(settings,'a'*40)

    def test_operator_digest_is_the_exact_approval_artifact(self):
        case=self.seed_job();flow=RescueFlow(self.store,Mock(),Mock(),self.policy,Mock(),Mock(),self.clock)
        flow.verify_origin=Mock(return_value={})
        with self.store.transaction() as db:db.execute("UPDATE rescue_jobs SET case_observation=NULL,stage='awaiting_approval'")
        issue=json.loads(self.journal.job()['issue_json']);basis={k:v for k,v in issue['basis'].items() if k!='operator_approval_digest'}
        artifact={'action':'issue','approved':True,'case_uuid':case['case_uuid'],'planned_prepare_uuid':case['planned_prepare_uuid'],
            'basis_sha256':digest(basis),'not_after':basis['not_after'],'target_date':TARGET,'target_slot':'breakfast',
            'scope':'same_day_breakfast_single_rescue','code_commit':'a'*40,'schema_sha256':SCHEMA_SHA,
            'hold_owner':OWNER,'hold_epoch':1,'original_image_sha256':basis['original_image_sha256'],'operator_approval_reference':'隔離合成承認'}
        result=flow.authorize_issue(artifact)
        self.assertEqual(result['basis']['operator_approval_digest'],digest(artifact))
        bad={**artifact,'code_commit':'c'*40}
        with self.assertRaisesRegex(ValueError,'approval_required'):flow.authorize_issue(bad)

    def test_case_rejects_reset_clock_and_extra_candidate(self):
        case=sample('CaseResponse')['case'];self.contract.case(case,active=True)
        for update in ({'hard_deadline_at':stamp(self.now+timedelta(minutes=16))}, {'used_ordinals':[1,2]}, {'attempt_ordinal':2}):
            bad=copy.deepcopy(case);bad.update(update)
            with self.assertRaises((ValueError,ValidationError)):self.contract.case(bad)

    def seed_job(self):
        case=sample('CaseResponse')['case'];issue=sample('GrantIssueInput')
        with self.store.transaction() as db:
            db.execute("INSERT INTO rescue_jobs VALUES(?,?,?,?,?,?,?,'ready',?)",(TARGET,OWNER,1,canonical(issue).decode(),case['case_uuid'],case['planned_prepare_uuid'],canonical(case).decode(),case['hard_deadline_at']))
        return case

    def test_reserved_case_and_prepare_uuid_cannot_enter_normal_m1_or_pdc(self):
        key=str(uuid.uuid4())
        with self.store.transaction() as db:reserve_uuid(db,key,'case',key)
        with self.assertRaisesRegex(ValueError,'collision'):self.ledger.prepare(key,'meal_register',TARGET,'lunch',{},b'lunch')
        from src.mina.replacement_journal import ReplacementJournal
        normal=ReplacementJournal(self.store)
        with self.assertRaisesRegex(ValueError,'collision'):normal.prepare(key,'candidate_prepare',TARGET,'breakfast',1,OWNER,b'wire')

    def test_sticky_saved_requery_does_not_invalidate_new_approval_or_rehold(self):
        case=self.seed_job();metadata=sample('CommitInput');metadata['rescue']['case_uuid']=case['case_uuid']
        key=metadata['request_uuid'];self.journal.prepare(metadata,'meal_replace',canonical(metadata),'application/json')
        self.journal.start(key,lambda db:None)
        value={'request':{'request_uuid':key,'operation':'meal_replace','input_hash':digest({'operation':'meal_replace',**{k:v for k,v in metadata.items() if k!='request_uuid'}},'ih1_'),'persistence':'saved'}}
        self.journal.observe(key,value)
        with self.store.transaction() as db:
            db.execute("UPDATE publication_holds SET state='released'")
            before=db.execute('SELECT count(*) FROM replacement_archives').fetchone()[0]
        self.journal.observe(key,value)
        unknown=copy.deepcopy(value);unknown['request']['persistence']='unknown'
        self.assertEqual(self.journal.observe(key,unknown)['persistence'],'saved')
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT state FROM publication_holds').fetchone()[0],'released')
            self.assertEqual(db.execute('SELECT count(*) FROM replacement_archives').fetchone()[0],before)

    def test_sent_wire_is_lookup_only_across_restart(self):
        case=self.seed_job();metadata=sample('PrepareInput');key=case['planned_prepare_uuid'];metadata['request_uuid']=key
        self.journal.prepare(metadata,'candidate_prepare',canonical(metadata),'application/json')
        self.journal.start(key,lambda db:None)
        restarted=RescueJournal(self.store,self.clock)
        with self.assertRaisesRegex(ValueError,'lookup_only'):restarted.start(key,lambda db:None)

    def test_route_uses_rescue_namespace_without_origin_cookie_or_post_on_lookup(self):
        calls=[]
        class Response:
            status=200
            def __enter__(inner):return inner
            def __exit__(inner,*args):pass
            def read(inner,*args):return json.dumps(sample('CaseResponse')).encode()
        def opener(request,timeout):calls.append(request);return Response()
        client=RescueClient('https://www.log-eats.com','synthetic-token-'+40*'x',decoder=self.contract.decode,clock=self.clock,opener=opener)
        client.case(sample('CaseResponse')['case']['case_uuid'])
        self.assertEqual(calls[0].method,'GET');self.assertNotIn('Origin',calls[0].headers);self.assertNotIn('Cookie',calls[0].headers)
        self.assertIn('/meal-rescue-cases/',calls[0].full_url)
        self.assertIn('/meal-rescues/',client.route('/api/internal/mina/requests/'+str(uuid.uuid4())))

    def test_default_disabled_prevents_calls(self):
        flow=RescueFlow(self.store,Mock(),Mock(),self.policy,Mock(),Mock(),self.clock)
        with patch.dict('os.environ',{'MINA_SAME_DAY_RESCUE_ENABLED':'false'}),self.assertRaisesRegex(ValueError,'disabled'):
            flow.prepare_candidate()
        flow.client.assert_not_called()

    def test_59_seconds_rejected_60_allowed_without_reset(self):
        case=self.seed_job();flow=RescueFlow(self.store,Mock(),Mock(),self.policy,Mock(),Mock(),self.clock)
        self.now=__import__('src.mina.replacement_client',fromlist=['timestamp']).timestamp(case['hard_deadline_at'])-timedelta(seconds=60)
        flow.checkpoint(remaining=60)
        self.now+=timedelta(milliseconds=1)
        with self.assertRaisesRegex(ValueError,'deadline'):flow.checkpoint(remaining=60)

if __name__=='__main__':unittest.main()
