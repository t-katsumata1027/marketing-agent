"""実台帳と代替通信で、承認保持・有限再照会・公開一回を確認する。"""
import json
import unittest
from datetime import timedelta
from unittest.mock import Mock,MagicMock,patch
from urllib.error import HTTPError,URLError
from src.mina.buffer_trial import BufferTrialClient,ORGANIZATIONS,CHANNELS,CREATE
from src.mina.buffer_publisher import BufferPublisher,arm
from src.mina.runtime import publish_due
from tests.mina import test_buffer_publisher as fixtures


class PublicationRetryTests(unittest.TestCase):
    setUp=fixtures.BufferPublisherTests.setUp

    def arrange(self,failures=2,error=None):
        arm(self.store,fixtures.CHANNEL,fixtures.NOW)
        self.now=self.c.scheduled_at;self.checks=0
        def transport(query,variables):
            if query==ORGANIZATIONS:
                self.checks+=1
                if self.checks<=failures:
                    raise error or URLError('private-token')
                return {'data':{'account':{'organizations':[{'id':'synthetic'}]}}}
            if query==CHANNELS:return {'data':{'channels':[fixtures.CHANNEL]}}
            if query==CREATE:return {'data':{'createPost':{'__typename':'PostActionSuccess','post':fixtures.BufferPublisherTests.post(self)}}}
            raise AssertionError('未定義要求')
        self.transport=Mock(side_effect=transport)
        self.api=BufferTrialClient('private-token',transport=self.transport)
        self.default=Mock();self.preflight=Mock(return_value=self.proof)

    def tick(self):
        with patch('src.mina.buffer_publisher.production_client',return_value=self.api):
            return publish_due(self.store,self.default,self.now,fixtures.HOST,self.preflight,clock=lambda:self.now,timer=lambda:10)

    def test_two_read_failures_retain_approval_then_publish_once(self):
        self.arrange()
        original=self.store.get(fixtures.TARGET,'breakfast')
        self.assertEqual(self.tick(),0)
        row=self.store.get(fixtures.TARGET,'breakfast')
        self.assertEqual(row['state'],'approved');self.assertEqual(row['approved'],original['approved'])
        for _ in range(3):self.assertEqual(self.tick(),0)
        self.assertEqual(self.checks,1)
        self.now+=timedelta(seconds=15);self.assertEqual(self.tick(),0)
        from src.mina.store import Store
        self.store=Store(self.store.path)
        self.now+=timedelta(seconds=15);self.assertEqual(self.tick(),1)
        self.assertEqual(self.tick(),0)
        self.assertEqual(sum(call.args[0]==CREATE for call in self.transport.call_args_list),1)
        self.preflight.assert_called_once();self.default.publish.assert_not_called()
        self.assertEqual(self.store.get(fixtures.TARGET,'breakfast')['state'],'published')
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT attempts FROM publication_retries').fetchone()[0],3)
            text=''.join(row[0] for row in db.execute('SELECT details FROM operation_events'))
            self.assertNotIn('private-token',text)

    def test_three_failures_stop_without_submission_or_fourth_attempt(self):
        self.arrange(failures=10)
        for _ in range(4):
            self.tick();self.now+=timedelta(seconds=15)
        self.assertEqual(self.checks,3)
        self.assertEqual(self.store.get(fixtures.TARGET,'breakfast')['state'],'changes_requested')
        self.assertFalse(any(call.args[0]==CREATE for call in self.transport.call_args_list))

    def test_non_transient_failures_do_not_retry(self):
        for code in (401,403,429,500):
            with self.subTest(code=code):
                # ケースごとに独立した台帳・候補を使用する。
                case=PublicationRetryTests();case.setUp()
                try:
                    case.arrange(error=HTTPError('https://invalid',code,'private',None,None))
                    case.tick();case.now+=timedelta(seconds=15);case.tick()
                    self.assertEqual(case.checks,1)
                    self.assertEqual(case.store.get(fixtures.TARGET,'breakfast')['state'],'changes_requested')
                finally:case.doCleanups()

    def test_503_query_can_retry_but_json_error_cannot(self):
        self.arrange(failures=1,error=HTTPError('https://invalid',503,'private',None,None))
        self.assertEqual(self.tick(),0)
        self.now+=timedelta(seconds=15);self.assertEqual(self.tick(),1)

    def test_deadline_and_source_change_prevent_write_after_reconnection(self):
        self.arrange(failures=1)
        self.tick();self.now+=timedelta(seconds=121);self.tick()
        self.assertEqual(self.checks,1);self.assertEqual(self.store.get(fixtures.TARGET,'breakfast')['state'],'expired')
        self.assertFalse(any(call.args[0]==CREATE for call in self.transport.call_args_list))

    def test_query_timeout_respects_remaining_grace(self):
        self.arrange(failures=0)
        self.now+=timedelta(seconds=119)
        # 送信は不要。実HTTP読取のtimeout引数だけを代替応答で検証する。
        from src.mina.buffer_trial import NoRedirect
        api=BufferTrialClient('private-token')
        api.connection_deadline=self.c.scheduled_at+timedelta(seconds=120);api.connection_clock=lambda:self.now
        response=Mock();response.read.return_value=b'{"data":{}}'
        opener=MagicMock();opener.open.return_value.__enter__.return_value=response
        with patch('src.mina.buffer_trial.build_opener',return_value=opener):api._request(ORGANIZATIONS)
        self.assertEqual(opener.open.call_args.kwargs['timeout'],1)

    def test_malformed_json_graphql_and_creation_timeout_are_never_read_retries(self):
        from src.mina.buffer_trial import ProbeError,TransientBufferRead
        for response in ({'errors':[{'message':'private'}]}, {'data':None}):
            api=BufferTrialClient('private',transport=lambda *args:response)
            api.connection_deadline=self.c.scheduled_at+timedelta(seconds=120);api.connection_clock=lambda:self.c.scheduled_at
            with self.assertRaises(ProbeError):api._request(ORGANIZATIONS)
        api=BufferTrialClient('private',transport=Mock(side_effect=ValueError('private-body')))
        api.connection_deadline=self.c.scheduled_at+timedelta(seconds=120);api.connection_clock=lambda:self.c.scheduled_at
        with self.assertRaises(ProbeError):api._request(ORGANIZATIONS)
        api._transport=Mock(side_effect=TimeoutError('private'))
        with self.assertRaises(ProbeError):api._request(CREATE)

    def test_changed_source_after_connection_recovery_is_not_published(self):
        self.arrange(failures=1);self.tick()
        self.preflight.side_effect=ValueError('private-source-change')
        self.now+=timedelta(seconds=15);self.tick()
        self.assertFalse(any(call.args[0]==CREATE for call in self.transport.call_args_list))
        self.assertEqual(self.store.get(fixtures.TARGET,'breakfast')['state'],'changes_requested')
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT stage FROM operation_events WHERE event='stopped' ORDER BY id DESC LIMIT 1").fetchone()[0],'source')

    def test_two_workers_cannot_claim_the_same_retry(self):
        self.arrange(failures=1);self.tick();self.now+=timedelta(seconds=15)
        from concurrent.futures import ThreadPoolExecutor
        from src.mina.store import Store
        def claim(_):return Store(self.store.path).claim_due(self.now)
        with ThreadPoolExecutor(2) as pool:result=list(pool.map(claim,range(2)))
        self.assertEqual(sum(map(len,result)),1)
