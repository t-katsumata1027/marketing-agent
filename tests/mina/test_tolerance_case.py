"""旧品質保留を保持した概算許容点検と、実工程の隔離受入。"""
import copy,io,json,os,unittest,uuid
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
import test_quality_case as quality
from src.mina.tolerance_checkpoint import capture,verify,VERSION
from src.mina.tolerance_case import ToleranceGate,execute
from src.mina.tolerance_review import validate,CHECKS,policy_sha256,scoped_policy
from src.mina.preparation import fingerprint
from src.mina.current_day_origin import TARGET
from src.mina.replacement_contract import stamp

def positive(meal,kind='image_acceptable'):
    return {'status':kind,'checks':{k:('not_applicable' if k=='nutrition' and kind=='image_acceptable' else 'pass') for k in CHECKS},
      'foods':[{'food_index':i,'presence':'visible','portion':'plausible','evidence':'no_substantial_difference','region':'center','observed':None} for i in range(len(meal['foods']))],
      'warnings':[],'blocking':[]}

class ToleranceTests(unittest.TestCase):
    def setUp(self):
        self.q=quality.QualityCaseTests();self.addCleanup(self.q.doCleanups);self.q.setUp();f=self.f=self.q.f
        self.q.issue();self.q.reply.update(status='hold',reason_codes=['image_analysis_mismatch']);self.q.reply['checks']['visible_portions']='hold';self.q.reply['foods'][2]['portion_relation']='obvious_excess'
        with self.assertRaises(Exception):self.q.run_pipeline()
        self.before_images=f.ai.images
        with f.store.transaction() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM preparation_steps')];by={r['step']:r for r in rows}
        for key in ('image:lunch:'+self.q.approval['quality_case_uuid'],'image_review:lunch:'+self.q.approval['quality_case_uuid']):
            row=by[key];d=Path(self.q.snapshot['receipts'])/uuid.uuid4().hex;d.mkdir()
            image=json.loads(by['image:lunch:'+self.q.approval['quality_case_uuid']]['result'])
            from src.mina.portion_review import schema as old_schema
            from src.mina.generation import object_schema
            schema=object_schema({'generated':{'type':'boolean'}}) if key.startswith('image:') else old_schema()
            raw=json.loads(row['result']);diag={'job':d.name,'exit_code':0,'input_image_sha256':[]}
            if key.startswith('image:'):
                raw={'generated':True};(d/'image.png').write_bytes(self.q.old.png)
            else:
                diag['input_image_sha256']=[image['sha256']];(d/'input-0.jpg').write_bytes(Path(image['path']).read_bytes())
            files={'invocation.json':{'job':d.name,'preparation':{'target_date':TARGET,'step':key,'input_digest':row['digest']},'schema_digest':fingerprint(schema)},'schema.json':schema,'result.json':raw,'state.json':{'state':'complete','reason':None},'diagnostic.json':diag}
            for name,value in files.items():(d/name).write_text(json.dumps(value))
        p=f.root/'quality-checkpoint.json';p.write_text(json.dumps(self.q.snapshot))
        with f.store.transaction() as db:self.snapshot=capture(db,self.q.snapshot,p)
        f.clock[0]+=timedelta(minutes=20)
        self.approval={'spec':VERSION,'target_date':TARGET,'recovery_uuid':self.q.approval['recovery_uuid'],'tolerance_case_uuid':str(uuid.uuid4()),'slot':'lunch',
          'selected_image_sha256':self.snapshot['selected_image']['sha256'],'code_commit':'f'*40,'not_after':stamp(f.clock[0]+timedelta(minutes=30)),
          'checkpoint_digest':fingerprint(self.snapshot),'initial_state_version':f.meal.sv,'approval_reference':'隔離の概算許容承認','mode':'prepare_only','cost_mode':'waived'}
        self.env=patch.dict(os.environ,{'MINA_CURRENT_DAY_TOLERANCE_ENABLED':'true'});self.env.start();self.addCleanup(self.env.stop)
        self.gate=ToleranceGate(f.store,self.approval,self.snapshot,lambda:f.clock[0],lambda:None,f.inventory)
        self.review_calls=[];self.mode='pass';old=f.ai.structured
        def structured(instructions,content,schema,name):
            if name!='mina_approximate_portion_review':return old(instructions,content,schema,name)
            context=json.loads(content[0]['text']);kind=context['positive'];value=positive(context['planned_meal'],kind);self.review_calls.append(context)
            if len(self.review_calls)==1:
                value['foods'][2].update(portion='minor_variation',evidence='minor_visual_variation')
                value['warnings']=[{'code':'minor_portion_variation','region':'left','food_index':2},{'code':'minor_rim_crop','region':'right','food_index':-1}]
            if self.mode=='hold':value.update(status='hold',blocking=[{'code':'food_missing','region':'center','food_index':0}]);value['checks']['food_identity']='hold'
            if self.mode in ('saved_image_hold','saved_crop_hold') and kind=='publishable':
                code='food_cropped' if self.mode=='saved_crop_hold' else 'major_portion_difference'
                value.update(status='hold',blocking=[{'code':code,'region':'center','food_index':0}]);value['checks']['visible_portions']='hold'
            if self.mode=='correct' and kind=='publishable' and context['analysis_response']['result']['analysis']['foods'][0]['amount']=='300g':
                value.update(status='hold',blocking=[{'code':'nutrition_inconsistent','region':'whole','food_index':0}]);value['checks']['nutrition']='hold'
            return value
        f.ai.structured=structured
    def issue(self):return self.gate.issue(self.f.meal.day_state(TARGET),self.f.inventory.check(TARGET))
    def run_pipeline(self):
        f=self.f
        with patch('src.mina.registration.datetime',wraps=datetime) as clock:
            clock.now.side_effect=lambda *a:f.clock[0]
            return execute(self.gate,f.ai,f.drive,f.meal,'logeats.test',f.history,f.capture,f.discord,'f'*40)
    def test_complete_without_lunch_generation_preserves_old_holds_warnings_and_policy(self):
        self.issue();result=self.run_pipeline();self.assertEqual(result['x_cards'],4);self.assertEqual(result['instagram_cards'],4)
        self.assertEqual(self.f.ai.images-self.before_images,1);self.assertEqual(self.f.discord.send_files.call_count,8);self.assertEqual(len(self.f.meal.calls),4)
        for context in self.review_calls:
            if context['positive']=='publishable':
                self.assertEqual(context['publication_context']['target_date_jst'],TARGET)
        with self.f.store.transaction() as db:
            verify(db,self.snapshot);reviews=[dict(r) for r in db.execute('SELECT * FROM tolerance_case_reviews')];self.assertEqual(len(reviews),5)
            self.assertEqual(len(json.loads(reviews[0]['raw'])['warnings']),2)
            for r in reviews:self.assertEqual(r['policy_sha256'],policy_sha256())
            for r in db.execute("SELECT step,digest FROM preparation_steps WHERE step='image_review:dinner' OR step LIKE 'meal_review:%'"):
                self.assertTrue(any(x['step']==r['step'] for x in reviews))
            self.assertEqual(db.execute('SELECT state FROM current_day_quality_cases').fetchone()[0],'held')
    def test_new_raw_hold_stops_without_food_or_lunch_generation_and_reissue(self):
        self.mode='hold';self.issue()
        with self.assertRaises(Exception):self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()
        with self.assertRaises(ValueError):self.issue()
        self.assertEqual(len(self.review_calls),1);self.assertEqual(self.f.ai.images,self.before_images);self.assertEqual(self.f.meal.calls,[])
    def test_review_unknown_not_resent_and_old_image_retained(self):
        self.issue();self.f.ai.structured=Mock(side_effect=TimeoutError('隔離点検不明'))
        with self.assertRaises(TimeoutError):self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.f.ai.structured.call_count,1)
        with self.f.store.transaction() as db:verify(db,self.snapshot)
    def test_snapshot_or_selected_image_or_quality_case_mutation_refused(self):
        self.approval['selected_image_sha256']='0'*64
        with self.assertRaises(ValueError):self.issue()
        self.approval['selected_image_sha256']=self.snapshot['selected_image']['sha256']
        with self.f.store.transaction() as db:db.execute("UPDATE current_day_quality_cases SET state='issued'")
        with self.assertRaises(ValueError):self.issue()
    def test_off_and_late_execution_stop_without_ai(self):
        with patch.dict(os.environ,{'MINA_CURRENT_DAY_TOLERANCE_ENABLED':'false'}):
            with self.assertRaises(ValueError):self.issue()
        self.issue();self.f.clock[0]+=timedelta(minutes=14,seconds=1)
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.review_calls,[])
    def test_foreign_ai_generation_and_replanning_are_refused_before_send(self):
        from src.mina.current_day_gate import Operations
        from src.mina.generation_receipts import operation
        self.issue();call=Mock()
        with self.assertRaises(ValueError):
            with self.gate.running(),operation(TARGET,'image:lunch','a'*64):Operations(self.gate).invoke('ai:image',{},call)
        call.assert_not_called()
    def test_discord_unknown_and_saved_response_loss_do_not_resend(self):
        self.issue();self.f.discord.send_files.side_effect=TimeoutError('隔離Discord不明')
        with self.assertRaises(TimeoutError):self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(self.f.discord.send_files.call_count,1)
    def test_saved_image_hold_does_not_generate_correction_or_patch(self):
        self.mode='saved_image_hold';self.issue()
        with self.assertRaises(Exception):self.run_pipeline()
        self.assertEqual(self.f.ai.proposals,0);self.assertEqual(len(self.f.meal.calls),1);self.assertEqual(self.f.meal.calls[0][0],'POST')
    def test_saved_crop_hold_does_not_generate_correction_or_patch(self):
        self.mode='saved_crop_hold';self.issue()
        with self.assertRaises(Exception):self.run_pipeline()
        self.assertEqual(self.f.ai.proposals,0);self.assertEqual(len(self.f.meal.calls),1)
    def test_proven_numeric_quantity_error_can_use_existing_single_correction(self):
        self.mode='correct';self.f.meal.mode='correct';self.issue()
        self.assertEqual(self.run_pipeline()['state'],'preparation_cards_delivered')
        self.assertEqual(self.f.ai.proposals,1);self.assertEqual([m for m,s,k in self.f.meal.calls],['POST','PATCH','POST','POST','POST'])
    def test_saved_response_unknown_is_not_posted_again(self):
        self.f.meal.mode='post_unknown';self.issue()
        with self.assertRaises(Exception):self.run_pipeline()
        with self.assertRaises(ValueError):self.run_pipeline()
        self.assertEqual(len(self.f.meal.calls),1)
    def test_slow_review_refreshes_inventory_before_next_external_operation(self):
        old=self.f.ai.structured
        def slow(*a,**kw):
            result=old(*a,**kw);self.f.clock[0]+=timedelta(seconds=130);return result
        self.f.ai.structured=slow;self.issue()
        self.assertEqual(self.run_pipeline()['state'],'preparation_cards_delivered')
    def test_third_dinner_generation_is_rejected_before_external_send(self):
        from src.mina.current_day_gate import Operations
        from src.mina.preparation import PreparationJournal
        self.issue();call=Mock()
        with self.assertRaisesRegex(ValueError,'夕画像は'):
            with self.gate.running():
                with self.f.store.transaction() as db:
                    for step in ('image:dinner:old1','image:dinner:old2'):
                        key=db.execute("INSERT INTO current_day_calls(recovery_uuid,operation,input_digest,state,result_digest,cost_mode) VALUES(?,'ai:image',?,'complete',?,'waived')",(self.approval['recovery_uuid'],'a'*64,'b'*64)).lastrowid
                        db.execute('INSERT INTO tolerance_case_calls VALUES(?,?,?,?)',(self.approval['tolerance_case_uuid'],key,'ai:image',step))
                PreparationJournal(self.f.store).run(TARGET,'image:dinner',{'isolated':True},lambda:Operations(self.gate).invoke('ai:image',{},call))
        call.assert_not_called()

