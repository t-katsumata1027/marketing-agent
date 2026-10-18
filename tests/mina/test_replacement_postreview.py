"""C保存後の4投稿点検を実検証器で通す。外部AI・業務API・OGPだけを合成に置換する。"""
import copy,hashlib,tempfile,unittest,uuid
from pathlib import Path
from src.mina.store import Store
from src.mina.request_ledger import RequestLedger
from src.mina.replacement_postreview import review_posts
from src.mina.domain import slot_time,date
from nightly_fakes import NightlyAI,NightlyMeal
from test_nightly_flow import NOW,TARGET,HOST
class PostreviewTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(Path(self.tmp.name)/'db');self.ledger=RequestLedger(self.store)
  self.clock=[NOW];self.ai=NightlyAI('plain',self.clock);self.meal=NightlyMeal('plain',self.clock,HOST);self.images={s:b'\xff\xd8\xffsynthetic-'+s.encode() for s in ('breakfast','lunch','dinner')}
  self.keys={}
  for slot in self.images:
   key=str(uuid.uuid4());self.keys[slot]=key;comment=self.ai.plan['meals'][slot]['comment']
   self.meal.register(self.images[slot],'fixture.jpg',slot,slot_time(date.fromisoformat(TARGET),slot).isoformat(),comment,key,self.meal.sv)
   self.ledger.prepare(key,'meal_register',TARGET,slot,{});self.ledger.record(key,'complete',{'persistence':'saved'})
  daily_key=str(uuid.uuid4());self.keys['daily']=daily_key
  self.daily=self.meal.json_write('/api/internal/mina/daily-share',{'date':TARGET,'comment':self.ai.plan['daily_comment'],'expected_state_version':self.meal.sv},request_key=daily_key)['result']
  self.ledger.prepare(daily_key,'daily_share',TARGET,'daily',{});self.ledger.record(daily_key,'complete',{'persistence':'saved'})
  before=self.meal.day_state
  def state(target):
   v=before(target);v['daily_share']=copy.deepcopy(self.daily);return v
  self.meal.day_state=state;self.c=str(uuid.uuid4());r=copy.deepcopy(self.meal.meals['dinner'])
  self.response={'request':{'request_uuid':self.c,'persistence':'saved','status':'succeeded','input_hash':'ih1_'+'a'*64},
   'result_status':'unchanged','current_state_version':self.meal.sv,'result':r,'current_result':r,
   'affected_daily_result':self.daily,'current_daily_result':self.daily}
 def run_review(self,capture=None):
  def default(url,host):return {'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':b'\xff\xd8\xffpreview'}
  return review_posts(self.store,self.meal,self.ai,capture or default,TARGET,self.c,self.response,self.ai.plan,self.images,HOST,lambda:self.clock[0])
 def test_saved_c_and_original_m1_make_four_freshly_reviewed_posts(self):
  posts,proof=self.run_review();self.assertEqual(set(posts['posts']),{'breakfast','lunch','dinner','daily'})
  self.assertEqual(posts['posts']['dinner']['approval_source']['contract_version'],'mina-replace-v1')
  self.assertNotIn('contract_version',posts['posts']['breakfast']['approval_source'])
  self.assertEqual(proof['verification'],'current-source-uuid-ogp-v1');self.assertEqual(proof['verdict'],'pass')
 def test_daily_created_by_m1_is_not_misrepresented_as_c_saved_daily(self):
  self.response['affected_daily_result']=None;self.response['current_daily_result']=None
  posts,_=self.run_review();self.assertEqual(posts['posts']['daily']['approval_source']['operation'],'daily_share')
  self.assertEqual(posts['posts']['daily']['approval_source']['request_key'],self.keys['daily'])
 def test_ogp_byte_mismatch_is_not_hidden_by_positive_ai(self):
  def altered(url,host):return {'meta':{'og:image':url+'/og-image','image_sha256':'0'*64},'digest':'d'*64,'review_image':b'\xff\xd8\xffwrong'}
  with self.assertRaises(ValueError):self.run_review(altered)
 def test_changed_current_state_and_independent_hold_remain_stopped(self):
  self.response['current_state_version']='sv1_'+'f'*64
  with self.assertRaises(ValueError):self.run_review()
  self.response['current_state_version']=self.meal.sv;self.ai.review_meal=lambda *a:{'status':'hold','reason_codes':['image_analysis_mismatch']}
  with self.assertRaisesRegex(ValueError,'saved_food_hold'):self.run_review()
if __name__=='__main__':unittest.main()
