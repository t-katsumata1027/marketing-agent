"""原因の追跡、秘密非漏洩、記録障害時の未送信を検証する。"""
import json
import tempfile
import unittest
from datetime import UTC,datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.audit import AuditUnavailable,read_events,safe_failure
from src.mina.store import Store
from src.mina.preparation import PreparationJournal
from src.mina.request_ledger import RequestLedger
from src.mina.preparation_status import KnownQualityRejected
from tests.mina import test_buffer_publisher as fixtures
from src.mina.buffer_publisher import arm
from src.mina.runtime import publish_due


class OperationAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'db')

    def test_failure_keeps_actual_stage_end_and_sanitized_location(self):
        journal=PreparationJournal(self.store)
        def fail():raise ValueError('private-token body nonce')
        with self.assertRaises(ValueError):journal.run('2026-10-18','image:lunch',{'key':'private-input'},fail)
        events=read_events(self.store.path,'2026-10-18')
        self.assertEqual([event['event'] for event in events],['started','failed'])
        self.assertEqual(events[-1]['stage'],'image');self.assertEqual(events[-1]['reason'],'validation_failed')
        self.assertNotIn('private',json.dumps(events))
        self.assertGreaterEqual(events[-1]['occurred_at_utc'],events[0]['occurred_at_utc'])

    def test_unknown_fields_and_secret_free_text_are_rejected(self):
        for details in ({'token':'secret'},{'frames':['C:/private/key.py']},{'observed':'private-body'},
                        {'findings':[{'code':'private'}]}):
            with self.assertRaises(ValueError):self.store.audit.emit('system',None,None,None,'launch','failed',details=details)

    def test_safe_observations_preserve_specific_crop_location(self):
        finding={'code':'dish_cropped','region':'right','food_index':2,'expected':None,'observed':None,'unit':'none'}
        self.store.audit.emit('creation','2026-10-18','lunch',None,'review_diagnosis','observed',details={'findings':[finding]})
        self.assertEqual(read_events(self.store.path,'2026-10-18')[0]['details']['findings'],[finding])

    def test_quality_not_saved_is_not_flattened_to_unknown(self):
        ledger=RequestLedger(self.store);key='11111111-1111-4111-8111-111111111111'
        ledger.prepare(key,'meal_register','2026-10-18','dinner',{},b'original')
        client=Mock();client.request_state.return_value={'contract_version':'mina-write-v1','request_uuid':key,'operation':'meal_register',
            'status':'failed','persistence':'not_saved','stage':'quality','reason':'quality_review_required','result_status':'unresolved','current_result':None,'result':None}
        with self.assertRaises(KnownQualityRejected):ledger.execute_once(key,client,Mock(side_effect=TimeoutError('secret')))
        self.assertEqual(ledger.restore(key)['phase'],'held')

    def test_audit_failure_prevents_publication(self):
        f=fixtures.BufferPublisherTests();f.setUp()
        try:
            arm(f.store,fixtures.CHANNEL,fixtures.NOW)
            api=Mock();api.channel.return_value=fixtures.CHANNEL
            original=f.store.audit.emit
            def emit(category,target,slot,revision,stage,event,*args,**kwargs):
                if stage=='submission' and event=='started':raise AuditUnavailable()
                return original(category,target,slot,revision,stage,event,*args,**kwargs)
            with patch.object(f.store.audit,'emit',side_effect=emit),patch('src.mina.buffer_publisher.production_client',return_value=api):
                publish_due(f.store,Mock(),f.c.scheduled_at,fixtures.HOST,lambda c:f.proof,clock=lambda:f.c.scheduled_at,timer=lambda:10)
            api._request.assert_not_called()
        finally:f.doCleanups()

    def test_process_health_does_not_hide_business_hold(self):
        now=datetime(2026,10,16,22,tzinfo=UTC)
        with self.store.transaction() as db:db.execute("INSERT INTO preparation VALUES('2026-10-18','held:images',?)",(now.timestamp(),))
        self.store.record_cycle_health(True,now,'live')
        self.assertFalse(self.store.business_health(now)['healthy'])

    def test_retention_keeps_unresolved_and_recent_evidence(self):
        now=datetime(2026,10,16,tzinfo=UTC);old=now-timedelta(days=40)
        RequestLedger(self.store)
        with self.store.transaction() as db:
            db.execute("INSERT INTO preparation VALUES('2026-09-01','complete',?)",(old.timestamp(),))
            db.execute("INSERT INTO preparation VALUES('2026-09-02','held:images',?)",(old.timestamp(),))
        for target in ('2026-09-01','2026-09-02'):
            self.store.audit.emit('creation',target,None,None,'images','held',now=old)
        self.store.audit.maintain(now)
        self.assertEqual(read_events(self.store.path,'2026-09-01'),[])
        self.assertEqual(len(read_events(self.store.path,'2026-09-02')),1)

    def test_real_review_adapter_keeps_fact_and_verdict_separate(self):
        import hashlib
        from src.mina.generation import OpenAIClient
        from src.mina.review_diagnostics import record_findings,record_verdict
        client=OpenAIClient.__new__(OpenAIClient)
        finding={'code':'portion_mismatch','region':'center','food_index':0,'expected':150,'observed':300,'unit':'g'}
        client.structured=Mock(return_value={'status':'hold','reason_codes':['nutrition_semantic_mismatch'],'findings':[finding]})
        raw=b'\xff\xd8\xffimage'
        result=client.review_meal(raw,{}, {'meal':{'logged_at':'2026-10-18T03:15:00Z','meal_type':'lunch'}},{})
        self.assertEqual(result,{'status':'hold','reason_codes':['nutrition_semantic_mismatch']})
        record_findings(self.store,client,'2026-10-18','lunch','meal_review',hashlib.sha256(raw).hexdigest())
        record_verdict(self.store,'2026-10-18','lunch','meal_review',result)
        events=read_events(self.store.path,'2026-10-18')
        self.assertEqual(events[0]['details']['findings'],[finding])
        self.assertEqual(events[1]['event'],'held')

    def test_m1_validation_frame_is_retained_without_error_body(self):
        from src.mina.m1_contract import validate_state
        try:validate_state({},'2026-10-18',datetime.now(UTC))
        except ValueError as error:
            reason,details=safe_failure(error)
        self.assertEqual(reason,'validation_failed')
        self.assertTrue(any('m1_contract.py:validate_state:' in frame for frame in details['frames']))
