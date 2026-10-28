"""欠けた将来日枠の収集とUUID前再開が既存保留を解除しないことを確認する。"""
import copy,json,tempfile,unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.store import Store
from src.mina.registration import register_day
from src.mina.preparation_status import QualityHold
from src.mina.publication_hold import acquire
from src.mina.replacement_collection import Collection
from src.mina.replacement_flow import ReplacementFlow
from src.mina.request_ledger import RequestLedger
from tests.mina.test_daily import NOW,TARGET

FLAGS={'MINA_MEAL_REPLACEMENT_ENABLED':'true','MINA_LIVE_WRITES':'true','MINA_REPLACEMENT_COLLECTION_ENABLED':'true'}

class CollectionTests(unittest.TestCase):
    def setUp(self):
        from tests.mina.test_registration import RegistrationTests
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
        fixture=RegistrationTests();self.draft,self.saved=fixture.setup_inputs(self.base)
        self.store=Store(self.base/'db');self.meal=fixture.client();self.ai=Mock();self.now=NOW
        day_state=self.meal.day_state.side_effect
        self.meal.day_state.side_effect=lambda target:{**day_state(target),'checked_at':self.now.isoformat()}
        current=patch('src.mina.registration.datetime',wraps=datetime);dt=current.start();dt.now.side_effect=lambda *args:self.now;self.addCleanup(current.stop)
        self.flags=patch.dict('os.environ',FLAGS);self.flags.start();self.addCleanup(self.flags.stop)
        self.capture=lambda url,host:{'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':b'\xff\xd8\xffpreview'}
        self.ai.review_meal.side_effect=[{'status':'publishable','reason_codes':[]},{'status':'hold','reason_codes':['image_analysis_mismatch']}]
        self.ai.review_ogp.return_value={'status':'publishable','reason_codes':[]}
        with self.assertRaises(QualityHold):register_day(self.store,self.draft,self.saved,self.base/'ogp',self.meal,self.ai,'www.log-eats.com',self.capture)
        self.ai.review_meal.side_effect=None;self.ai.review_meal.return_value={'status':'publishable','reason_codes':[]}
        for name,value in [('draft.json',self.draft),('drive.json',self.saved)]: (self.base/name).write_text(json.dumps(value),encoding='utf-8')
        owner='11111111-1111-4111-8111-111111111111';h=acquire(self.store,TARGET,owner,NOW)
        self.e={'target_date':TARGET,'slot':'lunch','owner':owner,'epoch':h['epoch'],'deadline':NOW.timestamp()+3600}
        with self.store.transaction() as db:
            db.execute('CREATE TABLE replacement_episodes(target_date TEXT,slot TEXT,owner TEXT,epoch INTEGER,stage TEXT,old_source TEXT,prepare_uuid TEXT,discard_uuid TEXT,commit_uuid TEXT)')
            db.execute("INSERT INTO replacement_episodes VALUES(?,?,?,?,'checking','{}',NULL,NULL,NULL)",(TARGET,'lunch',owner,h['epoch']))
        self.policy=Mock();self.policy.waived=True;self.policy.called=Mock();self.admit=Mock()
        self.collection=Collection(self.store,self.base,self.draft,self.saved,self.meal,self.ai,'www.log-eats.com',self.policy,Mock(),lambda:self.now,self.admit)
        self.patch=patch('src.mina.ogp.capture_ogp',self.capture);self.patch.start();self.addCleanup(self.patch.stop)
        with self.store.transaction() as db:self.old=[tuple(r) for r in db.execute('SELECT * FROM preparation_steps')]

    def test_only_missing_dinner_is_saved_and_restarting_never_reposts(self):
        self.collection(self.e);self.collection(self.e)
        self.assertEqual(self.meal.register.call_count,3);self.meal.json_write.assert_not_called()
        self.assertEqual([c.args[2] for c in self.meal.register.call_args_list],['breakfast','lunch','dinner'])
        self.admit.assert_called_once()
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT state FROM publication_holds').fetchone()[0],'held')
            self.assertEqual(json.loads(db.execute("SELECT result FROM preparation_steps WHERE step='meal_review:lunch'").fetchone()[0]),{'status':'hold','reason_codes':['image_analysis_mismatch']})
            for old in self.old:self.assertEqual(tuple(db.execute('SELECT * FROM preparation_steps WHERE target_date=? AND step=?',old[:2]).fetchone()),old)
            self.assertEqual(db.execute('SELECT count(*) FROM replacement_collection_claims').fetchone()[0],1)

    def test_missing_flag_and_expired_deadline_never_register(self):
        with patch.dict('os.environ',{'MINA_REPLACEMENT_COLLECTION_ENABLED':'false'}):
            with self.assertRaisesRegex(ValueError,'source_incomplete'):self.collection(self.e)
        self.now=NOW.replace(hour=20)
        with self.assertRaisesRegex(ValueError,'deadline'):self.collection(self.e)
        self.assertEqual(self.meal.register.call_count,2)

    def test_provider_rejection_and_bounded_policy_never_register(self):
        self.admit.side_effect=ValueError('replacement_policy_unverified')
        with self.assertRaises(ValueError):self.collection(self.e)
        self.admit.side_effect=None;self.policy.waived=False
        with self.assertRaisesRegex(ValueError,'budget_unconfigured'):self.collection(self.e)
        self.assertEqual(self.meal.register.call_count,2)

    def test_unknown_dinner_is_not_registered_again(self):
        from src.mina.clients import OutcomeUnknown
        self.meal.register.side_effect=OutcomeUnknown('synthetic')
        with self.assertRaises(Exception):self.collection(self.e)
        with self.assertRaises(ValueError):self.collection(self.e)
        self.assertEqual(self.meal.register.call_count,3)

    def test_new_dinner_quality_hold_does_not_become_other_meal_acceptance(self):
        self.ai.review_meal.return_value={'status':'hold','reason_codes':['image_analysis_mismatch']}
        with self.assertRaises(Exception):self.collection(self.e)
        with self.assertRaises(ValueError):self.collection(self.e)
        self.assertEqual(self.meal.register.call_count,3)
        self.meal.json_write.assert_not_called()

    def test_original_food_review_or_file_change_blocks_collection(self):
        with self.store.transaction() as db:db.execute("UPDATE preparation_steps SET digest=? WHERE step='meal_review:breakfast'",('a'*64,))
        with self.assertRaisesRegex(ValueError,'review_unverified'):self.collection(self.e)
        self.assertEqual(self.meal.register.call_count,2)

    def test_saved_target_hold_is_required_and_must_match_current_inputs(self):
        with self.store.transaction() as db:db.execute("UPDATE preparation_steps SET result=? WHERE step='meal_review:lunch'",(json.dumps({'status':'publishable','reason_codes':[]}),))
        with self.assertRaisesRegex(ValueError,'quality_hold_required'):self.collection(self.e)
        self.assertEqual(self.meal.register.call_count,2)

    def test_prior_first_start_deadline_applies_before_collection(self):
        with self.store.transaction() as db:
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",(TARGET,'meal_replan:lunch:1','a'*64,'{}',NOW.timestamp()-890))
        with self.assertRaisesRegex(ValueError,'deadline_exceeded'):self.collection(self.e)
        self.assertEqual(self.meal.register.call_count,2);self.admit.assert_not_called()

    def test_changed_artifact_does_not_become_new_baseline(self):
        (self.base/'draft.json').write_text(json.dumps({**self.draft,'daily_comment':'改変'}),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'source_changed'):self.collection(self.e)
        self.assertEqual(self.meal.register.call_count,2)

    def test_request_scope_cannot_change_held_lunch_or_daily(self):
        self.collection(self.e)
        for slot,op in [('lunch','meal_correction'),('daily','daily_share')]:
            with self.store.transaction() as db:
                with self.assertRaises(ValueError):self.collection.claim(db,{'target_date':TARGET,'slot':slot,'operation':op})

    def test_complete_day_still_requires_current_hold_when_collection_disabled(self):
        self.collection(self.e)
        with patch.dict('os.environ',{'MINA_REPLACEMENT_COLLECTION_ENABLED':'false'}):
            self.collection(self.e)
            current=self.meal.day_state.side_effect
            def changed(target):
                result=current(target)
                for value in result['meals']:
                    if value['meal']['meal_type']=='lunch':value['result_version']='rv1_'+'a'*64
                return result
            self.meal.day_state.side_effect=changed
            with self.assertRaises(ValueError):self.collection(self.e)
        self.assertEqual(self.meal.register.call_count,3)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        from tests.mina.test_replacement_integration import IntegrationTests
        self.fixture=IntegrationTests('test_changed_precondition_never_rebases');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups);self.f=self.fixture
        RequestLedger(self.f.store)

    def blocked(self):
        self.f.inventory.read_page=lambda *args:{'complete':False}
        with self.assertRaisesRegex(ValueError,'external_dispatch_unverified'):
            self.f.flow.begin('2030-01-01','dinner',quality_hold=self.f.hold())

    def test_uuid_before_inventory_failure_is_resumed_without_recreating_episode(self):
        from src.mina.replacement_inventory import REQUIRED_STATES
        from src.mina.domain import ACCOUNT
        self.blocked();before=self.f.flow.episode('2030-01-01')
        self.f.inventory.read_page=lambda channel,binding,target,cursor:{'account':ACCOUNT,'channel':binding,'date':target,'complete':True,'covered_states':sorted(REQUIRED_STATES),'items':[],'has_next':False}
        self.f.flow.resume_checking('2030-01-01');after=self.f.flow.episode('2030-01-01')
        self.assertEqual((before['owner'],before['epoch']),(after['owner'],after['epoch']))
        self.assertLessEqual(after['deadline'],before['deadline'])
        self.assertEqual(after['stage'],'ready');self.assertEqual(sum(m=='POST' for m,p in self.f.server.calls),1)
        with self.assertRaises(ValueError):self.f.flow.resume_checking('2030-01-01')

    def test_inventory_still_blocked_never_sends_or_resets_deadline(self):
        self.blocked();before=self.f.flow.episode('2030-01-01')
        with self.assertRaises(ValueError):self.f.flow.resume_checking('2030-01-01')
        after=self.f.flow.episode('2030-01-01');self.assertEqual(before['deadline'],after['deadline'])
        self.assertEqual(self.f.server.calls,[])

    def test_active_process_owner_cannot_be_stolen(self):
        from src.mina.instagram_preparation import owner
        self.blocked()
        with self.f.store.transaction() as db:
            db.execute('UPDATE replacement_preflight_owners SET identity=? WHERE target_date=?',(owner(),'2030-01-01'))
        with self.assertRaisesRegex(ValueError,'busy'):self.f.flow.resume_checking('2030-01-01')

    def test_undefined_quality_or_pending_requests_never_enter_resumption(self):
        self.blocked()
        ledger=RequestLedger(self.f.store);ledger.prepare('22222222-2222-4222-8222-222222222222','meal_register','2030-01-01','breakfast',{})
        with self.assertRaisesRegex(ValueError,'unresolved'):self.f.flow.resume_checking('2030-01-01')
        self.assertEqual(self.f.server.calls,[])

    def test_unknown_cost_and_orphan_material_block_before_lookup(self):
        self.blocked()
        with self.f.store.transaction() as db:
            db.execute("INSERT INTO replacement_cost_reservations VALUES(?,?,?,?,'unknown')",('orphan-call','2030-01-01','image',100))
        with self.assertRaisesRegex(ValueError,'not_pristine'):self.f.flow.resume_checking('2030-01-01')
        self.assertEqual(self.f.server.calls,[])

    def test_named_cost_exception_requires_exact_bounded_policy_row(self):
        self.blocked()
        with self.f.store.transaction() as db:
            db.execute("INSERT INTO replacement_cost_reservations VALUES(?,?,?,?,'unknown')",('not-the-approved-key','2030-01-01','opening_exposure',100))
        with self.assertRaisesRegex(ValueError,'not_pristine'):self.f.flow.resume_checking('2030-01-01')
        self.assertEqual(self.f.server.calls,[])

    def test_begin_holds_live_process_claim_through_inventory(self):
        from src.mina.instagram_preparation import alive
        observed=[]
        def checking(*args):
            with self.f.store.transaction() as db:
                row=db.execute('SELECT identity FROM replacement_preflight_owners').fetchone()
                observed.append(alive(row[0]))
            raise ValueError('external_dispatch_present')
        self.f.inventory.read_page=checking
        with self.assertRaises(ValueError):self.f.flow.begin('2030-01-01','dinner',quality_hold=self.f.hold())
        self.assertEqual(observed,[True])
        with self.f.store.transaction() as db:self.assertIsNone(db.execute('SELECT identity FROM replacement_preflight_owners').fetchone()[0])
