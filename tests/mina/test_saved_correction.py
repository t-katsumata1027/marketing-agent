"""PATCH応答の確定後は旧POSTや訂正を再送せず、新結果を承認元へ使う。"""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from src.mina.store import Store
from src.mina.request_ledger import RequestLedger
from src.mina.registration import register_day
import test_registration as fixtures
class SavedCorrectionTest(unittest.TestCase):
 def test_response_received_patch_without_legacy_meal_is_adopted_by_get_only(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);store=Store(root/'db');helper=fixtures.RegistrationTests();draft,saved=helper.setup_inputs(root)
   client=helper.client();ai=Mock();ai.review_meal.return_value=ai.review_ogp.return_value={'status':'publishable','reason_codes':[]}
   with self.assertRaises(ValueError):
    register_day(store,draft,saved,root/'old',client,ai,'www.log-eats.com',capture=Mock(side_effect=ValueError('読取停止')))
   with store.transaction() as db:
    original=json.loads(db.execute("SELECT result FROM preparation_steps WHERE step='meal_register:breakfast'").fetchone()[0])
   corrected=copy.deepcopy(original);corrected.pop('meal')
   corrected['result']['result_version']='rv1_'+'d'*64
   corrected['result']['ogp']['source_result_version']=corrected['result']['result_version']
   key='22222222-2222-4222-8222-222222222222'
   ledger=RequestLedger(store);ledger.prepare(key,'meal_correction','2026-10-08','breakfast',{'contract_version':'mina-write-v1'})
   ledger.record(key,'response_received',{'persistence':'saved'})
   with store.transaction() as db:
    db.execute("INSERT INTO preparation_steps VALUES(?,'correction_key:breakfast','test','complete',?,0)",('2026-10-08',json.dumps({'key':key})))
    db.execute("INSERT INTO preparation_steps VALUES(?,'meal_correction:breakfast','test','complete',?,0)",('2026-10-08',json.dumps(corrected)))
   state_original=client.day_state.side_effect;lookup_original=client.request_state.side_effect
   def state(target):
    value=state_original(target)
    value['meals']=[copy.deepcopy(corrected['result']) if meal['meal']['meal_type']=='breakfast' else meal for meal in value['meals']]
    return value
   def lookup(request,operation):
    if request!=key:return lookup_original(request,operation)
    return {'contract_version':'mina-write-v1','request_uuid':key,'operation':'meal_correction','status':'succeeded','persistence':'saved','stage':'completed','result_status':'unchanged','result_hold_reason':None,'current_result':copy.deepcopy(corrected['result']),'input_hash':'ih1_'+'f'*64,'image_sha256':None}
   client.day_state.side_effect=state;client.request_state.side_effect=lookup
   def capture(url,host):return {'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':b'preview'}
   result=register_day(store,draft,saved,root/'new',client,ai,'www.log-eats.com',capture=capture,capture_round='33333333-3333-4333-8333-333333333333',allow_correction=True)
   self.assertEqual(client.register.call_count,3)
   self.assertEqual([call.args[0] for call in client.json_write.call_args_list],['/api/internal/mina/daily-share'])
   self.assertEqual(result['posts']['breakfast']['approval_source']['operation'],'meal_correction')
   self.assertEqual(result['posts']['breakfast']['approval_source']['request_key'],key)
   self.assertEqual(ledger.restore(key)['phase'],'complete')
   with store.transaction() as db:
    self.assertEqual(json.loads(db.execute("SELECT result FROM preparation_steps WHERE step='meal_register:breakfast'").fetchone()[0]),original)