class ToleranceReviewTests(unittest.TestCase):
    def setUp(self):self.meal={'foods':[{'name':'ごはん'},{'name':'鶏ひき肉'},{'name':'卵'}]}
    def test_minor_variation_unmeasurable_and_rim_crop_are_warnings(self):
        value=positive(self.meal);value['foods'][2].update(portion='unmeasurable',evidence='cannot_measure_weight');value['warnings']=[{'code':'weight_unmeasurable','food_index':2,'region':'left'},{'code':'minor_rim_crop','food_index':-1,'region':'right'}]
        self.assertEqual(validate(value,self.meal,'image_acceptable')['status'],'image_acceptable')
    def test_major_difference_absence_unknown_identity_or_food_crop_remain_hold(self):
        for code in ('major_portion_difference','food_missing','food_identity_unknown','food_cropped'):
            value=positive(self.meal);value.update(status='hold',blocking=[{'code':code,'region':'left','food_index':2}]);value['checks']['visible_portions']='hold'
            self.assertEqual(validate(value,self.meal,'image_acceptable')['status'],'hold')
    def test_inconsistent_positive_old_hold_or_fabricated_weight_never_become_pass(self):
        for value in ({'status':'hold','reason_codes':['image_analysis_mismatch']},positive(self.meal)):
            if 'foods' in value:value['foods'][2]['observed']=25
            self.assertEqual(validate(value,self.meal,'image_acceptable')['status'],'hold')
        value=positive(self.meal);value['foods'][2].update(portion='major_excess',evidence='one_vs_multiple_servings')
        self.assertEqual(validate(value,self.meal,'image_acceptable')['status'],'hold')
    def test_custom_policy_requires_current_authorized_case_and_default_is_unchanged(self):
        self.assertEqual(scoped_policy('old',None),'old')
        with self.assertRaises(ValueError):scoped_policy('old',policy_sha256())

if __name__=='__main__':unittest.main()
