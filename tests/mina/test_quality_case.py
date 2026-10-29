"""旧2画像/2holdを保持し、追加昼1生成/1点検から3食/8案へ通す隔離試験。"""
import copy,io,json,os,unittest,uuid
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
import test_current_day_continuation as continuation
from src.mina.current_day_origin import TARGET,capture as capture_origin
from src.mina.preparation import fingerprint
from src.mina.generation import review_schema
from src.mina.quality_checkpoint import capture,verify,VERSION
from src.mina.quality_case import QualityGate,execute
from src.mina.portion_review import validate,CHECKS,visible_indices
from src.mina.replacement_contract import stamp


def positive(meal):
    visible=visible_indices(meal)
    return {'status':'image_acceptable','reason_codes':[],'checks':{k:'pass' for k in CHECKS},'findings':[],
        'foods':[{'food_index':i,'visibility':'visible' if i in visible else 'mixed_ingredient',
            'portion_relation':'no_obvious_difference' if i in visible else 'not_visually_measurable',
            'basis':'visible_relative_amount' if i in visible else 'measurement_unavailable',
            'comparison':'relative_to_planned_dish' if i in visible else 'none','region':'center','observed':None,'unit':'none'} for i in range(len(meal['foods']))]}


class QualityCaseTests(unittest.TestCase):
    def customize(self,f):
        f.draft['meals']['lunch']['foods']=[{'name':'ごはん','amount':'180g'},{'name':'鶏ひき肉','amount':'80g'},{'name':'卵','amount':'1/2個（可食部25g）'}]
        f.draft['meals']['lunch']['image_description']='鶏そぼろと半個分の炒り卵の丼'
        f.ai.plan=copy.deepcopy({k:f.draft[k] for k in ('meals','daily_comment')})
        f.raw=json.dumps(f.draft,ensure_ascii=False,indent=2).encode();(f.root/'draft.json').write_bytes(f.raw)
        inputs={'foods':{s:m['foods'] for s,m in f.draft['meals'].items()}};digest=fingerprint(inputs)
        with f.store.transaction() as db:
            old=db.execute("SELECT * FROM preparation_steps WHERE step LIKE 'menu_nutrition:%'").fetchone()
            values=json.loads(old['result']);values['lunch']=[300,200,78]
            db.execute('DELETE FROM preparation_steps WHERE step=?',(old['step'],))
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",(TARGET,'menu_nutrition:'+digest,digest,json.dumps(values),old['started']))
            digest=fingerprint({'draft_sha256':fingerprint(f.draft),'slot':'dinner','reason_codes':['daily_energy_out_of_range'],'attempt':1})
            db.execute("UPDATE preparation_steps SET digest=? WHERE step='meal_replan:dinner:1'",(digest,))
            f.origin=capture_origin(db,f.draft,f.policy)
            f.approval['origin_digest']=fingerprint(f.origin)

    def setUp(self):
        self.old=continuation.ContinuationTests();self.addCleanup(self.old.doCleanups);self.old.customize_source=self.customize;self.old.setUp()
        self.f=self.old.fixture;f=self.f;self.old.issue();f.ai.review_image=lambda *a:{'status':'hold','reason_codes':['image_analysis_mismatch']}
        with self.assertRaises(Exception):self.old.run_pipeline()
        self.before_images=f.ai.images;self.parent=copy.deepcopy(self.old.snapshot['parent'])
        root=Path(self.old.snapshot['receipts']);d=root/uuid.uuid4().hex;d.mkdir()
        key='image_review:lunch:'+self.old.snapshot['rounds']['lunch']
        with f.store.transaction() as db:
            row=dict(db.execute('SELECT * FROM preparation_steps WHERE step=?',(key,)).fetchone())
            image=json.loads(db.execute('SELECT result FROM preparation_steps WHERE step=?',(key.replace('image_review:','image:',1),)).fetchone()[0])
        schema=review_schema('image_acceptable',True)
        raw={'status':'hold','reason_codes':['image_analysis_mismatch'],'findings':[{'code':'portion_mismatch','region':'left','food_index':2,'expected':25,'observed':None,'unit':'g'}]}
        files={'invocation.json':{'job':d.name,'preparation':{'target_date':TARGET,'step':key,'input_digest':row['digest']},'schema_digest':fingerprint(schema)},
            'schema.json':schema,'result.json':raw,'state.json':{'state':'complete','reason':None},'diagnostic.json':{'job':d.name,'exit_code':0,'input_image_sha256':[image['sha256']]}}
        for name,value in files.items():(d/name).write_text(json.dumps(value))
        (d/'input-0.jpg').write_bytes(Path(image['path']).read_bytes())
        parent_file=f.root/'parent-checkpoint.json';parent_file.write_text(json.dumps(self.old.snapshot))
        trace=('current_day_continuation.py\nsrc.mina.preparation_status.QualityHold: 画像の事前点検で保留になりました\n').encode()
        with f.store.transaction() as db:self.snapshot=capture(db,self.old.snapshot,parent_file,trace)
        f.clock[0]+=timedelta(minutes=20)
        self.approval={'spec':VERSION,'target_date':TARGET,'recovery_uuid':self.parent['recovery_uuid'],'quality_case_uuid':str(uuid.uuid4()),'slot':'lunch',
            'code_commit':'e'*40,'not_after':stamp(f.clock[0]+timedelta(minutes=30)),'checkpoint_digest':fingerprint(self.snapshot),
            'initial_state_version':f.meal.sv,'approval_reference':'隔離の追加昼1画像承認','mode':'prepare_only','cost_mode':'waived'}
        self.env=patch.dict(os.environ,{'MINA_CURRENT_DAY_QUALITY_ENABLED':'true'});self.env.start();self.addCleanup(self.env.stop)
        self.gate=QualityGate(f.store,self.approval,self.snapshot,lambda:f.clock[0],lambda:None,f.inventory)
        self.reply=positive(f.draft['meals']['lunch']);self.typed_calls=0;original=f.ai.structured
        def structured(instructions,content,schema,name):
            if name=='mina_visible_portion_review':self.typed_calls+=1;return copy.deepcopy(self.reply)
            return original(instructions,content,schema,name)
        f.ai.structured=structured;f.ai.review_image=lambda *a:{'status':'image_acceptable','reason_codes':[]}

    def issue(self):return self.gate.issue(self.f.meal.day_state(TARGET),self.f.inventory.check(TARGET))
    def run_pipeline(self):
        f=self.f
        with patch('src.mina.registration.datetime',wraps=datetime) as clock:
            clock.now.side_effect=lambda *a:f.clock[0]
            return execute(self.gate,f.ai,f.drive,f.meal,'logeats.test',f.history,f.capture,f.discord,'e'*40)

    def test_full_recovery_keeps_old_holds_images_calls_and_both_expired_windows(self):
        self.issue();result=self.run_pipeline();self.assertEqual(result['x_cards'],4)
        self.assertEqual(self.f.ai.images-self.before_images,2);self.assertEqual(self.typed_calls,1)
        self.assertEqual(len(self.f.meal.calls),4);self.assertEqual(self.f.discord.send_files.call_count,8)
        with self.f.store.transaction() as db:
            verify(db,self.snapshot)
            self.assertEqual(db.execute('SELECT state FROM current_day_quality_cases').fetchone()[0],'complete')
            self.assertEqual(dict(db.execute('SELECT * FROM current_day_jobs').fetchone()),self.parent)
            self.assertEqual(db.execute("SELECT count(*) FROM preparation_steps WHERE step LIKE 'image:lunch%'").fetchone()[0],3)
            self.assertEqual(db.execute("SELECT count(*) FROM preparation_steps WHERE step LIKE 'meal_replan:%'").fetchone()[0],2)

    def test_null_by_itself_does_not_make_a_pass_or_repeat_a_review(self):
        self.issue();self.reply['foods'][2].update(portion_relation='not_visually_measurable',basis='measurement_unavailable',comparison='none')
        with self.assertRaises(Exception):self.run_pipeline()
        self.assertEqual(self.typed_calls,1);self.assertEqual(self.f.ai.images-self.before_images,1)
        self.assertEqual(self.f.meal.calls,[])
        with self.f.store.transaction() as db:
            verdict=json.loads(db.execute('SELECT verdict FROM quality_case_reviews').fetchone()[0]);self.assertEqual(verdict['reason_codes'],['diagnostic_incomplete'])
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.typed_calls,1)

    def test_visible_excess_with_unknown_grams_remains_hold(self):
        self.issue();self.reply.update(status='hold',reason_codes=['image_analysis_mismatch']);self.reply['checks']['visible_portions']='hold';self.reply['foods'][2]['portion_relation']='obvious_excess'
        with self.assertRaises(Exception):self.run_pipeline()
        self.assertEqual(self.f.ai.images-self.before_images,1);self.assertEqual(self.f.meal.calls,[])

    def test_generation_unknown_is_preserved_and_not_sent_twice(self):
        self.issue();self.f.ai.image=Mock(side_effect=TimeoutError('隔離の画像生成不明'))
        with self.assertRaises(TimeoutError):self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.f.ai.image.call_count,1);self.assertEqual(self.typed_calls,0)

    def test_review_unknown_is_preserved_and_old_image_not_rechecked(self):
        self.issue();self.f.ai.structured=Mock(side_effect=TimeoutError('隔離の点検不明'))
        with self.assertRaises(TimeoutError):self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.f.ai.structured.call_count,1)

    def test_reissue_clock_extension_wrong_slot_and_off_are_rejected(self):
        with patch.dict(os.environ,{'MINA_CURRENT_DAY_QUALITY_ENABLED':'false'}):
            with self.assertRaises(ValueError):self.issue()
        self.approval['slot']='breakfast'
        with self.assertRaises(ValueError):self.issue()
        self.approval['slot']='lunch';self.issue()
        with self.assertRaises(ValueError):self.issue()
        with self.f.store.transaction() as db:db.execute('UPDATE current_day_quality_cases SET activated_at=?,deadline=?',(stamp(self.f.clock[0]+timedelta(minutes=1)),stamp(self.f.clock[0]+timedelta(minutes=16))))
        with self.assertRaises(ValueError):self.run_pipeline()

    def test_old_hold_or_continuation_change_refuses_before_generation(self):
        self.issue()
        with self.f.store.transaction() as db:db.execute("UPDATE current_day_continuations SET state='issued'")
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.f.ai.images,self.before_images)

    def test_old_image_or_receipt_mutation_refuses(self):
        self.issue();p=Path(self.snapshot['files'][0]['path']);p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.f.ai.images,self.before_images)

    def test_long_generation_refreshes_inventory_before_review(self):
        self.issue();original=self.f.ai.image
        def image(description):self.f.clock[0]+=timedelta(seconds=130);return original(description)
        self.f.ai.image=image;self.assertEqual(self.run_pipeline()['state'],'preparation_cards_delivered')

    def test_expiry_and_inventory_unknown_stop_before_review(self):
        self.issue();original=self.f.ai.image
        def image(description):
            self.f.clock[0]+=timedelta(seconds=130);self.f.inventory.read_page=Mock(side_effect=ValueError('隔離の一覧不明'))
            return original(description)
        self.f.ai.image=image
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.typed_calls,0);self.assertEqual(self.f.meal.calls,[])

    def test_discord_lost_response_does_not_resend(self):
        self.issue();self.f.discord.send_files.side_effect=TimeoutError('隔離のDiscord不明')
        with self.assertRaises(TimeoutError):self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.f.discord.send_files.call_count,1)

    def test_existing_m1_correction_path_is_once_and_old_images_stay_fixed(self):
        self.issue();self.f.ai.mode='correct';self.f.meal.mode='correct'
        self.assertEqual(self.run_pipeline()['state'],'preparation_cards_delivered')
        self.assertEqual([(method,slot) for method,slot,key in self.f.meal.calls],[('POST','breakfast'),('PATCH','breakfast'),('POST','lunch'),('POST','dinner'),('POST','daily')])
        with self.f.store.transaction() as db:verify(db,self.snapshot)

    def test_saved_response_loss_is_not_posted_again(self):
        self.issue();self.f.meal.mode='post_unknown'
        with self.assertRaises(Exception):self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(len(self.f.meal.calls),1)

    def test_new_foreign_call_and_existing_card_are_not_adopted(self):
        from src.mina.current_day_cards import initialize
        with self.f.store.transaction() as db:
            initialize(db);db.execute("INSERT INTO current_day_cards VALUES(?,?,?,?,?,'unknown',NULL)",(self.approval['recovery_uuid'],'x','lunch','{}','a'*64))
        with self.assertRaises(ValueError):self.issue()
        with self.f.store.transaction() as db:
            db.execute('DELETE FROM current_day_cards')
            db.execute("INSERT INTO current_day_calls(recovery_uuid,operation,input_digest,state,cost_mode) VALUES(?,'ai:image',?,'unknown','waived')",(self.approval['recovery_uuid'],'f'*64))
        with self.assertRaises(ValueError):self.issue()

    def test_direct_extra_lunch_and_replan_calls_are_rejected_before_ai(self):
        from src.mina.current_day_gate import Operations
        from src.mina.generation_receipts import operation
        self.issue();fake=Mock()
        with self.assertRaises(ValueError):
            with self.gate.running(),operation(TARGET,'meal_replan:dinner:3','f'*64):
                Operations(self.gate).invoke('ai:structured',{},fake)
        fake.assert_not_called()

    def test_off_cli_status_and_case_do_not_construct_clients(self):
        import contextlib
        from src.mina.quality_case_cli import main
        args=['quality','run','--database',self.f.store.path,'--directory',str(self.f.root)]
        with patch.dict(os.environ,{'MINA_CURRENT_DAY_QUALITY_ENABLED':'false'}),patch('sys.argv',args),patch('src.mina.quality_case_cli.scoped_meal') as client:
            with self.assertRaisesRegex(ValueError,'disabled'):main()
            client.assert_not_called()
        args[1]='status';output=io.StringIO()
        with patch('sys.argv',args),contextlib.redirect_stdout(output):main()
        self.assertEqual(json.loads(output.getvalue()),{'state':'not_activated'})

    def test_instruction_artifact_mutation_stops_before_new_review(self):
        self.issue();original=self.f.ai.image
        def image(description):
            path=Path(self.snapshot['output'])/'images'/self.approval['quality_case_uuid']/'generation-policy.json'
            raw=original(description);path.write_bytes(path.read_bytes()+b' ');return raw
        self.f.ai.image=image
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.typed_calls,0);self.assertEqual(self.f.meal.calls,[])


