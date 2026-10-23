"""新Schema・waived・永続cooldown・明示resumeを実SQLiteと合成HTTPで検証する。"""
import copy
import json
import tempfile
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from src.mina.replacement_contract import Contract, digest, stamp
from src.mina.replacement_control import remember, require_available, observe_circuit, resume_paused, ReplacementPaused
from src.mina.replacement_policy import ReplacementPolicy
from src.mina.store import Store
from tests.mina import test_replacement_integration as fixture


def waived_policy():
    return {'approval_digest':'a'*64,'approval_reference_digest':'a'*64,'cost_control_mode':'waived',
        'currency':None,'limit_units':None,'opening_exposure_units':None,'server_allocation_units':None,
        'call_bounds':{k:None for k in ('menu','image','review','drive','rebuild')},
        'cost_bounds_verified':False,'existing_usage_verified':False,'server_allocation_verified':False,
        'policy_version':'mina-gemini-waived-v1','analysis_provider':'gemini','model':'gemini-3.1-flash-lite','max_output_tokens':2048}


def cooldown(now,recovery=False):
    return {'scope':'mina_saved_meal_replacement','retry_at':stamp(now+timedelta(minutes=10)),
        'trigger':'request_rate','mutation_started':False,'recovery_required':recovery}


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'db');self.now=fixture.NOW
        self.contract=Contract(lambda:self.now)

    def test_waived_keeps_amounts_null_and_unknown_without_asserting_free(self):
        policy=ReplacementPolicy(self.store,waived_policy(),lambda:self.now)
        policy.reserve(fixture.TARGET,'call','image');policy.called('call',False)
        with self.store.transaction() as db:
            self.assertEqual(tuple(db.execute('SELECT max_units,state FROM replacement_waived_calls').fetchone()),(None,'unknown'))
            self.assertEqual(db.execute('SELECT count(*) FROM replacement_cost_reservations').fetchone()[0],0)
        with self.assertRaisesRegex(ValueError,'lookup_only'):policy.reserve(fixture.TARGET,'call','image')
        for changes in ({'approval_reference_digest':'b'*64},{'currency':'JPY'},{'limit_units':0},
                        {'cost_bounds_verified':True},{'model':'other'},{'analysis_provider':'openai'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                ReplacementPolicy(self.store,{**waived_policy(),**changes},lambda:self.now)

    def test_waived_precondition_is_not_mistaken_for_budget_unconfigured(self):
        c=fixture.IntegrationTests();c.setUp();self.addCleanup(c.doCleanups)
        p=ReplacementPolicy(c.store,waived_policy(),c.clock);c.flow.policy=p
        c.server.pre['policy'].update(policy_version=p.policy['policy_version'],budget_policy_digest=p.policy_digest,
            budget_configured=False,cost_control={'mode':'waived','approval_reference_digest':'a'*64},analysis_provider='gemini')
        c.flow.begin(fixture.TARGET,'dinner',quality_hold=c.hold())
        self.assertEqual(c.flow.episode(fixture.TARGET)['stage'],'ready')

    def test_cooldown_survives_new_store_day_and_time_without_shortening(self):
        value={'cooldown':cooldown(self.now)};remember(self.store,value)
        other=Store(self.store.path)
        with self.assertRaisesRegex(ValueError,'cooldown'):require_available(other,self.now)
        shorter={'cooldown':{**cooldown(self.now),'retry_at':stamp(self.now+timedelta(seconds=1))}}
        remember(other,shorter)
        with self.assertRaises(ValueError):require_available(other,self.now+timedelta(minutes=9))
        require_available(other,self.now+timedelta(minutes=11))
        remember(other,{'cooldown':cooldown(self.now,True)})
        with self.assertRaisesRegex(ValueError,'recovery_required'):require_available(other,self.now+timedelta(days=1))
        pre={'circuit':{'state':'closed','recovery_required':False}}
        observe_circuit(other,pre,self.now+timedelta(days=1));require_available(other,self.now+timedelta(days=1))

    def test_expired_open_can_probe_but_half_open_and_unknown_cannot(self):
        pre={'circuit':{'state':'open','retry_at':stamp(self.now-timedelta(seconds=1)),'recovery_required':False}}
        observe_circuit(self.store,pre,self.now)
        for change in ({'state':'half_open'},{'recovery_required':True},{'retry_at':stamp(self.now+timedelta(seconds=1))}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                observe_circuit(self.store,{'circuit':{**pre['circuit'],**change}},self.now)

    def test_429_and_guard_errors_are_preserved_as_lookup_not_not_saved(self):
        error={'contract_version':'mina-replace-v1','error':'replacement_cooldown','reason':'replacement_cooldown',
            'read_outcome':'rejected','request':None,'candidate':None,'complete':False,'checked_at':stamp(self.now),
            'cooldown':cooldown(self.now)}
        with self.assertRaises(ReplacementPaused) as result:self.contract.decode('candidate_prepare',error)
        remember(self.store,result.exception.envelope)
        self.assertNotIn('persistence',result.exception.envelope)
        error={**error,'reason':'replacement_guard_unavailable'}
        with self.assertRaises(ReplacementPaused):self.contract.decode('candidate_prepare',error)

    def paused_case(self):
        c=fixture.IntegrationTests();c.setUp();self.addCleanup(c.doCleanups)
        original_transport=c.server
        def transport(req,timeout):
            response=original_transport(req,timeout)
            if req.method=='POST' and 'multipart' in req.get_header('Content-type'):
                value=json.loads(response.getvalue());key=value['request']['request_uuid']
                value['request'].update(outcome='processing',stage='paused',reason='replacement_cooldown')
                value['resume_stage_version']=3
                value['candidate'].update(candidate_state='processing',effective_usable=False)
                c.server.requests[key]=value;c.server.c=copy.deepcopy(value['candidate'])
                return fixture.Response(value)
            if '/meal-replacement-requests/' in req.full_url:
                key=req.full_url.split('/meal-replacement-requests/')[1].split('?')[0]
                return fixture.Response(c.server.requests[key])
            return response
        c.client.opener=transport
        c.flow.begin(fixture.TARGET,'dinner',quality_hold=c.hold())
        self.assertEqual(c.flow.episode(fixture.TARGET)['stage'],'paused')
        return c

    def test_paused_get_never_posts_and_explicit_resume_uses_image_sha_and_stage(self):
        c=self.paused_case();key=c.flow.episode(fixture.TARGET)['prepare_uuid']
        posts=sum(method=='POST' for method,_ in c.server.calls)
        c.flow.reconcile(fixture.TARGET)
        self.assertEqual(sum(method=='POST' for method,_ in c.server.calls),posts)
        original=c.client.send
        bodies=[]
        def send(operation,control,body,content_type,prepare_uuid=None):
            self.assertEqual(operation,'replacement_resume');data=json.loads(body);bodies.append(data)
            self.contract.validate('ResumeInput',data)
            with c.store.transaction() as db:wire=json.loads(db.execute('SELECT metadata FROM replacement_wire WHERE request_key=?',(key,)).fetchone()[0])
            self.assertEqual(data['expected_input_sha256'],wire['input_image_sha256'])
            self.assertEqual(data['expected_stage_version'],3)
            value=c.server.requests[key];value['request'].update(outcome='applied',stage='ready',reason=None)
            value['candidate'].update(candidate_state='ready',effective_usable=True);c.server.c=copy.deepcopy(value['candidate'])
            return {'contract_version':'mina-replace-v1','request':{'request_uuid':control,'original_request_uuid':key,
                'operation':'replacement_resume','input_hash':digest(data,'ih1_'),'outcome':'applied','stage':'completed','reason':None,'discard_input':None},
                'candidate':copy.deepcopy(value['candidate']),'freshness':fixture.fresh(c.clock()),'reason':None}
        c.client.send=send
        resume_paused(c.flow,fixture.TARGET)
        self.assertEqual(len(bodies),1);self.assertEqual(c.flow.episode(fixture.TARGET)['stage'],'ready')
        with self.assertRaisesRegex(ValueError,'not_paused'):resume_paused(c.flow,fixture.TARGET)
        self.assertEqual(len(bodies),1)

    def test_resume_response_loss_is_lookup_only_even_if_original_remains_paused(self):
        c=self.paused_case();c.client.send=Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):resume_paused(c.flow,fixture.TARGET)
        with self.assertRaisesRegex(ValueError,'lookup_only'):resume_paused(c.flow,fixture.TARGET)
        self.assertEqual(c.client.send.call_count,1)
        with c.store.transaction() as db:
            row=db.execute("SELECT phase FROM meal_replacement_requests WHERE operation='replacement_resume'").fetchone()
            self.assertEqual(row['phase'],'started')

    def test_saved_paused_commit_invalidates_old_approval_and_never_becomes_not_saved(self):
        c=fixture.IntegrationTests();c.setUp();self.addCleanup(c.doCleanups)
        c.flow.begin(fixture.TARGET,'dinner',quality_hold=c.hold());c.flow.decide_candidate(fixture.TARGET)
        key=c.flow.episode(fixture.TARGET)['commit_uuid'];value=copy.deepcopy(c.server.requests[key])
        value['request'].update(stage='paused',status='processing',saved_at=stamp(c.clock()))
        value['resume_stage_version']=4
        c.flow.accept(fixture.TARGET,key,value)
        self.assertEqual(c.flow.journal.get(key)['observation'],json.dumps({'persistence':'saved'}))
        with c.store.transaction() as db:self.assertEqual(db.execute('SELECT state FROM publication_holds').fetchone()[0],'saved_pending_review')
        self.assertEqual(c.flow.episode(fixture.TARGET)['stage'],'paused')

    def test_expired_prepare_cannot_resume_or_extend_deadline(self):
        c=self.paused_case();c.now+=timedelta(minutes=16);c.client.send=Mock()
        with self.assertRaises(ValueError):resume_paused(c.flow,fixture.TARGET)
        c.client.send.assert_not_called()

    def test_real_client_resume_post_and_control_get_use_distinct_routes(self):
        c=self.paused_case();original=c.flow.episode(fixture.TARGET)['prepare_uuid'];control=str(uuid.uuid4())
        body={'request_uuid':control,'original_request_uuid':original,'expected_stage_version':3,
            'expected_input_sha256':'a'*64,'policy_version':'synthetic-policy-v1','budget_policy_digest':'b'*64,
            'publication_hold':c.server.c['prepared_publication_hold']}
        response={'contract_version':'mina-replace-v1','request':{'request_uuid':control,'original_request_uuid':original,
            'operation':'replacement_resume','input_hash':digest(body,'ih1_'),'outcome':'applied','stage':'completed','reason':None,'discard_input':None},
            'candidate':copy.deepcopy(c.server.c),'freshness':fixture.fresh(c.clock()),'reason':None}
        calls=[]
        def transport(req,timeout):
            calls.append(req)
            return fixture.Response(response)
        c.client.opener=transport
        c.client.send('replacement_resume',control,fixture.canonical(body),'application/json')
        c.client.request(control,'replacement_resume')
        self.assertTrue(calls[0].full_url.endswith('/meal-replacement-resumes'))
        self.assertTrue(calls[1].full_url.endswith('/meal-replacement-resumes/'+control))
        self.assertEqual([r.method for r in calls],['POST','GET'])
        self.assertEqual(calls[0].get_header('Idempotency-key'),control)
        self.assertIsNone(calls[0].get_header('Origin'))

    def test_saved_resume_after_original_deadline_keeps_saved_and_uses_saved_window(self):
        c=fixture.IntegrationTests();c.setUp();self.addCleanup(c.doCleanups)
        c.flow.begin(fixture.TARGET,'dinner',quality_hold=c.hold());c.flow.decide_candidate(fixture.TARGET)
        key=c.flow.episode(fixture.TARGET)['commit_uuid'];value=c.server.requests[key]
        value['request'].update(stage='paused',status='processing',saved_at=stamp(c.clock()))
        value['resume_stage_version']=4
        c.flow.accept(fixture.TARGET,key,value)
        c.now+=timedelta(minutes=16)
        value['freshness']=fixture.fresh(c.clock())
        c.client.send=Mock()
        with self.assertRaisesRegex(ValueError,'deadline_exceeded'):resume_paused(c.flow,fixture.TARGET)
        c.client.send.assert_not_called()
        self.assertEqual(json.loads(c.flow.journal.get(key)['observation'])['persistence'],'saved')

    def test_new_policy_digest_is_reproducible_and_does_not_self_approve_integration(self):
        from scripts.build_replacement_waived_policy import build
        a,b=build(),build()
        self.assertEqual(a,b)
        self.assertEqual(a['policy_digest'],digest(a['agent_policy']))
        self.assertIs(a['provider_policy_proposal']['agent_integration_verified'],False)
        self.assertIsNone(a['provider_policy_proposal']['prepare_max_cost_minor'])
        self.assertEqual(a['approval_reference_digest'],digest(a['approval']))
