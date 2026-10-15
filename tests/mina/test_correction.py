"""訂正提案と別UUIDのPATCHを一度だけ実行し、不明時は再送しない。"""
import copy,hashlib,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from src.mina.correction import correct_meal_once
from src.mina.preparation import PreparationJournal
from src.mina.request_ledger import RequestLedger
from src.mina.store import Store
from src.mina.clients import OutcomeUnknown
import test_m1_contract as fixtures
class CorrectionTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.store=Store(Path(self.temp.name)/'db');self.journal=PreparationJournal(self.store);self.ledger=RequestLedger(self.store)
  self.raw=b'\xff\xd8\xfforiginal';self.original=fixtures.M1ContractTests().fixture()
  self.original['result']['image_sha256']=hashlib.sha256(self.raw).hexdigest()
  self.meal={'foods':[{'name':'ご飯','amount':'100g'},{'name':'納豆と卵','amount':'1人前'}]}
  self.foods=[{**food,'calories':250.25,'protein':10,'fat':5.25,'carbs':40.75} for food in self.meal['foods']]
  self.ai=Mock();self.ai.structured.return_value={'foods':self.foods};self.client=Mock()
  self.corrected=copy.deepcopy(self.original)
  for result in (self.corrected,self.corrected['result']):result['analysis']['foods']=copy.deepcopy(self.foods)
  self.corrected['result']['result_version']='rv1_'+'d'*64
  self.corrected['result']['ogp']['source_result_version']='rv1_'+'d'*64
  def send(path,body,method,request_key):
   row=self.ledger.restore(request_key)
   self.assertEqual((row['operation'],row['phase']),('meal_correction','dispatching'))
   self.assertEqual(row['payload'],body);self.assertIsNone(row['image']);self.assertEqual(method,'PATCH')
   return self.corrected
  self.client.json_write.side_effect=send
  self.client.request_state.side_effect=lambda key,op:{'contract_version':'mina-write-v1','request_uuid':key,'operation':op,'status':'succeeded','persistence':'saved','stage':'completed','result_status':'unchanged','result_hold_reason':None,'current_result':copy.deepcopy(self.corrected['result']),'input_hash':'ih1_'+'e'*64}
 def run_correction(self):
  return correct_meal_once(self.journal,self.ledger,self.client,self.ai,lambda:'sv1_'+'a'*64,'2026-10-08','breakfast',self.meal,self.raw,self.original,'www.log-eats.com')
 def test_patch_is_persisted_before_send_and_never_replaces_registration(self):
  self.run_correction();self.run_correction()
  self.assertEqual(self.client.json_write.call_count,1);self.ai.structured.assert_called_once();self.client.register.assert_not_called()
 def test_unknown_patch_never_retries_or_issues_another_uuid(self):
  self.client.json_write.side_effect=OutcomeUnknown('unknown');self.client.request_state.side_effect=RuntimeError('404')
  for _ in range(2):
   with self.assertRaises(OutcomeUnknown):self.run_correction()
  self.assertEqual(self.client.json_write.call_count,1)
  with self.store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM mina_requests').fetchone()[0],1)
 def test_proposal_cannot_change_planned_food_amounts(self):
  self.ai.structured.return_value={'foods':[{**self.foods[0],'amount':'200g'},self.foods[1]]}
  with self.assertRaises(ValueError):self.run_correction()
  self.client.json_write.assert_not_called()
