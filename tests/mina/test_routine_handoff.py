"""通知の応答喪失・クラッシュで再POSTせず、全操作の厳密な受領照合だけを行う。"""
import copy,json,tempfile,unittest
from datetime import datetime,UTC
from pathlib import Path
from unittest.mock import Mock
from src.mina.store import Store
from src.mina.routine_recovery import Routine
from src.mina.routine_handoff import transmit,maintain_notices,matches,prepare
from test_routine_pipeline import Discord
import test_daily as fixture

class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(Path(self.tmp.name)/'db')
        self.store.claim_preparation(fixture.TARGET,fixture.NOW);Routine(self.store,fixture.TARGET,'a'*40,fixture.HOST,lambda:fixture.NOW).admit()
        self.store.save_many([fixture.candidate()],fixture.HOST,fixture.NOW);self.store.issue_cards(fixture.TARGET,fixture.NOTIFY);self.notice=self.store.claim_notice();self.client=Discord()
    def unknown(self):
        original=self.client.send
        def lost(wire):
            original(wire);raise TimeoutError('隔離応答喪失')
        self.client.send=lost
        with self.assertRaises(TimeoutError):transmit(self.store,self.notice,'x',self.client)
        self.store.finish_notice(self.notice['id'],'outcome_unknown')
        actual=copy.deepcopy(self.client.messages['1']);actual['timestamp']=datetime.now(UTC).isoformat()
        self.client.history_since=Mock(return_value={'complete':True,'messages':[actual]})
        return actual
    def test_lost_receipt_complete_history_recovers_exact_controls_without_nonce(self):
        self.unknown();maintain_notices(self.store,'x',self.client)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT state FROM notices').fetchone()[0],'sent')
            receipt=json.loads(db.execute('SELECT receipt FROM routine_notice_receipts').fetchone()[0]);self.assertFalse(receipt['nonce_echo_present'])
            self.assertEqual(db.execute('SELECT count(*) FROM routine_notice_reconciliations').fetchone()[0],1)
        self.assertEqual(self.client.posts,1)
    def test_zero_multiple_incomplete_or_edited_receipts_stay_unknown(self):
        actual=self.unknown()
        for mode in ('zero','multiple','incomplete','button','author','channel','nonce','label','style','disabled','mention'):
            value=copy.deepcopy(actual);snapshot={'complete':True,'messages':[value]}
            if mode=='zero':snapshot['messages']=[]
            elif mode=='multiple':snapshot['messages'].append({**copy.deepcopy(value),'id':'2'})
            elif mode=='incomplete':snapshot['complete']=False
            elif mode=='button':value['components'][0]['components'][0]['custom_id']='other'
            elif mode=='author':value['author']['id']='other'
            elif mode=='channel':value['channel_id']='other'
            elif mode=='nonce':value['nonce']='other'
            elif mode=='mention':value['mention_everyone']=True
            else:value['components'][0]['components'][0][mode]=True if mode=='disabled' else 'other'
            self.client.history_since.return_value=snapshot
            from src.mina.routine_handoff import reconcile
            with self.subTest(mode=mode):self.assertFalse(reconcile(self.store,'x',self.notice['id'],self.client))
        self.assertEqual(self.client.posts,1)
    def test_crash_after_receipt_before_notice_commit_gets_same_id_only(self):
        transmit(self.store,self.notice,'x',self.client);self.store.finish_notice(self.notice['id'],'outcome_unknown')
        self.client.history_since=Mock(side_effect=AssertionError('保存済IDがあるため履歴不要'))
        maintain_notices(self.store,'x',self.client)
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT state FROM notices').fetchone()[0],'sent')
        self.assertEqual(self.client.posts,1)
    def test_identity_failure_before_post_can_only_requeue_original_wire(self):
        self.client.identity=Mock(side_effect=TimeoutError('隔離読取失敗'))
        with self.assertRaises(TimeoutError):transmit(self.store,self.notice,'x',self.client)
        self.store.finish_notice(self.notice['id'],'outcome_unknown');self.assertEqual(self.client.posts,0)
        self.client.identity=Mock(return_value='999');maintain_notices(self.store,'x',self.client)
        notice=self.store.claim_notice();self.assertIsNotNone(notice);transmit(self.store,notice,'x',self.client);self.assertEqual(self.client.posts,1)
    def test_changed_candidate_before_dispatch_is_not_sent(self):
        prepare(self.store,self.notice,'x')
        with self.store.transaction() as db:db.execute("UPDATE candidates SET state='changes_requested'")
        with self.assertRaises(ValueError):transmit(self.store,self.notice,'x',self.client)
        self.assertEqual(self.client.posts,0)
