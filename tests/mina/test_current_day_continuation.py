"""実SQLiteと工程で今回の停止を再現し、外部CLI/業務サービスだけを代替する。"""
import copy,hashlib,io,json,os,unittest,uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock,patch
import test_current_day_recovery as recovery
from src.mina.preparation import fingerprint
from src.mina.current_day_checkpoint import capture,verify,VERSION
from src.mina.current_day_continuation import ContinuationGate,execute
from src.mina.current_day_origin import TARGET
from src.mina.replacement_contract import stamp


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=recovery.CurrentDayRecoveryTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;f.issue();calls=[0];reviews={}
        from PIL import Image
        png=io.BytesIO();Image.new('RGB',(200,160),'#e1b990').save(png,'PNG');self.png=png.getvalue()
        def producer(description):
            f.ai.images+=1
            with Image.open(io.BytesIO(self.png)) as im:
                result=io.BytesIO();im.convert('RGB').save(result,format='JPEG',quality=90,optimize=True)
            return result.getvalue()
        def image(description):
            calls[0]+=1
            raw=producer(description)
            if calls[0]==4:
                f.clock[0]+=timedelta(seconds=130)
                # e491以前の、Journal前に更新できない境界を外部だけの遅延で再現。
                f.gate.refresh_external=lambda:None
            return raw
        def review(raw,meal):
            key=meal['menu'];reviews[key]=reviews.get(key,0)+1
            if key in (f.draft['meals']['breakfast']['menu'],f.draft['meals']['lunch']['menu']) and reviews[key]==1:return {'status':'hold','reason_codes':['image_analysis_mismatch']}
            return {'status':'image_acceptable','reason_codes':[]}
        f.ai.image=image;f.ai.review_image=review
        with self.assertRaisesRegex(ValueError,'外部予約の鮮度'):f.run_pipeline()
        self.old_images=f.ai.images;self.assertEqual(self.old_images,4)
        receipts=f.root/'receipt-fixture';receipts.mkdir()
        with f.store.transaction() as db:
            steps=[dict(r) for r in db.execute('SELECT * FROM preparation_steps')]
            old={r['step'] for r in f.origin['steps']}
            jobs=[r for r in steps if r['step'] not in old and r['step'].startswith(('image:','image_review:','meal_replan:','menu_nutrition:'))]
            by={r['step']:r for r in steps}
        # 実CLIのI/Oだけを代替し、元Journal入力・画像実バイトを保持する。
        for row in jobs:
            key=uuid.uuid4().hex;directory=receipts/key;directory.mkdir()
            prep={'target_date':TARGET,'step':row['step'],'input_digest':row['digest']}
            invocation={'spec':'generation-invocation-v1','job':key,'preparation':prep,'schema_digest':fingerprint({}),'stdin_sha256':'a'*64}
            files={'invocation.json':invocation,'schema.json':{},'result.json':json.loads(row['result']),'state.json':{'state':'complete','reason':None},'diagnostic.json':{'job':key,'exit_code':0,'input_image_sha256':[]}}
            if row['step']=='meal_replan:dinner:2':files['result.json']=files['result.json']['draft']['meals']['dinner']
            if row['step'].startswith('image:'):files['result.json']={'generated':True}
            if row['step'].startswith('image_review:'):
                source=json.loads(by[row['step'].replace('image_review:','image:',1)]['result'])
                files['diagnostic.json']['input_image_sha256']=[source['sha256']]
                (directory/'input-0.jpg').write_bytes(Path(source['path']).read_bytes())
            if row['step'].startswith('image:'):(directory/'image.png').write_bytes(self.png)
            for name,value in files.items():(directory/name).write_text(json.dumps(value))
        self.trace=b'current_day_gate.py\npreparation.py\n'+('ValueError: 外部予約の鮮度・原本を再照合してください\n').encode()
        with f.store.transaction() as db:self.snapshot=capture(db,f.root,receipts,self.trace)
        self.parent=copy.deepcopy(self.snapshot['parent']);f.clock[0]+=timedelta(minutes=20)
        self.approval={'spec':VERSION,'target_date':TARGET,'recovery_uuid':self.parent['recovery_uuid'],'continuation_uuid':str(uuid.uuid4()),
            'code_commit':'d'*40,'not_after':stamp(f.clock[0]+timedelta(minutes=30)),'checkpoint_digest':fingerprint(self.snapshot),
            'initial_state_version':f.meal.sv,'approval_reference':'隔離の継続承認','mode':'prepare_only','cost_mode':'waived'}
        self.env=patch.dict(os.environ,{'MINA_CURRENT_DAY_CONTINUATION_ENABLED':'true'});self.env.start();self.addCleanup(self.env.stop)
        self.gate=ContinuationGate(f.store,self.approval,self.snapshot,lambda:f.clock[0],lambda:None,f.inventory)
        self.read_page=Mock(wraps=f.inventory.read_page);f.inventory.read_page=self.read_page

    def issue(self):
        f=self.fixture
        return self.gate.issue(f.meal.day_state(TARGET),f.inventory.check(TARGET))

    def run_pipeline(self):
        f=self.fixture
        with patch('src.mina.registration.datetime',wraps=__import__('datetime').datetime) as clock:
            clock.now.side_effect=lambda *args:f.clock[0]
            return execute(self.gate,f.ai,f.drive,f.meal,'logeats.test',f.history,f.capture,f.discord,'d'*40)

    def test_exact_stop_continues_without_recreating_images_plans_or_parent(self):
        self.issue();f=self.fixture;result=self.run_pipeline()
        self.assertEqual(result['x_cards'],4);self.assertEqual(result['instagram_cards'],4)
        self.assertEqual(f.ai.images-self.old_images,1);self.assertEqual(f.discord.send_files.call_count,8)
        self.assertEqual(len(f.meal.calls),4)
        with f.store.transaction() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM current_day_jobs').fetchone()),self.parent)
            self.assertEqual(db.execute('SELECT state FROM current_day_continuations').fetchone()[0],'complete')
            self.assertEqual(db.execute("SELECT count(*) FROM preparation_steps WHERE step LIKE 'meal_replan:%'").fetchone()[0],2)
            verify(db,self.snapshot)

    def test_off_default_creates_no_continuation(self):
        with patch.dict(os.environ,{'MINA_CURRENT_DAY_CONTINUATION_ENABLED':'false'}):
            with self.assertRaises(ValueError):self.issue()
        with self.fixture.store.transaction() as db:self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='current_day_continuations'").fetchone())

    def test_reissue_restart_and_expiry_do_not_extend_new_or_old_window(self):
        deadline=self.issue();self.assertEqual(deadline,self.fixture.clock[0]+timedelta(minutes=15))
        with self.assertRaises(ValueError):self.issue()
        with self.gate.running():pass
        self.read_page.reset_mock()
        with self.assertRaises(ValueError):self.run_pipeline()
        self.read_page.assert_not_called()

    def test_image_mutation_or_receipt_mutation_refuses_before_external_work(self):
        p=Path(self.snapshot['files'][0]['path']);p.write_bytes(p.read_bytes()+b' ')
        self.read_page.reset_mock()
        with self.assertRaises(ValueError):
            with self.gate.store.transaction() as db:self.gate.parent(db)
        self.read_page.assert_not_called()

    def test_parent_deadline_mutation_refuses(self):
        with self.fixture.store.transaction() as db:db.execute("UPDATE current_day_jobs SET deadline='2026-10-29T23:59:00.000Z'")
        with self.assertRaises(ValueError):self.issue()

    def test_changed_old_input_digest_refuses(self):
        with self.fixture.store.transaction() as db:db.execute("UPDATE preparation_steps SET digest=? WHERE step='image:lunch'",('f'*64,))
        with self.assertRaises(ValueError):self.issue()

    def test_new_unknown_or_existing_m1_does_not_become_another_continuation(self):
        f=self.fixture
        with f.store.transaction() as db:db.execute("UPDATE current_day_calls SET state='unknown' WHERE operation='ai:image'")
        with self.assertRaises(ValueError):self.issue()

    def test_lunch_repair_failure_does_not_generate_dinner_or_send(self):
        self.issue();f=self.fixture;f.ai.review_image=lambda *args:{'status':'hold','reason_codes':['image_analysis_mismatch']}
        with self.assertRaises(Exception):self.run_pipeline()
        self.assertEqual(f.ai.images,self.old_images);self.assertEqual(f.meal.calls,[]);f.discord.send_files.assert_not_called()

    def test_unknown_lunch_review_is_not_retried(self):
        self.issue();f=self.fixture;review=Mock(side_effect=TimeoutError('隔離の応答喪失'));f.ai.review_image=review
        with self.assertRaises(TimeoutError):self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(review.call_count,1);self.assertEqual(f.ai.images,self.old_images)

    def test_less_than_sixty_seconds_or_future_first_is_rejected(self):
        self.issue();self.fixture.clock[0]+=timedelta(minutes=14,seconds=1)
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.fixture.ai.images,self.old_images)

    def test_long_dinner_refreshes_inventory_and_keeps_parent_unchanged(self):
        self.issue();f=self.fixture;original=f.ai.image
        def image(description):f.clock[0]+=timedelta(seconds=130);return original(description)
        f.ai.image=image;self.assertEqual(self.run_pipeline()['state'],'preparation_cards_delivered')
        with f.store.transaction() as db:self.assertEqual(dict(db.execute('SELECT * FROM current_day_jobs').fetchone()),self.parent)

    def test_discord_response_loss_is_not_sent_again(self):
        self.issue();f=self.fixture;f.discord.send_files.side_effect=TimeoutError('隔離のDiscord応答喪失')
        with self.assertRaises(TimeoutError):self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(f.discord.send_files.call_count,1)

    def test_missing_real_receipt_and_wrong_stop_reason_cannot_issue(self):
        f=self.fixture;receipts=Path(self.snapshot['receipts'])
        next(receipts.glob('*/diagnostic.json')).unlink()
        with f.store.transaction() as db:
            with self.assertRaises((ValueError,FileNotFoundError)):capture(db,f.root,receipts,self.trace)
            with self.assertRaises(ValueError):capture(db,f.root,receipts,'ValueError: 別障害'.encode())

    def test_wrong_approval_snapshot_and_nonempty_day_are_rejected(self):
        self.approval['checkpoint_digest']='f'*64
        with self.assertRaises(ValueError):self.issue()
        self.approval['checkpoint_digest']=fingerprint(self.snapshot)
        state=self.fixture.meal.day_state(TARGET);state['meal_count']=1
        with self.assertRaises(ValueError):self.gate.issue(state,self.fixture.inventory.check(TARGET))

    def test_hold_owner_or_new_unknown_blocks_activation(self):
        with self.fixture.store.transaction() as db:db.execute("UPDATE publication_holds SET epoch=epoch+1 WHERE target_date=?",(TARGET,))
        with self.assertRaises(ValueError):self.issue()

    def test_concurrent_claim_and_first_time_change_do_not_start_ai(self):
        self.issue()
        with self.gate.running():
            with self.assertRaises(ValueError):
                with self.gate.running():self.fail('二重起動禁止')
        self.assertEqual(self.fixture.ai.images,self.old_images)
        with self.fixture.store.transaction() as db:db.execute("UPDATE current_day_continuations SET activated_at=?,deadline=?",(stamp(self.fixture.clock[0]+timedelta(minutes=1)),stamp(self.fixture.clock[0]+timedelta(minutes=16))))
        with self.assertRaises(ValueError):self.gate.refresh_external()

    def test_expired_dinner_and_unknown_inventory_do_not_reach_drive(self):
        self.issue();f=self.fixture;original=f.ai.image
        def image(description):
            f.clock[0]+=timedelta(seconds=130);f.inventory.read_page=Mock(side_effect=ValueError('隔離一覧不明'))
            return original(description)
        f.ai.image=image
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(f.drive.uploads,{});self.assertEqual(f.meal.calls,[]);f.discord.send_files.assert_not_called()

    def test_dinner_repairs_only_once_and_never_replans(self):
        self.issue();f=self.fixture;normal=f.ai.review_image
        def review(raw,meal):
            if meal['menu']==f.ai.plan['meals']['dinner']['menu']:return {'status':'hold','reason_codes':['image_analysis_mismatch']}
            return normal(raw,meal)
        f.ai.review_image=review
        with self.assertRaises(Exception):self.run_pipeline()
        self.assertEqual(f.ai.images-self.old_images,2)
        with f.store.transaction() as db:self.assertEqual(db.execute("SELECT count(*) FROM preparation_steps WHERE step LIKE 'meal_replan:%'").fetchone()[0],2)

    def test_cli_off_and_read_only_status_do_not_construct_clients(self):
        import contextlib
        from src.mina.current_day_continuation_cli import main
        args=['operator','run','--database',self.fixture.store.path,'--directory',str(self.fixture.root)]
        with patch.dict(os.environ,{'MINA_CURRENT_DAY_CONTINUATION_ENABLED':'false'}),patch('sys.argv',args),patch('src.mina.current_day_continuation_cli.scoped_meal') as client:
            with self.assertRaisesRegex(ValueError,'disabled'):main()
            client.assert_not_called()
        args[1]='status';output=io.StringIO()
        with patch('sys.argv',args),contextlib.redirect_stdout(output):main()
        self.assertEqual(json.loads(output.getvalue()),{'state':'not_activated'})

    def test_receipt_pass_substitution_for_old_hold_is_rejected(self):
        f=self.fixture;root=Path(self.snapshot['receipts'])
        for p in root.glob('*/invocation.json'):
            if json.loads(p.read_bytes())['preparation']['step']=='image_review:breakfast':
                (p.parent/'result.json').write_text(json.dumps({'status':'image_acceptable','reason_codes':[]}));break
        with f.store.transaction() as db:
            with self.assertRaisesRegex(ValueError,'判定が不一致'):capture(db,f.root,root,self.trace)

    def test_extra_unknown_operation_and_all_existing_card_states_refuse_issue(self):
        f=self.fixture
        with f.store.transaction() as db:
            db.execute("INSERT INTO current_day_calls(recovery_uuid,operation,input_digest,state,cost_mode) VALUES(?,'ai:image',?,'unknown','waived')",(self.parent['recovery_uuid'],'f'*64))
        with self.assertRaises(ValueError):self.issue()
        with f.store.transaction() as db:db.execute("DELETE FROM current_day_calls WHERE input_digest=?",('f'*64,))
        from src.mina.current_day_cards import initialize
        for state in ('prepared','sending','unknown','sent'):
            with f.store.transaction() as db:
                initialize(db);db.execute('DELETE FROM current_day_cards')
                db.execute('INSERT INTO current_day_cards VALUES(?,?,?,?,?,?,NULL)',(self.parent['recovery_uuid'],'x','breakfast','{}','a'*64,state))
            with self.assertRaises(ValueError):self.issue()

    def test_result_digest_or_producer_png_change_is_rejected(self):
        f=self.fixture
        with f.store.transaction() as db:
            db.execute("UPDATE current_day_calls SET result_digest=? WHERE operation='ai:structured'",('f'*64,))
            with self.assertRaisesRegex(ValueError,'旧操作digest'):capture(db,f.root,self.snapshot['receipts'],self.trace)

    def test_positive_review_with_conflicting_findings_is_rejected(self):
        f=self.fixture;root=Path(self.snapshot['receipts'])
        for p in root.glob('*/invocation.json'):
            if json.loads(p.read_bytes())['preparation']['step'].startswith('image_review:breakfast:'):
                (p.parent/'result.json').write_text(json.dumps({'status':'image_acceptable','reason_codes':[],'findings':[{'code':'uncertain','region':'unknown','food_index':-1,'expected':None,'observed':None,'unit':'none'}]}));break
        with f.store.transaction() as db:
            with self.assertRaises(ValueError):capture(db,f.root,root,self.trace)


if __name__=='__main__':unittest.main()
