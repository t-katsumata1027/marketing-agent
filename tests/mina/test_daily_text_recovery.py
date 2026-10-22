"""日次コメント更新の一度送信、保存後GET復旧、食品と画像の不変を検証する。"""
import copy,json,tempfile,unittest,uuid
from pathlib import Path
from datetime import datetime,UTC
from unittest.mock import Mock,patch
from tests.mina import test_registration as fixtures
from src.mina.registration import register_day
from src.mina.store import Store
from src.mina.daily_text_recovery import repair_daily_text
from src.mina.x_text import weighted_length
from src.mina.clients import OutcomeUnknown

class DailyTextRecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  fixture=fixtures.RegistrationTests();self.draft,self.saved=fixture.setup_inputs(self.root)
  self.draft['daily_comment']='一日の献立の組み合わせを紹介します。'*5
  self.store=Store(self.root/'db');self.client=fixture.client();self.ai=Mock()
  self.ai.review_meal.return_value=self.ai.review_ogp.return_value={'status':'publishable','reason_codes':[]}
  self.ai.structured.return_value={'comment':'主食とおかずを組み合わせた献立案です。'}
  self.capture=lambda url,host:{'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':b'preview'}
  self.now=datetime(2026,10,7,10,tzinfo=UTC)
  self.reviewed=register_day(self.store,self.draft,self.saved,self.root/'ogp',self.client,self.ai,'www.log-eats.com',capture=self.capture)
  current='sv1_'+'3'*64
  previous_lookup=self.client.request_state.side_effect
  self.client.request_state.side_effect=lambda key,op:{**previous_lookup(key,op),'current_state_version':current}
  saved_state=self.client.day_state('2026-10-08')
  self.client.day_state.side_effect=lambda target:{**copy.deepcopy(saved_state),'checked_at':self.now.isoformat()}
  self.key=str(uuid.uuid4())
  self.flags=patch.dict('os.environ',{'MINA_LIVE_WRITES':'true'});self.flags.start();self.addCleanup(self.flags.stop)
 def repair(self):
  return repair_daily_text(self.store,self.reviewed,self.saved,self.client,self.ai,'www.log-eats.com',lambda:self.now,self.capture,self.key,'a'*40)
 def test_daily_is_updated_once_and_repeat_uses_get_without_food_or_image_writes(self):
  before=copy.deepcopy(self.reviewed['posts']);registered=self.client.register.call_count
  fixed=self.repair();self.assertLessEqual(weighted_length(fixed['posts']['daily']['publish_text']),280)
  self.assertEqual(self.client.json_write.call_count,2);self.assertEqual(self.client.register.call_count,registered)
  for s in ('breakfast','lunch','dinner'):self.assertEqual(before[s],fixed['posts'][s])
  again=self.repair();self.assertEqual(fixed,again);self.assertEqual(self.client.json_write.call_count,2)
  with self.store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM mina_requests').fetchone()[0],5)
 def test_response_loss_is_reconciled_by_original_uuid_without_another_post(self):
  original=self.client.json_write.side_effect
  def lost(*a,**kw):original(*a,**kw);raise TimeoutError()
  self.client.json_write.side_effect=lost
  with self.assertRaises(OutcomeUnknown):self.repair()
  count=self.client.json_write.call_count
  self.client.json_write.side_effect=original
  self.assertLessEqual(weighted_length(self.repair()['posts']['daily']['publish_text']),280)
  self.assertEqual(self.client.json_write.call_count,count)
 def test_new_round_cannot_reset_existing_request_and_review_hold_cannot_publish(self):
  self.ai.review_ogp.return_value={'status':'hold','reason_codes':['share_ogp_mismatch']}
  with self.assertRaises(ValueError):self.repair()
  calls=self.client.json_write.call_count;self.key=str(uuid.uuid4())
  with self.assertRaises(ValueError):self.repair()
  self.assertEqual(self.client.json_write.call_count,calls);self.assertEqual(self.store.list_day('2026-10-08'),[])
 def test_changed_source_and_deadline_stop_before_daily_update(self):
  self.client.request_state.side_effect=ValueError('changed')
  with self.assertRaises(ValueError):self.repair()
  self.assertEqual(self.client.json_write.call_count,1)
  self.now=datetime(2026,10,7,11,tzinfo=UTC)
  with self.assertRaises(ValueError):self.repair()
 def test_competing_round_is_rejected_before_first_request_marker_exists(self):
  original=self.client.request_state.side_effect
  entered=[]
  def interleave(key,op):
   if not entered:
    entered.append(True)
    with self.store.transaction() as db:
     self.assertIsNone(db.execute("SELECT 1 FROM preparation_steps WHERE step LIKE 'daily_text_request:%'").fetchone())
    other=str(uuid.uuid4())
    with self.assertRaisesRegex(ValueError,'実行ID'):
     repair_daily_text(self.store,self.reviewed,self.saved,self.client,self.ai,'www.log-eats.com',lambda:self.now,self.capture,other,'a'*40)
    changed=copy.deepcopy(self.reviewed);changed['posts']['daily']['comment']+='変更'
    with self.assertRaisesRegex(ValueError,'元入力'):
     repair_daily_text(self.store,changed,self.saved,self.client,self.ai,'www.log-eats.com',lambda:self.now,self.capture,self.key,'a'*40)
   return original(key,op)
  self.client.request_state.side_effect=interleave
  self.repair();self.assertTrue(entered);self.assertEqual(self.client.json_write.call_count,2)
