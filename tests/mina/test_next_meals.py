"""日次holdを保持し、六実承認の個別leaseだけで公開できることを検証する。"""
import json,tempfile,unittest,uuid
from datetime import datetime,timedelta
from pathlib import Path
from dataclasses import replace,asdict
from unittest.mock import patch
from src.mina import next_meals as p
from src.mina.domain import JST
from src.mina.store import Store
from src.mina.instagram_store import InstagramStore
from src.mina.request_ledger import RequestLedger
from src.mina.preparation import PreparationJournal,fingerprint
from src.mina.next_saved import read_rows
from src.mina.publication_hold import held,require_clear,require_claim,require_buffer_mutation,require_m1_write
from src.mina.next_meals_cli import send
from test_daily import candidate as xfixture,HOST
from test_instagram import candidate as igfixture
from test_routine_pipeline import Discord

class MealsOnlyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(Path(self.tmp.name)/'db');self.ig=InstagramStore(self.store.path);RequestLedger(self.store);PreparationJournal(self.store);self.now=datetime(2026,10,30,0,5,tzinfo=JST);self.key=str(uuid.uuid4())
  for module in ('src.mina.next_meals.datetime','src.mina.next_meals_cli.datetime'):
   patcher=patch(module);patcher.start().now.return_value=self.now;self.addCleanup(patcher.stop)
  self.xs=[];self.igs=[]
  for slot in p.SLOTS:
   c=xfixture(slot);self.xs.append(replace(c,target_date=p.TARGET,publish_text=c.comment+'\n\n'+c.api_text+'\n'+c.share_url,approval_source={**(c.approval_source or {}),'state_version':'sv1_'+'a'*64,p.KEY:p.marker(self.key)}))
  for slot in p.SLOTS:
   c=igfixture(slot);self.igs.append(replace(c,target_date=p.TARGET,source={'bundle_sha256':'b'*64,'state_version':'sv1_'+'a'*64,'x_digests':{x.slot:x.digest for x in self.xs},p.KEY:p.marker(self.key)}))
  with self.store.transaction() as db:
   db.execute('CREATE TABLE publication_holds(target_date TEXT PRIMARY KEY,epoch INTEGER,owner TEXT,state TEXT,created REAL,reason TEXT)');db.execute("INSERT INTO publication_holds VALUES(?,1,'original-owner','held',0,'daily_ogp_hold')",(p.TARGET,));hold=dict(db.execute('SELECT * FROM publication_holds').fetchone())
   db.execute('CREATE TABLE next_saved_jobs(target_date TEXT,case_uuid TEXT,state TEXT)');db.execute("INSERT INTO next_saved_jobs VALUES(?,'original-case','held')",(p.TARGET,))
   origin={'hold':hold,'parent_case':'original-case','files':{},'requests_digest':fingerprint(read_rows(db,'mina_requests')),'steps_digest':fingerprint(read_rows(db,'preparation_steps'))};members=[]
   for platform,cs in (('x',self.xs),('instagram',self.igs)):
    table='candidates' if platform=='x' else 'instagram_candidates'
    for c in cs:
     raw=asdict(c);db.execute('INSERT INTO '+table+"(target_date,slot,revision,payload,digest,scheduled,state) VALUES(?,?,1,?,?,?,'approval_pending')",(p.TARGET,c.slot,json.dumps(raw,ensure_ascii=False),c.digest,c.scheduled_at.timestamp()));members.append({'platform':platform,'slot':c.slot,'revision':1,'digest':c.digest,'payload_digest':fingerprint(raw)})
   db.execute('CREATE TABLE next_meal_jobs(target_date TEXT PRIMARY KEY,case_uuid TEXT,state TEXT,origin TEXT,members TEXT,deadline REAL,material TEXT)');db.execute("INSERT INTO next_meal_jobs VALUES(?,?,'approval_pending',?,?,?,NULL)",(p.TARGET,self.key,json.dumps(origin),json.dumps(members),(self.now+timedelta(minutes=20)).timestamp()))
  self.store.issue_cards(p.TARGET,self.now)
  for row in self.ig.rows(p.TARGET):self.ig.queue_approval(row)
 def sent(self):
  client=Discord();send(self.store,client,lambda:None,(self.now+timedelta(minutes=20)).timestamp());self.assertEqual(client.posts,6);return client
 def approve(self):
  with self.store.transaction() as db:tokens=[(platform,json.loads(r['payload'])['components'][0]['components'][0]['custom_id']) for platform,table in (('x','notices'),('instagram','instagram_notices')) for r in db.execute('SELECT * FROM '+table+' WHERE target_date=?',(p.TARGET,))]
  for platform,token in tokens:(self.store if platform=='x' else self.ig).decide(token,'human',self.now)
 def test_six_receipts_and_decisions_allow_exact_lease_but_keep_day_hold(self):
  self.sent();self.approve()
  with self.store.transaction() as db:
   self.assertTrue(held(db,p.TARGET));self.assertEqual(p.job(db,p.TARGET)['state'],'ready')
   with self.assertRaises(ValueError):require_clear(db,p.TARGET)
   with self.assertRaises(ValueError):require_m1_write(db,p.TARGET,'daily_share')
  due=self.xs[0].scheduled_at;rows=self.store.claim_due(due);self.assertEqual([r['slot'] for r in rows],['breakfast']);row=rows[0]
  require_claim(self.store,row);require_buffer_mutation(self.store,p.TARGET,('x',row))
  with self.assertRaises(ValueError):require_buffer_mutation(self.store,p.TARGET)
  with self.assertRaises(ValueError):require_buffer_mutation(self.store,p.TARGET,('instagram',row))
  with self.assertRaises(ValueError):require_buffer_mutation(self.store,p.TARGET,('x',{**row,'slot':'daily'}))
  from src.mina.buffer_publisher import bind_route
  with self.store.transaction() as db:bind_route(db,row,{'id':'channel','serviceId':'123'})
  igrow=self.ig.claim_due(due);self.assertEqual(igrow['slot'],'breakfast');self.ig.begin(igrow,{'text':'isolated'},due);require_claim(self.ig,igrow,'instagram_candidates')
 def test_six_tokens_without_six_receipts_do_not_enable_publication(self):
  self.approve()
  with self.store.transaction() as db:self.assertEqual(p.job(db,p.TARGET)['state'],'approval_pending')
  self.assertEqual(self.store.claim_due(self.xs[0].scheduled_at),[])
 def test_one_unknown_blocks_remaining_members_and_does_not_release_day(self):
  self.sent();self.approve()
  with self.store.transaction() as db:db.execute("UPDATE instagram_candidates SET state='outcome_unknown' WHERE slot='breakfast'")
  self.assertEqual(self.store.claim_due(self.xs[1].scheduled_at),[])
  with self.store.transaction() as db:self.assertTrue(held(db,p.TARGET))
 def test_owner_or_payload_mutation_blocks_real_lease(self):
  self.sent();self.approve()
  with self.store.transaction() as db:db.execute("UPDATE publication_holds SET epoch=2")
  self.assertEqual(self.store.claim_due(self.xs[0].scheduled_at),[])
 def test_lost_notice_is_not_reposted_or_ready(self):
  client=Discord();original=client.send
  def lost(wire):original(wire);raise TimeoutError('隔離応答喪失')
  client.send=lost
  with self.assertRaises(TimeoutError):send(self.store,client,lambda:None,(self.now+timedelta(minutes=20)).timestamp())
  self.assertEqual(client.posts,1)
  with self.store.transaction() as db:self.assertFalse(p.receipts(db,p.job(db,p.TARGET)))
 def test_daily_instagram_marker_is_rejected(self):
  with self.assertRaises(ValueError):replace(self.igs[0],slot='daily',format='post').validate(self.now,('drive.google.com',))
 def test_real_buffer_publisher_propagates_member_lease_through_create(self):
  self.sent();self.approve();c=self.xs[0];due=c.scheduled_at;row=self.store.claim_due(due)[0]
  from src.mina.buffer_publisher import bind_route,BufferPublisher
  with self.store.transaction() as db:
   bind_route(db,row,{'id':'channel','serviceId':'123'})
   route=dict(db.execute('SELECT * FROM buffer_publication').fetchone())
  outer=self
  class API:
   calls=0
   def _request(self,query,variables):
    self.calls+=1;require_buffer_mutation(outer.store,p.TARGET,('x',row));return {'createPost':{'__typename':'PostActionSuccess','post':{'id':'buffer-one','text':c.content,'channel':{'id':'channel','service':'twitter'},'status':'sent','sentAt':due.isoformat(),'externalLink':'https://x.com/EatsLog88161/status/12345'}}}
  api=API();publisher=BufferPublisher(self.store,row,route,api,clock=lambda:due,timer=lambda:10)
  publisher.proof={k:due.isoformat() for k in ('started_at','checked_at','observed_at')};publisher.proof.update(started_monotonic=10,expires_at=(due+timedelta(hours=1)).isoformat())
  with patch.dict('os.environ',{'MINA_LIVE_WRITES':'true','MINA_PUBLISH_ENABLED':'true'}):self.assertEqual(publisher.publish(c.content),'12345')
  self.assertEqual(api.calls,1);self.store.finish(row,'12345',due)
  self.assertEqual(len(self.store.claim_due(self.xs[1].scheduled_at)),1)
 def test_attempt_unknown_blocks_even_when_candidate_is_still_approved(self):
  self.sent();self.approve()
  with self.store.transaction() as db:db.execute("INSERT INTO buffer_publication(target_date,slot,revision,digest,channel_id,service_id,state) VALUES(?,?,1,?,'channel','123','outcome_unknown')",(p.TARGET,'breakfast',self.xs[0].digest))
  self.assertEqual(self.store.claim_due(self.xs[1].scheduled_at),[])

if __name__=='__main__':unittest.main()
