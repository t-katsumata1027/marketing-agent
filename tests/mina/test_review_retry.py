"""副作用のないCLI処理だけを同入力・有限回・期限内に再試行する。"""
import json,subprocess,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import test_codex_client as fixtures
from src.mina.codex_client import CodexClient
from src.mina.external_guard import guard,normal_retry,managed_call
from src.mina.review_retry import NAMES
from src.mina.preparation_status import PreparationDeadline


class ReviewRetryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.CodexClientTest();self.f.setUp();self.addCleanup(self.f.doCleanups)
        allowed=normal_retry.set(True);self.addCleanup(normal_retry.reset,allowed)
        checked=guard.set(lambda:None);self.addCleanup(guard.reset,checked)
        self.commands=[];self.failures=1;self.error='stream disconnected';self.output={'status':'publishable','reason_codes':[]}
        def launch(command,**kwargs):
            self.commands.append(command)
            path=Path(command[command.index('--output-last-message')+1])
            fail=len(self.commands)<=self.failures
            if not fail:path.write_text(json.dumps(self.output),encoding='utf-8')
            process=Mock(returncode=1 if fail else 0);process.communicate.return_value=('',self.error if fail else '')
            return process
        self.popen=patch('src.mina.codex_client.subprocess.Popen',side_effect=launch);self.popen.start();self.addCleanup(self.popen.stop)
        self.sleep=patch('src.mina.review_retry.time.sleep');self.sleep.start();self.addCleanup(self.sleep.stop)

    def invoke(self,name='mina_independent_review'):return CodexClient().structured('private-prompt','same-input',{'type':'object'},name)
    def chain(self):return json.loads(next(self.f.directory.glob('review-chain-*/state.json')).read_text())

    def test_failed_attempt_is_retained_and_same_input_succeeds_once(self):
        self.assertEqual(self.invoke(),self.output);self.assertEqual(len(self.commands),2)
        chain=self.chain();self.assertEqual(chain['state'],'complete')
        self.assertEqual([a['state'] for a in chain['attempts']],['failed','complete'])
        self.assertEqual(chain['attempts'][0]['usage_status'],'unmeasured')
        self.assertNotIn('private-prompt',json.dumps(chain))
        self.assertEqual(json.loads((self.f.directory/chain['attempts'][0]['job']/'state.json').read_text())['state'],'failed')
        self.assertTrue(all('--disable' in c and c[c.index('--sandbox')+1]=='read-only' for c in self.commands))

    def test_only_whitelisted_read_only_names_retry(self):
        for name in NAMES:
            self.commands.clear();self.assertEqual(self.invoke(name),self.output);self.assertEqual(len(self.commands),2)

    def test_three_attempt_cap_does_not_free_unknown_usage(self):
        self.failures=10
        with self.assertRaises(RuntimeError):self.invoke()
        self.assertEqual(len(self.commands),3);self.assertEqual(self.chain()['state'],'held')
        self.assertTrue(all(a['usage_status']=='unmeasured' for a in self.chain()['attempts']))

    def test_authentication_and_quota_and_invalid_schema_are_not_retried(self):
        for error in ('HTTP 401 unauthorized','usage limit reached','invalid_json_schema'):
            self.commands.clear();self.error=error
            with self.assertRaises(RuntimeError):self.invoke()
            self.assertEqual(len(self.commands),1)

    def test_successful_quality_hold_is_not_retried(self):
        self.failures=0;self.output={'status':'hold','reason_codes':['nutrition_semantic_mismatch']}
        self.assertEqual(self.invoke(),self.output);self.assertEqual(len(self.commands),1)

    def test_images_and_other_operations_do_not_retry(self):
        with self.assertRaises(RuntimeError):CodexClient().image('food')
        self.assertEqual(len(self.commands),1)
        self.commands.clear()
        with self.assertRaises(RuntimeError):self.invoke('unapproved-operation')
        self.assertEqual(len(self.commands),1)

    def test_deadline_guard_blocks_next_attempt(self):
        calls=[]
        def check():
            calls.append(1)
            if len(calls)>1:raise PreparationDeadline('expired')
        token=guard.set(check)
        try:
            with self.assertRaises(PreparationDeadline):self.invoke()
        finally:guard.reset(token)
        self.assertEqual(len(self.commands),1);self.assertEqual(self.chain()['state'],'held')

    def test_default_scope_and_managed_calls_do_not_retry(self):
        token=normal_retry.set(False)
        try:
            with self.assertRaises(RuntimeError):self.invoke()
        finally:normal_retry.reset(token)
        self.assertEqual(len(self.commands),1);self.commands.clear()
        with self.assertRaises(RuntimeError):managed_call(self.invoke)
        self.assertEqual(len(self.commands),1)

    def test_real_replacement_flow_retains_one_unknown_budget_reservation(self):
        import test_replacement_integration as integration
        from src.mina.daily_pipeline import DeadlineClient
        fixture=integration.IntegrationTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.flow.begin(integration.TARGET,'dinner',quality_hold=fixture.hold())
        episode=fixture.flow.episode(integration.TARGET)
        client=DeadlineClient(CodexClient(),lambda:None,allow_retry=True)
        operation=lambda:client.structured('input','same',{'type':'object'},'mina_independent_review')
        with self.assertRaises(RuntimeError):fixture.flow.call(episode,'review',operation)
        self.assertEqual(len(self.commands),1)
        with fixture.store.transaction() as db:
            rows=list(db.execute("SELECT state FROM replacement_cost_reservations WHERE kind='review'"))
        self.assertEqual([r['state'] for r in rows],['unknown'])
        with self.assertRaises(ValueError):fixture.flow.call(episode,'review',operation)
        self.assertEqual(len(self.commands),1)

    def test_real_rescue_review_retains_one_unknown_waived_operation(self):
        import test_rescue as rescue
        from src.mina.rescue_flow import RescueFlow
        from src.mina.daily_pipeline import DeadlineClient
        fixture=rescue.RescueTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        flow=RescueFlow(fixture.store,Mock(),Mock(),fixture.policy,Mock(),Mock(),fixture.clock)
        client=DeadlineClient(CodexClient(),lambda:None,allow_retry=True)
        operation=lambda:client.structured('input','same',{'type':'object'},'mina_ogp_review')
        with self.assertRaises(RuntimeError):flow.collection_review('lunch','meal',{'sha256':'a'*64},operation)
        self.assertEqual(len(self.commands),1)
        with fixture.store.transaction() as db:rows=list(db.execute("SELECT state FROM replacement_waived_calls WHERE kind='collection_review'"))
        self.assertEqual([r['state'] for r in rows],['unknown'])
        fixture.now+=__import__('datetime').timedelta(hours=2)
        with self.assertRaises(ValueError):flow.collection_review('lunch','meal',{'sha256':'a'*64},operation)
        self.assertEqual(len(self.commands),1)

    def test_http_status_is_not_inferred_from_random_401_digits(self):
        self.assertEqual(CodexClient.failure_reason('run a401b failed'),'execution')
        self.assertEqual(CodexClient.failure_reason('HTTP/1.1 401'),'authentication')
        self.assertEqual(CodexClient.failure_reason('status code: 401'),'authentication')
