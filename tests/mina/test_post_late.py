"""旧夕食2公開を保全し、新日次2ボタンから実送信境界までをFakeで通す。"""
import copy,json,unittest,uuid
from datetime import datetime,timedelta,UTC
from dataclasses import asdict
from unittest.mock import Mock,patch
import test_post_promotion as fixture
from src.mina import post_late as late
from src.mina.domain import Candidate
from src.mina.preparation import fingerprint
from src.mina.publication_hold import held
from src.mina.runtime import publish_due,send_notifications
from src.mina.instagram_publisher import publish_once
from src.mina.instagram_domain import InstagramCandidate
from src.mina.instagram_media import HOSTS
from src.mina.buffer_publisher import bind_route

class LateTests(unittest.TestCase):
    def setUp(self):
        f=self.f=fixture.PromotionTests();f.setUp();self.addCleanup(f.doCleanups);cards,igs=f.staged();f.approve(cards,igs)
        self.old_tokens=(cards,igs)
        with f.store.transaction() as db:
            for r in db.execute('SELECT * FROM candidates').fetchall():bind_route(db,r,{'id':'xchannel','serviceId':'2032214868485685249'})
            db.execute("UPDATE candidates SET state='published',x_id='123' WHERE slot='dinner'");db.execute("UPDATE candidates SET state='changes_requested',reason='preflight_failed',approved=NULL WHERE slot='daily'")
            db.execute("UPDATE instagram_candidates SET state='published' WHERE slot='dinner'");db.execute("UPDATE instagram_candidates SET state='held' WHERE slot='daily'")
            db.execute("UPDATE buffer_publication SET state='sent',post_id='old-dinner' WHERE slot='dinner'")
            db.execute("UPDATE post_promotion_phases SET state='held' WHERE phase='daily'")
            for t in ('notices','instagram_notices'):db.execute("UPDATE "+t+" SET state='sent'")
            self.old=late.snapshot(db)
        self.now=datetime(2026,10,29,12,50,tzinfo=UTC)
        outer=self
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return outer.now.astimezone(tz) if tz else outer.now.replace(tzinfo=None)
        self.clock=patch('src.mina.post_late.datetime',Clock);self.clock.start();self.addCleanup(self.clock.stop)
        env=patch.dict('os.environ',{'MINA_DAILY_LATE_ENABLED':'true','MINA_LIVE_WRITES':'true','MINA_PUBLISH_ENABLED':'true','MINA_INSTAGRAM_PUBLISH_ENABLED':'true'});env.start();self.addCleanup(env.stop)
        self.proof={'source':{**f.proof,'read_started_at':fixture.stamp(self.now)},'external':{'read_started_at':self.now.isoformat()},'media_verified':True,'external_verified':True}
        self.a={'case_uuid':str(uuid.uuid4()),'commit':'f'*40,'old_root':'/isolated-old-root','old_commit':'d'*40,'config_path':'/root/config','config_sha256':'a'*64,'approval_reference':'隔離の本人承認','snapshot_digest':fingerprint(self.old),'not_after':late.END.isoformat()}
    def stage(self):
        # 固定本番hostだけを、隔離fixtureのhostへ写す。内容検査本体は実行する。
        original=Candidate.validate
        with patch.object(Candidate,'validate',lambda c,host,now:original(c,fixture.HOST,now)):
            late.stage(self.f.store,self.a,self.old,self.proof,lambda:self.now)
        xs=self.f.store.issue_cards(late.TARGET,self.now,slots=('daily',))
        self.assertEqual(len(xs),1)
        r=next(r for r in self.f.ig.rows(late.TARGET) if r['slot']=='daily');self.f.ig.queue_approval(r)
        with self.f.store.transaction() as db:
            notices=list(db.execute("SELECT payload FROM instagram_notices WHERE slot='daily' AND state='pending'"));self.assertEqual(len(notices),1);ig=json.loads(notices[0][0])
        from test_routine_pipeline import Discord
        self.discord=Discord();late.send_cards(self.f.store,self.discord)
        return xs[0]['components'][0]['components'][0]['custom_id'],ig['components'][0]['components'][0]['custom_id']
    def approve(self,tokens):
        self.f.store.decide(tokens[0],'human',self.now);self.f.ig.decide(tokens[1],'human',self.now)
    def backend(self,unknown=False):
        f=self.f;outer=self;api=Mock();api.channel.return_value={'id':'xchannel','serviceId':'2032214868485685249','isQueuePaused':False}
        def xrequest(query,variables):
            if unknown:raise TimeoutError('隔離送信不明')
            return {'createPost':{'__typename':'PostActionSuccess','post':{'id':'new-x','text':variables['input']['text'],'status':'sent','sentAt':self.now.isoformat(),'externalLink':'https://x.com/2032214868485685249/status/456','channel':{'id':'xchannel','service':'twitter'}}}}
        api._request.side_effect=xrequest
        proof={'started_at':self.now.isoformat(),'observed_at':self.now.isoformat(),'checked_at':self.now.isoformat(),'expires_at':(self.now+timedelta(hours=1)).isoformat(),'started_monotonic':0,'observed_monotonic':0}
        def x():
            with patch('src.mina.buffer_publisher.production_client',return_value=api):
                return publish_due(f.store,Mock(),self.now,fixture.HOST,lambda c:proof,clock=lambda:self.now,timer=lambda:0)
        igapi=Mock()
        def igrequest(query,variables=None):
            if 'organizations' in query:return {'account':{'organizations':[{'id':'org'}]}}
            if 'channels(' in query:return {'channels':[{'id':'ichannel','serviceId':'iaccount','name':'log_eats','service':'instagram','isQueuePaused':False,'isDisconnected':False,'isLocked':False}]}
            request=variables['input'];return {'createPost':{'__typename':'PostActionSuccess','post':{'id':'new-ig','text':request['text'],'channel':{'id':'ichannel','service':'instagram'},'schedulingType':'automatic','metadata':request['metadata']['instagram'],'assets':[{'source':a['image']['url'],'mimeType':'image/jpeg'} for a in request['assets']],'status':'sent','sentAt':self.now.isoformat(),'externalLink':'https://www.instagram.com/p/daily/'}}}
        igapi._request.side_effect=igrequest
        def ig():
            r=f.ig.claim_due(self.now);self.assertEqual(r['slot'],'daily')
            with f.store.transaction() as db:
                db.execute('CREATE TABLE IF NOT EXISTS instagram_acceptance(channel_id TEXT,format TEXT,layout_version TEXT,PRIMARY KEY(channel_id,format,layout_version))');c=InstagramCandidate(**json.loads(r['payload']));db.execute('INSERT OR IGNORE INTO instagram_acceptance VALUES(?,?,?)',(c.channel_id,c.format,c.layout_version))
            return publish_once(f.ig,r,lambda c:late.verify_candidate(f.store,c),HOSTS,client=igapi,clock=lambda:self.now,verify_media=Mock())
        return x,ig,api,igapi
    def test_two_new_buttons_real_publishers_old_dinner_never_sent(self):
        tokens=self.stage();discord=Mock();self.assertEqual(send_notifications(self.f.store,discord),0);self.assertEqual(self.f.ig.send_notices(discord),0);self.assertEqual(self.discord.posts,2)
        self.f.store.decide(tokens[0],'human',self.now)
        with self.f.store.transaction() as db:self.assertTrue(held(db,late.TARGET));self.assertEqual(late.row(db)['state'],'approval_pending')
        self.f.ig.decide(tokens[1],'human',self.now)
        self.assertEqual(self.f.store.claim_due(self.now),[]);self.assertIsNone(self.f.ig.claim_due(self.now))
        x,ig,api,igapi=self.backend();self.assertEqual(late.execute(self.f.store,lambda:self.proof,x,ig,lambda:self.now),'complete')
        self.assertEqual(api._request.call_count,1);self.assertEqual(sum('createPost(' in c.args[0] for c in igapi._request.call_args_list),1)
        with self.f.store.transaction() as db:
            self.assertEqual(late.row(db)['state'],'complete');self.assertEqual(json.loads(late.row(db)['old_snapshot']),self.old)
            self.assertEqual(db.execute("SELECT state FROM post_promotion_phases WHERE phase='daily'").fetchone()[0],'held')
            self.assertEqual(db.execute("SELECT x_id FROM candidates WHERE slot='dinner'").fetchone()[0],'123')
        with self.assertRaises(ValueError):late.execute(self.f.store,lambda:self.proof,x,ig,lambda:self.now)
    def test_old_tokens_cannot_approve_new_revision_and_one_or_reject_keeps_hold(self):
        tokens=self.stage()
        with self.assertRaises(ValueError):self.f.store.decide(self.old_tokens[0][-1],'human',self.now)
        self.f.store.decide(tokens[0].replace('approve|','changes|'),'human',self.now)
        self.f.ig.decide(tokens[1],'human',self.now)
        with self.f.store.transaction() as db:self.assertTrue(held(db,late.TARGET))
        with self.assertRaises(ValueError):late.execute(self.f.store,Mock(),Mock(),Mock(),lambda:self.now)
    def test_expiry_does_not_reset_first_active_or_allow_reissue(self):
        tokens=self.stage();self.approve(tokens);self.now+=timedelta(minutes=16)
        with self.assertRaises(ValueError):late.execute(self.f.store,Mock(),Mock(),Mock(),lambda:self.now)
        with self.assertRaises(ValueError):self.stage()
    def test_first_send_unknown_stops_second_and_preserves_attempt(self):
        self.approve(self.stage());x,ig,api,igapi=self.backend(unknown=True)
        with self.assertRaises(ValueError):late.execute(self.f.store,lambda:self.proof,x,ig,lambda:self.now)
        self.assertEqual(api._request.call_count,1);igapi._request.assert_not_called()
        with self.f.store.transaction() as db:
            self.assertEqual(late.row(db)['state'],'held');self.assertEqual(db.execute("SELECT state FROM candidates WHERE slot='daily'").fetchone()[0],'outcome_unknown')
    def test_stale_source_or_external_cannot_stage(self):
        self.proof['external']['read_started_at']=(self.now-timedelta(minutes=3)).isoformat()
        with self.assertRaises(ValueError):self.stage()
    def test_changed_original_or_already_attempted_never_stages(self):
        with self.f.store.transaction() as db:db.execute("UPDATE buffer_publication SET attempted=1 WHERE slot='daily'")
        with self.assertRaises(ValueError):self.stage()
    def test_changed_epoch_or_attempt_unknown_blocks_authority(self):
        tokens=self.stage()
        with self.f.store.transaction() as db:db.execute('UPDATE publication_holds SET epoch=epoch+1')
        with self.assertRaises(ValueError):self.f.store.decide(tokens[0],'human',self.now)
        with self.f.store.transaction() as db:db.execute('UPDATE publication_holds SET epoch=epoch-1')
        self.approve(tokens)
        with self.f.store.transaction() as db:
            db.execute("UPDATE daily_late_jobs SET state='executing'");db.execute("UPDATE buffer_publication SET state='unknown' WHERE slot='daily'")
        with late.scope(self.a['case_uuid']),self.f.store.transaction() as db:self.assertFalse(late.clear(db,late.TARGET))
