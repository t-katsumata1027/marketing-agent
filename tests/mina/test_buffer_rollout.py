"""4枠・翌日候補の経路固定、旧台帳保持、停止と使用量を検証する。"""

import json
import sqlite3
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock,patch

from src.mina.buffer_publisher import (BufferPublisher, configure_policy, production_client,
    publication_client, requires_x_verification, recover_publication)
from src.mina.domain import SLOTS
from src.mina.runtime import publish_due
from src.mina.store import Store
from tests.mina import test_buffer_publisher as fixtures
from tests.mina.test_buffer_publisher import NOW,CHANNEL,TARGET,HOST
from tests.mina.test_daily import candidate


class BufferRolloutTests(unittest.TestCase):
    setUp = fixtures.BufferPublisherTests.setUp

    def add(self, slot, target=TARGET, approved=True):
        base=replace(candidate(slot),target_date=target)
        c=replace(base,publish_text=base.content,approval_source=self.c.approval_source)
        self.store.save(c,HOST,NOW)
        if approved:
            key=self.store.issue_cards(target,c.scheduled_at-timedelta(hours=1))[0]['components'][0]['components'][0]['custom_id']
            self.store.decide(key,'human',c.scheduled_at-timedelta(hours=1))
        return c

    def configure(self):
        configure_policy(self.store,CHANNEL,NOW,'2026-10-18')

    def test_policy_pins_all_existing_approved_slots_without_changing_candidates(self):
        for slot in ('lunch','dinner','daily'):self.add(slot)
        before=self.store.list_day(TARGET)
        self.configure();self.configure()
        self.assertEqual(before,self.store.list_day(TARGET))
        with self.store.transaction() as db:
            rows=list(db.execute('SELECT slot,state FROM buffer_publication ORDER BY slot'))
        self.assertEqual(dict(rows),{s:'armed' for s in SLOTS})

    def test_future_new_candidates_require_approval_and_route_automatically(self):
        self.configure()
        c=self.add('lunch','2026-10-17',approved=False)
        self.assertEqual(self.store.claim_due(c.scheduled_at),[])
        key=self.store.issue_cards(c.target_date,c.scheduled_at-timedelta(hours=1))[0]['components'][0]['components'][0]['custom_id']
        self.store.decide(key,'human',c.scheduled_at-timedelta(hours=1))
        row=self.store.claim_due(c.scheduled_at)[0]
        with patch.dict('os.environ',{'BUFFER_API_KEY':'合成値'}):
            self.assertIsInstance(publication_client(self.store,row,Mock()),BufferPublisher)

    def test_missing_key_or_paused_policy_never_falls_back(self):
        self.configure();row=self.store.get(TARGET,'breakfast')
        with patch.dict('os.environ',{'BUFFER_API_KEY':''}):
            with self.assertRaises(Exception):publication_client(self.store,row,Mock())
        with self.store.transaction() as db:db.execute("UPDATE buffer_policy SET state='paused'")
        with self.assertRaises(Exception):publication_client(self.store,row,Mock())

    def test_previous_unknown_blocks_next_slot(self):
        c=self.add('lunch');self.configure()
        with self.store.transaction() as db:
            db.execute("UPDATE candidates SET state='outcome_unknown' WHERE slot='breakfast'")
            db.execute("UPDATE buffer_publication SET state='accepted' WHERE slot='breakfast'")
        with self.assertRaises(Exception):publication_client(self.store,self.store.get(TARGET,'lunch'),Mock())

    def test_buffer_due_does_not_call_own_x_account_api(self):
        self.configure()
        self.assertFalse(requires_x_verification(self.store,self.c.scheduled_at))
        api=Mock();api.channel.return_value=CHANNEL
        default=Mock()
        with patch('src.mina.buffer_publisher.production_client',return_value=api):
            publish_due(self.store,default,self.c.scheduled_at,HOST,Mock(side_effect=ValueError()),clock=lambda:self.c.scheduled_at)
        default.verify_account.assert_not_called();default.publish.assert_not_called()

    def test_multiple_successes_update_only_their_own_routes(self):
        meals=[self.c]+[self.add(slot) for slot in ('lunch','dinner','daily')]
        self.configure()
        for index,c in enumerate(meals):
            row=self.store.claim_due(c.scheduled_at)[0]
            with self.store.transaction() as db:
                route=dict(db.execute('SELECT * FROM buffer_publication WHERE slot=?',(c.slot,)).fetchone())
            api=Mock();api._request.return_value={'createPost':{'__typename':'PostActionSuccess','post':{
                'id':'buffer-'+c.slot,'text':c.content,'channel':{'id':'channel','service':'twitter'},
                'status':'sent','sentAt':c.scheduled_at.isoformat(),'externalLink':'https://x.com/EatsLog88161/status/'+str(100+index)}}}
            publisher=BufferPublisher(self.store,row,route,api,clock=lambda:c.scheduled_at,timer=lambda:10)
            publisher.proof={'started_at':c.scheduled_at.isoformat(),'started_monotonic':10,
                             'expires_at':(c.scheduled_at+timedelta(hours=1)).isoformat()}
            self.store.finish(row,publisher.publish(c.content),c.scheduled_at)
            self.assertEqual(api._request.call_count,1)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(DISTINCT post_id) FROM buffer_publication WHERE state='sent'").fetchone()[0],4)
        self.assertEqual({r['state'] for r in self.store.list_day(TARGET)},{'published'})

    def test_legacy_singleton_migration_preserves_all_publication_fields(self):
        with self.store.transaction() as db:
            db.execute('DROP TABLE buffer_publication')
            db.execute('''CREATE TABLE buffer_publication (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                target_date TEXT NOT NULL,slot TEXT NOT NULL CHECK(slot='breakfast'),revision INTEGER NOT NULL,
                digest TEXT NOT NULL,channel_id TEXT NOT NULL,service_id TEXT NOT NULL,state TEXT NOT NULL,
                post_id TEXT,request_json TEXT,response_json TEXT,attempted REAL,proof_json TEXT)''')
            db.execute("INSERT INTO buffer_publication VALUES(1,?,'breakfast',1,?,'channel','native-account','sent','known-id','{}','{}',10,'{}')",
                       (TARGET,self.c.digest))
            old=tuple(db.execute('SELECT * FROM buffer_publication').fetchone())
        store=Store(self.store.path)
        with store.transaction() as db:
            self.assertEqual(old,tuple(db.execute('SELECT * FROM buffer_publication').fetchone()))
        self.add('lunch');self.configure()
        with store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM buffer_publication').fetchone()[0],2)

    def test_api_usage_is_recorded_without_body_or_credentials(self):
        with patch.dict('os.environ',{'BUFFER_API_KEY':'合成値'}):api=production_client(self.store)
        api._transport=lambda query,variables:{'data':{'post':None}}
        api.post('synthetic-id')
        with self.store.transaction() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM buffer_api_usage')]
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['operation'],'read')
        self.assertNotIn('合成値',json.dumps(rows));self.assertNotIn('synthetic-id',json.dumps(rows))

    def test_quota_stops_before_external_request(self):
        with patch.dict('os.environ',{'BUFFER_API_KEY':'合成値'}):api=production_client(self.store)
        api._transport=Mock()
        from datetime import UTC,datetime
        with self.store.transaction() as db:
            db.executemany("INSERT INTO buffer_api_usage(requested,operation) VALUES(?,'read')",[(datetime.now(UTC).timestamp(),)]*90)
        with self.assertRaises(Exception):api.post('synthetic-id')
        api._transport.assert_not_called()

    def test_policy_does_not_auto_revert_after_review_period(self):
        self.configure()
        c=self.add('breakfast','2026-10-19')
        self.assertFalse(requires_x_verification(self.store,c.scheduled_at))

    def test_wrong_channel_cannot_replace_configured_identity(self):
        self.configure()
        with self.assertRaises(Exception):configure_policy(self.store,{**CHANNEL,'serviceId':'other'},NOW,'2026-10-18')

    def test_restart_before_external_attempt_stops_without_resending(self):
        self.configure()
        self.store.claim_due(self.c.scheduled_at)
        api=Mock()
        recover_publication(Store(self.store.path),self.c.scheduled_at+timedelta(seconds=181),lambda:api)
        api.assert_not_called()
        self.assertEqual(self.store.get(TARGET,'breakfast')['state'],'changes_requested')
