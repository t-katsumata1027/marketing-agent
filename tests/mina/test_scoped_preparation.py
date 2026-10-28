"""準備専用例外の承認原本と、通常の期限ゲートを隔離検証する。"""
import copy, hashlib, json, tempfile, unittest, uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock
from src.mina.scoped_preparation import validate, marker, proposed, blocked
from src.mina.replacement_contract import stamp
from src.mina.scoped_rescue_runtime import acceptance
from src.mina.scoped_rescue_contract import SCHEMA_SHA

class LatePreparationTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,28,2,tzinfo=UTC)
        self.case=str(uuid.uuid4())
        self.window={'target_date':'2026-10-28','target_slot':'lunch','case_uuid':self.case,'mode':'prepare_only',
            'not_after':stamp(self.now+timedelta(minutes=15)),
            'proposed_times':{s:stamp(self.now+timedelta(hours=i+1)) for i,s in enumerate(('breakfast','lunch','dinner','daily'))}}

    def test_missing_or_changed_scope_and_expiry_are_not_permission(self):
        validate(self.window,'2026-10-28','lunch',self.case,self.now)
        for name,value in [('target_date','2026-10-29'),('target_slot','breakfast'),('case_uuid',str(uuid.uuid4())),('mode','publish')]:
            changed={**self.window,name:value}
            with self.assertRaises(ValueError):validate(changed,'2026-10-28','lunch',self.case,self.now)
        with self.assertRaises(ValueError):validate(self.window,'2026-10-28','lunch',self.case,self.now+timedelta(minutes=15))
        with self.assertRaises(ValueError):validate(self.window,'2026-10-28','lunch',self.case,self.now+timedelta(days=1))

    def test_proposal_is_future_same_day_ordered_and_not_a_publication_permission(self):
        for name,value in [('breakfast',self.window['not_after']),('dinner',self.window['proposed_times']['breakfast']),('daily','2026-10-29T01:00:00Z')]:
            changed=copy.deepcopy(self.window);changed['proposed_times'][name]=value
            with self.assertRaises(ValueError):validate(changed,'2026-10-28','lunch',self.case,self.now)
        m=marker(self.window,'breakfast')
        self.assertEqual(proposed({'scoped_preparation_only':m},'2026-10-28').hour,3)
        self.assertTrue(blocked({'approval_source':{'scoped_preparation_only':m}}))
        self.assertTrue(blocked({'source':{'scoped_preparation_only':None}}))
        self.assertFalse(blocked({'approval_source':{}}))

    def test_execution_artifact_binds_the_exact_window(self):
        settings={'target_date':'2026-10-28','target_slot':'lunch','case_uuid':self.case,'contract_version':'mina-rescue-v3',
            'schema_sha256':SCHEMA_SHA,'agent_commit':'a'*40,'body_commit':'b'*40,'preparation_window':self.window}
        with tempfile.TemporaryDirectory() as directory:
            for role in ('provider_acceptance','agent_acceptance','implementation_review','deployment_approval','execution_approval'):
                document={'role':role,'contract_version':'mina-rescue-v3','scope':'operator_scoped_meal_replacement','target_date':'2026-10-28',
                    'target_slot':'lunch','case_uuid':self.case,'agent_commit':'a'*40,'body_commit':'b'*40,'schema_sha256':SCHEMA_SHA,
                    'verdict':'approved' if role.endswith('_approval') else 'Go'}
                if role=='execution_approval':document['preparation_window']=self.window
                path=Path(directory)/role;path.write_text(json.dumps(document),encoding='utf-8')
                settings[role]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            self.assertEqual(acceptance(settings,'a'*40),('2026-10-28','lunch'))
            changed=copy.deepcopy(settings);changed['preparation_window']['not_after']=stamp(self.now+timedelta(minutes=16))
            with self.assertRaisesRegex(ValueError,'approval_changed'):acceptance(changed,'a'*40)
            changed=copy.deepcopy(settings);changed.pop('execution_approval')
            with self.assertRaises(ValueError):acceptance(changed,'a'*40)

if __name__=='__main__':unittest.main()