class PortionReviewTests(unittest.TestCase):
    def setUp(self):self.meal={'foods':[{'name':'ごはん','amount':'180g'},{'name':'鶏ひき肉','amount':'80g'},{'name':'卵','amount':'1/2個（可食部25g）'},{'name':'油','amount':'2g'}]}
    def test_unmeasurable_mixed_seasoning_does_not_invent_weight(self):
        self.assertEqual(validate(positive(self.meal),self.meal),{'status':'image_acceptable','reason_codes':[]})
    def test_visible_food_cannot_be_hidden_as_mixed_ingredient(self):
        value=positive(self.meal);value['foods'][2].update(visibility='mixed_ingredient',portion_relation='not_visually_measurable',basis='measurement_unavailable',comparison='none')
        self.assertEqual(validate(value,self.meal)['reason_codes'],['diagnostic_incomplete'])
    def test_precise_grams_or_counted_scrambled_egg_are_not_fabricated(self):
        value=positive(self.meal);value['foods'][2].update(observed=25,unit='none')
        self.assertEqual(validate(value,self.meal)['status'],'hold')
        value['foods'][2].update(observed=.5,unit='count',basis='countable_units',comparison='countable_unit')
        self.assertEqual(validate(value,self.meal)['status'],'hold')
    def test_old_hold_shape_cannot_become_pass(self):
        self.assertEqual(validate({'status':'hold','reason_codes':['image_analysis_mismatch'],'findings':[]},self.meal)['reason_codes'],['diagnostic_incomplete'])
    def test_hold_with_only_unknown_measurement_keeps_diagnostic_incomplete(self):
        value=positive(self.meal);value['status']='hold';value['reason_codes']=['image_analysis_mismatch']
        self.assertEqual(validate(value,self.meal)['reason_codes'],['diagnostic_incomplete'])


if __name__=='__main__':unittest.main()
