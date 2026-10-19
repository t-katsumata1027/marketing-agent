"""実SQLiteの読取不変性と、作成・送信・品質保留の独立した分類を確認する。"""
import hashlib,json,tempfile,unittest
from datetime import UTC,datetime,timedelta
from pathlib import Path
from unittest.mock import Mock
from src.mina.store import Store
from src.mina.preparation import PreparationJournal
from src.mina.request_ledger import RequestLedger
from src.mina.continuity import summarize
from src.mina.clients import await_checked_at,MealClient,publication_source_client,ClockSkewError
from src.mina.m1_contract import validate_state

TARGET='2026-10-18';NOW=datetime(2026,10,18,0,40,tzinfo=UTC)

class ContinuityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'db';self.store=Store(self.path);PreparationJournal(self.store);RequestLedger(self.store)
  with self.store.transaction() as db:
   db.execute('INSERT INTO preparation VALUES(?,?,?)',(TARGET,'held:context_review',NOW.timestamp()-3600))
   for slot,kcal,status in [('breakfast',484,'publishable'),('lunch',633,'publishable'),('dinner',610,'hold')]:
    r={'result':{'analysis':{'summary':{'totalCalories':kcal}}}}
    db.execute('INSERT INTO preparation_steps VALUES(?,?,?,?,?,?)',(TARGET,'meal_correction:'+slot,'synthetic','complete',json.dumps(r),NOW.timestamp()-100))
    db.execute('INSERT INTO preparation_steps VALUES(?,?,?,?,?,?)',(TARGET,'meal_review:'+slot,'synthetic','complete',json.dumps({'status':status}),NOW.timestamp()-50))
 def test_eighteenth_quality_failure_does_not_become_delivery_failure_or_pass(self):
  report=summarize(self.path,TARGET,NOW);slots={r['slot']:r for r in report['slots']}
  self.assertEqual(report['recorded_total_calories'],1727);self.assertFalse(report['daily_energy_in_range'])
  self.assertEqual(report['creation_status'],'held');self.assertEqual(report['publication_failures'],[])
  self.assertEqual(slots['breakfast']['time_window'],'elapsed');self.assertEqual(slots['lunch']['recovery'],'individual_source_recheck_candidate')
  self.assertEqual(slots['dinner']['recovery'],'saved_meal_quality_hold');self.assertEqual(slots['daily']['recovery'],'daily_energy_out_of_range')
  self.assertEqual(report['external_inventory'],'unverified');self.assertTrue(report['planning_only'])
 def test_reading_keeps_every_byte_and_business_row_unchanged(self):
  before=hashlib.sha256(self.path.read_bytes()).digest();summarize(self.path,TARGET,NOW)
  self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(),before)
 def test_missing_database_is_not_created_and_incomplete_schema_is_not_empty(self):
  absent=Path(self.tmp.name)/'absent';self.assertFalse(summarize(absent,TARGET,NOW)['database_available']);self.assertFalse(absent.exists())
  with self.store.transaction() as db:db.execute('DROP TABLE mina_requests')
  self.assertFalse(summarize(self.path,TARGET,NOW)['ledger_complete'])
 def test_unknown_request_overrides_individual_recovery_suggestion(self):
  RequestLedger(self.store).prepare('11111111-1111-4111-8111-111111111111','meal_register',TARGET,'breakfast',{})
  report=summarize(self.path,TARGET,NOW)
  self.assertTrue(all(s['recovery']=='request_reconciliation_required' for s in report['slots']))
 def test_unknown_dispatch_overrides_local_review(self):
  with self.store.transaction() as db:
   db.execute("INSERT INTO buffer_publication(target_date,slot,revision,digest,channel_id,service_id,state) VALUES(?,? ,1,'synthetic','synthetic','synthetic','outcome_unknown')",(TARGET,'lunch'))
  r=next(r for r in summarize(self.path,TARGET,NOW)['slots'] if r['slot']=='lunch')
  self.assertEqual(r['recovery'],'external_reconciliation_required')
 def test_corrupt_json_hides_partial_hints_and_raw_body(self):
  with self.store.transaction() as db:db.execute("UPDATE preparation_steps SET result='private-token' WHERE step='meal_review:lunch'")
  r=summarize(self.path,TARGET,NOW);self.assertFalse(r['database_available']);self.assertEqual(r['slots'],[]);self.assertNotIn('private-token',json.dumps(r))

class PublicationClockTests(unittest.TestCase):
 def test_incident_gap_waits_in_real_time_and_never_retimes_response(self):
  client=publication_source_client('https://logeats.test','a'*32)
  self.assertEqual(MealClient('https://logeats.test','a'*32).max_clock_ahead,1)
  future=NOW+timedelta(seconds=1.079563);state={'checked_at':future.isoformat(),'account':'EatsLog88161','contract_version':'mina-write-v1','complete':True,'date':TARGET,'meal_count':0,'meals':[],'state_version':'sv1_'+'a'*64}
  values=iter((NOW,future));sleep=Mock()
  self.assertIs(await_checked_at(state,clock=lambda:next(values),sleep=sleep,max_ahead=client.max_clock_ahead),state)
  sleep.assert_called_once_with(1.079563);validate_state(state,TARGET,future)
  with self.assertRaises(ValueError):validate_state(state,TARGET,NOW)
 def test_large_gap_and_clock_that_does_not_advance_are_still_rejected(self):
  client=publication_source_client('https://logeats.test','a'*32)
  sleep=Mock()
  with self.assertRaises(ClockSkewError):await_checked_at({'checked_at':(NOW+timedelta(seconds=5.001)).isoformat()},clock=lambda:NOW,sleep=sleep,max_ahead=client.max_clock_ahead)
  sleep.assert_not_called()
  with self.assertRaises(ClockSkewError):await_checked_at({'checked_at':(NOW+timedelta(seconds=1.079563)).isoformat()},clock=lambda:NOW,sleep=sleep,max_ahead=client.max_clock_ahead)
 def test_expired_response_is_not_refreshed(self):
  result={'checked_at':(NOW-timedelta(seconds=121)).isoformat()};sleep=Mock()
  self.assertIs(await_checked_at(result,clock=lambda:NOW,sleep=sleep,max_ahead=5),result);sleep.assert_not_called()

if __name__=='__main__':unittest.main()
