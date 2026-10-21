"""当日試行でも3食を確認し、朝食の公開と翌日への除外持越しを拒否する。"""
import json,tempfile,unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock,patch
from src.mina.domain import JST,Candidate
from src.mina.store import Store
from src.mina.readiness import build_candidates
from src.mina.runtime import import_manifest
from src.mina.daily_pipeline import prepare_day
from test_readiness import evidence,reviewed
from test_daily import HOST,TARGET

NOW=datetime(2026,10,8,9,30,tzinfo=JST)
class SameDayTests(unittest.TestCase):
 def test_only_three_future_cards_and_permanent_breakfast_rejection(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);store=Store(root/'db')
   with store.transaction() as db:
    db.execute("INSERT INTO publication_exclusions VALUES(?,'breakfast','本人指定')",(TARGET,))
   data=build_candidates(reviewed(),evidence(),'a'*40,HOST,NOW,excluded_slots=('breakfast',))
   self.assertEqual({r['slot'] for r in data},{'lunch','dinner','daily'})
   manifest=root/'candidates.json';manifest.write_text(json.dumps(data))
   import_manifest(store,manifest,HOST,NOW,excluded_slots=('breakfast',))
   self.assertEqual(len(store.issue_cards(TARGET,NOW)),3)
   store.queue_delay(TARGET,NOW)
   with store.transaction() as db:self.assertEqual(db.execute("SELECT count(*) FROM notices WHERE kind='delay'").fetchone()[0],0)
   breakfast=build_candidates(reviewed(),evidence(),'a'*40,HOST,datetime(2026,10,7,19,tzinfo=JST))[0]
   with self.assertRaisesRegex(ValueError,'本人指定'):
    store.save(Candidate(**breakfast),HOST,datetime(2026,10,7,19,tzinfo=JST))
 def test_default_still_rejects_past_breakfast_and_invalid_exclusions(self):
  for excluded in ((),('lunch',),('breakfast','daily')):
   with self.subTest(excluded=excluded),self.assertRaises(ValueError):
    build_candidates(reviewed(),evidence(),'a'*40,HOST,NOW,excluded_slots=excluded)
  with self.assertRaises(ValueError):
   build_candidates(reviewed(),evidence(),'a'*40,HOST,datetime(2026,10,9,9,tzinfo=JST),excluded_slots=('breakfast',))
 def test_too_late_stops_before_any_external_operation(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);store=Store(root/'db');ai=Mock();drive=Mock();meal=Mock()
   with self.assertRaises(ValueError):
    prepare_day(store,root,None,None,'a'*40,root,HOST,ai,drive,meal,clock=lambda:datetime(2026,10,8,12,15,tzinfo=JST),same_day=True)
   self.assertEqual(ai.mock_calls+drive.mock_calls+meal.mock_calls,[])
 def test_cli_honors_existing_stop_switch_before_store(self):
  from src.mina.__main__ import main
  with patch.dict('os.environ',{'MINA_PREPARE_ENABLED':'false'}),patch('sys.argv',['mina','prepare-today']),patch('src.mina.__main__.Store') as store:
   with self.assertRaises(ValueError):main()
   store.assert_not_called()
