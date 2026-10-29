"""共有画像の保留を残した添付画像と、六件・二件の独立承認を検証する。"""
import copy,hashlib,json,tempfile,unittest,uuid
from dataclasses import asdict,replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from src.mina import morning_registry as m,native_media as n
from src.mina.domain import JST
from src.mina.store import Store
from src.mina.instagram_store import InstagramStore
from src.mina.request_ledger import RequestLedger
from src.mina.preparation import PreparationJournal,fingerprint
from src.mina.publication_hold import held,require_clear
from src.mina.morning_cli import send_cohort
from test_daily import candidate as xf
from test_instagram import candidate as igf
from test_routine_pipeline import Discord

def native(slot='breakfast'):
 c=replace(xf(slot),target_date=m.TARGET,ogp_verified=False);c=replace(c,publish_text=c.source_content)
 source={'state_version':'sv1_'+'1'*64,'result_version':'rv1_'+'2'*64,'request_key':str(uuid.uuid4()),'input_hash':'ih1_'+'3'*64,'result_sha256':'4'*64,'ogp_revision':'version1','ogp_image_sha256':'b'*64}
 images={s:'a'*64 for s in m.MEALS};c=replace(c,image_sha256=fingerprint(images) if slot=='daily' else 'a'*64)
 source[n.KEY]={'contract_version':n.VERSION,'source_images':images,**{k:source[k] for k in ('state_version','result_version','request_key','input_hash','result_sha256')},'body_sha256':hashlib.sha256(c.source_content.encode()).hexdigest(),
 'asset':{'path':'/immutable/image.jpg','sha256':'c'*64,'role':'x_native_'+slot,'width':1200,'height':630,'mime_type':'image/jpeg','size':1000,'url':'https://drive.google.com/uc?export=download&id=fixture'},
 'review':{'status':'publishable','context_digest':'d'*64,'image_sha256':'c'*64,'source_images':images,'receipt_sha256':'e'*64},'legacy_ogp':{'state':'hold','reason_codes':['share_ogp_mismatch'],'revision':'version1','image_sha256':'b'*64}}
 return replace(c,approval_source=source)

class NativeTests(unittest.TestCase):
 def test_contract_never_turns_ogp_hold_into_pass(self):
  c=native();self.assertEqual(n.validate(c)['legacy_ogp']['state'],'hold');self.assertFalse(c.ogp_verified)
  for changed in (replace(c,ogp_verified=True),replace(c,publish_text='別本文'),replace(c,image_sha256='f'*64),replace(c,target_date='2026-10-31')):
   with self.assertRaises(ValueError):n.validate(changed)
 def test_missing_reordered_or_proxy_asset_is_not_accepted(self):
  c=native();asset={'source':n.validate(c)['asset']['url'],'mimeType':'image/jpeg'};self.assertTrue(n.check_post(c,{'assets':[asset]}))
  for assets in ([],[asset,asset],[{**asset,'source':'https://cdn.example/other.jpg'}],[{**asset,'mimeType':'image/png'}]):
   with self.assertRaises(ValueError):n.check_post(c,{'assets':assets})
 def test_short_and_uuid_html_routes_are_allowed_but_wrong_revision_rejected(self):
  c=native();image='https://logeats.test/s/breakfast/og-image?v=version1';result={'ogp':{'image_url':image,'revision':'version1'}}
  for alias in (image,'https://logeats.test/share/12345678-1234-1234-1234-123456789abc/og-image?v=version1'):
   with patch.object(n,'link_snapshot',return_value={'meta':{'og:image':alias},'page_url':c.share_url,'html_sha256':'f'*64}):self.assertEqual(n.verify_link(c,'logeats.test',result)['delivery'],'native_image')
  with patch.object(n,'link_snapshot',return_value={'meta':{'og:image':image.replace('version1','other')},'page_url':c.share_url,'html_sha256':'f'*64}):
   with self.assertRaises(ValueError):n.verify_link(c,'logeats.test',result)

class CohortTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(Path(self.tmp.name)/'db');self.ig=InstagramStore(self.store.path);RequestLedger(self.store);PreparationJournal(self.store);self.key=str(uuid.uuid4());self.now=datetime(2026,10,30,1,15,tzinfo=JST)
  p=patch('src.mina.morning_registry.datetime');p.start().now.return_value=self.now;self.addCleanup(p.stop)
  with self.store.transaction() as db:
   m.initialize(db);db.execute('CREATE TABLE publication_holds(target_date TEXT PRIMARY KEY,epoch INTEGER,owner TEXT,state TEXT)');db.execute("INSERT INTO publication_holds VALUES(?,1,'owner','held')",(m.TARGET,));hold=dict(db.execute('SELECT * FROM publication_holds').fetchone());parents={}
   for table in ('next_saved_jobs','next_meal_jobs'):
    db.execute('CREATE TABLE '+table+'(target_date TEXT,case_uuid TEXT,state TEXT,first_started REAL,deadline REAL)');db.execute('INSERT INTO '+table+" VALUES(?,?,'held',1,2)",(m.TARGET,str(uuid.uuid4())));r=dict(db.execute('SELECT * FROM '+table).fetchone());parents[table]={**{k:r[k] for k in ('case_uuid','first_started','deadline')},'record_digest':fingerprint(r)}
   origin={'hold':hold,'parents':parents,'files':{},'discord_channel':'1234','mina_requests_digest':fingerprint(m.read_rows(db,'mina_requests')),'preparation_steps_digest':fingerprint(m.read_rows(db,'preparation_steps')),'scoped_preparation_locks':None}
   db.execute("INSERT INTO morning_preparations VALUES(?,?,?,'config','sha',?,'partial',1,?)",(m.TARGET,self.key,'c'*40,json.dumps(origin),self.now.timestamp()+3600))
   self.xs=[]
   for cohort,slots in m.COHORTS.items():
    db.execute("INSERT INTO morning_cohorts VALUES(?,?,'staged',NULL)",(self.key,cohort))
    for slot in slots:
     x=replace(xf(slot),target_date=m.TARGET,approval_source={m.KEY:m.marker(self.key,slot)});self.xs.append(x)
     ig=replace(igf(slot),target_date=m.TARGET,source={'bundle_sha256':'b'*64,'state_version':'sv1_'+'a'*64,'x_digests':{s:'a'*64 for s in (m.MEALS if slot!='daily' else (*m.MEALS,'daily'))},m.KEY:m.marker(self.key,slot)})
     for platform,c,table in (('x',x,'candidates'),('instagram',ig,'instagram_candidates')):
      payload=asdict(c);db.execute('INSERT INTO '+table+"(target_date,slot,revision,payload,digest,scheduled,state) VALUES(?,?,1,?,?,?,'approval_pending')",(m.TARGET,slot,json.dumps(payload),c.digest,c.scheduled_at.timestamp()));db.execute('INSERT INTO morning_members VALUES(?,?,?,?,1,?,?)',(self.key,platform,slot,cohort,c.digest,fingerprint(payload)))
  self.run=type('Run',(),{'store':self.store,'clock':lambda _s:self.now,'guard':lambda _s:None,'key':self.key})()
 def sent(self,cohort):
  if not hasattr(self,'discord'):self.discord=Discord()
  send_cohort(self.run,self.ig,self.discord,cohort)
 def approve(self,cohort):
  with self.store.transaction() as db:tokens=[(platform,json.loads(r['payload'])['components'][0]['components'][0]['custom_id']) for platform,table in (('x','notices'),('instagram','instagram_notices')) for r in db.execute('SELECT * FROM '+table) if r['slot'] in m.COHORTS[cohort]]
  for platform,token in tokens:(self.store if platform=='x' else self.ig).decide(token,'human',self.now)
 def test_meals_six_ready_without_daily_and_day_hold_stays(self):
  self.sent('meals');self.assertEqual(self.discord.posts,6);self.approve('meals')
  with self.store.transaction() as db:
   states={r['cohort']:r['state'] for r in db.execute('SELECT * FROM morning_cohorts')};self.assertEqual(states,{'meals':'ready','daily':'staged'});self.assertTrue(held(db,m.TARGET))
   with self.assertRaises(ValueError):require_clear(db,m.TARGET)
  self.assertEqual([r['slot'] for r in self.store.claim_due(self.xs[0].scheduled_at)],['breakfast'])
  self.sent('daily');self.assertEqual(self.discord.posts,8)
  with self.store.transaction() as db:self.assertTrue(all(m.receipts(db,m.job(db,m.TARGET),c) for c in m.COHORTS))
 def test_cancel_or_missing_receipt_never_counts_as_all_approved(self):
  self.sent('meals');self.approve('meals')
  with self.store.transaction() as db:db.execute("UPDATE candidates SET state='cancelled' WHERE slot='lunch'")
  self.assertEqual(self.store.claim_due(self.xs[0].scheduled_at),[])
 def test_unknown_blocks_only_affected_cohort(self):
  self.sent('meals');self.approve('meals');self.sent('daily');self.approve('daily')
  with self.store.transaction() as db:db.execute("UPDATE instagram_candidates SET state='outcome_unknown' WHERE slot='daily'")
  self.assertEqual(len(self.store.claim_due(self.xs[0].scheduled_at)),1)
 def test_approved_expired_breakfast_never_reposts_but_lunch_can_proceed(self):
  self.sent('meals');self.approve('meals')
  with self.store.transaction() as db:
   for table in ('candidates','instagram_candidates'):db.execute('UPDATE '+table+" SET state='expired',nonce_hash=NULL WHERE slot='breakfast'")
  self.assertEqual(self.store.claim_due(self.xs[0].scheduled_at),[])
  self.assertEqual([v['slot'] for v in self.store.claim_due(self.xs[1].scheduled_at)],['lunch'])
 def test_unapproved_expired_breakfast_never_enables_lunch(self):
  self.sent('meals');self.approve('meals')
  with self.store.transaction() as db:db.execute("UPDATE candidates SET state='expired',actor=NULL,approved=NULL,nonce_hash=NULL WHERE slot='breakfast'")
  self.assertEqual(self.store.claim_due(self.xs[1].scheduled_at),[])
 def test_old_parent_or_receipt_binding_mutation_stops_claim(self):
  self.sent('meals');self.approve('meals')
  with self.store.transaction() as db:db.execute("UPDATE next_saved_jobs SET deadline=3")
  self.assertEqual(self.store.claim_due(self.xs[0].scheduled_at),[])
 def test_lost_discord_post_keeps_unknown_and_never_reposts(self):
  self.discord=Discord();original=self.discord.send
  def lost(wire):original(wire);raise TimeoutError('応答喪失')
  self.discord.send=lost
  with self.assertRaises(TimeoutError):self.sent('meals')
  self.assertEqual(self.discord.posts,1)
  with self.assertRaises(ValueError):self.sent('meals')
  self.assertEqual(self.discord.posts,1)
 def test_native_buffer_one_create_and_same_id_get_are_required(self):
  c=native();c=replace(c,approval_source={**c.approval_source,m.KEY:m.marker(self.key,'breakfast')})
  with self.store.transaction() as db:
   db.execute('UPDATE candidates SET payload=?,digest=? WHERE slot=?',(json.dumps(asdict(c)),c.digest,c.slot))
   db.execute("UPDATE morning_members SET digest=?,payload_digest=? WHERE platform='x' AND slot=?",(c.digest,fingerprint(asdict(c)),c.slot))
  self.sent('meals');self.approve('meals');due=c.scheduled_at;row=self.store.claim_due(due)[0]
  from src.mina.buffer_publisher import bind_route,BufferPublisher
  with self.store.transaction() as db:
   bind_route(db,row,{'id':'channel','serviceId':'123'});route=dict(db.execute('SELECT * FROM buffer_publication').fetchone())
  post={'id':'buffer-one','text':c.content,'channel':{'id':'channel','service':'twitter'},'status':'sent','sentAt':due.isoformat(),'externalLink':'https://x.com/EatsLog88161/status/12345','schedulingType':'automatic','assets':[{'source':n.validate(c)['asset']['url'],'mimeType':'image/jpeg'}]}
  class API:
   calls=0;gets=[]
   def _request(self,query,variables):
    self.calls+=1
    if variables['input']['assets']!=n.assets(c):raise AssertionError('画像の送信元が不一致')
    return {'createPost':{'__typename':'PostActionSuccess','post':post}}
   def post(self,key):self.gets.append(key);return copy.deepcopy(post)
  api=API();publisher=BufferPublisher(self.store,row,route,api,clock=lambda:due,timer=lambda:10)
  from datetime import timedelta
  publisher.proof={k:due.isoformat() for k in ('started_at','checked_at','observed_at')};publisher.proof.update(started_monotonic=10,expires_at=(due+timedelta(hours=1)).isoformat(),native_image_sha256=n.validate(c)['asset']['sha256'])
  with patch.dict('os.environ',{'MINA_LIVE_WRITES':'true','MINA_PUBLISH_ENABLED':'true'}):self.assertEqual(publisher.publish(c.content),'12345')
  self.assertEqual(api.calls,1);self.assertEqual(api.gets,['buffer-one'])

if __name__=='__main__':unittest.main()
