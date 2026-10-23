"""本人指定の当日昼食だけを、Discord承認後に一度公開する。"""
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock
from src.mina.domain import Candidate,JST
from src.mina.store import Store
from src.mina.readiness import build_candidates
from src.mina.runtime import publish_due
from test_readiness import evidence,reviewed
from test_daily import HOST,TARGET

NOW=datetime(2026,10,8,13,tzinfo=JST)
UNTIL=datetime(2026,10,8,19,tzinfo=JST).isoformat()

class LunchExceptionTests(unittest.TestCase):
    def data(self):
        return next(Candidate(**p) for p in build_candidates(reviewed(),evidence(),'a'*40,HOST,NOW,
            excluded_slots=('breakfast',),lunch_after_approval_until=UNTIL) if p['slot']=='lunch')
    def test_requires_explicit_record_and_human_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'db');c=self.data()
            with self.assertRaises(ValueError):store.save(c,HOST,NOW)
            with store.transaction() as db:db.execute("INSERT INTO publication_exceptions VALUES(?,'lunch',?,'本人指定')",(TARGET,UNTIL))
            store.save(c,HOST,NOW)
            self.assertEqual(store.claim_due(NOW),[])
            card=store.issue_cards(TARGET,NOW)[0]
            self.assertIn('承認後に臨時公開',card['content'])
            key=card['components'][0]['components'][0]['custom_id']
            store.decide(key,'本人',NOW)
            self.assertEqual(store.get(TARGET,'lunch')['scheduled'],NOW.timestamp())
            client=Mock();client.publish.return_value='123'
            proof={'started_at':NOW.isoformat(),'checked_at':NOW.isoformat(),'observed_at':NOW.isoformat(),
                   'expires_at':(NOW+timedelta(hours=1)).isoformat(),'started_monotonic':1,'observed_monotonic':1}
            self.assertEqual(publish_due(store,client,NOW,HOST,lambda c:proof,clock=lambda:NOW,timer=lambda:1),1)
            self.assertEqual(publish_due(store,client,NOW,HOST,lambda c:proof,clock=lambda:NOW,timer=lambda:1),0)
            client.publish.assert_called_once()
    def test_unapproved_and_expired_exception_never_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'db');c=self.data()
            with store.transaction() as db:db.execute("INSERT INTO publication_exceptions VALUES(?,'lunch',?,'本人指定')",(TARGET,UNTIL))
            store.save(c,HOST,NOW)
            self.assertEqual(store.claim_due(NOW+timedelta(minutes=1)),[])
            with self.assertRaises(ValueError):c.validate(HOST,datetime(2026,10,8,19,tzinfo=JST))
