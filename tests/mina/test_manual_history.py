"""手動投稿の自動取込と、自動投稿の消失・改変・成否不明の保護を検証する。"""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import test_automatic_history as history_fixtures
import test_nightly_flow as nightly
from src.mina.automatic_history import build_automatic_history
from src.mina.domain import JST
from src.mina.runtime import notify_day,send_notifications
from src.mina.x_history import collect_timeline
from tests.mina.test_daily import candidate,NOW,HOST

class ManualHistoryTests(unittest.TestCase):
 def fixture(self):
  f=history_fixtures.AutomaticHistoryTest();f.setUp();self.addCleanup(f.doCleanups);return f
 def manual(self,key='456',**extra):
  return {'id':key,'author_id':'99','created_at':'2026-10-08T12:00:00+09:00','text':'告知 https://t.co/video',**extra}
 def published(self,f):
  c=candidate();f.store.save(c,HOST,NOW)
  with f.store.transaction() as db:db.execute("UPDATE candidates SET state='published',x_id='123'")
  return c
 def test_manual_media_long_form_and_meal_style_text_need_no_invented_food_history(self):
  f=self.fixture();before=copy.deepcopy(f.bootstrap)
  posts=[self.manual(),self.manual('457',note_tweet={'text':'長い告知'*500}),self.manual('458',text='今日の食事解析結果を手動で紹介します')]
  result=build_automatic_history(f.root/'db',f.snapshot(posts),f.bootstrap,f.cutover,f.now)
  self.assertEqual((result['manual_post_count'],result['coverage_post_count'],result['reviewed_other_post_count']),(3,3,0))
  self.assertEqual(result['entries'],[]);self.assertEqual(f.bootstrap,before)
  for entry in result['manual_posts']:
   self.assertEqual(entry['kind'],'manual');self.assertEqual(entry['source'],'x_api_verified_account')
   self.assertEqual(len(entry['source_sha256']),64);self.assertNotIn('publish_text',entry);self.assertNotIn('menu',entry)
 def test_matching_auto_post_and_manual_post_are_separated_without_database_writes(self):
  f=self.fixture();c=self.published(f)
  with f.store.transaction() as db:before=[tuple(r) for r in db.execute('SELECT * FROM candidates')]
  snapshot=f.snapshot([self.manual(),self.manual('123',text=c.content)])
  result=build_automatic_history(f.root/'db',snapshot,f.bootstrap,f.cutover,f.now)
  self.assertEqual(result['manual_post_count'],1);self.assertEqual(result['entries'][0]['menu'],c.menu)
  with f.store.transaction() as db:self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM candidates')])
 def test_missing_auto_post_is_not_replaced_by_manual_with_identical_body(self):
  f=self.fixture();c=self.published(f)
  with self.assertRaises(ValueError):build_automatic_history(f.root/'db',f.snapshot([self.manual(text=c.content)]),f.bootstrap,f.cutover,f.now)
 def test_auto_body_change_and_invalid_url_expansion_still_hold_with_manual_posts(self):
  f=self.fixture();c=self.published(f)
  for auto in (self.manual('123',text=c.content+'変更'),self.manual('123',text='本文 https://t.co/x',entities={'urls':[{'url':'https://t.co/x','expanded_url':'http://invalid'}]})):
   with self.subTest(auto=auto),self.assertRaises(ValueError):build_automatic_history(f.root/'db',f.snapshot([auto,self.manual()]),f.bootstrap,f.cutover,f.now)
 def test_unresolved_auto_publish_still_holds_with_untracked_manual_posts(self):
  f=self.fixture();f.store.save(candidate(),HOST,NOW)
  for state in ('dispatching','outcome_unknown'):
   with f.store.transaction() as db:db.execute('UPDATE candidates SET state=?',(state,))
   with self.subTest(state=state),self.assertRaises(ValueError):build_automatic_history(f.root/'db',f.snapshot([self.manual()]),f.bootstrap,f.cutover,f.now)
 def test_manual_origin_and_dates_require_verified_snapshot_identity(self):
  f=self.fixture();snapshot=f.snapshot([self.manual()])
  for changed in ({'account_id':'88'},{'account_id':None},{'timeline_pages_complete':False}):
   with self.subTest(changed=changed),self.assertRaises(ValueError):build_automatic_history(f.root/'db',{**snapshot,**changed},f.bootstrap,f.cutover,f.now)
  snapshot['posts'][0]['created_at']='2026-10-11T12:00:00+09:00'
  with self.assertRaises(ValueError):build_automatic_history(f.root/'db',snapshot,f.bootstrap,f.cutover,f.now)
 def test_manual_posts_flow_through_normal_cli_to_four_cards_without_manual_registration(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);f=nightly.NightlyFlowTests();flow=f.setup_flow(root,'plain');store,clock,ai,drive,meal,_=flow
   posts=[{'id':str(100+i),'author_id':'99','created_at':nightly.NOW.replace(hour=17).isoformat(),'text':'手動告知'+str(i)} for i in range(2)]
   reader=Mock();reader.read_history.side_effect=lambda account,target:collect_timeline(lambda *a,**k:{'data':posts,'meta':{'result_count':2}},'99',target,as_of=clock[0])
   with patch.object(nightly,'NightlyXHistory',return_value=reader):f.prepare(root,flow,'plain')
   evidence=json.loads(next((root/'automatic-history').glob('*.json')).read_text(encoding='utf-8'))
   self.assertEqual(evidence['manual_post_count'],2);self.assertEqual(len(store.list_day(nightly.TARGET)),4)
   self.assertEqual((ai.images,len(drive.uploads),len(meal.calls)),(3,3,4))
   notify_day(store,nightly.TARGET,nightly.NOW.replace(hour=20));self.assertEqual(send_notifications(store,Mock()),4)