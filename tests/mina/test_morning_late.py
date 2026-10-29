"""最新の直接指示だけを旧expiredから分離し、朝X/IGの各一回へ束縛する。"""
import copy,json,unittest,uuid
from dataclasses import asdict,replace
from datetime import datetime,timedelta
from unittest.mock import patch
import test_morning_preparation as fixture
from src.mina import morning_late as p,morning_registry as m
from src.mina.domain import Candidate,JST
from src.mina.instagram_domain import InstagramCandidate
from src.mina.preparation import fingerprint

class LateTests(unittest.TestCase):
 def setUp(self):
  self.f=fixture.CohortTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.store,self.ig=self.f.store,self.f.ig;self.now=datetime(2026,10,30,8,0,tzinfo=JST)
  patcher=patch('src.mina.morning_late.datetime',wraps=datetime);patcher.start().now.return_value=self.now;self.addCleanup(patcher.stop)
  with self.store.transaction() as db:
   r=db.execute("SELECT * FROM candidates WHERE slot='breakfast'").fetchone();c=Candidate(**json.loads(r['payload']));c=replace(c,share_url='https://www.log-eats.com/s/breakfast',approval_source={**c.approval_source,'request_key':str(uuid.uuid4()),'state_version':'sv1_'+'a'*64,'input_hash':'ih1_'+'b'*64,'result_sha256':'c'*64});c=replace(c,publish_text=c.source_content)
   db.execute("UPDATE candidates SET payload=?,digest=? WHERE slot='breakfast'",(json.dumps(asdict(c)),c.digest));db.execute("UPDATE morning_members SET digest=?,payload_digest=? WHERE platform='x' AND slot='breakfast'",(c.digest,fingerprint(asdict(c))))
   material={'xs':{v['slot']:json.loads(v['payload']) for v in db.execute('SELECT * FROM candidates')},'igs':{v['slot']:json.loads(v['payload']) for v in db.execute('SELECT * FROM instagram_candidates')},'bundle':{},'manifest':{}}
   db.execute("UPDATE morning_cohorts SET material=? WHERE cohort='meals'",(json.dumps(material),))
  self.f.sent('meals');self.f.approve('meals')
  with self.store.transaction() as db:
   for t in ('candidates','instagram_candidates'):db.execute('UPDATE '+t+" SET state='expired',nonce_hash=NULL WHERE slot='breakfast'")
   old={platform:dict(db.execute('SELECT * FROM '+t+" WHERE slot='breakfast'").fetchone()) for platform,t in (('x','candidates'),('instagram','instagram_candidates'))}
  self.config={'contract_version':p.VERSION,'request_uuid':str(uuid.uuid4()),'case_uuid':self.f.key,'target_date':m.TARGET,'slot':'breakfast','approval_method':'explicit_user_instruction','approval_reference':{'text':'朝食は早急に投稿して下さい','received_at':self.now.isoformat()},'not_after':p.END.isoformat(),'actor':'human','old_rows':old,'commit':'c'*40}
  self.inventory={'verified':True,'checked_at':self.now.timestamp(),'proof':{'complete':True}}
  self.protection=patch('src.mina.current_day_cli.protected_json',return_value=(self.config,'f'*64));self.protection.start();self.addCleanup(self.protection.stop)
 def register(self):return p.register(self.store,self.config,'sealed','f'*64,self.inventory,self.now)
 def test_direct_authority_preserves_expired_and_binds_same_contents_to_new_time(self):
  result=self.register();self.assertEqual(datetime.fromisoformat(result['publish_at']),self.now+timedelta(seconds=30))
  with self.store.transaction() as db:
   history=db.execute('SELECT * FROM morning_late_authorities').fetchone();self.assertEqual(json.loads(history['old_rows']),self.config['old_rows'])
   for platform,t in (('x','candidates'),('instagram','instagram_candidates')):
    row=dict(db.execute('SELECT * FROM '+t+" WHERE slot='breakfast'").fetchone());self.assertEqual(row['revision'],2);self.assertEqual(row['actor'],'human');self.assertEqual(row['approved'],self.now.timestamp());self.assertTrue(p.authorized(db,m.job(db,m.TARGET),platform,row))
    before=json.loads(self.config['old_rows'][platform]['payload']);after=json.loads(row['payload'])
    for k in (('comment','api_text','publish_text','image_sha256','share_url') if platform=='x' else ('caption','assets')):self.assertEqual(before[k],after[k])
   self.assertTrue(m.receipts(db,m.job(db,m.TARGET),'meals',only=('x','lunch')))
  with self.assertRaises(ValueError):self.register()
 def test_x_and_instagram_claim_once_at_same_new_time(self):
  result=self.register();due=datetime.fromisoformat(result['publish_at']);xs=self.store.claim_due(due);ig=self.ig.claim_due(due)
  self.assertEqual([r['slot'] for r in xs],['breakfast']);self.assertEqual(ig['slot'],'breakfast');self.assertEqual(xs[0]['scheduled'],ig['scheduled'])
  self.assertEqual(self.store.claim_due(due),[]);self.assertIsNone(self.ig.claim_due(due))
 def test_missing_old_receipt_or_changed_old_row_prevents_new_authority(self):
  with self.store.transaction() as db:db.execute("UPDATE routine_notice_receipts SET state='unknown' WHERE platform='x' AND notice_id=(SELECT id FROM notices WHERE slot='breakfast')")
  with self.assertRaises(ValueError):self.register()
  with self.store.transaction() as db:self.assertEqual(db.execute("SELECT state FROM candidates WHERE slot='breakfast'").fetchone()[0],'expired')
 def test_expired_inventory_or_end_never_enables_a_post(self):
  self.inventory['checked_at']-=121
  with self.assertRaises(ValueError):self.register()
  self.inventory['checked_at']=self.now.timestamp()
  with self.assertRaises(ValueError):p.register(self.store,self.config,'sealed','f'*64,self.inventory,p.END)
 def test_modified_authority_body_or_unknown_keeps_publication_stopped(self):
  result=self.register();due=datetime.fromisoformat(result['publish_at'])
  self.protection.stop();self.protection=patch('src.mina.current_day_cli.protected_json',return_value=(self.config,'e'*64));self.protection.start();self.addCleanup(self.protection.stop)
  self.assertEqual(self.store.claim_due(due),[])
 def test_real_instagram_publisher_uses_own_claim_and_never_retries_unknown(self):
  from src.mina.instagram_publisher import publish_once
  from src.mina.instagram_media import HOSTS
  from test_instagram import API
  result=self.register();due=datetime.fromisoformat(result['publish_at']);row=self.ig.claim_due(due);c=InstagramCandidate(**json.loads(row['payload']));api=API(c,fail=True)
  with self.store.transaction() as db:
   db.execute('CREATE TABLE instagram_acceptance(channel_id TEXT,format TEXT,layout_version TEXT)');db.execute('INSERT INTO instagram_acceptance VALUES(?,?,?)',(c.channel_id,c.format,c.layout_version))
  with patch.dict('os.environ',{'MINA_LIVE_WRITES':'true','MINA_INSTAGRAM_PUBLISH_ENABLED':'true'}):
   with self.assertRaises(Exception) as failure:publish_once(self.ig,row,lambda _:None,HOSTS,api,clock=lambda:due,verify_media=lambda _:True)
  if not api.mutations:raise failure.exception
  self.assertEqual(len(api.mutations),1);self.assertIsNone(self.ig.claim_due(due))
 def test_real_x_publisher_submits_only_the_new_direct_approval_once(self):
  from src.mina.buffer_publisher import bind_route,BufferPublisher
  result=self.register();due=datetime.fromisoformat(result['publish_at']);row=self.store.claim_due(due)[0];c=Candidate(**json.loads(row['payload']))
  with self.store.transaction() as db:
   bind_route(db,row,{'id':'channel','serviceId':'123'});route=dict(db.execute('SELECT * FROM buffer_publication').fetchone())
  class API:
   calls=0
   def _request(self,query,variables):
    self.calls+=1;return {'createPost':{'__typename':'PostActionSuccess','post':{'id':'buffer-one','text':c.content,'channel':{'id':'channel','service':'twitter'},'status':'sent','sentAt':due.isoformat(),'externalLink':'https://x.com/EatsLog88161/status/12345'}}}
  api=API();publisher=BufferPublisher(self.store,row,route,api,clock=lambda:due,timer=lambda:10)
  publisher.proof={k:due.isoformat() for k in ('started_at','checked_at','observed_at')};publisher.proof.update(started_monotonic=10,expires_at=(due+timedelta(hours=1)).isoformat())
  with patch.dict('os.environ',{'MINA_LIVE_WRITES':'true','MINA_PUBLISH_ENABLED':'true'}):self.assertEqual(publisher.publish(c.content),'12345')
  self.store.finish(row,'12345',due);self.assertEqual(api.calls,1);self.assertEqual(self.store.claim_due(due),[])

if __name__=='__main__':unittest.main()
