"""時計差停止した朝食の本人指定復旧と、一度送信・他枠保持を確認する。"""
import copy,json
from dataclasses import replace
from datetime import timedelta
from unittest import TestCase
from unittest.mock import Mock,patch
from src.mina.clock_recovery import authorize_breakfast
from src.mina.buffer_publisher import arm
from src.mina.runtime import publish_due
from tests.mina import test_buffer_publisher as f

class ClockRecoveryTests(TestCase):
    setUp=f.BufferPublisherTests.setUp

    def stop(self):
        arm(self.store,f.CHANNEL,f.NOW)
        row=self.store.claim_due(self.c.scheduled_at)[0]
        self.now=self.c.scheduled_at+timedelta(minutes=10)
        self.store.stop_before_publish(row,self.now,'source','clock_ahead',{'clock_ahead_ms':1359.379})
        self.inventory={'date':f.TARGET,'checked_at':self.now.isoformat(),'complete':True,'fixed_x_matches':True,'target_or_unknown_posts':0}
        self.verify=Mock()

    def recover(self,**kw):
        return authorize_breakfast(self.store,f.TARGET,self.c.digest,'今朝の投稿を復旧して下さい',kw.get('inventory',self.inventory),f.HOST,self.verify,lambda:self.now)

    def test_explicit_recovery_preserves_content_then_publishes_exactly_once(self):
        self.stop()
        later=replace(self.c,slot='lunch');self.store.save(later,f.HOST,f.NOW)
        unchanged=self.store.get(f.TARGET,'lunch')
        self.assertEqual(self.recover()['revision'],2)
        row=self.store.get(f.TARGET,'breakfast');new=type(self.c)(**json.loads(row['payload']))
        self.assertEqual(new.content,self.c.content);self.assertEqual(new.image_sha256,self.c.image_sha256)
        self.assertEqual(row['scheduled'],row['approved']);self.assertEqual(self.store.get(f.TARGET,'lunch'),unchanged)
        with self.assertRaises(ValueError):self.recover()
        proof={k:self.now.isoformat() for k in ('started_at','observed_at','checked_at')}
        proof.update(expires_at=(self.now+timedelta(hours=1)).isoformat(),started_monotonic=10,observed_monotonic=10)
        api=Mock();api.channel.return_value=f.CHANNEL
        api._request.return_value={'createPost':{'__typename':'PostActionSuccess','post':{'id':'synthetic','text':self.c.content,'channel':{'id':'channel','service':'twitter'},'status':'sent','sentAt':self.now.isoformat(),'externalLink':'https://x.com/EatsLog88161/status/12345'}}}
        with patch('src.mina.buffer_publisher.production_client',return_value=api):
            self.assertEqual(publish_due(self.store,Mock(),self.now,f.HOST,lambda c:proof,clock=lambda:self.now,timer=lambda:10),1)
            self.assertEqual(publish_due(self.store,Mock(),self.now,f.HOST,lambda c:proof,clock=lambda:self.now,timer=lambda:10),0)
        self.assertEqual(api._request.call_count,1)
        self.assertEqual(self.store.get(f.TARGET,'lunch'),unchanged)

    def test_unknown_or_attempted_route_is_never_recovered(self):
        self.stop()
        for field,value in [('post_id','unknown'),('attempted',self.now.timestamp()),('request_json','{}')]:
            with self.store.transaction() as db:db.execute('UPDATE buffer_publication SET '+field+'=?',(value,))
            with self.assertRaises(ValueError):self.recover()
            with self.store.transaction() as db:db.execute('UPDATE buffer_publication SET '+field+'=NULL')

    def test_only_clock_stop_with_explicit_unsent_proof_is_allowed(self):
        self.stop()
        with self.store.transaction() as db:db.execute("UPDATE operation_events SET reason='source_changed' WHERE event='stopped'")
        with self.assertRaises(ValueError):self.recover()

    def test_stale_incomplete_or_conflicting_external_inventory_is_rejected(self):
        self.stop()
        for change in ({'complete':False},{'fixed_x_matches':False},{'target_or_unknown_posts':1},{'checked_at':(self.now-timedelta(seconds=121)).isoformat()}):
            with self.assertRaises(ValueError):self.recover(inventory={**self.inventory,**change})

    def test_source_change_and_disabled_writes_keep_failed_state(self):
        self.stop();self.verify.side_effect=ValueError('changed')
        with self.assertRaises(ValueError):self.recover()
        self.assertEqual(self.store.get(f.TARGET,'breakfast')['state'],'changes_requested')
        with patch.dict('os.environ',{'MINA_LIVE_WRITES':'false'}):
            with self.assertRaises(RuntimeError):self.recover()

    def test_late_window_and_excluded_slot_are_rejected(self):
        self.stop()
        with self.store.transaction() as db:db.execute("INSERT INTO publication_exclusions VALUES(?,'breakfast','本人指定')",(f.TARGET,))
        with self.assertRaises(ValueError):self.recover()
        self.now=self.c.scheduled_at+timedelta(hours=2)
        with self.assertRaises(ValueError):self.recover()
