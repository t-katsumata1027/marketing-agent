"""一件限定の経路固定、承認・鮮度、応答喪失後の二重送信防止を検証する。"""

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from src.mina.buffer_publisher import TARGET, BufferPublisher, arm, publication_client, recover_publication
from src.mina.domain import JST
from src.mina.runtime import publish_due
from src.mina.store import Store
from tests.mina.test_daily import candidate, HOST

NOW = datetime(2026,10,15,23,0,tzinfo=JST)
CHANNEL = {'id':'channel','serviceId':'native-account','service':'twitter','name':'EatsLog88161',
           'isQueuePaused':False,'isDisconnected':False,'isLocked':False}


class BufferPublisherTests(unittest.TestCase):
    def setUp(self):
        flags = patch.dict('os.environ',{'MINA_LIVE_WRITES':'true','MINA_PUBLISH_ENABLED':'true'})
        flags.start();self.addCleanup(flags.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/'db')
        c = replace(candidate(),target_date=TARGET)
        self.c = replace(c,publish_text=c.content,approval_source={'request_key':'11111111-1111-4111-8111-111111111111',
            'state_version':'sv1_'+'a'*64,'input_hash':'ih1_'+'b'*64,'result_sha256':'c'*64})
        self.store.save(self.c,HOST,NOW)
        key = self.store.issue_cards(TARGET,NOW)[0]['components'][0]['components'][0]['custom_id']
        self.store.decide(key,'human',NOW)
        self.proof = {k:self.c.scheduled_at.isoformat() for k in ('started_at','observed_at','checked_at')}
        self.proof.update(expires_at=(self.c.scheduled_at+timedelta(hours=1)).isoformat(),
                          started_monotonic=10,observed_monotonic=10)

    def publisher(self):
        arm(self.store,CHANNEL,NOW)
        row = self.store.claim_due(self.c.scheduled_at)[0]
        with self.store.transaction() as db:
            route = dict(db.execute('SELECT * FROM buffer_publication').fetchone())
        api = Mock()
        api.channel.return_value = CHANNEL
        publisher = BufferPublisher(self.store,row,route,api,clock=lambda:self.c.scheduled_at,timer=lambda:10,sleep=Mock())
        publisher.proof = self.proof
        return publisher,api,row

    def post(self,status='sent',**changes):
        return {'id':'buffer-123','text':self.c.content,'channel':{'id':'channel','service':'twitter'},
                'status':status,'sentAt':self.c.scheduled_at.isoformat() if status=='sent' else None,
                'externalLink':'https://x.com/EatsLog88161/status/12345' if status=='sent' else None,**changes}

    def test_arm_preserves_approval_and_only_target_slot_routes(self):
        before = self.store.get(TARGET,'breakfast')
        arm(self.store,CHANNEL,NOW)
        arm(self.store,CHANNEL,NOW)
        self.assertEqual(before,self.store.get(TARGET,'breakfast'))
        default = Mock()
        self.assertIs(publication_client(self.store,{**before,'slot':'lunch'},default),default)
        self.assertIs(publication_client(self.store,{**before,'target_date':'2026-10-17'},default),default)
        with patch.dict('os.environ',{'BUFFER_API_KEY':'合成値'}):
            self.assertIsInstance(publication_client(self.store,before,default),BufferPublisher)

    def test_arm_rejects_unapproved_paused_or_wrong_account(self):
        for changes in ({'isQueuePaused':True},{'name':'other'},{'isDisconnected':True}):
            with self.assertRaises(Exception):arm(self.store,{**CHANNEL,**changes},NOW)
        with self.store.transaction() as db:db.execute("UPDATE candidates SET state='approval_pending'")
        with self.assertRaises(Exception):arm(self.store,CHANNEL,NOW)

    def test_binding_change_never_falls_back(self):
        arm(self.store,CHANNEL,NOW)
        row = self.store.get(TARGET,'breakfast')
        with self.assertRaises(Exception):publication_client(self.store,{**row,'revision':2},Mock())

    def test_success_uses_completed_native_x_id_and_single_attempt(self):
        publisher,api,row = self.publisher()
        api._request.return_value = {'createPost':{'__typename':'PostActionSuccess','post':self.post()}}
        self.assertEqual(publisher.publish(self.c.content),'12345')
        request = api._request.call_args.args[1]['input']
        self.assertEqual(request['mode'],'shareNow')
        self.assertEqual(request['text'],self.c.content)
        with self.assertRaises(Exception):publisher.publish(self.c.content)
        self.assertEqual(api._request.call_count,1)

    def test_same_saved_x_account_numeric_url_is_stable_after_publication(self):
        publisher,api,row=self.publisher();publisher.route['service_id']='2032214868485685249'
        initial=self.post(externalLink='https://twitter.com/EatsLog88161/status/12345')
        later=self.post(externalLink='https://x.com/2032214868485685249/status/12345')
        self.assertEqual(publisher.validate(initial,self.c.content),'12345')
        self.assertEqual(publisher.validate(later,self.c.content),'12345')
        api._request.assert_not_called()
        for url in ('https://x.com/999/status/12345','https://x.com/other/status/12345','https://x.com/2032214868485685249/status/12345?x=1','https://x.com.evil/2032214868485685249/status/12345','http://x.com/2032214868485685249/status/12345'):
            with self.subTest(url=url),self.assertRaises(Exception):publisher.validate(self.post(externalLink=url),self.c.content)
        with self.assertRaises(Exception):publisher.validate({**later,'text':'変更'},self.c.content)
        with self.assertRaises(Exception):publisher.validate({**later,'channel':{'id':'other','service':'twitter'}},self.c.content)

    def test_timeout_never_retries_or_uses_default_x(self):
        arm(self.store,CHANNEL,NOW)
        api = Mock();api.channel.return_value = CHANNEL;api._request.side_effect = TimeoutError()
        default = Mock()
        with patch('src.mina.buffer_publisher.BufferTrialClient',return_value=api), \
             patch('src.mina.buffer_publisher.BufferPublisher',side_effect=lambda store,row,route,api:
                   BufferPublisher(store,row,route,api,clock=lambda:self.c.scheduled_at,timer=lambda:10)):
            self.assertEqual(publish_due(self.store,default,self.c.scheduled_at,HOST,lambda c:self.proof,
                              clock=lambda:self.c.scheduled_at,timer=lambda:10),0)
        default.publish.assert_not_called()
        self.assertEqual(self.store.get(TARGET,'breakfast')['state'],'outcome_unknown')
        self.assertEqual(api._request.call_count,1)
        self.assertEqual(self.store.claim_due(self.c.scheduled_at),[])

    def test_changed_source_stops_before_buffer_or_x_write(self):
        arm(self.store,CHANNEL,NOW)
        api = Mock();api.channel.return_value = CHANNEL
        with patch('src.mina.buffer_publisher.BufferTrialClient',return_value=api):
            default = Mock()
            publish_due(self.store,default,self.c.scheduled_at,HOST,Mock(side_effect=ValueError()),clock=lambda:self.c.scheduled_at)
        api._request.assert_not_called();default.publish.assert_not_called()
        self.assertEqual(self.store.get(TARGET,'breakfast')['state'],'changes_requested')

    def test_freshness_failure_never_sends(self):
        publisher,api,row = self.publisher()
        publisher.timer = lambda:131
        with self.assertRaises(Exception):publisher.publish(self.c.content)
        api._request.assert_not_called()

    def test_disabled_or_early_publication_never_sends(self):
        publisher,api,row = self.publisher()
        with patch.dict('os.environ',{'MINA_PUBLISH_ENABLED':'false'}):
            with self.assertRaises(Exception):publisher.publish(self.c.content)
        publisher.clock = lambda: NOW
        with self.assertRaises(Exception):publisher.publish(self.c.content)
        api._request.assert_not_called()

    def test_sent_wrong_text_target_or_id_not_success(self):
        publisher,api,row = self.publisher()
        for changes in ({'text':'変更文'},{'channel':{'id':'other','service':'twitter'}},
                        {'externalLink':'https://x.com/other/status/12345'},{'externalLink':None}):
            with self.assertRaises(Exception):publisher.validate(self.post(**changes),self.c.content)

    def test_provider_late_delivery_not_success(self):
        publisher,api,row = self.publisher()
        with self.assertRaises(Exception):
            publisher.validate(self.post(sentAt=(self.c.scheduled_at+timedelta(seconds=121)).isoformat()),self.c.content)

    def test_scheduled_is_not_published_and_polling_is_bounded(self):
        publisher,api,row = self.publisher()
        api._request.return_value = {'createPost':{'__typename':'PostActionSuccess','post':self.post('scheduled')}}
        api.post.return_value = self.post('scheduled')
        with self.assertRaises(Exception):publisher.publish(self.c.content)
        self.assertEqual(api._request.call_count,1)
        self.assertEqual(api.post.call_count,6)

    def test_restart_reads_known_id_without_new_create(self):
        publisher,api,row = self.publisher()
        api._request.return_value = {'createPost':{'__typename':'PostActionSuccess','post':self.post('scheduled')}}
        api.post.side_effect = TimeoutError()
        with self.assertRaises(Exception):publisher.publish(self.c.content)
        api.post.side_effect = None;api.post.return_value = self.post()
        recover_publication(Store(self.store.path),self.c.scheduled_at+timedelta(seconds=181),lambda:api)
        self.assertEqual(self.store.get(TARGET,'breakfast')['x_id'],'12345')
        self.assertEqual(api._request.call_count,1)
        self.assertEqual(Store(self.store.path).claim_due(self.c.scheduled_at),[])

    def test_restart_after_unknown_create_never_resends(self):
        publisher,api,row = self.publisher()
        api._request.side_effect = TimeoutError()
        with self.assertRaises(Exception):publisher.publish(self.c.content)
        recover_publication(Store(self.store.path),self.c.scheduled_at+timedelta(seconds=181),lambda:api)
        self.assertEqual(self.store.get(TARGET,'breakfast')['state'],'outcome_unknown')
        api.post.assert_not_called()
        self.assertEqual(api._request.call_count,1)
