"""巡回の既定無効・再照会上限・期限後の書込み禁止を隔離DBで確認する。"""
import json,tempfile,unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.store import Store
from src.mina.publication_hold import acquire
from src.mina.replacement_runtime import maintain,build_coordinator
from tests.mina.test_daily import NOW,TARGET

class RuntimeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(Path(self.tmp.name)/'db');self.now=NOW
  self.flags=patch.dict('os.environ',{'MINA_MEAL_REPLACEMENT_ENABLED':'true'});self.flags.start();self.addCleanup(self.flags.stop)
  acquire(self.store,TARGET,'11111111-1111-4111-8111-111111111111',NOW)
  with self.store.transaction() as db:
   db.execute('CREATE TABLE replacement_episodes (target_date TEXT,stage TEXT,deadline REAL)')
   db.execute('INSERT INTO replacement_episodes VALUES(?,?,?)',(TARGET,'unknown',(NOW+timedelta(minutes=15)).timestamp()))
   db.execute('CREATE TABLE replacement_runtime_context (target_date TEXT,context TEXT)')
   db.execute('INSERT INTO replacement_runtime_context VALUES(?,?)',(TARGET,json.dumps({'directory':'synthetic'})))
  self.coordinator=Mock();self.coordinator.resume.return_value={'state':'held:replacement'}
 def test_disabled_does_not_build_or_poll(self):
  factory=Mock()
  with patch.dict('os.environ',{'MINA_MEAL_REPLACEMENT_ENABLED':'false'}):self.assertEqual(maintain(self.store,lambda:self.now,factory=factory),[])
  factory.assert_not_called()
  with self.store.transaction() as db:self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='replacement_poll'").fetchone())
 def test_unknown_reconciles_with_persisted_backoff(self):
  self.assertEqual(maintain(self.store,lambda:self.now,factory=lambda _:self.coordinator)[0]['state'],'held:replacement')
  maintain(self.store,lambda:self.now,factory=lambda _:self.coordinator)
  self.coordinator.resume.assert_called_once_with(TARGET)
  self.now+=timedelta(seconds=31);maintain(self.store,lambda:self.now,factory=lambda _:self.coordinator)
  self.assertEqual(self.coordinator.resume.call_count,2)
 def test_after_deadline_only_reconciles_and_never_advances(self):
  self.now+=timedelta(minutes=16)
  self.assertEqual(maintain(self.store,lambda:self.now,factory=lambda _:self.coordinator)[0]['state'],'held:deadline')
  self.coordinator.flow.reconcile.assert_called_once_with(TARGET);self.coordinator.resume.assert_not_called()
 def test_missing_config_cannot_become_enabled(self):
  with patch.dict('os.environ',{'MINA_REPLACEMENT_AGENT_CONFIG':''}):
   with self.assertRaisesRegex(ValueError,'not_configured'):build_coordinator(self.store,'synthetic',{}, {},{},'commit','host',None,None,None,lambda:self.now)
 def test_poll_failure_does_not_release_and_is_recorded_without_raw_error(self):
  self.coordinator.resume.side_effect=RuntimeError('synthetic-sensitive-body')
  result=maintain(self.store,lambda:self.now,factory=lambda _:self.coordinator)
  self.assertEqual(result[0]['state'],'held:replacement_lookup')
  with self.store.transaction() as db:
   self.assertEqual(db.execute('SELECT state FROM publication_holds').fetchone()[0],'held')
   rows=[dict(r) for r in db.execute('SELECT * FROM operation_events')]
  self.assertEqual(rows[-1]['stage'],'reconciliation');self.assertNotIn('synthetic-sensitive-body',json.dumps(rows))

class MigrationTests(unittest.TestCase):
 def test_initialization_preserves_all_existing_business_rows(self):
  from src.mina.replacement_flow import ReplacementFlow
  from src.mina.replacement_policy import ReplacementPolicy
  from src.mina.request_ledger import RequestLedger
  from src.mina.instagram_store import InstagramStore
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp)/'db');InstagramStore(store.path);ledger=RequestLedger(store)
   key='11111111-1111-4111-8111-111111111111';ledger.prepare(key,'meal_register',TARGET,'breakfast',{'synthetic':True});ledger.record(key,'complete',{'persistence':'saved'})
   with store.transaction() as db:
    names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    old={n:[tuple(r) for r in db.execute('SELECT * FROM '+n)] for n in names}
   policy={'approval_digest':'a'*64,'currency':'synthetic-units','limit_units':0,'opening_exposure_units':0,'server_allocation_units':0,'call_bounds':{},'cost_bounds_verified':True,'existing_usage_verified':True,'server_allocation_verified':True,'policy_version':'synthetic'}
   port=Mock();ReplacementFlow(store,port,ReplacementPolicy(store,policy,lambda:NOW),port,port,lambda:NOW)
   with store.transaction() as db:
    self.assertEqual(old,{n:[tuple(r) for r in db.execute('SELECT * FROM '+n)] for n in names})
    self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')
   self.assertEqual(port.mock_calls,[])

if __name__=='__main__':unittest.main()
