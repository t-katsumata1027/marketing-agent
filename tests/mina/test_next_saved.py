"""既存六要求・旧保留を維持した翌日八案の限定継続を隔離検証する。"""
import copy,json,tempfile,unittest,uuid
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock
from src.mina import next_saved as n
from src.mina.store import Store
from src.mina.instagram_store import InstagramStore
from src.mina.request_ledger import RequestLedger
from src.mina.preparation import PreparationJournal,fingerprint
from src.mina.domain import JST
from test_tolerance_case import positive

class NextSavedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(Path(self.tmp.name)/'db');InstagramStore(self.store.path);RequestLedger(self.store);PreparationJournal(self.store)
        self.now=datetime(2026,10,29,23,40,tzinfo=JST);self.keys=[]
        self.meal={'menu':'ご飯','foods':[{'name':'ご飯','amount':'180g'}]};self.response={'result':'saved'}
        self.inputs={'meal':self.meal,'response':self.response,'image_sha256':'a'*64}
        with self.store.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS publication_holds(target_date TEXT PRIMARY KEY,epoch INTEGER,owner TEXT,state TEXT,created REAL,reason TEXT)")
            db.execute("INSERT INTO publication_holds VALUES(?,1,'old','held',0,'external_inventory_required')",(n.TARGET,))
            for slot in ('breakfast','lunch','dinner'):
                for op in ('meal_register','meal_correction'):
                    key=str(uuid.uuid4());self.keys.append(key)
                    db.execute("INSERT INTO mina_requests VALUES(?,?,?,?,?,?,NULL,NULL,'complete',NULL,0)",(key,op,n.TARGET,slot,'{}','x'))
            for step in ('meal_review:breakfast:corrected','meal_review:dinner:corrected','ogp_review:breakfast','ogp_review:dinner',n.STEP):
                result={'status':'hold','reason_codes':['image_analysis_mismatch']} if step==n.STEP else n.PASS
                db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,0)",(n.TARGET,step,fingerprint(self.inputs),json.dumps(result)))
            self.old=n.snapshot(db)
        self.a={'case_uuid':str(uuid.uuid4()),'target_date':n.TARGET,'commit':'a'*40,'snapshot_digest':fingerprint(self.old),'approval_reference':'本人の翌日準備指示','not_after':n.END.isoformat(),'state_version':'sv1_'+'a'*64,'review_context_digest':fingerprint({'target_date':n.TARGET,'meal':self.meal,'response':self.response})}
        self.manual={'context_digest':self.a['review_context_digest'],'policy_sha256':n.POLICY,'image_sha256':'a'*64,'daily_comment':'翌日の三食です。','raw':positive(self.meal,'publishable')}
        self.g=n.Gate(self.store,self.a,self.old,self.manual,{},lambda:self.now)
    def test_old_hold_required(self):
        for value in ([],[{**self.old['publication_holds'][0],'state':'released'}]):
            self.g.old={**self.old,'publication_holds':value}
            with self.assertRaises(ValueError):self.g.register()
    def test_sealed_approval_and_manual_changed_rejected(self):
        self.g.register()
        with self.store.transaction() as db:db.execute("UPDATE next_saved_jobs SET manual='{}'")
        with self.assertRaises(ValueError):self.g.check()
    def test_old_request_and_old_step_change_rejected(self):
        self.g.register()
        with self.store.transaction() as db:db.execute('UPDATE mina_requests SET updated=1 WHERE request_key=?',(self.keys[0],))
        with self.assertRaises(ValueError):self.g.check()
    def test_deadline_minimum_and_no_reissue(self):
        self.g.register()
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT deadline FROM next_saved_jobs').fetchone()[0],min(self.now+timedelta(minutes=45),n.END).timestamp())
        with self.assertRaises(Exception):self.g.register()
        self.now=n.END
        with self.assertRaises(ValueError):self.g.check()
    def test_separate_positive_keeps_old_hold_no_ai(self):
        self.g.register();operation=Mock(side_effect=AssertionError('追加AI禁止'))
        with n.scope(self.g):
            self.assertEqual(self.g.review(PreparationJournal(self.store),n.TARGET,n.STEP,self.inputs,operation),n.PASS)
        operation.assert_not_called()
        with self.store.transaction() as db:
            old=db.execute('SELECT result FROM preparation_steps WHERE step=?',(n.STEP,)).fetchone()[0]
            self.assertEqual(json.loads(old),{'status':'hold','reason_codes':['image_analysis_mismatch']})
            self.assertEqual(len(n.read_rows(db,'preparation_steps')),6)
    def test_raw_hold_not_converted(self):
        self.manual['raw']['status']='hold';self.g.register()
        with self.assertRaises(ValueError):self.g.review(PreparationJournal(self.store),n.TARGET,n.STEP,self.inputs,Mock())
    def test_only_daily_and_one_fixed_request(self):
        self.g.register();r={'target_date':n.TARGET,'slot':'daily','operation':'daily_share','payload':{'date':n.TARGET,'expected_meal_count':3,'expected_state_version':self.a['state_version'],'comment':self.manual['daily_comment']},'request_key':str(uuid.uuid4()),'input_digest':'b'*64}
        with self.store.transaction() as db:
            with self.assertRaises(ValueError):self.g.require_daily(db,{**r,'operation':'meal_correction'})
            self.assertTrue(self.g.require_daily(db,r))
        with self.assertRaises(Exception):
            with self.store.transaction() as db:self.g.require_daily(db,{**r,'request_key':str(uuid.uuid4())})
    def test_provider_reserved_once_per_step_and_no_image(self):
        self.g.register()
        with n.scope(self.g):
            n.provider_started('1',{'preparation':{'target_date':n.TARGET,'step':'ogp_review:lunch'}})
            with self.assertRaises(Exception):n.provider_started('2',{'preparation':{'target_date':n.TARGET,'step':'ogp_review:lunch'}})
            n.provider_started('3',{'preparation':{'target_date':n.TARGET,'step':'ogp_review:daily'}})
            with self.assertRaises(ValueError):n.provider_started('4',{'preparation':{'target_date':n.TARGET,'step':'image:lunch'}})
    def test_non_operator_cannot_start_new_stage(self):
        self.g.register()
        with self.store.transaction() as db:
            with self.assertRaises(ValueError):n.guard_stage(self.store,n.TARGET,db)
        with n.scope(self.g):
            with self.store.transaction() as db:n.guard_stage(self.store,n.TARGET,db)
    def test_preserves_completed_original_request(self):
        self.g.register()
        with n.scope(self.g):
            self.assertTrue(n.preserve_request(self.store,self.keys[0],'complete'))
            with self.assertRaises(ValueError):n.preserve_request(self.store,self.keys[0],'held')
    def test_held_stops_normal_approval_notice_and_release_fallback(self):
        from src.mina.replacement_bundle import approval_allowed,notice_allowed,maybe_release
        self.g.register()
        with self.store.transaction() as db:
            db.execute("UPDATE next_saved_jobs SET state='held'")
            with self.assertRaises(ValueError):approval_allowed(db,n.TARGET,'x','lunch')
            self.assertFalse(notice_allowed(db,{'target_date':n.TARGET},'x'))
            maybe_release(db,n.TARGET,self.now)
            self.assertEqual(db.execute('SELECT state FROM publication_holds').fetchone()[0],'held')
    def test_eight_confirmed_receipts_required_not_human_tokens_only(self):
        self.g.register()
        with self.store.transaction() as db:
            db.execute("UPDATE next_saved_jobs SET state='approval_pending'")
            self.assertTrue(n.blocked_handoff(db,n.TARGET,release=True))
            db.execute('CREATE TABLE routine_notice_receipts(target_date TEXT,state TEXT,policy_id TEXT,message_id TEXT)')
            for i in range(8):db.execute('INSERT INTO routine_notice_receipts VALUES(?,?,?,?)',(n.TARGET,'sent',self.g.key,str(i+1)))
            self.assertFalse(n.blocked_handoff(db,n.TARGET,release=True))
            db.execute("UPDATE routine_notice_receipts SET state='unknown' WHERE message_id='8'")
            self.assertTrue(n.blocked_handoff(db,n.TARGET,release=True))

if __name__=='__main__':unittest.main()
