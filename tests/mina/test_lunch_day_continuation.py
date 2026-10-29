"""昼食の固定対応と夕日だけの送付を隔離する。"""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.lunch_day_continuation import mapping,NAMES,AMOUNTS,LunchGate
from src.mina.store import Store
from src.mina.preparation import fingerprint
from src.mina.current_day_cards import initialize,marker,deliver,VERSION,TARGET,KEY,SLOTS

class LunchTests(unittest.TestCase):
    def setUp(self):
        self.meal={'foods':[{'name':n,'amount':a} for n,a in zip(NAMES,AMOUNTS)]}
        self.response={'result':{'analysis':{'foods':[{'name':n} for n in ['そぼろ丼（鶏そぼろと炒り卵）','ほうれん草の胡麻和え','味噌汁（わかめと人参）']]}}}
        self.review={'status':'hold','checks':dict.fromkeys(('composition','style','food_identity','visible_portions'),'pass'),'blocking':[{'code':'nutrition_inconsistent'}]};self.review['checks']['nutrition']='hold'
    def test_shared_ingredients_once_and_complete(self):
        result=mapping(self.meal,self.response,self.review)
        self.assertEqual(sorted([i for d in result['dishes'] for i in d]+[s['index'] for s in result['shared']]),list(range(14)))
        for item in result['shared']:self.assertEqual(sum(item['allocations']),item['total'])
    def test_portion_or_image_change_rejected(self):
        self.meal['foods'][0]['amount']='100g'
        with self.assertRaises(ValueError):mapping(self.meal,self.response,self.review)
        self.meal['foods'][0]['amount']=AMOUNTS[0];self.review['checks']['food_identity']='hold'
        with self.assertRaises(ValueError):mapping(self.meal,self.response,self.review)
    def test_only_evening_and_daily_sent_without_deleting_other_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db');gate=Mock();gate.store=store;gate.approval={'recovery_uuid':'isolated'};gate.delivery_slots=LunchGate.delivery_slots
            with store.transaction() as db:
                initialize(db)
                for platform in ('x','instagram'):
                    for slot in SLOTS:
                        payload={'contract_version':VERSION,'target_date':TARGET,'slot':slot,'platform':platform,'scheduled_at':None,'source':{KEY:marker(gate)},'assets':[],'body':'隔離','original_scheduled_at':'2026-10-29T19:15:00+09:00'}
                        db.execute("INSERT INTO current_day_cards VALUES(?,?,?,?,?,'prepared',NULL)",('isolated',platform,slot,json.dumps(payload),fingerprint(payload)))
            discord=Mock();discord.send_files.return_value={'id':'123'};verify=Mock()
            with patch('src.mina.current_day_cards.assets_valid'):deliver(gate,discord,directory,verify)
            self.assertEqual(discord.send_files.call_count,4);self.assertEqual(verify.call_count,4)
            with store.transaction() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM current_day_cards WHERE state='prepared'").fetchone()[0],4)
                self.assertEqual(db.execute("SELECT count(*) FROM current_day_cards WHERE state='sent' AND slot IN ('dinner','daily')").fetchone()[0],4)
            with patch('src.mina.current_day_cards.assets_valid'):deliver(gate,discord,directory,verify)
            self.assertEqual(discord.send_files.call_count,4)
