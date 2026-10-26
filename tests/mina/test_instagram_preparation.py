"""日跨ぎ依存復旧・並行起動・外部不明の停止と4枠原子保存を検証する。"""
from dataclasses import replace
from datetime import UTC,datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile,unittest,os,subprocess,sys
from unittest.mock import Mock,patch
from test_instagram import candidate
from src.mina.domain import SLOTS
from src.mina.instagram_store import InstagramStore
from src.mina.instagram_media import HOSTS
from src.mina.instagram_preparation import prepare,catch_up,initialize,PreparationWaiting

NOW=datetime(2026,10,26,12,tzinfo=UTC)
TARGET='2026-10-27'


class InstagramPreparationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=InstagramStore(Path(temp.name)/'db.sqlite3')
        with self.store.transaction() as db:db.execute('CREATE TABLE candidates(target_date TEXT,slot TEXT,digest TEXT)')
        self.values=[replace(candidate(s),target_date=TARGET) for s in SLOTS]
        def finish(*args):
            self.runtime.preparation_begin_write()
            self.store.save_many(self.values,NOW,HOSTS,guard=self.runtime.preparation_guard)
            return self.values
        self.runtime=SimpleNamespace(store=self.store,render=Mock(),_prepare=Mock(side_effect=finish),notify=Mock())
        self.now=[NOW]

    def source(self):
        with self.store.transaction() as db:
            for s in SLOTS:db.execute('INSERT INTO candidates VALUES(?,?,?)',(TARGET,s,'a'*64))

    def invoke(self):return prepare(self.runtime,TARGET,True,clock=lambda:self.now[0])
    def row(self):
        with self.store.transaction() as db:return dict(db.execute('SELECT * FROM instagram_preparations').fetchone())

    def test_dependency_recovers_without_media_or_api_calls_while_missing(self):
        with self.assertRaises(PreparationWaiting):self.invoke()
        self.runtime.render.assert_not_called();self.runtime._prepare.assert_not_called()
        self.source();self.assertEqual(len(self.invoke()),4)
        self.assertEqual(self.row()['state'],'complete')
        self.invoke();self.assertEqual(self.runtime._prepare.call_count,1)

    def test_source_read_failure_backoff_has_no_external_write(self):
        self.source();self.runtime.render.side_effect=ValueError('source invalid')
        with self.assertRaises(ValueError):self.invoke()
        with self.assertRaises(PreparationWaiting):self.invoke()
        self.assertEqual(self.runtime.render.call_count,1);self.runtime._prepare.assert_not_called()
        self.now[0]+=timedelta(seconds=61);self.runtime.render.side_effect=None
        self.invoke();self.assertEqual(self.row()['state'],'complete')

    def claim(self,state):
        initialize(self.store)
        with self.store.transaction() as db:db.execute('INSERT INTO instagram_preparations VALUES(?,?,?,?,?,?,?)',(TARGET,state,'old-owner',None,0,'none',0))

    def test_live_owner_is_not_stolen(self):
        self.source();self.claim('checking_source')
        with patch('src.mina.instagram_preparation.alive',return_value=True):
            with self.assertRaises(PreparationWaiting):self.invoke()
        self.runtime.render.assert_not_called()

    def test_read_only_interruption_can_recover_after_owner_exit(self):
        self.source();self.claim('checking_source')
        with patch('src.mina.instagram_preparation.alive',return_value=False):self.invoke()
        self.assertEqual(self.row()['state'],'complete')

    def test_external_interruption_persistently_stops_without_retry(self):
        self.source();self.claim('external_preparing')
        with patch('src.mina.instagram_preparation.alive',return_value=False):
            with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(self.row()['state'],'reconciliation_required')
        with self.assertRaises(ValueError):self.invoke()
        self.runtime._prepare.assert_not_called()

    def test_external_failure_is_not_retried(self):
        self.source()
        def fail(*args):self.runtime.preparation_begin_write();raise TimeoutError()
        self.runtime._prepare.side_effect=fail
        with self.assertRaises(TimeoutError):self.invoke()
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(self.runtime._prepare.call_count,1)

    def test_candidates_saved_before_notify_crash_are_delivered_next_tick_once(self):
        self.store.save_many(self.values,NOW,HOSTS);client=Mock()
        def notify(target):
            for row in self.store.rows(target):self.store.queue_approval(row)
            return self.store.send_notices(client)
        self.runtime.notify=notify
        with patch.dict('os.environ',{'MINA_INSTAGRAM_PREPARE_ENABLED':'true'}):
            self.assertEqual(catch_up(self.runtime,NOW),'already_prepared')
            self.assertEqual(catch_up(self.runtime,NOW),'already_prepared')
        self.assertEqual(client.send.call_count,4);self.runtime._prepare.assert_not_called()

    def test_hold_during_render_does_not_leave_live_owner_stuck(self):
        self.source()
        with self.store.transaction() as db:db.execute('CREATE TABLE publication_holds(target_date TEXT,state TEXT)')
        def held(*args):
            with self.store.transaction() as db:db.execute('INSERT INTO publication_holds VALUES(?,?)',(TARGET,'held'))
        self.runtime.render.side_effect=held
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(self.row()['state'],'waiting_source');self.assertIsNone(self.row()['owner'])
        with self.store.transaction() as db:db.execute("UPDATE publication_holds SET state='released'")
        self.now[0]+=timedelta(seconds=61);self.runtime.render.side_effect=None
        self.invoke();self.assertEqual(self.row()['state'],'complete')

    def test_real_runtime_channel_get_failure_stays_retryable_without_media_write(self):
        from src.mina.instagram_runtime import InstagramRuntime
        self.source()
        runtime=InstagramRuntime(self.store.path,'unused',Path(self.store.path).parent/'images',store=self.store,api=Mock())
        bundle={'target_date':TARGET,'bundle_sha256':'b'*64,'state_version':'sv1_'+'c'*64,
                'x_digests':{s:'d'*64 for s in SLOTS},'total':{'calories':1800,'protein':90,'fat':60,'carbs':225},
                'meals':{s:{'comment':'食事記録','menu':'料理','nutrition':{'calories':600,'protein':30,'fat':20,'carbs':75}} for s in SLOTS if s!='daily'}}
        manifest={'assets':{c.slot:c.assets for c in self.values}}
        destination=Path(self.store.path).parent/'rendered';destination.mkdir()
        runtime.render=Mock(return_value=(bundle,manifest,destination));runtime.verify=Mock()
        host=Mock(side_effect=lambda asset,*a,**k:(runtime.preparation_begin_write() or asset))
        with patch('src.mina.instagram_runtime.instagram_channel',side_effect=TimeoutError()),patch('src.mina.instagram_runtime.host_asset',host):
            with self.assertRaises(TimeoutError):prepare(runtime,TARGET,True,clock=lambda:self.now[0])
        self.assertEqual(self.row()['state'],'waiting_source');host.assert_not_called()
        self.now[0]+=timedelta(seconds=61)
        with patch('src.mina.instagram_runtime.instagram_channel',return_value={'id':'channel','serviceId':'account'}),patch('src.mina.instagram_runtime.host_asset',host),patch('src.mina.instagram_runtime.DriveClient.from_env',return_value=Mock()),patch('src.mina.instagram_runtime.datetime') as dt:
            dt.now.return_value=NOW
            self.assertEqual(len(prepare(runtime,TARGET,True,clock=lambda:self.now[0])),4)
        self.assertEqual(self.row()['state'],'complete')

    def test_source_change_and_deadline_during_external_preflight_stop(self):
        self.source()
        def change(*args):
            with self.store.transaction() as db:db.execute("UPDATE candidates SET digest=?",('b'*64,))
            self.runtime.preparation_begin_write()
        self.runtime._prepare.side_effect=change
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(self.store.rows(TARGET),[]);self.assertEqual(self.row()['state'],'waiting_source')

    def test_deadline_during_render_releases_owner_without_write(self):
        self.source()
        self.runtime.render.side_effect=lambda *a:self.now.__setitem__(0,datetime(2026,10,26,22,30,tzinfo=UTC))
        with self.assertRaisesRegex(ValueError,'deadline'):self.invoke()
        self.assertEqual(self.row()['state'],'waiting_source');self.assertIsNone(self.row()['owner'])
        self.runtime._prepare.assert_not_called()

    @unittest.skipUnless(os.name=='posix','実Linuxプロセスの所有証明')
    def test_real_process_owner_is_alive_then_dead_without_lease_timeout(self):
        from src.mina.instagram_preparation import owner,alive
        self.assertTrue(alive(owner()))
        process=subprocess.Popen([sys.executable,'-c','from src.mina.instagram_preparation import owner;import sys;print(owner(),flush=True);sys.stdin.read()'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            identity=process.stdout.readline().strip();self.assertTrue(alive(identity))
            process.communicate('',timeout=10);self.assertFalse(alive(identity))
        finally:
            if process.poll() is None:process.kill();process.wait()

    def test_atomic_save_guard_failure_rolls_back_every_candidate(self):
        count=[]
        def guard(db):
            count.append(1)
            if len(count)==2:raise ValueError('changed')
        with self.assertRaises(ValueError):self.store.save_many(self.values,NOW,HOSTS,guard=guard)
        self.assertEqual(self.store.rows(TARGET),[])

    def test_four_candidates_save_rolls_back_on_fourth_conflict(self):
        old=replace(self.values[-1],caption='旧本文')
        self.store.save(old,NOW,HOSTS)
        with self.store.transaction() as db:db.execute("UPDATE instagram_candidates SET state='dispatching'")
        with self.assertRaises(ValueError):self.store.save_many(self.values,NOW,HOSTS)
        self.assertEqual(len(self.store.rows(TARGET)),1)
        self.assertEqual(self.store.rows(TARGET)[0]['digest'],old.digest)

    def test_partial_candidates_are_not_automatically_replaced(self):
        self.source();self.store.save(self.values[0],NOW,HOSTS)
        with self.assertRaises(ValueError):self.invoke()
        self.runtime._prepare.assert_not_called()

    def test_deadline_prevents_external_work(self):
        self.source();self.now[0]=datetime(2026,10,26,22,30,tzinfo=UTC)
        with self.assertRaisesRegex(ValueError,'deadline'):self.invoke()
        self.runtime._prepare.assert_not_called()

    def test_midnight_catchup_targets_today_not_tomorrow(self):
        with patch.dict('os.environ',{'MINA_INSTAGRAM_PREPARE_ENABLED':'true'}),patch('src.mina.instagram_preparation.prepare') as run:
            self.assertEqual(catch_up(self.runtime,datetime(2026,10,26,16,tzinfo=UTC)),'prepared')
        self.assertEqual(run.call_args.args[1],TARGET)

    def test_before_2010_does_not_start_next_day(self):
        with patch.dict('os.environ',{'MINA_INSTAGRAM_PREPARE_ENABLED':'true'}),patch('src.mina.instagram_preparation.prepare') as run:
            self.assertEqual(catch_up(self.runtime,datetime(2026,10,26,10,tzinfo=UTC)),'not_due')
        run.assert_not_called()

    def test_disabled_catchup_never_calls_preparation(self):
        with patch.dict('os.environ',{'MINA_INSTAGRAM_PREPARE_ENABLED':'false'}),patch('src.mina.instagram_preparation.prepare') as run:
            self.assertEqual(catch_up(self.runtime,NOW),'disabled')
        run.assert_not_called()
