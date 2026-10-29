"""定時公開の実経路・外部照合cache・専用照会枠を、外部Fakeで接続する。"""
import copy,json,uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch,Mock
import test_post_promotion as fixtures
import unittest
from src.mina.post_promotion import TARGET,verify_ig
from src.mina.post_promotion_external import ensure_external,own_sent
from src.mina.domain import slot_time,date,Candidate
from src.mina.buffer_limits import record_request
from src.mina.buffer_budget import setup,using,subject
from src.mina.buffer_trial import GET_POST,ProbeError

class RoutingTests(unittest.TestCase):
    setUp=fixtures.PromotionTests.setUp
    staged=fixtures.PromotionTests.staged
    approve=fixtures.PromotionTests.approve
    def released(self):
        cards,ig=self.staged();self.approve(cards,ig)
        with self.store.transaction() as db:r=dict(db.execute('SELECT * FROM post_promotions').fetchone())
        return r,json.loads(r['document'])
    def test_cache_hit_unknown_stops_without_any_external_call(self):
        r,d=self.released()
        with self.store.transaction() as db:
            db.execute("UPDATE post_promotion_phases SET state='complete',receipt=? WHERE phase='dinner'",(json.dumps({'read_started_at':self.now.isoformat()}),));db.execute("UPDATE candidates SET state='outcome_unknown' WHERE slot='dinner'")
        with patch('src.mina.post_promotion_external.load',return_value=(r,d,self.settings,self.snapshot,lambda:None)),patch('src.mina.buffer_publisher.production_client') as api:
            with self.assertRaises(ValueError):ensure_external(self.store,TARGET,'dinner',lambda:self.now)
            api.assert_not_called()
    def test_only_promotion_reservation_can_read_individual_post(self):
        key=subject('isolated');normal=str(uuid.uuid4());new=str(uuid.uuid4())
        with self.store.transaction() as db:
            setup(db)
            for table,budget in (('buffer_request_reservations',normal),('buffer_promotion_reservations',new)):db.execute('INSERT INTO '+table+" VALUES(?,?,?,2,2,?,'active')",(budget,key,TARGET,self.now.timestamp()+100000))
        with patch('src.mina.buffer_limits.datetime') as dt,patch('src.mina.buffer_budget.time.time',return_value=self.now.timestamp()):
            dt.now.return_value=self.now
            with using(normal),self.assertRaises(ProbeError):record_request(self.store,GET_POST,subject=key)
            with using(new):record_request(self.store,GET_POST,subject=key)
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM buffer_api_usage').fetchone()[0],1)
    def own_route(self,d):
        due=slot_time(date.fromisoformat(TARGET),'dinner');c=Candidate(**d['xs']['dinner']);proof={'started_at':due.isoformat(),'expires_at':(due+timedelta(minutes=5)).isoformat()}
        with self.store.transaction() as db:
            db.execute("UPDATE candidates SET state='published',x_id='77' WHERE slot='dinner'");db.execute("INSERT INTO buffer_publication(target_date,slot,revision,digest,channel_id,service_id,state,post_id,proof_json) VALUES(?,'dinner',1,?,'xchannel','xaccount','sent','native-post',?)",(TARGET,c.digest,json.dumps(proof)))
        return {'id':'native-post','status':'sent','text':c.content,'sentAt':(due+timedelta(seconds=1)).isoformat(),'externalLink':'https://x.com/EatsLog88161/status/77','channel':{'id':'xchannel','service':'twitter'}}
    def test_changed_or_missing_own_post_is_never_excluded(self):
        r,d=self.released();post=self.own_route(d)
        class API:
            def _request(self,*a):return {'post':post}
        api=API();self.assertTrue(own_sent(self.store,api,TARGET,d)['x']);post['text']='変更'
        with self.assertRaises(ProbeError):own_sent(self.store,api,TARGET,d)
        with patch.object(api,'_request',side_effect=ProbeError('buffer_http_404')):
            with self.assertRaises(ProbeError):own_sent(self.store,api,TARGET,d)
    def test_x_and_instagram_regular_due_routes_call_inventory_and_publish_once(self):
        r,d=self.released();due=slot_time(date.fromisoformat(TARGET),'dinner');now=due+timedelta(seconds=1)
        with self.store.transaction() as db:
            db.execute("INSERT INTO buffer_policy VALUES(1,?,?,? ,?,'enabled')",(TARGET,TARGET,'xchannel','xaccount'));db.execute("UPDATE post_promotion_phases SET state='complete',receipt=? WHERE phase='dinner'",(json.dumps({'read_started_at':now.isoformat()}),))
        outer=self
        class API:
            mutations=0
            def channel(self):return {'id':'xchannel','serviceId':'xaccount','isQueuePaused':False}
            def _request(self,query,variables=None):
                if 'createPost(' in query:
                    self.mutations+=1;return {'createPost':{'__typename':'PostActionSuccess','post':{'id':'native-x','text':variables['input']['text'],'status':'sent','sentAt':now.isoformat(),'externalLink':'https://x.com/EatsLog88161/status/77','channel':{'id':'xchannel','service':'twitter'}}}}
                raise AssertionError('未計画のAPI')
        api=API();proof={'started_at':now.isoformat(),'checked_at':now.isoformat(),'observed_at':now.isoformat(),'expires_at':(now+timedelta(minutes=5)).isoformat(),'started_monotonic':100,'observed_monotonic':100}
        env={'MINA_LIVE_WRITES':'true','MINA_PUBLISH_ENABLED':'true','MINA_INSTAGRAM_PUBLISH_ENABLED':'true'}
        with patch.dict('os.environ',env),patch('src.mina.post_promotion_external.load',return_value=(r,d,self.settings,self.snapshot,lambda:None)),patch('src.mina.buffer_publisher.production_client',return_value=api):
            from src.mina.runtime import publish_due
            self.assertEqual(publish_due(self.store,Mock(),now,fixtures.HOST,lambda c:proof,clock=lambda:now,timer=lambda:100),1);self.assertEqual(api.mutations,1)
            row=self.ig.claim_due(now);c=next(c for c in self.igs if c.slot=='dinner')
            with self.store.transaction() as db:db.execute('CREATE TABLE instagram_acceptance(channel_id TEXT,format TEXT,layout_version TEXT)');db.execute('INSERT INTO instagram_acceptance VALUES(?,?,?)',(c.channel_id,c.format,c.layout_version))
            class IGAPI:
                mutations=0
                def _request(self,query,variables=None):
                    if 'organizations' in query:return {'account':{'organizations':[{'id':'org'}]}}
                    if 'channels(' in query:return {'channels':[{'id':'ichannel','serviceId':'iaccount','name':'log_eats','service':'instagram','isDisconnected':False,'isLocked':False,'isQueuePaused':False}]}
                    if 'createPost(' in query:
                        self.mutations+=1;return {'createPost':{'__typename':'PostActionSuccess','post':{'id':'native-ig','text':c.caption,'channel':{'id':c.channel_id,'service':'instagram'},'schedulingType':'automatic','metadata':{'type':c.format,'isAiGenerated':True,'shouldShareToFeed':False},'assets':[{'source':a['url']} for a in c.assets],'status':'sent','sentAt':now.isoformat(),'externalLink':'https://www.instagram.com/stories/log_eats/123/'}}}
                    raise AssertionError('未計画のAPI')
            igapi=IGAPI();runtime=SimpleNamespace(store=self.ig,meal=Mock())
            from src.mina.instagram_publisher import publish_once
            with patch('src.mina.post_promotion.load',return_value=(r,d,self.settings,self.snapshot,lambda:None)),patch('src.mina.post_promotion.verify_all') as verified:
                publish_once(self.ig,row,lambda c:verify_ig(runtime,c),('drive.google.com',),client=igapi,clock=lambda:now,verify_media=lambda a:True)
                verified.assert_called_once();self.assertEqual(set(verified.call_args.args[1]),{'breakfast','lunch','dinner','daily'})
            self.assertEqual(igapi.mutations,1)
            self.assertEqual(publish_due(self.store,Mock(),now,fixtures.HOST,lambda c:proof,clock=lambda:now),0);self.assertIsNone(self.ig.claim_due(now))
