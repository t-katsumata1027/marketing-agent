"""通常prepareを実際の工程・SQLiteで通し、訂正・停止・通知・再起動を確認する。"""
import contextlib,io,json,os,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.__main__ import main
from src.mina.domain import JST
from src.mina.runtime import notify_day,send_notifications
from src.mina.store import Store
from nightly_fakes import NightlyAI,NightlyDrive,NightlyMeal,NightlyXHistory
from test_readiness import evidence

NOW=datetime(2026,10,7,19,tzinfo=JST);TARGET='2026-10-08';HOST='logeats.test'


class NightlyFlowTests(unittest.TestCase):
    def setup_flow(self,root,mode):
        clock=[NOW];ai=NightlyAI(mode,clock);drive=NightlyDrive();meal=NightlyMeal(mode,clock,HOST)
        history=root/'history.json';history.write_text(json.dumps(evidence()))
        store=Store(root/'db')
        (root/'bootstrap.json').write_text(json.dumps({'account':'EatsLog88161','reviewed':True,'reviewer':'隔離試験','entries':[]}))
        (root/'cutover.json').write_text(json.dumps({'account':'EatsLog88161','old_reservations_zero':True,
            'old_generation_stopped':True,'confirmed_by':'隔離試験','confirmed_at':NOW.isoformat()}))
        return store,clock,ai,drive,meal,history

    def prepare(self,root,flow,mode):
        store,clock,ai,drive,meal,history=flow
        clock[0]+=timedelta(microseconds=1)
        def capture(url,host):
            return {'meta':{'og:image':url+'/og-image','image_sha256':('a' if mode=='sha_changed' else 'e')*64},
                    'digest':'d'*64,'review_image':b'\xff\xd8\xffpreview'}
        args=['mina','prepare','--database',str(root/'db'),'--history',str(history),
              '--source-commit','a'*40,'--output',str(root/'daily')]
        # 通常CLIの設定と呼出しを使い、外部サービスと試験時計だけを置き換える。
        with patch.dict(os.environ,{'MINA_PREPARE_ENABLED':'true','MINA_LIVE_WRITES':'true',
                'MINA_HISTORY_AUTO_ENABLED':'true','MINA_SHARE_HOST':HOST,
                'MINA_HISTORY_BOOTSTRAP':str(root/'bootstrap.json'),'MINA_CUTOVER_FILE':str(root/'cutover.json'),
                'MINA_HISTORY_DIRECTORY':str(root/'automatic-history')}),patch('sys.argv',args), \
             patch('src.mina.__main__.datetime',wraps=datetime) as cli_time, \
             patch('src.mina.registration.datetime',wraps=datetime) as api_time, \
             patch('src.mina.daily_pipeline.datetime',wraps=datetime) as pipeline_time, \
             patch('src.mina.codex_client.generation_client',return_value=ai), \
             patch('src.mina.clients.MealClient',return_value=meal), \
             patch('src.mina.__main__.XClient',return_value=NightlyXHistory(clock)), \
             patch('src.mina.drive.DriveClient.from_env',return_value=drive), \
             patch('src.mina.daily_pipeline.release_commit',return_value='a'*40), \
             patch('src.mina.ogp.capture_ogp',side_effect=capture),contextlib.redirect_stdout(io.StringIO()):
            cli_time.now.side_effect=lambda *a:clock[0]
            api_time.now.side_effect=lambda *a:clock[0]
            pipeline_time.now.side_effect=lambda *a:clock[0]
            main()

    def test_normal_cli_corrects_once_and_delivers_four_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);flow=self.setup_flow(root,'correct');store,clock,ai,drive,meal,_=flow
            self.prepare(root,flow,'correct')
            self.assertEqual([(m,s) for m,s,k in meal.calls],
                [('POST','breakfast'),('PATCH','breakfast'),('POST','lunch'),('POST','dinner'),('POST','daily')])
            self.assertEqual(len({k for m,s,k in meal.calls}),5)
            self.assertEqual((ai.images,ai.drafts,ai.proposals,len(drive.uploads)),(3,1,1,3))
            notify_day(store,TARGET,NOW+timedelta(hours=1));discord=Mock()
            self.assertEqual(send_notifications(store,discord),4)
            self.assertEqual(len(store.list_day(TARGET)),4)
            self.assertIn('朝食',discord.send.call_args_list[0].args[0]['content'])
            self.assertIn('1日まとめ',discord.send.call_args_list[-1].args[0]['content'])
            self.prepare(root,flow,'correct')
            self.assertEqual((len(meal.calls),ai.images,ai.drafts,len(drive.uploads)),(5,3,1,3))
            self.assertEqual(send_notifications(store,discord),0)

    def test_no_correction_is_needed_for_already_matching_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);flow=self.setup_flow(root,'plain');self.prepare(root,flow,'plain')
            self.assertEqual(len(flow[4].calls),4);self.assertEqual(flow[2].proposals,0)

    def test_second_quality_hold_stops_and_alerts_before_20_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);flow=self.setup_flow(root,'quality_hold');store=flow[0]
            for _ in range(2):
                with self.assertRaises((RuntimeError,ValueError)):self.prepare(root,flow,'quality_hold')
            self.assertEqual(len(flow[4].calls),2);self.assertEqual(flow[2].proposals,1)
            with store.transaction() as db:
                self.assertEqual({r[0] for r in db.execute('SELECT phase FROM mina_requests')},{'complete'})
                self.assertEqual(db.execute('SELECT status FROM preparation_progress').fetchone()[0],'quality_hold')
                notice=db.execute("SELECT payload,created FROM notices WHERE kind='preparation_hold'").fetchone()
                self.assertLess(notice['created'],(NOW+timedelta(hours=1)).timestamp())
                self.assertIn('朝食／食品・栄養点検',json.loads(notice['payload'])['content'])
                self.assertIn('自動再開しません',json.loads(notice['payload'])['content'])
            notify_day(store,TARGET,NOW+timedelta(hours=1))
            discord=Mock();self.assertEqual(send_notifications(store,discord),1)
            self.assertEqual(store.list_day(TARGET),[])

    def test_unknown_response_404_and_modified_result_never_resend(self):
        for mode in ('post_unknown','post_unknown_404','patch_unknown','lookup_404','modified','sha_changed'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);flow=self.setup_flow(root,mode);store=flow[0]
                for _ in range(2):
                    with self.assertRaises((RuntimeError,ValueError)):self.prepare(root,flow,mode)
                self.assertEqual(len(flow[4].calls),2 if mode=='patch_unknown' else 1)
                self.assertEqual(len({k for m,s,k in flow[4].calls}),len(flow[4].calls))
                self.assertEqual(store.list_day(TARGET),[])
                with store.transaction() as db:
                    self.assertEqual(db.execute('SELECT status FROM preparation_progress').fetchone()[0],'reconciliation_required')

    def test_deadline_stops_next_external_step_and_notifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);flow=self.setup_flow(root,'deadline');store=flow[0]
            with self.assertRaises(RuntimeError):self.prepare(root,flow,'deadline')
            self.assertEqual(flow[2].images,1);self.assertEqual(flow[4].calls,[])
            with store.transaction() as db:
                self.assertEqual(db.execute('SELECT status FROM preparation_progress').fetchone()[0],'stopped')
                self.assertEqual(db.execute("SELECT state FROM preparation_steps WHERE step='image_review:breakfast'").fetchone()[0],'stopped')
