"""Buffer全状態のページ照会と、完成後の通常承認期限を合成データで検証する。"""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.replacement_inventory import BufferInventoryAdapter,ExternalInventory
from src.mina.replacement_contract import Contract,FROZEN_DIRECTORY,source_digest
from src.mina.store import Store
from src.mina.domain import ACCOUNT
from src.mina.buffer_trial import BufferTrialClient
from tests.mina.test_replacement_integration import NOW,TARGET
class InventoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(Path(self.tmp.name)/'db')
  self.posts=[];self.next=False;self.api=Mock()
  self.channels=[{'id':'x','service':'twitter','name':ACCOUNT,'isDisconnected':False,'isLocked':False,'isQueuePaused':False},
                 {'id':'ig','service':'instagram','name':'log_eats','isDisconnected':False,'isLocked':False,'isQueuePaused':False}]
  def query(q,v=None):
   if 'organizations' in q:return {'account':{'organizations':[{'id':'org'}]}}
   if 'channels(' in q:return {'channels':self.channels}
   return {'posts':{'edges':[{'node':p} for p in self.posts],'pageInfo':{'hasNextPage':self.next,'endCursor':'cursor'}}}
  self.api._request.side_effect=query;self.reader=BufferInventoryAdapter(self.api,'org');self.inventory=ExternalInventory(self.store,self.reader,lambda:NOW,{'x':'x','instagram':'ig'})
 def post(self,status='scheduled',due='2030-01-01T03:15:00.000Z'):
  return {'id':'synthetic-post','status':status,'dueAt':due,'sentAt':None,'channel':{'id':'x','service':'twitter'}}
 def test_empty_both_channels_is_complete_without_status_or_date_filter(self):
  receipt=self.inventory.check(TARGET);self.assertEqual(len(receipt['pages']),2)
  for args in self.api._request.call_args_list:
   q=args.args[0]
   self.assertNotIn('mutation',q)
   if 'posts(' in q:self.assertEqual(set(args.args[1]['input']['filter']),{'channelIds'})
 def test_undated_draft_is_not_filtered_as_another_day(self):
  self.posts=[self.post('draft',None)]
  with self.assertRaisesRegex(ValueError,'dispatch_present'):self.inventory.check(TARGET)
 def test_new_status_and_wrong_channel_are_not_empty(self):
  self.posts=[self.post('new-provider-state')]
  with self.assertRaises(ValueError):self.inventory.check(TARGET)
  self.posts=[{**self.post(),'channel':{'id':'another-channel','service':'twitter'}}]
  with self.assertRaises(ValueError):self.inventory.check(TARGET)
 def test_repeated_page_cursor_is_incomplete(self):
  self.next=True
  with self.assertRaisesRegex(ValueError,'incomplete'):self.inventory.check(TARGET)
 def test_expired_replanning_window_does_not_change_completed_source_identity(self):
  f=json.loads((FROZEN_DIRECTORY/'FIXTURES.json').read_text(encoding='utf-8'));p=copy.deepcopy(next(s['value'] for s in f['samples'] if s['case_id']=='precondition_success'))
  old=source_digest(p);p['policy']['blocking_reasons']=['deadline_exceeded'];p['policy']['available_replans']=0
  self.assertEqual(source_digest(p),old)
  p['policy']['budget_policy_digest']='f'*64;self.assertNotEqual(source_digest(p),old)
 def test_actual_client_uses_existing_quota_callback_once_per_query(self):
  from src.mina.buffer_publisher import production_client
  query=self.api._request.side_effect
  with patch.dict('os.environ',{'BUFFER_API_KEY':'synthetic-test-token'}):api=production_client(self.store)
  api._transport=lambda q,v:{'data':query(q,v)}
  inventory=ExternalInventory(self.store,BufferInventoryAdapter(api,'org'),lambda:NOW,{'x':'x','instagram':'ig'})
  self.assertEqual(len(inventory.check(TARGET)['pages']),2)
  self.assertEqual(api.calls,6)
  with self.store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM buffer_api_usage').fetchone()[0],6)
 def test_actual_client_without_quota_callback_is_rejected_before_transport(self):
  api=BufferTrialClient('synthetic-test-token',transport=Mock())
  with self.assertRaisesRegex(ValueError,'quota_unverified'):BufferInventoryAdapter(api,'org')
  self.assertEqual(api.calls,0)
if __name__=='__main__':unittest.main()
