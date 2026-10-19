"""保存済み朝食を再送せず、読取失敗の原記録を保持して当日準備を続ける。"""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from datetime import datetime
from src.mina.domain import JST
from src.mina.store import Store
from src.mina.registration import register_day
from src.mina.daily_pipeline import resume_today_read_failure
import test_registration as fixtures
from test_readiness import evidence
TARGET='2026-10-08';NOW=datetime(2026,10,8,9,30,tzinfo=JST)
class RecoveryTest(unittest.TestCase):
 def test_saved_breakfast_is_not_resent_and_old_unknown_is_kept(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);store=Store(root/'db');helper=fixtures.RegistrationTests()
   draft,saved=helper.setup_inputs(root);client=helper.client();ai=Mock()
   ai.review_meal.return_value=ai.review_ogp.return_value={'status':'publishable','reason_codes':[]}
   state_call=client.day_state.side_effect;request_call=client.request_state.side_effect
   def state(target):
    value=state_call(target);value['checked_at']=NOW.isoformat();return value
   def request(key,op):
    value=request_call(key,op);value['current_state_version']=state(TARGET)['state_version'];return value
   client.day_state.side_effect=state;client.request_state.side_effect=request
   class Clock(datetime):
    @classmethod
    def now(cls,tz=None):return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
   with patch('src.mina.registration.datetime',Clock):
    with self.assertRaises(ValueError):
     register_day(store,draft,saved,root/'old-ogp',client,ai,'www.log-eats.com',capture=Mock(side_effect=ValueError('読取停止')))
   self.assertEqual(client.register.call_count,1)
   store.claim_preparation(TARGET,NOW)
   with store.transaction() as db:
    db.execute("UPDATE preparation SET state='held:registration'")
    db.execute("INSERT INTO publication_exclusions VALUES(?,'breakfast','本人指定')",(TARGET,))
   base=root/'daily'/TARGET;base.mkdir(parents=True)
   (base/'draft.json').write_text(json.dumps(draft));(base/'drive.json').write_text(json.dumps(saved))
   history=root/'history';history.mkdir();(history/'proof.json').write_text(json.dumps(evidence()))
   def capture(url,host):return {'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':b'preview'}
   with patch('src.mina.daily_pipeline.release_commit',return_value='e'*40),patch('src.mina.registration.datetime',Clock),patch('src.mina.ogp.capture_ogp',side_effect=capture),patch('src.mina.registration.capture_ogp',side_effect=capture):
    # register_dayの既定引数には固定関数があるため、明示的に同じ注入点へ渡す。
    original=register_day
    def wrapped(*args,**kwargs):return original(*args,**kwargs,capture=capture)
    with patch('src.mina.daily_pipeline.register_day',side_effect=wrapped):
     result=resume_today_read_failure(store,root/'daily','e'*40,root,'www.log-eats.com',ai,client,'22222222-2222-4222-8222-222222222222',clock=lambda:NOW,history_directory=history)
   self.assertEqual(result['post_count'],3)
   self.assertEqual(client.register.call_count,3)
   self.assertEqual(client.json_write.call_count,1)
   self.assertIsNone(store.get(TARGET,'breakfast'))
   with store.transaction() as db:
    self.assertEqual(db.execute("SELECT state FROM preparation_steps WHERE step='ogp_capture:breakfast'").fetchone()[0],'outcome_unknown')
