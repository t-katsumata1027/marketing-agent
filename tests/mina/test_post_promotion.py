"""実SQLiteと通常ボタンで夕食・日次四承認と公開排他を検証する。"""
import copy,json,os,tempfile,unittest,uuid,hashlib
from pathlib import Path
from datetime import datetime,UTC,timedelta
from dataclasses import asdict,replace
from unittest.mock import patch
from tests.mina.test_daily import candidate as fixture,HOST
from src.mina.store import Store
from src.mina.instagram_store import InstagramStore
from src.mina.instagram_domain import InstagramCandidate
from src.mina.post_promotion import capture,register,new_xs,stage,VERSION,KEY,TARGET,SLOTS,lock_released,guard_source
from src.mina.domain import slot_time,date
from src.mina.preparation import fingerprint
from src.mina.replacement_contract import stamp
from src.mina.publication_hold import initialize,held
from src.mina.current_day_cards import initialize as card_init
from src.mina.replacement_bundle import CHECKS,maybe_release

class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.store=Store(self.root/'db');self.ig=InstagramStore(self.store.path);self.now=datetime(2026,10,29,9,tzinfo=UTC);self.key=str(uuid.uuid4());self.owner=str(uuid.uuid4());p=self.root/'image.jpg';p.write_bytes(b'jpeg');sha=hashlib.sha256(b'jpeg').hexdigest()
        with self.store.transaction() as db:
            initialize(db);card_init(db);db.execute('CREATE TABLE scoped_preparation_locks(target_date TEXT PRIMARY KEY,case_uuid TEXT,owner TEXT,epoch INTEGER,window_json TEXT,candidates_digest TEXT)');db.execute('INSERT INTO scoped_preparation_locks VALUES(?,?,?,?,?,?)',(TARGET,self.owner,self.owner,1,'{}','a'*64));db.execute("INSERT INTO publication_holds VALUES(?,1,?,'held',0,'prepare_only')",(TARGET,self.owner))
            db.execute('CREATE TABLE current_day_discord_delivery_attempts(platform TEXT,slot TEXT,state TEXT,message_id TEXT,receipt TEXT)');db.execute('CREATE TABLE preparation_steps(target_date TEXT,step TEXT,state TEXT,result TEXT)')
            for name in ('meal_review:breakfast:corrected','ogp_review:breakfast','meal_review:lunch:corrected','ogp_review:lunch','meal_review:dinner:corrected','ogp_review:dinner','ogp_review:daily'):db.execute('INSERT INTO preparation_steps VALUES(?,?,?,?)',(TARGET,name,'complete',json.dumps({'status':'publishable','reason_codes':[]})))
            for slot in ('breakfast','lunch','dinner','daily'):
                c=fixture(slot);source={'request_key':self.owner,'state_version':'sv1_'+'c'*64,'input_hash':'ih1_'+'f'*64,'result_sha256':'9'*64,'scoped_preparation_only':{'old':'原本'}};c=replace(c,target_date=TARGET,approval_source=source,publish_text=c.comment+'\n\n'+c.api_text+'\n'+c.share_url)
                for platform in ('x','instagram'):
                    selected=slot in SLOTS;message='1'+str(len(slot))+str(len(platform)) if selected else None
                    asset={'path':str(p),'sha256':sha,'width':1080,'height':1350 if slot=='daily' else 1920,'mime_type':'image/jpeg'}
                    payload={'target_date':TARGET,'scheduled_at':None,'platform':platform,'slot':slot,'body':c.content if platform=='x' else '既存Instagram本文','assets':[asset]*(5 if slot=='daily' and platform=='instagram' else 1),'source':{'candidate':asdict(c),'bundle_sha256':'b'*64,'nutrition_targets':{'calories':2000,'protein':None,'fat':None,'carbs':None}}}
                    db.execute('INSERT INTO current_day_cards VALUES(?,?,?,?,?,?,?)',(self.owner,platform,slot,json.dumps(payload),fingerprint(payload),'sent' if selected else 'prepared',message))
                    if selected:db.execute('INSERT INTO current_day_discord_delivery_attempts VALUES(?,?,?,?,?)',(platform,slot,'sent',message,json.dumps({'assets_verified':True})))
            self.snapshot=capture(db)
        self.a={'spec':VERSION,'target_date':TARGET,'promotion_uuid':self.key,'mode':'approve_then_scheduled','source_digest':fingerprint(self.snapshot),'publication_times':{s:stamp(slot_time(date.fromisoformat(TARGET),s)) for s in SLOTS},'not_after':'2026-10-29T10:05:00.000Z','code_commit':'d'*40}
        self.settings={'approval':self.a,'host':HOST,'channels':{'x':'xchannel','instagram':'ichannel'}}
        self.env=patch.dict(os.environ,{'MINA_POST_PROMOTION_ENABLED':'true'});self.env.start();self.addCleanup(self.env.stop)
        register(self.store,self.settings,self.snapshot,'/root/config','a'*64,lambda:self.now)
        self.xs=new_xs(self.snapshot,self.a['code_commit'],self.key);self.igs=[]
        self.proof={'verification':'current-source-uuid-ogp-v1','checks':sorted(CHECKS),'verdict':'pass','source_digest':fingerprint(self.snapshot),'read_started_at':stamp(self.now),'state_version':'sv1_'+'c'*64}
        for slot in SLOTS:
            original=json.loads(next(r['payload'] for r in self.snapshot['cards'] if (r['platform'],r['slot'])==('instagram',slot)))
            assets=[{**a,'url':'https://drive.google.com/uc?id=existing'} for a in original['assets']]
            self.igs.append(InstagramCandidate(TARGET,slot,'ichannel','iaccount','post' if slot=='daily' else 'story',original['body'],assets,{'bundle_sha256':'b'*64,'state_version':'sv1_'+'c'*64,'x_digests':{s:c.digest for s,c in self.xs.items()},KEY:self.key}))
        with self.store.transaction() as db:db.execute("UPDATE post_promotion_phases SET state='complete',receipt=? WHERE phase='stage'",(json.dumps({'read_started_at':stamp(self.now)}),))
    def staged(self):
        stage(self.store,self.settings,self.snapshot,self.xs,self.igs,self.proof,lambda:self.now)
        cards=self.store.issue_cards(TARGET,self.now,slots=SLOTS)
        for row in self.ig.rows(TARGET):self.ig.queue_approval(row)
        with self.store.transaction() as db:ig=[json.loads(r[0])['components'][0]['components'][0]['custom_id'] for r in db.execute("SELECT payload FROM instagram_notices WHERE kind='approval'")]
        return [c['components'][0]['components'][0]['custom_id'] for c in cards],ig
    def approve(self,cards,ig,now=None):
        now=now or self.now
        for token in cards:self.store.decide(token,'human',now)
        for token in ig:self.ig.decide(token,'human',now)
    def test_four_buttons_release_only_after_all_and_keep_old_lock(self):
        cards,ig=self.staged();self.assertEqual((len(cards),len(ig)),(2,2));self.assertIsNotNone(self.store.claim_notice())
        self.approve(cards,ig[:1])
        with self.store.transaction() as db:self.assertTrue(held(db,TARGET))
        self.ig.decide(ig[-1],'human',self.now)
        with self.store.transaction() as db:
            self.assertFalse(held(db,TARGET));self.assertTrue(lock_released(db,TARGET));self.assertEqual(dict(db.execute('SELECT * FROM scoped_preparation_locks').fetchone()),self.snapshot['lock']);self.assertEqual(db.execute('SELECT count(*) FROM publication_exclusions').fetchone()[0],2)
        with self.assertRaises(ValueError):self.store.decide(cards[0],'human',self.now)
        due=slot_time(date.fromisoformat(TARGET),'dinner');self.assertEqual([r['slot'] for r in self.store.claim_due(due)],['dinner']);self.assertEqual(self.ig.claim_due(due)['slot'],'dinner')
    def test_duplicate_registration_and_staging_are_rejected(self):
        self.staged()
        with self.assertRaises(Exception):register(self.store,self.settings,self.snapshot,'/root/config','a'*64,lambda:self.now)
        with self.assertRaises(ValueError):stage(self.store,self.settings,self.snapshot,self.xs,self.igs,self.proof,lambda:self.now)
    def test_cancel_one_keeps_entire_bundle_held(self):
        cards,ig=self.staged();self.store.decide(cards[0].replace('approve|','cancel|'),'human',self.now);self.approve(cards[1:],ig)
        with self.store.transaction() as db:self.assertTrue(held(db,TARGET))
    def test_stale_proof_and_lock_owner_change_do_not_release(self):
        cards,ig=self.staged();self.approve(cards,ig,self.now+timedelta(minutes=3))
        with self.store.transaction() as db:
            self.assertTrue(held(db,TARGET));db.execute("UPDATE scoped_preparation_locks SET epoch=2");db.execute('UPDATE replacement_bundles SET proof=?',(json.dumps({**self.proof,'read_started_at':stamp(self.now+timedelta(minutes=3))}),));maybe_release(db,TARGET,self.now+timedelta(minutes=3));self.assertTrue(held(db,TARGET))
    def test_source_image_and_review_change_are_rejected(self):
        Path(self.snapshot['files'][0]['path']).write_bytes(b'changed')
        with self.store.transaction() as db:
            with self.assertRaises(ValueError):guard_source(db,self.snapshot)
    def test_changed_caption_or_x_reference_is_not_staged(self):
        bad=[replace(self.igs[0],caption='改変'),self.igs[1]]
        with self.assertRaises(ValueError):stage(self.store,self.settings,self.snapshot,self.xs,bad,self.proof,lambda:self.now)
        bad=copy.deepcopy(self.xs);bad['dinner']=replace(bad['dinner'],comment='改変')
        with self.assertRaises(ValueError):stage(self.store,self.settings,self.snapshot,bad,self.igs,self.proof,lambda:self.now)
    def test_cache_guard_rejects_midflight_unknown(self):
        cards,ig=self.staged();self.approve(cards,ig)
        from src.mina.post_promotion_external import current
        with self.store.transaction() as db:
            d=json.loads(db.execute('SELECT document FROM post_promotions').fetchone()[0]);current(db,d,TARGET,'dinner');db.execute("UPDATE candidates SET state='outcome_unknown' WHERE slot='dinner'")
            with self.assertRaises(ValueError):current(db,d,TARGET,'dinner')
    def test_past_schedule_does_not_stage_or_send(self):
        with self.assertRaises(ValueError):stage(self.store,self.settings,self.snapshot,self.xs,self.igs,self.proof,lambda:self.now+timedelta(hours=2))
