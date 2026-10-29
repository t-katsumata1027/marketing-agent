"""送付専用の実SQLiteとDiscord Fake。4件/各一回/旧不明保持を検証する。"""
import copy,hashlib,json,os,tempfile,unittest,uuid
from pathlib import Path
from datetime import datetime,UTC,timedelta
from unittest.mock import patch
from src.mina.discord_delivery_recovery import Delivery,VERSION
from src.mina.store import Store
from src.mina.preparation import fingerprint
from src.mina.clients import OutcomeUnknown

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.store=Store(self.root/'db');self.now=datetime(2026,10,29,8,15,tzinfo=UTC)
        self.env=patch.dict(os.environ,{'MINA_DISCORD_DELIVERY_RECOVERY_ENABLED':'true'});self.env.start();self.addCleanup(self.env.stop)
        self.path=self.root/'card.jpg';self.path.write_bytes(b'jpeg');asset={'path':str(self.path),'sha256':hashlib.sha256(b'jpeg').hexdigest()}
        self.cards=[]
        with self.store.transaction() as db:
            db.execute('CREATE TABLE current_day_dinner_continuations(target_date TEXT,state TEXT,recovery_uuid TEXT)');db.execute("INSERT INTO current_day_dinner_continuations VALUES('2026-10-29','held','owner')")
            db.execute('CREATE TABLE scoped_preparation_locks(target_date TEXT,owner TEXT,epoch INTEGER)');db.execute("INSERT INTO scoped_preparation_locks VALUES('2026-10-29','owner',1)")
            from src.mina.publication_hold import initialize
            initialize(db);db.execute("INSERT INTO publication_holds VALUES('2026-10-29',1,'owner','held',0,'prepare_only')")
            from src.mina.current_day_cards import initialize as cards
            cards(db)
            for platform in ('instagram','x'):
                for slot in ('breakfast','daily','dinner','lunch'):
                    payload={'platform':platform,'slot':slot,'body':'固定案','assets':[asset],'original_scheduled_at':'2026-10-29T19:15:00+09:00'};state='unknown' if (platform,slot)==('instagram','daily') else 'prepared'
                    row={'recovery_uuid':'owner','platform':platform,'slot':slot,'payload':json.dumps(payload),'digest':fingerprint(payload),'state':state,'message_id':None};self.cards.append(row)
                    db.execute('INSERT INTO current_day_cards VALUES(?,?,?,?,?,?,?)',tuple(row.values()))
        self.snapshot={'owner':'owner','parent':[{'target_date':'2026-10-29','state':'held','recovery_uuid':'owner'}],'cards':self.cards,'files':[{'path':str(self.path.resolve()),'sha256':asset['sha256']}],'old_lookup':{}}
        self.approval={'spec':VERSION,'target_date':'2026-10-29','mode':'discord_only','allow_unknown_resend':True,'delivery_uuid':str(uuid.uuid4()),'checkpoint_digest':fingerprint(self.snapshot),'not_after':'2026-10-29T08:30:00.000Z'}
        self.gate=Delivery(self.store,self.approval,self.snapshot,lambda:self.now,lambda:None,'123','456');self.sent=[];self.messages={};outer=self
        class Discord:
            def send_files(self,payload,files):
                outer.sent.append(payload)
                if getattr(outer,'unknown',False):raise OutcomeUnknown('隔離不明')
                result={'id':str(100+len(outer.sent)),'channel_id':'456','author':{'id':'123'},'content':payload['content'],'nonce':payload['nonce'],'attachments':[{'filename':'card.jpg','url':'https://cdn.discordapp.com/card.jpg'}]};outer.messages[result['id']]=result;return result
        class Reader:
            def message(self,key):return outer.messages[key]
            def asset(self,url):return b'jpeg'
        self.discord,self.reader=Discord(),Reader()
    def test_four_once_and_old_unknown_origin(self):
        self.gate.issue();result=self.gate.run(self.discord,self.reader,lambda p:None);self.assertEqual(result['discord_sent'],4);self.assertEqual(len(self.sent),4)
        with self.store.transaction() as db:
            originals=[json.loads(r[0]) for r in db.execute('SELECT original FROM current_day_discord_delivery_attempts')];self.assertEqual(sum(r['state']=='unknown' for r in originals),1)
            self.assertEqual(db.execute("SELECT count(*) FROM current_day_cards WHERE state='sent'").fetchone()[0],4);self.assertEqual(db.execute("SELECT count(*) FROM current_day_cards WHERE state='prepared'").fetchone()[0],4)
            self.assertEqual(db.execute('SELECT state FROM publication_holds').fetchone()[0],'held')
        with self.assertRaises(ValueError):self.gate.run(self.discord,self.reader,lambda p:None)
        with self.assertRaises(ValueError):self.gate.issue()
        self.assertEqual(len(self.sent),4)
    def test_unknown_first_has_no_second_post_or_new_nonce(self):
        self.unknown=True;self.gate.issue()
        with self.assertRaises(OutcomeUnknown):self.gate.run(self.discord,self.reader,lambda p:None)
        with self.assertRaises(ValueError):self.gate.run(self.discord,self.reader,lambda p:None)
        self.assertEqual(len(self.sent),1)
    def test_changed_asset_and_deadline_block_before_post(self):
        self.gate.issue();self.now+=timedelta(minutes=14,seconds=1)
        with self.assertRaises(ValueError):self.gate.run(self.discord,self.reader,lambda p:None)
        self.assertEqual(self.sent,[])
    def test_other_sender_state_change_blocks_all_posts(self):
        self.gate.issue()
        with self.store.transaction() as db:db.execute("UPDATE current_day_cards SET state='sent',message_id='999' WHERE platform='x' AND slot='daily'")
        with self.assertRaises(ValueError):self.gate.run(self.discord,self.reader,lambda p:None)
        self.assertEqual(self.sent,[])
    def test_four_source_checks_finish_before_first_post(self):
        self.gate.issue();checked=[]
        def verify(payload):
            checked.append(payload)
            if len(checked)==4:raise ValueError('四件目不一致')
        with self.assertRaises(ValueError):self.gate.run(self.discord,self.reader,verify)
        self.assertEqual(len(checked),4);self.assertEqual(self.sent,[])
