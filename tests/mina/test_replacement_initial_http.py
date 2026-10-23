"""固定本体の実ハンドラーへ実HTTP接続し、初回Pの移行証跡を検証する。"""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from tests.mina import test_replacement_integration as fixture
from tests.mina.test_replacement_control import waived_policy
from src.mina.replacement_policy import ReplacementPolicy
from src.mina.replacement_contract import digest, stamp
from src.mina.replacement_client import ReplacementClient
from src.mina.replacement_flow import ReplacementFlow
from src.mina.request_ledger import RequestLedger
from src.mina.preparation import fingerprint

ROOT=Path(__file__).resolve().parents[2]
REPORT=[]


@unittest.skipUnless(os.getenv('MINA_ISOLATED_PROVIDER_ROOT'), '固定本体を使う専用隔離HTTP受入で実行する')
class InitialHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        provider=Path(os.environ['MINA_ISOLATED_PROVIDER_ROOT']).resolve()
        policy=waived_policy()
        synthetic={'version':policy['policy_version'],'budget_policy_digest':digest(policy),
            'approval_reference_digest':policy['approval_reference_digest'],'analysis_provider':'gemini',
            'model':'gemini-3.1-flash-lite','max_output_tokens':2048,'agent_integration_verified':True,
            'cost_control_mode':'waived','currency':None,'budget_total_minor':None,'prepare_max_cost_minor':None,
            'commit_max_cost_minor':None,'cost_bounds_verified':False}
        config=Path(cls.temp.name)/'synthetic-policy.json';config.write_text(json.dumps(synthetic),encoding='utf-8')
        cls.process=subprocess.Popen(['node',str(provider/'node_modules/tsx/dist/cli.mjs'),'--tsconfig',str(provider/'tsconfig.json'),
            str(ROOT/'scripts/mina_initial_prepare_http.ts'),str(provider),str(config),str(ROOT/'scripts/fixtures/logeats_initial_prepare_0e80.json')],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        cls.addClassCleanup(cls.stop)
        line=cls.process.stdout.readline()
        if not line:raise RuntimeError(cls.process.stderr.read())
        cls.version=json.loads(line);cls.base='http://127.0.0.1:'+str(cls.version['port'])

    @classmethod
    def stop(cls):
        cls.process.terminate()
        try:cls.process.communicate(timeout=10)
        except subprocess.TimeoutExpired:cls.process.kill();cls.process.communicate()
        output=os.getenv('MINA_INITIAL_ACCEPTANCE_REPORT')
        if output:Path(output).write_text(json.dumps({'fixture':cls.version,'cases':REPORT},ensure_ascii=False,indent=2),encoding='utf-8')

    def fixture(self,**command):
        body=json.dumps(command).encode() if command else None
        with urlopen(Request(self.base+'/__fixture',data=body,headers={'Content-Type':'application/json'}),timeout=10) as r:return json.load(r)

    def setUp(self):
        state=self.fixture(reset=True)
        self.c=fixture.IntegrationTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        c=self.c;c.now=datetime.fromtimestamp(state['now']/1000,UTC);self.target=state['target']
        c.policy=ReplacementPolicy(c.store,waived_policy(),c.clock)
        def transport(request,timeout):
            url=urlsplit(request.full_url)
            if url.hostname!='www.log-eats.com':raise AssertionError('隔離接続先以外を拒否')
            return urlopen(Request(self.base+url.path+('?' + url.query if url.query else ''),data=request.data,
                headers=dict(request.header_items()),method=request.method),timeout=timeout)
        c.client=ReplacementClient('https://www.log-eats.com',state['token'],decoder=c.contract.decode,
            opener=transport,clock=c.clock,write_authorized=True)
        self.transport=transport
        pre=c.client.preconditions(self.target);c.server.pre=pre
        c.ports.previous_image_hashes.return_value={m['expected']['target_slot']:m['image_sha256'] for m in pre['meals']}
        c.flow=ReplacementFlow(c.store,c.client,c.policy,c.inventory,c.ports,c.clock)
        ledger=RequestLedger(c.store)
        for result in state['results']:
            slot=result['meal']['meal_type'];key=str(uuid.uuid4());raw=base64.b64decode(state['original'])
            payload={'contract_version':'mina-write-v1','comment':result['normalized_comment'],
                'meal_type':slot,'logged_at':result['meal']['logged_at']}
            ledger.prepare(key,'meal_register',self.target,slot,payload,raw)
            ledger.record(key,'complete',{'persistence':'saved','stage':'completed'})
            with c.store.transaction() as db:
                value={'result':result}
                db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",
                    (self.target,'meal_register:'+slot,fingerprint({'sha256':result['image_sha256'],'logged_at':payload['logged_at'],'comment':payload['comment']}),json.dumps(value),c.now.timestamp()-60))
                db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",
                    (self.target,'meal_request_key:'+slot,fingerprint({'sha256':result['image_sha256'],'logged_at':payload['logged_at'],'comment':payload['comment']}),json.dumps({'key':key}),c.now.timestamp()-61))
        with c.store.transaction() as db:
            held={'classification':'quality_hold','step':'meal_review:dinner','reason_codes':['image_analysis_mismatch']}
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'held',?,?)",
                (self.target,'meal_review:dinner',fingerprint(held),json.dumps(held),c.now.timestamp()-10))
        self.pre=pre

    def tearDown(self):
        state=self.fixture()
        REPORT.append({'case':self.id().rsplit('.',1)[-1],'http_and_external_counts':state['counts'],
            'contexts':state['contexts'],'history':state['history'],'requests':state['requests']})

    def begin(self):return self.c.flow.begin(self.target,'dinner',quality_hold=self.c.hold())
    def attempts(self):
        with self.c.store.transaction() as db:return db.execute('SELECT count(*) FROM replacement_attempts').fetchone()[0]
    def no_provider_work(self):
        state=self.fixture();self.assertEqual(state['counts']['analysis'],0);self.assertEqual(state['counts']['image'],0)
        self.assertEqual(state['contexts'],[])

    def test_contextless_get_initial_p_review_commit_and_no_repost(self):
        c=self.c
        self.assertFalse(self.pre['policy']['existing_preparation_deadline_verified'])
        self.assertEqual(self.pre['policy']['blocking_reasons'],['deadline_unverified'])
        self.begin();self.assertEqual(self.attempts(),1)
        state=self.fixture();self.assertEqual(state['contexts'],['7:'+self.target+':dinner'])
        self.assertFalse(c.client.preconditions(self.target)['policy']['existing_preparation_deadline_verified'])
        before=copy.deepcopy(state['counts']);c.flow.reconcile(self.target)
        self.assertEqual(self.fixture()['counts']['analysis'],before['analysis'])
        c.flow.decide_candidate(self.target);self.assertEqual(c.flow.episode(self.target)['stage'],'saved')
        self.assertEqual(self.fixture()['history'],1);self.assertEqual(self.attempts(),1)
        c.flow.reconcile(self.target);self.assertEqual(self.fixture()['history'],1)
        with self.assertRaises(ValueError):self.begin()
        owner=c.flow.episode(self.target)['owner']
        with c.store.transaction() as db:
            row=db.execute('SELECT * FROM replacement_origin_receipts').fetchone()
            origin=json.loads(row['origin']);self.assertEqual(digest(origin),row['origin_digest'])
            self.assertEqual(origin['owner'],owner)
            self.assertEqual(len(origin['requests']),3)

    def test_missing_original_stops_before_generation(self):
        with self.c.store.transaction() as db:db.execute('DELETE FROM mina_requests WHERE slot=?',('dinner',))
        with self.assertRaisesRegex(ValueError,'migration_unverified'):self.begin()
        self.assertEqual(self.c.ports.plan.call_count,0);self.assertEqual(self.attempts(),0);self.no_provider_work()

    def test_unknown_original_requires_original_lookup(self):
        with self.c.store.transaction() as db:db.execute("UPDATE mina_requests SET phase='held' WHERE slot='dinner'")
        with self.assertRaisesRegex(ValueError,'unresolved'):self.begin()
        self.no_provider_work()

    def test_future_first_timestamp_is_not_reset(self):
        self.legacy_replan(started=self.c.now.timestamp()+1)
        with self.assertRaisesRegex(ValueError,'migration_unverified'):self.begin()
        self.no_provider_work()

    def test_old_first_timestamp_is_not_reset(self):
        self.legacy_replan(started=self.c.now.timestamp()-901)
        with self.assertRaisesRegex(ValueError,'deadline_exceeded'):self.begin()
        self.no_provider_work()

    def legacy_replan(self,ordinal=1,started=None,state='complete'):
        c=self.c
        meals={slot:{'menu':'合成献立'+slot,'comment':'合成'+slot,'estimated_calories':630,
            'foods':[{'name':'合成主食','amount':'100g'},{'name':'合成主菜','amount':'100g'}],'image_description':'合成の献立'} for slot in ('breakfast','lunch','dinner')}
        meals['dinner']['comment']='合成'
        value={'round':str(uuid.uuid4()),'draft':{'target_date':self.target,'meals':meals,'daily_comment':'合成の日次'}}
        with self.c.store.transaction() as db:
            request=dict(db.execute("SELECT * FROM mina_requests WHERE slot='dinner'").fetchone())
            saved=dict(db.execute("SELECT * FROM preparation_steps WHERE step='meal_register:dinner'").fetchone())
            path=Path(c.tmp.name)/('legacy-'+str(ordinal)+'.png');path.write_bytes(request['image'])
            image={'path':str(path),'sha256':request['image_sha256'],'size':len(request['image'])}
            for step,result in (('image:dinner:',image),('meal_request_key:dinner:',{'key':request['request_key']}),
                                ('meal_register:dinner:',json.loads(saved['result']))):
                db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",(self.target,step+value['round'],saved['digest'],json.dumps(result),c.now.timestamp()-40))
            db.execute('INSERT INTO preparation_steps VALUES(?,?,?,?,?,?)',(self.target,f'meal_replan:dinner:{ordinal}',
                fingerprint(value),state,json.dumps(value),started or self.c.now.timestamp()-30))

    def test_ordinal_gap_is_not_zeroed(self):
        self.legacy_replan(ordinal=2)
        with self.assertRaisesRegex(ValueError,'migration_unverified'):self.begin()
        self.no_provider_work()

    def test_started_generation_is_not_retried(self):
        self.legacy_replan(state='started')
        with self.assertRaisesRegex(ValueError,'migration_unverified'):self.begin()
        self.no_provider_work()

    def test_max_two_includes_registration_replans(self):
        self.legacy_replan();self.legacy_replan(ordinal=2)
        with self.assertRaisesRegex(ValueError,'replan_limit'):self.begin()
        self.no_provider_work()

    def test_generation_leaving_59_seconds_does_not_send_p(self):
        c=self.c;image=c.ports.image.side_effect
        def slow(plan):
            value=image(plan);c.now+=timedelta(seconds=841);self.fixture(advance_ms=841000);return value
        c.ports.image.side_effect=slow
        with self.assertRaisesRegex(ValueError,'deadline_exceeded'):self.begin()
        self.assertEqual(self.attempts(),1);self.no_provider_work()

    def test_partial_day_is_not_bypassed(self):
        self.fixture(partial=True)
        with self.assertRaises(ValueError):self.begin()
        self.assertEqual(self.attempts(),0);self.no_provider_work()

    def test_version_change_before_commit_rejected(self):
        self.begin();self.fixture(version_change=True)
        with self.assertRaisesRegex(ValueError,'preconditions_changed'):self.c.flow.decide_candidate(self.target)
        self.assertEqual(self.fixture()['history'],0)

    def test_lost_p_and_restart_only_gets_original_uuid(self):
        c=self.c;self.fixture(lose='meal-replacement-candidates')
        with self.assertRaises(ValueError):self.begin()
        flow=ReplacementFlow(c.store,c.client,c.policy,c.inventory,c.ports,c.clock)
        flow.reconcile(self.target);self.assertEqual(flow.episode(self.target)['stage'],'ready')
        self.assertEqual(self.attempts(),1);self.assertEqual(self.fixture()['counts']['analysis'],1)
        self.assertEqual(self.fixture()['counts']['http']['POST /api/internal/mina/meal-replacement-candidates'],1)

    def test_lost_c_saved_never_downgrades(self):
        c=self.c;self.begin();self.fixture(lose='meal-replacements')
        with self.assertRaises(ValueError):c.flow.decide_candidate(self.target)
        c.flow.reconcile(self.target);c.flow.reconcile(self.target)
        self.assertEqual(c.flow.episode(self.target)['stage'],'saved');self.assertEqual(self.fixture()['history'],1)
        self.assertEqual(self.fixture()['counts']['http']['POST /api/internal/mina/meal-replacements'],1)

    def test_service_stop_not_waived(self):
        self.fixture(enabled=False)
        with self.assertRaisesRegex(ValueError,'policy_unverified'):self.begin()
        self.assertEqual(self.attempts(),0);self.no_provider_work()

    def test_provider_stop_not_waived(self):
        self.fixture(provider_missing=True)
        with self.assertRaisesRegex(ValueError,'policy_unverified'):self.begin()
        self.assertEqual(self.attempts(),0);self.no_provider_work()

    def test_guard_missing_not_waived(self):
        self.fixture(guard_missing=True)
        with self.assertRaisesRegex(ValueError,'policy_unverified'):self.begin()
        self.assertEqual(self.attempts(),0);self.no_provider_work()

    def test_open_cooldown_blocks_before_generation(self):
        self.fixture(open=True)
        with self.assertRaisesRegex(ValueError,'cooldown'):self.begin()
        self.assertEqual(self.attempts(),0);self.no_provider_work()

    def test_expired_open_allows_one_probe(self):
        from src.mina.replacement_control import observe_circuit
        self.fixture(open=True)
        with self.assertRaises(ValueError):observe_circuit(self.c.store,self.c.client.preconditions(self.target),self.c.clock())
        self.fixture(advance_ms=600000);self.c.now+=timedelta(minutes=10)
        self.begin();self.assertEqual(self.attempts(),1)
        with self.c.store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM replacement_probe_claims').fetchone()[0],1)
        self.assertEqual(self.fixture()['counts']['analysis'],1)
        with self.assertRaises(ValueError):self.begin()

    def test_explicit_resume_does_not_repeat_completed_analysis(self):
        from src.mina.replacement_control import resume_paused
        c=self.c;self.fixture(hooks={'pause_after_analysis':True})
        self.begin()
        self.assertEqual(c.flow.episode(self.target)['stage'],'paused')
        self.assertEqual(self.fixture()['counts']['analysis'],1);self.assertEqual(self.fixture()['counts']['image'],0)
        c.flow.reconcile(self.target)
        self.assertEqual(self.fixture()['counts']['image'],0)
        self.fixture(advance_ms=600000);c.now+=timedelta(minutes=10)
        resume_paused(c.flow,self.target)
        self.assertEqual(c.flow.episode(self.target)['stage'],'ready')
        self.assertEqual(self.fixture()['counts']['analysis'],1);self.assertEqual(self.fixture()['counts']['image'],1)
        self.assertEqual(self.attempts(),1)

    def test_saved_ogp_pause_remains_saved_through_resume(self):
        from src.mina.replacement_control import resume_paused
        c=self.c;self.begin();self.fixture(hooks={'pause_saved':True})
        c.flow.decide_candidate(self.target)
        self.assertEqual(c.flow.episode(self.target)['stage'],'paused')
        c.flow.reconcile(self.target)
        with c.store.transaction() as db:
            record=db.execute("SELECT observation FROM meal_replacement_requests WHERE operation='meal_replace'").fetchone()
            self.assertEqual(json.loads(record[0])['persistence'],'saved')
        self.fixture(advance_ms=600000);c.now+=timedelta(minutes=10)
        resume_paused(c.flow,self.target)
        self.assertEqual(c.flow.episode(self.target)['stage'],'saved');self.assertEqual(self.fixture()['history'],1)

    def test_unknown_analysis_lookup_only(self):
        from src.mina.replacement_control import resume_paused
        c=self.c;self.fixture(hooks={'fail_analysis':True})
        self.begin()
        with self.assertRaises(ValueError):c.flow.next_candidate(self.target)
        with self.assertRaises(ValueError):resume_paused(c.flow,self.target)
        self.assertEqual(self.fixture()['counts']['analysis'],1);self.assertEqual(self.attempts(),1)

    def test_rejected_candidate_next_consumes_second_once(self):
        c=self.c;c.ports.review.side_effect=[{'verdict':'fail','observations':['image_analysis_mismatch']},{'verdict':'pass','observations':[]}]
        self.begin();c.flow.decide_candidate(self.target)
        self.assertEqual(c.flow.episode(self.target)['stage'],'discarded')
        c.flow.next_candidate(self.target);self.assertEqual(self.attempts(),2)
        c.flow.decide_candidate(self.target);self.assertEqual(c.flow.episode(self.target)['stage'],'saved')
        self.assertEqual(self.fixture()['counts']['analysis'],2)

    def test_tampered_origin_and_receipt_not_accepted(self):
        self.begin()
        with self.c.store.transaction() as db:db.execute("UPDATE replacement_origin_receipts SET origin='{}'")
        with self.assertRaisesRegex(ValueError,'migration_unverified'):self.c.flow.decide_candidate(self.target)
        self.assertEqual(self.fixture()['history'],0)

    def test_existing_registration_ordinal_one_is_inherited(self):
        self.legacy_replan()
        self.begin();self.assertEqual(self.attempts(),1)
        with self.c.store.transaction() as db:
            receipt=json.loads(db.execute('SELECT receipt FROM replacement_attempts').fetchone()[0])
        self.assertEqual(receipt['attempt_ordinal'],2);self.assertEqual(receipt['prior_consumed_ordinals'],[1])

    def test_wire_replay_has_no_external_increment(self):
        from urllib.error import HTTPError
        self.begin();c=self.c;e=c.flow.episode(self.target);key=e['prepare_uuid']
        with c.store.transaction() as db:
            row=db.execute('SELECT body FROM meal_replacement_requests WHERE request_key=?',(key,)).fetchone()
            wire=db.execute('SELECT content_type FROM replacement_wire WHERE request_key=?',(key,)).fetchone()
        before=self.fixture()['counts']
        request=Request('https://www.log-eats.com/api/internal/mina/meal-replacement-candidates',data=bytes(row[0]),
            headers={'Authorization':'Bearer '+c.client.token,'Content-Type':wire[0],'Idempotency-Key':key},method='POST')
        with self.assertRaises(HTTPError) as rejected:self.transport(request,60)
        self.assertEqual(rejected.exception.code,409)
        after=self.fixture()['counts']
        for kind in ('analysis','image','render','put'):self.assertEqual(after[kind],before[kind])

    def test_probe_success_then_discard_allows_second_in_closed_state(self):
        from src.mina.replacement_control import observe_circuit
        c=self.c;self.fixture(open=True)
        with self.assertRaises(ValueError):observe_circuit(c.store,c.client.preconditions(self.target),c.clock())
        self.fixture(advance_ms=600000);c.now+=timedelta(minutes=10)
        c.ports.review.return_value={'verdict':'fail','observations':['image_analysis_mismatch']}
        self.begin();c.flow.decide_candidate(self.target);c.flow.next_candidate(self.target)
        self.assertEqual(self.attempts(),2);self.assertEqual(self.fixture()['counts']['analysis'],2)

    def test_unobserved_half_open_does_not_unlock_by_time(self):
        self.fixture(open=True);self.fixture(advance_ms=600000);self.c.now+=timedelta(minutes=10)
        with self.assertRaisesRegex(ValueError,'cooldown'):self.begin()
        self.no_provider_work()

    def test_resume_service_stop_has_zero_post_increment(self):
        from src.mina.replacement_control import resume_paused
        c=self.c;self.fixture(hooks={'pause_after_analysis':True});self.begin()
        self.fixture(advance_ms=600000,enabled=False);c.now+=timedelta(minutes=10)
        with self.assertRaisesRegex(ValueError,'policy_unverified'):resume_paused(c.flow,self.target)
        self.assertFalse(any(key.startswith('POST ') and key.endswith('resumes') for key in self.fixture()['counts']['http']))

    def test_resume_provider_stop_has_zero_post_increment(self):
        from src.mina.replacement_control import resume_paused
        c=self.c;self.fixture(hooks={'pause_after_analysis':True});self.begin()
        self.fixture(advance_ms=600000,provider_missing=True);c.now+=timedelta(minutes=10)
        with self.assertRaisesRegex(ValueError,'policy_unverified'):resume_paused(c.flow,self.target)
        self.assertFalse(any(key.startswith('POST ') and key.endswith('resumes') for key in self.fixture()['counts']['http']))

    def test_resume_budget_unconfigured_has_zero_post_increment(self):
        from src.mina.replacement_control import resume_paused
        c=self.c;self.fixture(hooks={'pause_after_analysis':True});self.begin()
        self.fixture(advance_ms=600000,policy_missing=True);c.now+=timedelta(minutes=10)
        with self.assertRaisesRegex(ValueError,'policy_unverified'):resume_paused(c.flow,self.target)
        self.assertFalse(any(key.startswith('POST ') and key.endswith('resumes') for key in self.fixture()['counts']['http']))

    def test_policy_changed_during_generation_stops_before_p(self):
        c=self.c;image=c.ports.image.side_effect
        def disable(plan):
            raw=image(plan);self.fixture(enabled=False);return raw
        c.ports.image.side_effect=disable
        with self.assertRaisesRegex(ValueError,'policy_unverified'):self.begin()
        self.assertEqual(self.attempts(),1);self.no_provider_work()

    def test_exactly_60_seconds_remaining_is_accepted(self):
        c=self.c;image=c.ports.image.side_effect
        def slow(plan):
            raw=image(plan);self.fixture(advance_ms=840000);c.now+=timedelta(seconds=840);return raw
        c.ports.image.side_effect=slow;self.begin()
        self.assertEqual(c.flow.episode(self.target)['stage'],'ready');self.assertEqual(self.attempts(),1)

    def test_missing_legacy_full_plan_is_rejected(self):
        self.legacy_replan()
        with self.c.store.transaction() as db:
            r=db.execute("SELECT result FROM preparation_steps WHERE step='meal_replan:dinner:1'").fetchone()
            value=json.loads(r[0]);value['draft']={'target_date':self.target}
            db.execute("UPDATE preparation_steps SET result=? WHERE step='meal_replan:dinner:1'",(json.dumps(value),))
        with self.assertRaisesRegex(ValueError,'migration_unverified'):self.begin()
        self.no_provider_work()

    def test_registration_input_digest_mismatch_is_rejected(self):
        with self.c.store.transaction() as db:db.execute("UPDATE preparation_steps SET digest=? WHERE step='meal_register:dinner'",('a'*64,))
        with self.assertRaisesRegex(ValueError,'migration_unverified'):self.begin()
        self.no_provider_work()

    def test_original_removed_after_reservation_stops_p(self):
        c=self.c;image=c.ports.image.side_effect
        def remove(plan):
            raw=image(plan)
            with c.store.transaction() as db:db.execute("DELETE FROM mina_requests WHERE slot='dinner'")
            return raw
        c.ports.image.side_effect=remove
        with self.assertRaisesRegex(ValueError,'migration_unverified'):self.begin()
        self.assertEqual(self.attempts(),1);self.no_provider_work()

    def test_sqlite_publication_claim_blocks_initial_p(self):
        from src.mina.publication_hold import initialize
        with self.c.store.transaction() as db:
            initialize(db);db.execute("INSERT INTO publication_mutation_leases VALUES(?,?,?,'open')",('synthetic-send',self.target,'buffer'))
        with self.assertRaisesRegex(ValueError,'external_dispatch_unverified'):self.begin()
        self.assertEqual(self.attempts(),0);self.no_provider_work()

    def test_missing_local_context_history_does_not_reset_provider_ordinal(self):
        self.begin()
        c=self.c
        with c.store.transaction() as db:
            db.execute('DELETE FROM replacement_attempts');db.execute('DELETE FROM replacement_origin_receipts')
            db.execute("DELETE FROM preparation_steps WHERE step LIKE 'meal_replan:%'")
            db.execute("UPDATE replacement_episodes SET stage='discarded'")
        with self.assertRaisesRegex(ValueError,'migration_unverified'):c.flow.next_candidate(self.target)
        self.assertEqual(self.fixture()['counts']['analysis'],1)

    def rejected_provider(self,variant):
        import hashlib
        from urllib.error import HTTPError
        c=self.c
        if variant=='remaining_59':
            now=datetime.fromisoformat('2030-01-01T10:59:01+00:00');self.fixture(advance_ms=int((now-c.now).total_seconds()*1000));c.now=now
        pre=c.client.preconditions(self.target);expected=next(m for m in pre['meals'] if m['expected']['target_slot']=='dinner')
        metadata=copy.deepcopy(fixture.SAMPLES['prepare_input']);key=str(uuid.uuid4())
        raw=c.image({'menu':'別メニュー1'})
        metadata.update(request_uuid=key,expected=expected['expected'],precondition_digest=expected['precondition_digest'],input_image_sha256=hashlib.sha256(raw).hexdigest())
        metadata['publication_hold']['provider_checked_at']=stamp(c.now)
        migration=metadata['migration'];migration.update(policy_version=c.policy.policy['policy_version'],budget_policy_digest=c.policy.policy_digest,
            first_replan_started_at=stamp(c.now),existing_preparation_deadline_at='2030-01-01T11:00:00.000Z')
        if variant=='future':migration['first_replan_started_at']=stamp(c.now+timedelta(seconds=1))
        if variant=='old':migration['first_replan_started_at']=stamp(c.now-timedelta(seconds=901))
        if variant=='gap':migration.update(attempt_ordinal=2,prior_consumed_ordinals=[],agent_step_key='meal_replan:dinner:2')
        boundary='isolated-negative-'+key
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n'.encode()+json.dumps(metadata).encode()+
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="fixture.png"\r\nContent-Type: image/png\r\n\r\n'.encode()+raw+f'\r\n--{boundary}--\r\n'.encode())
        request=Request('https://www.log-eats.com/api/internal/mina/meal-replacement-candidates',data=body,
            headers={'Authorization':'Bearer '+c.client.token,'Idempotency-Key':key,'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
        with self.assertRaises(HTTPError) as rejected:self.transport(request,60)
        self.assertEqual(rejected.exception.code,503)
        response=json.load(rejected.exception)
        self.assertEqual(response['reason'],{'future':'deadline_unverified','old':'deadline_unverified','gap':'migration_unverified','remaining_59':'deadline_exceeded'}[variant])
        self.no_provider_work()

    def test_real_handler_rejects_future_first(self):self.rejected_provider('future')
    def test_real_handler_rejects_old_first(self):self.rejected_provider('old')
    def test_real_handler_rejects_ordinal_gap(self):self.rejected_provider('gap')
    def test_real_handler_rejects_59_seconds(self):self.rejected_provider('remaining_59')


if __name__=='__main__':unittest.main()
