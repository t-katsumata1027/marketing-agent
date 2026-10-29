"""一回probe、実窓整合、共通予約を実SQLite/外部Fakeで検証する。"""
import tempfile,time,unittest,uuid
from pathlib import Path
from datetime import datetime,UTC
from email.message import Message
from unittest.mock import patch
from src.mina.store import Store
from src.mina.buffer_budget import *
from src.mina.buffer_limits import record_request

NOW=datetime(2026,10,29,5,30,tzinfo=UTC);END=datetime(2026,10,29,6,tzinfo=UTC).timestamp()

def headers(day=600,missing=False):
    h=Message()
    for w,q,r in ((900,150,120),(86400,day,350 if day>=600 else 9),(2592000,3000,2500)):
        if missing and w==86400:continue
        h['RateLimit-Policy']=f'"policy-{w}"; q={q}; w={w}; pk=:private:'
        h['RateLimit']=f'"policy-{w}"; r={r}; t={w}'
    return h

class BudgetTests(unittest.TestCase):
    def test_lunch_approval_uses_separate_claim_and_preserves_old_deadlines(self):
        run_probe(self.store,self.api(),self.grant);grant=str(uuid.uuid4());deadline=END+3600
        with patch('src.mina.buffer_budget.os.geteuid',return_value=0):grant_lunch_refresh(self.store,grant,self.key,deadline)
        run_continuation_refresh(self.store,self.api(),grant,'buffer_lunch_refresh')
        budget=str(uuid.uuid4());lunch_reserve(self.store,budget,self.key,78,deadline)
        with using(budget):record_request(self.store,ORGANIZATIONS,subject=self.key)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT deadline FROM buffer_quota_probes').fetchone()[0],END)
            self.assertEqual(db.execute('SELECT remaining FROM buffer_lunch_reservations').fetchone()[0],77)
        with self.assertRaises(ProbeError):lunch_reserve(self.store,str(uuid.uuid4()),self.key,1,deadline)
        with self.assertRaises(ProbeError):run_continuation_refresh(self.store,self.api(),grant,'buffer_lunch_refresh')
    def test_continuation_refresh_once_and_preserves_old_reservation(self):
        run_probe(self.store,self.api(),self.grant)
        old=str(uuid.uuid4());reserve(self.store,old,self.key,'2026-10-29',30,END);release(self.store,old)
        refresh=str(uuid.uuid4());deadline=END+1800
        with patch('src.mina.buffer_budget.os.geteuid',return_value=0):grant_continuation_refresh(self.store,refresh,self.key,deadline)
        api=self.api();run_continuation_refresh(self.store,api,refresh)
        with self.assertRaises(ProbeError):run_continuation_refresh(self.store,api,refresh)
        budget=str(uuid.uuid4());continuation_reserve(self.store,budget,self.key,40,deadline)
        with using(budget):record_request(self.store,ORGANIZATIONS,subject=self.key)
        release(self.store,budget)
        with self.store.transaction() as db:
            self.assertEqual(tuple(db.execute('SELECT remaining,original,state FROM buffer_request_reservations').fetchone()),(0,30,'closed'))
            self.assertEqual(tuple(db.execute('SELECT remaining,original,state FROM buffer_continuation_reservations').fetchone()),(0,40,'closed'))
    def test_continuation_refresh_unknown_cannot_resend(self):
        run_probe(self.store,self.api(),self.grant);key=str(uuid.uuid4())
        with patch('src.mina.buffer_budget.os.geteuid',return_value=0):grant_continuation_refresh(self.store,key,self.key,END+1800)
        api=self.api(error=TimeoutError('隔離不明'))
        with self.assertRaises(TimeoutError):run_continuation_refresh(self.store,api,key)
        with self.assertRaises(ProbeError):run_continuation_refresh(self.store,api,key)
        self.assertEqual(api.calls,1)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(Path(self.tmp.name)/'db');self.key=subject('isolated-key');self.grant=str(uuid.uuid4())
        self.tp=patch('src.mina.buffer_budget.time.time',return_value=NOW.timestamp());self.tp.start();self.addCleanup(self.tp.stop)
        self.dp=patch('src.mina.buffer_limits.datetime');self.dp.start().now.return_value=NOW;self.addCleanup(self.dp.stop)
        with self.store.transaction() as db:
            for _ in range(240):db.execute("INSERT INTO buffer_api_usage(requested,operation) VALUES(?,'read')",(NOW.timestamp()-1000,))
        with patch('src.mina.buffer_budget.os.geteuid',return_value=0):grant_probe(self.store,self.grant,self.key,'2026-10-29',END,'isolated-org')
    def api(self,h=None,error=None):
        outer=self
        class API:
            _token='isolated-key';calls=0
            def _request(self,query):
                key=record_request(outer.store,query,subject=outer.key);self.calls+=1
                if error:raise error
                observe(outer.store,h or headers(),outer.key,key)
                return {'account':{'organizations':[{'id':'isolated-org'}]}}
        return API()
    def test_probe_once_counts_241_and_observes_all_windows_without_pk(self):
        api=self.api();self.assertEqual(len(run_probe(self.store,api,self.grant)),3)
        with self.assertRaises(ProbeError):run_probe(self.store,api,self.grant)
        self.assertEqual(api.calls,1)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM buffer_api_usage').fetchone()[0],241)
            self.assertEqual(db.execute('SELECT state FROM buffer_quota_probes').fetchone()[0],'complete')
            self.assertNotIn('private',str([tuple(r) for r in db.execute('SELECT * FROM buffer_rate_observations')]))
    def test_missing_or_lower_windows_stop_without_retry_or_reset(self):
        api=self.api(headers(missing=True))
        with self.assertRaises(ProbeError):run_probe(self.store,api,self.grant)
        with self.assertRaises(ProbeError):run_probe(self.store,api,self.grant)
        self.assertEqual(api.calls,1)
        with self.assertRaises(ProbeError):record_request(self.store,ORGANIZATIONS,subject=self.key)
    def test_unknown_probe_stays_consumed(self):
        api=self.api(error=TimeoutError('隔離不明'))
        with self.assertRaises(TimeoutError):run_probe(self.store,api,self.grant)
        with self.assertRaises(ProbeError):run_probe(self.store,api,self.grant)
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT state FROM buffer_quota_probes').fetchone()[0],'unknown')
    def test_unsuccessful_probe_with_valid_headers_cannot_unlock_capacity(self):
        api=self.api();old=api._request
        def wrong_org(query):old(query);return {'account':{'organizations':[]}}
        api._request=wrong_org
        with self.assertRaises(ProbeError):run_probe(self.store,api,self.grant)
        with self.assertRaises(ProbeError):record_request(self.store,ORGANIZATIONS,subject=self.key)
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT state FROM buffer_quota_probes').fetchone()[0],'failed')
    def test_reservation_claim_and_release_only_untransmitted(self):
        run_probe(self.store,self.api(),self.grant);budget=str(uuid.uuid4());reserve(self.store,budget,self.key,'2026-10-29',50,END)
        with using(budget):record_request(self.store,ORGANIZATIONS,subject=self.key)
        release(self.store,budget)
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM buffer_request_reservations').fetchone();self.assertEqual((row['remaining'],row['original'],row['state']),(0,50,'closed'))
            self.assertEqual(db.execute('SELECT count(*) FROM buffer_api_usage').fetchone()[0],242)
    def test_old_default_and_wrong_subject_cannot_use_observed_capacity(self):
        with self.assertRaises(ProbeError):record_request(self.store,ORGANIZATIONS,subject=self.key)
        run_probe(self.store,self.api(),self.grant)
        with self.assertRaises(ProbeError):record_request(self.store,ORGANIZATIONS,subject='another')
    def test_lower_q_missing_headers_and_expiry_disable_extension(self):
        run_probe(self.store,self.api(),self.grant)
        observe(self.store,headers(day=250),self.key,241)
        with self.assertRaises(ProbeError):record_request(self.store,ORGANIZATIONS,subject=self.key)
        observe(self.store,headers(),self.key,241);observe(self.store,headers(missing=True),self.key,241)
        with self.assertRaises(ProbeError):record_request(self.store,ORGANIZATIONS,subject=self.key)
    def test_normal_calls_cannot_steal_reserved_quota_and_mutation_cannot_claim(self):
        run_probe(self.store,self.api(),self.grant);budget=str(uuid.uuid4());reserve(self.store,budget,self.key,'2026-10-29',78,END)
        with using(budget):
            with self.assertRaises(ProbeError):record_request(self.store,'mutation { createPost(input:{}) { id } }',subject=self.key)
            with self.assertRaises(ProbeError):record_request(self.store,'# comment\nmutation { createPost(input:{}) { id } }',subject=self.key)
        with self.store.transaction() as db:
            for _ in range(12):db.execute("INSERT INTO buffer_api_usage(requested,operation) VALUES(?,'read')",(NOW.timestamp(),))
        with self.assertRaises(ProbeError):record_request(self.store,ORGANIZATIONS,subject=self.key)
    def test_retry_after_and_expired_evidence_are_fail_closed(self):
        run_probe(self.store,self.api(),self.grant);h=headers();h['Retry-After']='120';observe(self.store,h,self.key,241)
        with self.assertRaisesRegex(ProbeError,'retry_after'):record_request(self.store,ORGANIZATIONS,subject=self.key)
        with self.store.transaction() as db:
            db.execute('UPDATE buffer_retry_deadlines SET until=0');db.execute('UPDATE buffer_rate_observations SET expires=0')
        with self.assertRaises(ProbeError):record_request(self.store,ORGANIZATIONS,subject=self.key)
    def test_x_and_instagram_share_verified_subject_but_other_token_cannot(self):
        from src.mina.buffer_publisher import production_client
        from src.mina.instagram_buffer_client import InstagramBufferClient
        run_probe(self.store,self.api(),self.grant)
        with patch.dict('os.environ',{'BUFFER_API_KEY':'isolated-key'}):client=production_client(self.store)
        client._transport=lambda *a:{'data':{'ok':True}}
        self.assertEqual(client._request(ORGANIZATIONS),{'ok':True})
        ig=InstagramBufferClient(self.store,'isolated-key',lambda *a:{'data':{'ok':True}})
        self.assertEqual(ig._request(ORGANIZATIONS),{'ok':True})
        wrong=InstagramBufferClient(self.store,'other',lambda *a:self.fail('別キーで送信しません'))
        with self.assertRaises(ProbeError):wrong._request(ORGANIZATIONS)

if __name__=='__main__':unittest.main()
