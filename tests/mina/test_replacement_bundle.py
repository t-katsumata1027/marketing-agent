"""新版の承認だけを停止中に受け付け、別日と旧承認を保護する。"""
from dataclasses import replace
from datetime import timedelta
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from src.mina.store import Store
from src.mina.instagram_store import InstagramStore
from src.mina.publication_hold import acquire,held
from src.mina.replacement_bundle import stage,refresh,invalidate_saved
from src.mina.replacement_contract import stamp
from src.mina.replacement_artifacts import write_version,adopt,resolve
from tests.mina.test_daily import candidate,NOW,NOTIFY,TARGET,HOST
from tests.mina.test_instagram import candidate as ig_candidate
from src.mina.instagram_media import HOSTS
OWNER='11111111-1111-4111-8111-111111111111'
class BundleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(Path(self.tmp.name)/'db');self.ig=InstagramStore(self.store.path)
  self.store.save(candidate(),HOST,NOW);self.old=self.store.issue_cards(TARGET,NOTIFY)[0]['components'][0]['components'][0]['custom_id']
  with patch.dict('os.environ',{'MINA_MEAL_REPLACEMENT_ENABLED':'true'}):acquire(self.store,TARGET,OWNER,NOW)
  with self.store.transaction() as db:
   invalidate_saved(db,TARGET);db.execute("UPDATE publication_holds SET state='saved_pending_review'")
  self.proof={'verification':'current-source-uuid-ogp-v1','state_version':'sv1_'+'c'*64,'checks':['current_state','uuid_results','original_bytes','ogp_bytes','independent_review'],'verdict':'pass','source_digest':'a'*64,'read_started_at':stamp(NOTIFY)}
 def staged(self):
  xs=[]
  for slot in ('breakfast','lunch','dinner','daily'):
   c=candidate(slot);source={'request_key':OWNER,'state_version':'sv1_'+'c'*64,'input_hash':'ih1_'+'f'*64,'result_sha256':'9'*64}
   xs.append(replace(c,menu='新献立',approval_source=source,publish_text=c.comment+'\n\n'+c.api_text+'\n'+c.share_url))
  igs=[replace(ig_candidate(s),target_date=TARGET,source={'bundle_sha256':'b'*64,'state_version':'sv1_'+'c'*64,'x_digests':{c.slot:c.digest for c in xs}}) for s in ('breakfast','lunch','dinner','daily')]
  stage(self.store,TARGET,OWNER,1,'a'*64,xs,igs,HOST,HOSTS,NOTIFY,self.proof)
  return self.store.issue_cards(TARGET,NOTIFY)
 def approve(self,cards,now=NOTIFY):
  for card in cards:self.store.decide(card['components'][0]['components'][0]['custom_id'],'human',now)
  for row in self.ig.rows(TARGET):self.ig.queue_approval(row)
  with self.store.transaction() as db:tokens=[json.loads(r[0])['components'][0]['components'][0]['custom_id'] for r in db.execute("SELECT payload FROM instagram_notices WHERE kind='approval'")]
  for token in tokens:self.ig.decide(token,'human',now)
 def test_new_approval_is_allowed_but_all_delivery_waits_for_other_channel(self):
  cards=self.staged()
  with self.assertRaises(ValueError):self.store.decide(self.old,'human',NOTIFY)
  for card in cards:self.store.decide(card['components'][0]['components'][0]['custom_id'],'human',NOTIFY)
  self.assertEqual(self.store.claim_due(candidate().scheduled_at),[])
  for row in self.ig.rows(TARGET):self.ig.queue_approval(row)
  with self.store.transaction() as db:tokens=[json.loads(r[0])['components'][0]['components'][0]['custom_id'] for r in db.execute("SELECT payload FROM instagram_notices WHERE kind='approval'")]
  for token in tokens:self.ig.decide(token,'human',NOTIFY)
  with self.store.transaction() as db:self.assertFalse(held(db,TARGET))
  self.assertEqual(len(self.store.claim_due(candidate().scheduled_at)),1)
 def test_stale_proof_is_not_extended_by_human_approval(self):
  cards=self.staged();later=NOTIFY+timedelta(minutes=4);self.approve(cards,later)
  with self.store.transaction() as db:self.assertTrue(held(db,TARGET))
  refresh(self.store,TARGET,'a'*64,{**self.proof,'read_started_at':stamp(later)},later)
  with self.store.transaction() as db:self.assertFalse(held(db,TARGET))
 def test_missing_member_cannot_release_even_after_all_decisions(self):
  cards=self.staged()
  with self.store.transaction() as db:db.execute("DELETE FROM replacement_bundle_members WHERE channel='x' AND slot='dinner'")
  # 欠落した枠の操作を除いても、必要集合の不一致で解除できない。
  for card in cards:
   try:self.store.decide(card['components'][0]['components'][0]['custom_id'],'human',NOTIFY)
   except ValueError:pass
  with self.store.transaction() as db:
   db.execute("UPDATE candidates SET state='approved',actor='human'");db.execute("UPDATE instagram_candidates SET state='approved',actor='human'")
  refresh(self.store,TARGET,'a'*64,self.proof,NOTIFY)
  with self.store.transaction() as db:self.assertTrue(held(db,TARGET))
 def test_changed_current_digest_never_sends_approval_notice(self):
  self.staged()
  with self.store.transaction() as db:db.execute("UPDATE candidates SET digest=?",('0'*64,))
  self.assertIsNone(self.store.claim_notice())
 def test_new_notices_are_deliverable_while_stopped_and_old_notice_obsolete(self):
  self.staged()
  self.assertIsNotNone(self.store.claim_notice())
  with self.store.transaction() as db:self.assertEqual(db.execute("SELECT state FROM notices WHERE revision=1 AND kind='approval'").fetchone()[0],'obsolete')
 def test_artifact_pointer_preserves_old_and_rejects_tampering(self):
  root=Path(self.tmp.name)/'daily';base=root/TARGET;base.mkdir(parents=True);(base/'drive.json').write_bytes(b'old')
  sha=write_version(base/'replacement-1',{'drive.json':b'new'})
  adopted=adopt(self.store,TARGET,OWNER,1,root,'replacement-1',sha)
  self.assertEqual(adopted,resolve(base));self.assertEqual((base/'drive.json').read_bytes(),b'old')
  (adopted/'drive.json').write_bytes(b'tampered')
  with self.assertRaises(ValueError):resolve(base)
if __name__=='__main__':unittest.main()
