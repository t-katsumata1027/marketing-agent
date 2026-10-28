"""同caseの明示再点検を実SQLiteと合成原本で検査する。外部通信は行わない。"""
import copy,hashlib,json,unittest,uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.replacement_contract import canonical,digest,stamp
from src.mina.replacement_client import timestamp
from src.mina.scoped_rescue_flow import ScopedRescueFlow
from src.mina.scoped_rescue_contract import SCHEMA_SHA
from src.mina.scoped_review_history import complete_history,parent_history
from src.mina.scoped_review_refresh import source_digest


class RefreshTests(unittest.TestCase):
    def setUp(self):
        from tests.mina.test_scoped_rescue import ScopedOriginTests
        ScopedOriginTests.setUp(self);n=self.native
        flags=patch.dict('os.environ',{'MINA_SCOPED_RESCUE_ENABLED':'true'});flags.start();self.addCleanup(flags.stop)
        from tests.mina.test_replacement_integration import fresh
        normal=Mock()
        def pre(target):
            value=copy.deepcopy(n.pre);value['freshness']=fresh(n.now);return value
        normal.preconditions.side_effect=pre
        old=n.meal.day_state.side_effect
        def state(target):
            value=old(target);value['checked_at']=stamp(n.now);return value
        n.meal.day_state.side_effect=state
        self.flow=ScopedRescueFlow(n.store,Mock(),normal,n.policy,Mock(),Mock(),lambda:n.now,n.target,'lunch',str(uuid.uuid4()))
        self.anchors=[{'target_slot':m['expected']['target_slot'],'target_result_version':m['expected']['target_result_version'],
            'original_sha256':m['image_sha256'],'stored_sha256':m['stored_image_sha256'],'analysis_digest':m['analysis_digest'],'ogp_input_digest':'d'*24}
            for m in n.pre['meals'] if m['expected']['target_slot']!='lunch']
        self.policy='synthetic-review-v3';self.end=stamp(n.now+timedelta(minutes=15))
        self.args=(n.meal,n.ai,n.capture,n.draft,n.saved,'www.log-eats.com',self.anchors,self.policy,self.end,n.e['owner'],1)
        self.original=self.flow.review_other_meals(*self.args)
        with n.store.transaction() as db:
            self.before=complete_history(db,self.flow.journal.scope);_,parent=parent_history(db,self.flow.journal.scope)
        n.now+=timedelta(seconds=121)
        # この単体検査は既存10/8の通常登録fixtureを使う。28日の時刻例外は実HTTP受入で別に検査する。
        self.flow.preparation_window={'not_after':self.end}
        self.flow.schedule_gate=lambda value: self.assertGreater(timestamp(value),n.now)
        self.flow.integration={'agent_commit':'a'*40,'body_commit':'b'*40,'schema_sha256':SCHEMA_SHA}
        self.flow.execution_approval_sha256='f'*64
        self.flow.pregrant_refresh_approval={'version':1,'approved':True,'target_date':n.target,'target_slot':'lunch',
            'case_uuid':self.flow.journal.scope[3],'max_rounds':1,'max_calls':4,'planned_prepare_uuid':str(uuid.uuid4()),
            'parent_history_digest':parent,'source_digest':source_digest(n.pre,self.anchors,self.policy,n.e['owner'],1),'not_after':self.end}
        self.proof={
            'artifact_spec':'mina-rescue-v3-unissued-proof-v1','target_date':n.target,'target_slot':'lunch','case_uuid':self.flow.journal.scope[3],
            'planned_prepare_uuid':self.flow.pregrant_refresh_approval['planned_prepare_uuid'],'source_body_commit':'b'*40,'schema_sha256':SCHEMA_SHA,
            'complete':True,'read_only':True,'same_case_count':0,'same_day_slot_case_count':0,'planned_prepare_request_count':0,
            'planned_prepare_candidate_count':0,'first_activation_present':False,'read_started_at':stamp(n.now),
            'read_finished_at':stamp(n.now),'valid_until':stamp(n.now+timedelta(seconds=120))}
        self.flow.planned_prepare_uuid=self.flow.pregrant_refresh_approval['planned_prepare_uuid']
        self.path=n.base/'management.json';self.reference=self.write_proof(self.proof)
        def local_file(ref):
            raw=Path(ref['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('scoped_coordination_reference_changed')
            return raw
        # Windows単体では所有権部分だけを合成し、Linux UID境界は別の隔離受入で実行する。
        permission=patch('src.mina.scoped_management_proofs.root_file',side_effect=local_file);permission.start();self.addCleanup(permission.stop)
        n.ai.reset_mock()

    def write_proof(self,value):
        raw=canonical(value);self.path.write_bytes(raw)
        return {'path':str(self.path),'sha256':hashlib.sha256(raw).hexdigest()}

    def refresh(self):return self.flow.refresh_other_meals(*self.args,management_reference=self.reference)

    def history(self):
        with self.native.store.transaction() as db:return complete_history(db,self.flow.journal.scope)

    def test_refresh_appends_exact_four_and_preserves_old_history_costs_ordinals(self):
        result=self.refresh();history=self.history()
        self.assertEqual(history['round'],1);self.assertEqual(len(history['calls']),8)
        self.assertEqual(history['receipts'][:2],self.before['receipts'])
        self.assertEqual(history['calls'][:4],self.before['calls']);self.assertEqual(history['costs'][:4],self.before['costs'])
        self.assertEqual(result,[r['formal'] for r in history['selected']])
        self.assertEqual((self.native.ai.review_meal.call_count,self.native.ai.review_ogp.call_count),(2,2))
        self.assertEqual(self.flow.journal.rows('jobs'),[]);self.assertEqual(self.flow.journal.rows('requests'),[])
        with self.native.store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM replacement_attempts').fetchone()[0],0)
        from src.mina.scoped_rescue_origin import capture
        origin=capture(self.native.store,self.native.pre,self.native.e['owner'],1,self.native.now,self.native.target,'lunch',self.flow.journal.scope[3])
        self.assertEqual(len(origin['document']['ledgers']['scoped_pregrant_call_receipts']),8)
        self.assertEqual(origin['baseline_consumed_ordinals'],[])
        with self.assertRaises(ValueError):self.refresh()
        self.assertEqual(self.native.ai.review_meal.call_count,2)

    def test_unknown_old_call_refuses_before_new_paid_call(self):
        with self.native.store.transaction() as db:db.execute("UPDATE scoped_rescue_calls SET state='started' WHERE call_key=?",(self.before['calls'][0]['call_key'],))
        with self.assertRaises(ValueError):self.refresh()
        self.native.ai.review_meal.assert_not_called()

    def test_extra_key_refuses_before_claim(self):
        with self.native.store.transaction() as db:
            db.execute("INSERT INTO scoped_rescue_calls VALUES(?,?,?,?,?,'review','started',?,NULL,NULL,NULL)",(*self.flow.journal.scope,self.flow.journal.scope[3]+':pregrant:unknown','0'*64))
        with self.assertRaises(ValueError):self.refresh()
        self.native.ai.review_meal.assert_not_called()

    def test_missing_or_unapproved_config_refuses(self):
        self.flow.pregrant_refresh_approval['approved']=False
        with self.assertRaises(ValueError):self.refresh()
        self.native.ai.review_meal.assert_not_called()

    def test_parent_change_refuses(self):
        self.flow.pregrant_refresh_approval['parent_history_digest']='0'*64
        with self.assertRaises(ValueError):self.refresh()
        self.native.ai.review_meal.assert_not_called()

    def test_source_change_refuses(self):
        old=self.native.capture
        self.native.capture=lambda *a:{**old(*a),'digest':'a'*64}
        self.args=(*self.args[:2],self.native.capture,*self.args[3:])
        with self.assertRaises(ValueError):self.refresh()
        self.native.ai.review_meal.assert_not_called()

    def test_incomplete_or_already_activated_management_proof_refuses(self):
        for changed in ({'complete':False},{'first_activation_present':True},{'same_case_count':False}):
            self.reference=self.write_proof({**self.proof,**changed})
            with self.assertRaises(ValueError):self.refresh()
        self.native.ai.review_meal.assert_not_called()

    def test_partial_new_round_never_falls_back_and_never_restarts(self):
        self.native.ai.review_ogp.side_effect=RuntimeError('synthetic response loss')
        with self.assertRaises(RuntimeError):self.refresh()
        calls=self.native.ai.review_meal.call_count
        with self.assertRaises(ValueError):self.history()
        with self.assertRaises(ValueError):self.refresh()
        self.assertEqual(self.native.ai.review_meal.call_count,calls)
        with self.assertRaises(ValueError):self.flow.review_other_meals(*self.args)

    def test_old_or_mixed_formal_reviews_cannot_be_selected(self):
        fresh=self.refresh()
        from src.mina.scoped_rescue_reviews import verify_receipts
        verify_receipts(self.flow,{'other_meals_reviews':fresh,'review_policy_version':self.policy})
        for values in (self.original,[self.original[0],fresh[1]]):
            with self.assertRaises(ValueError):verify_receipts(self.flow,{'other_meals_reviews':values,'review_policy_version':self.policy})

    def test_refresh_claim_is_single_transaction_under_concurrency(self):
        from concurrent.futures import ThreadPoolExecutor
        def run():
            try:self.refresh();return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(lambda _:run(),range(2)))
        self.assertEqual(sorted(result),[False,True]);self.assertEqual(len(self.history()['claims']),1)
        self.assertEqual((self.native.ai.review_meal.call_count,self.native.ai.review_ogp.call_count),(2,2))

    def test_fresh_complete_round_stages_real_packet_without_issuing(self):
        from src.mina.replacement_inventory import ExternalInventory,REQUIRED_STATES
        from src.mina.domain import ACCOUNT
        from src.mina.scoped_issue_builder import stage
        n=self.native
        self.flow.inventory=ExternalInventory(n.store,lambda ch,b,t,c:{'account':ACCOUNT,'channel':b,'date':t,
            'covered_states':sorted(REQUIRED_STATES),'complete':True,'items':[],'has_next':False},lambda:n.now,{'x':'x','instagram':'ig'})
        reviews=self.refresh()
        packet=stage(self.flow,reviews,self.anchors,{'native_reference':{'path':'synthetic-native','sha256':'a'*64},
            'unissued_reference':self.reference},n.e['owner'],1)
        self.assertFalse(packet['agent_issues_authority'])
        self.assertEqual(packet['input']['planned_prepare_uuid'],self.flow.pregrant_refresh_approval['planned_prepare_uuid'])
        self.assertEqual(packet['input']['attempt_ordinal'],1)
        self.assertEqual(len(packet['evidence']['source_ledger']['native_origin']['document']['ledgers']['scoped_pregrant_call_receipts']),8)
        self.flow.client.case.assert_not_called();self.assertEqual(self.flow.journal.rows('requests'),[])

    def test_new_complete_round_does_not_allow_hold_epoch_change(self):
        reviews=self.refresh()
        with self.native.store.transaction() as db:db.execute('UPDATE publication_holds SET epoch=2 WHERE target_date=?',(self.native.target,))
        from src.mina.scoped_rescue_reviews import verify_receipts
        with self.assertRaises(ValueError):verify_receipts(self.flow,{'other_meals_reviews':reviews,'review_policy_version':self.policy})

    def test_schema_tamper_is_checked_even_after_cached_validation(self):
        changed=self.native.base/'wrong-schema.json';changed.write_bytes(b'{}')
        with patch('src.mina.scoped_rescue_contract.SCHEMA_PATH',changed):
            with self.assertRaisesRegex(ValueError,'schema_changed'):self.history()

    def test_expired_management_proof_never_claims_or_calls(self):
        self.reference=self.write_proof({**self.proof,'valid_until':stamp(self.native.now)})
        with self.assertRaises(ValueError):self.refresh()
        self.assertEqual(self.flow.journal.rows('events',"AND kind='pregrant_refresh_claim'"),[])
        self.native.ai.review_meal.assert_not_called()

    def coordination(self):
        from src.mina.scoped_pregrant_coordinator import scope_for,coordinate
        n=self.native;pre=self.flow.normal.preconditions(n.target)
        def anchor(m):
            q=m['analysis']['quality']
            return {**{k:m['expected'][k] for k in ('target_slot','target_result_version','account_generation','day_generation','meal_generation')},
                'original_sha256':m['image_sha256'],'stored_sha256':m['stored_image_sha256'],
                'analysis_digest':m['analysis_digest'],'ogp_input_digest':'d'*24,'calories':m['analysis']['summary']['totalCalories'],
                'api_quality':{**{k:q[k] for k in ('status','atwater_calories','calorie_difference','calorie_difference_ratio')},
                    'warning_codes':[w['code'] for w in q['warnings']]}}
        metadata={'artifact_spec':'mina-native-anchors-artifact-v1','complete':True,'metadata_only':True,
            'independent_review_provided':False,'image_bytes_verified':False,'source_body_commit':'b'*40,
            'schema_sha256':SCHEMA_SHA,'date':n.target,'target_slot':'lunch','read_started_at':stamp(n.now),
            'checked_at':stamp(n.now),
            'valid_until':stamp(n.now+timedelta(seconds=120)),'state_version':pre['state_version'],
            'target_native':anchor(next(m for m in pre['meals'] if m['expected']['target_slot']=='lunch')),
            'other_meals_native':[anchor(m) for m in pre['meals'] if m['expected']['target_slot']!='lunch']}
        metadata['artifact_sha256']=digest(metadata)
        self.flow.management_helpers={k:'a'*64 for k in ('agent','management','fs_proxy','grant_cli')}
        port=Mock();port.scope=scope_for(self.flow,self.policy);port.helper_shas=self.flow.management_helpers.copy()
        port.snapshot.return_value={'native':metadata,'unissued_reference':self.reference}
        port.wait.side_effect=ValueError('scoped_coordination_wait_exhausted')
        stage=Mock(return_value={'synthetic':'packet'});self.flow.bind_case=Mock(return_value={'synthetic':'original-case'})
        def run():return coordinate(self.flow,port,n.meal,n.ai,n.capture,n.draft,n.saved,'www.log-eats.com',
            self.policy,n.e['owner'],1,stage)
        return port,stage,run

    def test_coordinator_complete_refresh_restart_never_repeats_paid_calls(self):
        self.refresh();port,stage,run=self.coordination()
        before=(self.native.ai.review_meal.call_count,self.native.ai.review_ogp.call_count)
        self.assertEqual(run(),{'synthetic':'original-case'})
        self.assertEqual(before,(self.native.ai.review_meal.call_count,self.native.ai.review_ogp.call_count))
        self.assertEqual(stage.call_count,1);self.assertEqual(port.publish_bundle.call_count,1)
        self.flow.bind_case.assert_called_once()

    def test_coordinator_partial_refresh_stops_without_replay(self):
        self.native.ai.review_ogp.side_effect=RuntimeError('synthetic loss')
        with self.assertRaises(RuntimeError):self.refresh()
        port,stage,run=self.coordination();before=self.native.ai.review_meal.call_count
        with self.assertRaises(ValueError):run()
        self.assertEqual(self.native.ai.review_meal.call_count,before);stage.assert_not_called()
        port.publish.assert_not_called();self.flow.bind_case.assert_not_called()

    def test_coordinator_staged_job_looks_up_original_case_after_off_and_expiry(self):
        port,stage,run=self.coordination()
        original=self.flow.journal.rows
        self.flow.journal.rows=lambda table,*a,**k:([{'case_uuid':self.flow.journal.scope[3]}] if table=='jobs' else original(table,*a,**k))
        self.native.now+=timedelta(hours=1)
        with patch.dict('os.environ',{'MINA_SCOPED_RESCUE_ENABLED':'false'}):self.assertEqual(run(),{'synthetic':'original-case'})
        port.snapshot.assert_not_called();port.publish.assert_not_called();port.publish_bundle.assert_not_called()
        stage.assert_not_called();self.native.ai.review_meal.assert_not_called();self.flow.bind_case.assert_called_once()

    def test_helper_manifest_and_refresh_plan_must_match_execution_approval(self):
        port,stage,run=self.coordination();port.helper_shas={**port.helper_shas,'agent':'f'*64}
        with self.assertRaisesRegex(ValueError,'helpers_unapproved'):run()
        self.native.ai.review_meal.assert_not_called()
        self.flow.pregrant_refresh_approval['planned_prepare_uuid']=str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError,'plan_changed'):self.refresh()
        self.native.ai.review_meal.assert_not_called()
