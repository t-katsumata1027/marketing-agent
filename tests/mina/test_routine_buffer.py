"""実使用316回後の失効観測を再確認し、上限・不明・主体境界を維持する。"""
import tempfile,time,unittest
from pathlib import Path
from datetime import datetime,timedelta,UTC
from unittest.mock import patch
from src.mina.store import Store
from src.mina.buffer_budget import setup,subject,observe,check
from src.mina.buffer_limits import record_request
from src.mina.buffer_trial import ORGANIZATIONS,ProbeError
from src.mina.routine_recovery import Routine,scope
from src.mina.routine_buffer import maybe_refresh
from test_buffer_budget import headers

class RoutineBufferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(Path(self.tmp.name)/'db');self.now=datetime.now(UTC).replace(hour=10,minute=0,second=0,microsecond=0)
        self.target=(self.now.date()+timedelta(days=1)).isoformat();self.key=subject('isolated-key');self.store.claim_preparation(self.target,self.now)
        self.run=Routine(self.store,self.target,'a'*40,'www.log-eats.com',lambda:self.now);self.run.admit();self.scope=scope(self.run);self.scope.__enter__();self.addCleanup(self.scope.__exit__,None,None,None)
        self.tp=patch('src.mina.buffer_budget.time.time',return_value=self.now.timestamp());self.tp.start();self.addCleanup(self.tp.stop)
        self.dp=patch('src.mina.buffer_limits.datetime');self.dp.start().now.return_value=self.now;self.addCleanup(self.dp.stop)
        with self.store.transaction() as db:
            setup(db);db.execute("INSERT INTO buffer_quota_probes VALUES('old',?,?,?,'complete',0,?)",(self.key,'2026-10-29',self.now.timestamp()-100,subject('isolated-org')))
            for _ in range(316):db.execute("INSERT INTO buffer_api_usage(requested,operation) VALUES(?,'read')",(self.now.timestamp()-2000,))
        observe(self.store,headers(),self.key,0)
        with self.store.transaction() as db:db.execute('UPDATE buffer_rate_observations SET expires=?',(self.now.timestamp()-1,))
    def api(self,error=None,missing=False,day=600):
        outer=self
        class API:
            _token='isolated-key';calls=0
            def _request(self,query):
                self.calls+=1;key=record_request(outer.store,query,subject=outer.key)
                if error:raise error
                observe(outer.store,headers(day=day,missing=missing),outer.key,key)
                return {'account':{'organizations':[{'id':'isolated-org'}]}}
        return API()
    def test_expired_all_windows_refresh_once_preserves_316_consumed(self):
        api=self.api();maybe_refresh(api,self.store,api._token);maybe_refresh(api,self.store,api._token);self.assertEqual(api.calls,1)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM buffer_api_usage').fetchone()[0],317);check(db,self.key,self.now.timestamp())
    def test_unknown_cannot_reissue_even_next_bucket_or_new_target(self):
        api=self.api(error=TimeoutError('隔離応答喪失'))
        with self.assertRaises(TimeoutError):maybe_refresh(api,self.store,api._token)
        self.now+=timedelta(minutes=16)
        with self.assertRaises(ProbeError):maybe_refresh(api,self.store,api._token)
        self.assertEqual(api.calls,1)
    def test_missing_or_lower_actual_quota_does_not_enable_business(self):
        api=self.api(missing=True)
        with self.assertRaises(ProbeError):maybe_refresh(api,self.store,api._token)
        with self.assertRaises(ProbeError):maybe_refresh(api,self.store,api._token)
        self.assertEqual(api.calls,1)
    def test_lower_actual_quota_does_not_enable_business(self):
        api=self.api(day=100)
        with self.assertRaises(ProbeError):maybe_refresh(api,self.store,api._token)
        with self.assertRaises(ProbeError):maybe_refresh(api,self.store,api._token)
        self.assertEqual(api.calls,1)
    def test_retry_after_cap_and_other_subject_no_network(self):
        api=self.api()
        with self.assertRaises(ProbeError):maybe_refresh(api,self.store,'another-token')
        with self.store.transaction() as db:db.execute('INSERT INTO buffer_retry_deadlines VALUES(?,?,0)',(self.key,self.now.timestamp()+30))
        with self.assertRaises(ProbeError):maybe_refresh(api,self.store,api._token)
        with self.store.transaction() as db:
            db.execute('DELETE FROM buffer_retry_deadlines')
            for _ in range(225):db.execute("INSERT INTO buffer_api_usage(requested,operation) VALUES(?,'read')",(self.now.timestamp()-2000,))
        with self.assertRaises(ProbeError):maybe_refresh(api,self.store,api._token)
        self.assertEqual(api.calls,0)
    def test_target_alone_without_real_publication_claim_is_not_authority(self):
        from src.mina.routine_recovery import CURRENT
        api=self.api();api.publication_target=self.target;token=CURRENT.set(None)
        try:maybe_refresh(api,self.store,api._token)
        finally:CURRENT.reset(token)
        self.assertEqual(api.calls,0)
