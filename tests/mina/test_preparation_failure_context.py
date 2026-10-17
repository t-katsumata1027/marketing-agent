"""登録前の読取停止を画像保存や成否不明へ丸めないことを検証する。"""
import json
import tempfile
import unittest
from datetime import UTC,datetime,timedelta
from pathlib import Path
from unittest.mock import Mock

from src.mina.audit import read_events
from src.mina.clients import ClockSkewError,MealReadFailure,MealClient,preparation_client,await_checked_at
from src.mina.store import Store
from tests.mina import test_registration as fixtures


class PreparationFailureContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'db')
        self.now=datetime.now(UTC)
        self.target='2026-10-08'

    def notice(self):
        return json.loads(self.store.claim_notice()['payload'])['content']

    def test_completed_drive_is_not_reported_as_registration_failure(self):
        self.store.record_preparation_progress(self.target,'drive_upload:dinner','running')
        try:
            raise MealReadFailure('secret-response-body','http_error',503)
        except Exception as error:
            self.store.record_preparation_failure(self.target,'registration',error,self.now)
        text=self.notice()
        self.assertIn('全体／登録・共有',text)
        self.assertNotIn('夕食／画像保存',text)
        self.assertNotIn('secret-response-body',text)
        event=read_events(self.store.path,self.target)[-1]
        self.assertEqual((event['stage'],event['reason'],event['details']['http_status']),('registration','http_error',503))

    def test_initial_clock_failure_is_recorded_before_any_request_key(self):
        fixture=fixtures.RegistrationTests()
        draft,saved=fixture.setup_inputs(self.root)
        client=fixture.client()
        client.day_state.side_effect=ClockSkewError(1.62017)
        with self.assertRaises(ClockSkewError) as caught:
            fixture.run_pipeline(self.store,draft,saved,self.root,client,Mock())
        self.store.record_preparation_failure(self.target,'registration',caught.exception,self.now)
        events=read_events(self.store.path,self.target)
        failures=[event for event in events if event['reason']=='clock_ahead']
        self.assertTrue(failures)
        self.assertTrue(all(event['stage']=='source_state' for event in failures))
        self.assertAlmostEqual(failures[0]['details']['clock_ahead_ms'],1620.17)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM mina_requests').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM preparation_steps').fetchone()[0],0)
        client.register.assert_not_called()
        self.assertIn('全体／登録前の正本照合',self.notice())

    def test_actual_registration_substep_and_no_secret_are_preserved(self):
        self.store.record_preparation_progress(self.target,'ogp_capture:breakfast:private-round','running')
        self.store.record_preparation_failure(self.target,'registration',TimeoutError('secret-key'),self.now)
        text=self.notice()
        self.assertIn('朝食／共有画像取得',text)
        self.assertNotIn('private-round',text)
        self.assertNotIn('secret-key',text)
        self.assertNotIn('secret-key',json.dumps(read_events(self.store.path,self.target)))

    def test_initial_state_failure_records_reason_without_reclassifying_quality(self):
        self.store.record_preparation_progress(self.target,'source_state:lunch','reviewing')
        self.store.record_preparation_failure(self.target,'registration',MealReadFailure('private','io_failed'),self.now)
        text=self.notice()
        self.assertIn('準備を停止',text)
        self.assertIn('昼食／登録前の正本照合',text)
        self.assertNotIn('品質点検で停止',text)

    def test_preparation_waits_for_observed_clock_gap_without_accepting_future(self):
        client=preparation_client('https://logeats.test','a'*32)
        self.assertEqual(client.max_clock_ahead,5)
        self.assertEqual(MealClient('https://logeats.test','a'*32).max_clock_ahead,1)
        future=self.now+timedelta(seconds=1.62017)
        times=iter((self.now,future))
        slept=[]
        result={'checked_at':future.isoformat()}
        self.assertIs(await_checked_at(result,clock=lambda:next(times),sleep=slept.append,max_ahead=client.max_clock_ahead),result)
        self.assertEqual(slept,[1.62017])
        with self.assertRaises(ClockSkewError):
            await_checked_at(result,clock=lambda:self.now,sleep=lambda _:None,max_ahead=client.max_clock_ahead)
        with self.assertRaises(ClockSkewError):
            await_checked_at({'checked_at':(self.now+timedelta(seconds=5.001)).isoformat()},clock=lambda:self.now,sleep=lambda _:None,max_ahead=client.max_clock_ahead)


if __name__=='__main__':
    unittest.main()
