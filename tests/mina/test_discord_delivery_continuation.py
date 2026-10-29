"""旧期限・入力・nonceを変えず残三件だけを継続する隔離試験。"""
import json,uuid
from datetime import timedelta
import unittest
import test_discord_delivery_recovery as fixtures
from src.mina.discord_delivery_continuation import Continuation,VERSION

class ContinuationTests(unittest.TestCase):
    setUp=fixtures.DeliveryTests.setUp
    def held(self):
        self.gate.issue();original=self.reader.asset;self.reader.asset=lambda url:b'wrong'
        with self.assertRaises(ValueError):self.gate.run(self.discord,self.reader,lambda p:None)
        self.reader.asset=original;self.now+=timedelta(minutes=16)
        self.n={'spec':VERSION,'target_date':'2026-10-29','continuation_uuid':str(uuid.uuid4()),'parent_delivery_uuid':self.approval['delivery_uuid'],'not_after':'2026-10-29T09:15:00.000Z','approval_reference':'本人承認','mode':'remaining_three_only'}
        gate=Continuation(self.store,self.approval,self.snapshot,lambda:self.now,lambda:None,'123','456',continuation=self.n)
        return gate
    def test_remaining_three_keep_old_case_and_nonce(self):
        gate=self.held()
        with self.store.transaction() as db:
            old=dict(db.execute('SELECT * FROM current_day_discord_delivery_jobs').fetchone());inputs=[tuple(r) for r in db.execute('SELECT input,nonce FROM current_day_discord_delivery_attempts ORDER BY platform,slot')]
        gate.issue();gate.reconcile_old(self.reader);self.assertEqual(len(self.sent),1);gate.run(self.discord,self.reader,lambda p:None);self.assertEqual(len(self.sent),4)
        with self.store.transaction() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM current_day_discord_delivery_jobs').fetchone()),old)
            self.assertEqual([tuple(r) for r in db.execute('SELECT input,nonce FROM current_day_discord_delivery_attempts ORDER BY platform,slot')],inputs)
            self.assertEqual(db.execute('SELECT state FROM current_day_discord_delivery_continuations').fetchone()[0],'complete')
            origin=json.loads(db.execute('SELECT unknown_record FROM current_day_discord_delivery_reconciliations').fetchone()[0]);self.assertEqual(origin['state'],'unknown')
        with self.assertRaises(Exception):gate.issue()
        with self.assertRaises(ValueError):gate.run(self.discord,self.reader,lambda p:None)
        self.assertEqual(len(self.sent),4)
    def test_changed_nonce_stops_before_any_new_post(self):
        gate=self.held();gate.issue()
        with self.store.transaction() as db:db.execute("UPDATE current_day_discord_delivery_attempts SET nonce='changed' WHERE platform='x' AND slot='daily'")
        with self.assertRaises(ValueError):gate.reconcile_old(self.reader)
        self.assertEqual(len(self.sent),1)
    def test_unconfirmed_image_stops_and_keeps_unknown(self):
        gate=self.held();gate.issue();self.reader.asset=lambda url:b'wrong'
        with self.assertRaises(ValueError):gate.reconcile_old(self.reader)
        self.assertEqual(len(self.sent),1)
    def test_continuation_expiry_does_not_extend_parent(self):
        gate=self.held();gate.issue();self.now+=timedelta(minutes=16)
        with self.assertRaises(ValueError):gate.reconcile_old(self.reader)
        self.assertEqual(len(self.sent),1)
