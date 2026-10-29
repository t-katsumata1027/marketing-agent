"""当日専用入口を実SQLite/工程/画像で通し、外部だけを代替する。"""
import copy,hashlib,io,json,os,tempfile,unittest,uuid
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
from nightly_fakes import NightlyAI,NightlyDrive,NightlyMeal
from test_readiness import evidence
from src.mina.domain import JST,ACCOUNT
from src.mina.store import Store
from src.mina.preparation import PreparationJournal,fingerprint
from src.mina.current_day_origin import TARGET,VERSION,UNKNOWN,LEGACY_COMMIT,capture
from src.mina.current_day_gate import Gate
from src.mina.current_day_recovery import execute
from src.mina.menu_budget import INSTRUCTIONS
from src.mina.replacement_inventory import ExternalInventory,REQUIRED_STATES
from src.mina.replacement_contract import stamp


def jpeg():
    out=io.BytesIO();Image.new('RGB',(200,160),'#e1b990').save(out,'JPEG');return out.getvalue()


class RecoveryAI(NightlyAI):
    def structured(self,instructions,content,schema,name):
        if name=='mina_menu_nutrition_check':
            inputs=json.loads(content);return {s:[419.5,419.5] for s in inputs['foods']}
        result=super().structured(instructions,content,schema,name)
        if name=='mina_meal_replan':result['estimated_calories']=839;self.plan['meals']['dinner']=copy.deepcopy(result)
        return result
    def image(self,description):self.images+=1;return jpeg()


class RecoveryMeal(NightlyMeal):
    def day_state(self,target):
        state=super().day_state(target)
        state['daily_share']=next((v for v in self.requests.values() if 'meal_count' in v),None)
        state['summary']={key:sum(m['analysis']['summary'][key] for m in state['meals']) for key in ('totalCalories','totalProtein','totalFat','totalCarbs')}
        state['nutrition_targets']=getattr(self,'targets',{'calories':1900,'protein':None,'fat':None,'carbs':None})
        return state


class CurrentDayRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root/'db');self.clock=[datetime(2026,10,29,8,30,tzinfo=JST)]
        self.env=patch.dict(os.environ,{'MINA_CURRENT_DAY_RECOVERY_ENABLED':'true'});self.env.start();self.addCleanup(self.env.stop)
        self.ai=RecoveryAI('plain',self.clock);self.meal=RecoveryMeal('plain',self.clock,'logeats.test');self.drive=NightlyDrive()
        self.draft={'target_date':TARGET,'state':'draft_unreviewed',**copy.deepcopy(self.ai.plan)}
        for slot,n in zip(self.draft['meals'],(590,680,660)):self.draft['meals'][slot]['estimated_calories']=n
        self.ai.plan=copy.deepcopy({k:self.draft[k] for k in ('meals','daily_comment')})
        self.raw=json.dumps(self.draft,ensure_ascii=False,indent=2).encode();(self.root/'draft.json').write_bytes(self.raw)
        self.history=evidence();self.history.update(target_date=TARGET,checked_at=self.clock[0].isoformat(),history_from='2026-10-15',history_to='2026-10-28',entries=[])
        self.policy={'legacy_commit':LEGACY_COMMIT,'module_sha256':'b'*64,'instructions_sha256':hashlib.sha256(INSTRUCTIONS.encode()).hexdigest()}
        inputs={'foods':{s:m['foods'] for s,m in self.draft['meals'].items()}};key='menu_nutrition:'+fingerprint(inputs)
        old_values={'breakfast':[241.5,241.5],'lunch':[289,289],'dinner':[310.7,310.7]}
        first=datetime(2026,10,28,19,1,tzinfo=JST).timestamp()
        PreparationJournal(self.store)
        with self.store.transaction() as db:
            db.execute("INSERT INTO preparation_steps VALUES(?, 'daily_draft',?,'complete',?,?)",(TARGET,'a'*64,json.dumps({'target_date':TARGET,'state':'draft_unreviewed','meal_count':3}),first-50))
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",(TARGET,key,fingerprint(inputs),json.dumps(old_values),first-10))
            digest=fingerprint({'draft_sha256':fingerprint(self.draft),'slot':'dinner','reason_codes':['daily_energy_out_of_range'],'attempt':1})
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'outcome_unknown',NULL,?)",(TARGET,UNKNOWN,digest,first))
            self.store.audit.emit('creation',TARGET,'dinner',None,'meal_replan','failed','validation_failed',
                {'frames':['src/mina/preparation.py:run:51','src/mina/meal_replanning.py:propose:86','src/mina/generation.py:validate_plan:262']},db=db)
            self.origin=capture(db,self.draft,self.policy)
        def page(channel,binding,target,cursor):
            return {'account':ACCOUNT,'channel':binding,'date':target,'covered_states':list(REQUIRED_STATES),'complete':True,'items':[],'has_next':False,'end_cursor':None}
        page.usage_counted=True
        self.inventory=ExternalInventory(self.store,page,lambda:self.clock[0],{'x':'x-channel','instagram':'ig-channel'})
        self.receipt=self.inventory.check(TARGET)
        self.approval={'spec':VERSION,'target_date':TARGET,'recovery_uuid':str(uuid.uuid4()),'code_commit':'c'*40,
            'not_after':stamp(self.clock[0]+timedelta(hours=1)),'origin_digest':fingerprint(self.origin),
            'initial_state_version':self.meal.sv,'approval_reference':'隔離本人承認','mode':'prepare_only','cost_mode':'waived'}
        self.gate=Gate(self.store,self.approval,lambda:self.clock[0],lambda:None,self.inventory)
        self.discord=Mock();self.discord.send_files.side_effect=lambda *a:{'id':str(100+self.discord.send_files.call_count)}
    def issue(self):return self.gate.issue(self.draft,self.policy,self.meal.day_state(TARGET),self.receipt)
    def capture(self,url,host):return {'meta':{'page_url':url,'og:image':url.split('?')[0]+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':jpeg()}
    def run_pipeline(self):
        with patch('src.mina.registration.datetime',wraps=datetime) as clock:
            clock.now.side_effect=lambda *a:self.clock[0]
            return execute(self.gate,self.root,self.ai,self.drive,self.meal,'logeats.test',self.history,self.capture,self.discord,'c'*40)

    def test_full_three_meal_pipeline_creates_eight_unscheduled_cards_and_keeps_old_unknown(self):
        self.issue();result=self.run_pipeline()
        self.assertEqual(result['state'],'preparation_cards_delivered');self.assertIsNone(result['scheduled_at'])
        self.assertEqual(len(self.meal.calls),4);self.assertEqual(self.ai.images,3);self.assertEqual(len(self.drive.uploads),3)
        self.assertEqual(self.discord.send_files.call_count,8);self.assertEqual((self.root/'draft.json').read_bytes(),self.raw)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT state FROM preparation_steps WHERE step=?',(UNKNOWN,)).fetchone()[0],'outcome_unknown')
            self.assertEqual(db.execute("SELECT count(*) FROM preparation_steps WHERE step LIKE 'meal_replan:dinner:%'").fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM candidates').fetchone()[0],0)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='instagram_candidates'").fetchone():self.assertEqual(db.execute('SELECT count(*) FROM instagram_candidates').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM current_day_cards WHERE state=\'sent\'').fetchone()[0],8)
            self.assertEqual(db.execute('SELECT state FROM current_day_jobs').fetchone()[0],'complete')
            old=dict(db.execute('SELECT * FROM preparation_steps WHERE step=?',(UNKNOWN,)).fetchone())
            self.assertEqual(fingerprint(old),self.origin['unknown_digest'])
        for call in self.discord.send_files.call_args_list:
            self.assertIn('公開時刻：未定',call.args[0]['content']);self.assertNotIn('components',call.args[0])
        breakfast=[call.args[0]['content'] for call in self.discord.send_files.call_args_list if ' 朝食 / x' in call.args[0]['content']]
        self.assertIn('10/29 07:30 JST',breakfast[0])

    def test_off_is_default_and_creates_no_job_or_hold(self):
        with patch.dict(os.environ,{'MINA_CURRENT_DAY_RECOVERY_ENABLED':'false'}):
            with self.assertRaises(ValueError):self.issue()
        with self.store.transaction() as db:self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='current_day_jobs'").fetchone())

    def test_reissue_restart_and_clock_rollback_do_not_create_a_new_window(self):
        deadline=self.issue();self.assertEqual(deadline,self.clock[0]+timedelta(minutes=15))
        with self.assertRaises(ValueError):self.issue()
        with self.gate.running():pass
        with self.assertRaises(ValueError):
            with self.gate.running():self.fail('二重実行禁止')
        self.assertEqual(self.ai.images,0)

    def test_wrong_origin_and_existing_meal_refuse_before_any_write(self):
        self.approval['origin_digest']='f'*64
        with self.assertRaises(ValueError):self.issue()
        self.assertEqual(self.meal.calls,[])

    def test_old_unknown_change_does_not_allow_consumption(self):
        self.issue()
        with self.store.transaction() as db:db.execute('UPDATE preparation_steps SET digest=? WHERE step=?',('f'*64,UNKNOWN))
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.ai.images,0);self.assertEqual(self.meal.calls,[])

    def test_deadline_and_less_than_sixty_seconds_stop_new_calls(self):
        self.issue();self.clock[0]+=timedelta(minutes=14,seconds=1)
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.ai.images,0);self.assertEqual(self.meal.calls,[])

    def test_unknown_image_is_not_retried_or_freed(self):
        self.issue();self.ai.image=Mock(side_effect=TimeoutError('隔離タイムアウト'))
        with self.assertRaises(TimeoutError):self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.ai.image.call_count,1);self.assertEqual(self.meal.calls,[])
        with self.store.transaction() as db:self.assertEqual(db.execute("SELECT count(*) FROM current_day_calls WHERE state='unknown'").fetchone()[0],1)

    def test_discord_response_loss_preserves_unknown_and_does_not_resend(self):
        self.issue();self.discord.send_files.side_effect=TimeoutError('応答喪失')
        with self.assertRaises(TimeoutError):self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.discord.send_files.call_count,1)
        with self.store.transaction() as db:self.assertEqual(db.execute("SELECT count(*) FROM current_day_cards WHERE state='unknown'").fetchone()[0],1)

    def test_other_meal_replanning_is_never_added_after_image_holds(self):
        self.issue();self.ai.review_image=lambda *a:{'status':'hold','reason_codes':['image_analysis_mismatch']}
        with self.assertRaises(ValueError):self.run_pipeline()
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM preparation_steps WHERE step LIKE 'meal_replan:breakfast:%' OR step LIKE 'meal_replan:lunch:%'").fetchone()[0],0)
        self.assertEqual(self.meal.calls,[])

    def test_missing_trace_or_changed_policy_is_not_an_adjudication(self):
        with self.store.transaction() as db:db.execute('DELETE FROM operation_events')
        with self.assertRaises(ValueError):self.issue()
        self.assertEqual(self.ai.images,0)

    def test_existing_actual_meal_prevents_initial_activation(self):
        self.meal.register(jpeg(),'meal.jpg','breakfast','2026-10-29T07:30:00+09:00','朝食の候補です。',str(uuid.uuid4()),self.meal.sv)
        previous=len(self.meal.calls)
        with self.assertRaises(ValueError):self.issue()
        self.assertEqual(len(self.meal.calls),previous)

    def test_future_clock_reissue_and_wrong_target_refuse_without_extending_window(self):
        self.approval['target_date']='2026-10-28'
        with self.assertRaises(ValueError):self.issue()
        self.approval['target_date']=TARGET;self.issue();self.clock[0]-=timedelta(seconds=1)
        with self.assertRaises(ValueError):
            with self.gate.running():pass
        self.assertEqual(self.ai.images,0)

    def test_unpersisted_inventory_receipt_cannot_set_verified_true(self):
        self.receipt=copy.deepcopy(self.receipt);self.receipt['pages'][0]['response_digest']='f'*64
        with self.assertRaises(ValueError):self.issue()

    def test_stale_inventory_is_refreshed_before_calls_and_unknown_page_stops(self):
        self.issue();self.clock[0]+=timedelta(seconds=121)
        self.inventory.read_page=Mock(side_effect=ValueError('外部全頁取得不可'))
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.ai.images,0);self.assertEqual(self.meal.calls,[])

    def test_long_image_refreshes_real_inventory_before_next_journal_step(self):
        self.issue();original=self.ai.image
        def image(description):
            self.clock[0]+=timedelta(seconds=130)
            return original(description)
        self.ai.image=image
        result=self.run_pipeline()
        self.assertEqual(result['state'],'preparation_cards_delivered')
        self.assertEqual(self.ai.images,3);self.assertEqual(self.discord.send_files.call_count,8)
        with self.store.transaction() as db:
            self.assertGreaterEqual(db.execute('SELECT count(*) FROM replacement_inventory_checks').fetchone()[0],4)

    def test_inventory_change_during_long_image_stops_before_review(self):
        self.issue();original=self.ai.image;review=Mock(wraps=self.ai.review_image);self.ai.review_image=review
        def image(description):
            self.clock[0]+=timedelta(seconds=130)
            self.inventory.read_page=Mock(side_effect=ValueError('隔離：生成中に予約状態が不明'))
            return original(description)
        self.ai.image=image
        with self.assertRaises(ValueError):self.run_pipeline()
        review.assert_not_called();self.assertEqual(self.meal.calls,[]);self.discord.send_files.assert_not_called()

    def test_terminal_job_does_not_refresh_inventory_or_restart(self):
        self.issue()
        with self.assertRaises(RuntimeError):
            with self.gate.running():raise RuntimeError('隔離：実行停止')
        self.gate.latest_inventory=None;self.inventory.check=Mock()
        with self.assertRaises(ValueError):self.gate.refresh_external()
        self.inventory.check.assert_not_called()

    def test_legacy_1c_code_blocks_even_after_hold_release_or_marker_damage(self):
        import sys,types
        root=Path(__file__).parent/'fixtures/legacy-1c-preparation'
        manifest=json.loads((root/'manifest.json').read_bytes());modules={}
        self.assertEqual(manifest['commit'],LEGACY_COMMIT)
        for name in ('scoped_preparation','publication_hold'):
            raw=(root/(name+'.py.txt')).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),manifest['files'][name+'.py.txt'])
            module=types.ModuleType('src.mina.legacy_'+name);module.__package__='src.mina'
            exec(compile(raw,str(root/(name+'.py.txt')),'exec'),module.__dict__);modules[name]=module
        self.issue()
        from src.mina.scoped_preparation import KEY
        with patch.dict(sys.modules,{'src.mina.scoped_preparation':modules['scoped_preparation']}):
            with self.store.transaction() as db:
                db.execute("UPDATE publication_holds SET state='released' WHERE target_date=?",(TARGET,))
                with self.assertRaises(ValueError):modules['publication_hold'].require_clear(db,TARGET)
            self.assertTrue(modules['scoped_preparation'].blocked({'source':{KEY:None}}))
            self.assertTrue(modules['scoped_preparation'].blocked({'approval_source':{KEY:{'broken':True}}}))
        self.assertEqual(self.discord.mock_calls,[])

    def test_normal_notification_daemon_does_not_send_preparation_cards(self):
        self.issue();self.run_pipeline()
        from src.mina.runtime import send_notifications
        daemon=Mock();self.assertEqual(send_notifications(self.store,daemon),0);daemon.send.assert_not_called()

    def test_unknown_operation_allows_only_get_lookup_not_new_paid_call(self):
        from src.mina.current_day_gate import Operations
        self.issue()
        with self.gate.running():
            ops=Operations(self.gate)
            with self.assertRaises(TimeoutError):ops.invoke('ai:image',{},lambda:(_ for _ in ()).throw(TimeoutError()))
            self.assertEqual(ops.invoke('m1:request_state',{},lambda:{'status':'processing'},lookup=True),{'status':'processing'})
            paid=Mock()
            with self.assertRaises(ValueError):ops.invoke('ai:image',{},paid)
            paid.assert_not_called()

    def test_direct_replanning_after_hold_and_expiry_does_not_call_ai(self):
        from src.mina.current_day_recovery import seed_legacy
        from src.mina.meal_replanning import MealReplanner
        from src.mina.preparation_status import QualityHold
        self.issue()
        with self.assertRaises(RuntimeError):
            with self.gate.running():
                seed_legacy(self.gate,self.draft);raise RuntimeError('途中停止')
        self.clock[0]+=timedelta(hours=2)
        ai=Mock(wraps=self.ai)
        planner=MealReplanner(self.store,self.draft,self.root/'draft.json',ai)
        with self.assertRaises(ValueError):planner.replan('dinner',QualityHold('保留','menu_nutrition:daily',('daily_energy_out_of_range',)))
        ai.structured.assert_not_called()

    def test_old_step_mutation_during_image_stops_before_review_or_m1(self):
        self.issue();calls=[]
        def image(description):
            calls.append(description)
            with self.store.transaction() as db:db.execute("UPDATE preparation_steps SET started=started+1 WHERE step='daily_draft'")
            return jpeg()
        self.ai.image=image
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(len(calls),1);self.assertEqual(self.meal.calls,[]);self.discord.send_files.assert_not_called()

    def test_old_audit_mutation_during_image_stops_before_m1(self):
        self.issue()
        def image(description):
            with self.store.transaction() as db:db.execute("UPDATE operation_events SET reason='none' WHERE stage='meal_replan' AND event='failed'")
            return jpeg()
        self.ai.image=image
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.meal.calls,[]);self.discord.send_files.assert_not_called()

    def test_original_draft_byte_mutation_is_detected_at_next_external_boundary(self):
        self.issue()
        def image(description):(self.root/'draft.json').write_bytes(self.raw+b' ');return jpeg()
        self.ai.image=image
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.meal.calls,[]);self.discord.send_files.assert_not_called()

    def test_origin_self_digest_replacement_does_not_change_approved_origin(self):
        self.issue()
        with self.store.transaction() as db:
            changed={**self.origin,'old_cost_status':'wrong'}
            db.execute('UPDATE current_day_jobs SET origin=?,origin_digest=?',(json.dumps(changed),fingerprint(changed)))
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.ai.images,0)

    def test_activation_time_cannot_be_moved_to_extend_deadline(self):
        self.issue()
        with self.store.transaction() as db:
            db.execute('UPDATE current_day_jobs SET activated_at=?,deadline=?',(stamp(self.clock[0]+timedelta(minutes=1)),stamp(self.clock[0]+timedelta(minutes=16))))
        self.clock[0]+=timedelta(minutes=2)
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.ai.images,0)

    def test_actor_owned_approved_files_are_not_execution_authority(self):
        from src.mina.current_day_cli import config
        if os.name!='posix' or os.getuid()==0:self.skipTest('非root Actor UIDの実権限で検査する')
        path=self.root/'forged.json';path.write_text(json.dumps({'approval':self.approval}));path.chmod(0o600)
        with self.assertRaisesRegex(ValueError,'root所有'):config(path,self.root)

    def test_lookup_cli_is_get_only_with_feature_off_and_missing_config(self):
        import contextlib
        from src.mina.current_day_cli import main
        from src.mina.request_ledger import RequestLedger
        key=str(uuid.uuid4());ledger=RequestLedger(self.store)
        ledger.prepare(key,'meal_register',TARGET,'breakfast',{},b'original')
        before=[]
        with self.store.transaction() as db:before=[tuple(r) for r in db.execute('SELECT * FROM mina_requests')]
        client=Mock();client.request_state.side_effect=RuntimeError('404')
        args=['operator','lookups','--database',self.store.path,'--directory',str(self.root)]
        output=io.StringIO()
        with patch('sys.argv',args),patch.dict(os.environ,{'MINA_CURRENT_DAY_RECOVERY_ENABLED':'false','MINA_MEAL_API_BASE_URL':'https://www.log-eats.com','MINA_AGENT_API_TOKEN':''}),patch('src.mina.clients.preparation_client',return_value=client),contextlib.redirect_stdout(output):main()
        result=json.loads(output.getvalue());self.assertEqual(result['lookups'][0]['persistence'],'unknown')
        self.assertEqual(result['post_count'],0);self.assertEqual(result['ledger_updates'],0)
        client.request_state.assert_called_once_with(key,'meal_register');client.register.assert_not_called()
        with self.store.transaction() as db:self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM mina_requests')],before)

    def test_policy_decoder_checks_fixed_source_literal_without_executing_it(self):
        from src.mina.current_day_origin import policy_proof
        good=('client.structured('+repr(INSTRUCTIONS)+', content, schema, "mina_menu_nutrition_check")').encode()
        result=Mock(stdout=good)
        with patch('src.mina.current_day_origin.subprocess.run',return_value=result) as run:
            proof=policy_proof(self.root)
            self.assertEqual(proof['module_sha256'],hashlib.sha256(good).hexdigest())
            self.assertIn(LEGACY_COMMIT+':src/mina/meal_replanning.py',run.call_args.args[0])
        with patch('src.mina.current_day_origin.subprocess.run',return_value=Mock(stdout=b'print("not a compatible policy")')):
            with self.assertRaises(ValueError):policy_proof(self.root)

    def test_existing_utm_policy_is_frozen_into_x_preparation_cards(self):
        self.issue()
        with patch.dict(os.environ,{'MINA_X_UTM_ENABLED':'true'}):self.run_pipeline()
        messages=[call.args[0]['content'] for call in self.discord.send_files.call_args_list if '/ x\n' in call.args[0]['content']]
        self.assertEqual(len(messages),4)
        self.assertTrue(all('utm_source=x' in text for text in messages))

    def test_goal_change_without_m1_version_change_stops_remaining_card_delivery(self):
        self.issue()
        def send(*args):
            self.meal.targets={'calories':2000,'protein':None,'fat':None,'carbs':None}
            return {'id':'123'}
        self.discord.send_files.side_effect=send
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.discord.send_files.call_count,1)

    def test_drive_wire_requests_recheck_expiry_and_restore_previous_guard(self):
        from src.mina.current_day_recovery import bounded_drive
        from src.mina.drive import DriveClient
        drive=DriveClient('id','secret','refresh','folder0123456789');drive._transport_request=Mock(return_value=b'{}')
        self.issue()
        with self.assertRaises(ValueError):
            with self.gate.running(),bounded_drive(drive,self.gate):
                self.clock[0]+=timedelta(minutes=15)
                drive._request('https://www.googleapis.com/drive/v3/files',method='POST',write=True)
        drive._transport_request.assert_not_called();self.assertIsNone(drive.request_guard)


if __name__=='__main__':unittest.main()
