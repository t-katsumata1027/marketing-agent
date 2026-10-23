"""準備の状態・保留理由を安全に伝え、同じ通知や保留工程を反復しない。"""
import json,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock
from src.mina.domain import JST
from src.mina.preparation import PreparationJournal
from src.mina.preparation_status import QualityHold
from src.mina.store import Store
from src.mina.runtime import send_notifications

NOW=datetime(2026,10,7,19,59,tzinfo=JST);TARGET='2026-10-08'

class PreparationStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'db')
        self.store.claim_preparation(TARGET,NOW)

    def test_quality_hold_is_known_and_not_unknown_or_reexecuted(self):
        journal=PreparationJournal(self.store)
        error=QualityHold('点検保留','meal_review:lunch',('nutrition_semantic_mismatch',))
        operation=Mock(side_effect=error)
        for _ in range(2):
            with self.assertRaises(QualityHold):journal.run(TARGET,'whole_day',{},operation)
        operation.assert_called_once()
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT state FROM preparation_steps WHERE step='whole_day'").fetchone()[0],'held')

    def test_correcting_and_terminal_stop_have_different_messages(self):
        self.store.record_preparation_progress(TARGET,'meal_correction:lunch','correcting',now=NOW)
        self.store.queue_delay(TARGET,NOW+timedelta(minutes=1))
        first=self.store.claim_notice();message=json.loads(first['payload'])['content']
        self.assertIn('訂正・再点検中',message);self.assertIn('昼食／解析訂正',message)
        self.assertIn('朝食、昼食、夕食、1日まとめ',message)
        self.store.finish_notice(first['id'],'sent')
        error=QualityHold('保留','meal_review:lunch',('context_mismatch',))
        self.store.record_preparation_failure(TARGET,'registration',error,NOW+timedelta(minutes=1))
        self.store.queue_delay(TARGET,NOW+timedelta(minutes=2))
        held=self.store.claim_notice();message=json.loads(held['payload'])['content']
        self.assertEqual(held['kind'],'preparation_hold');self.assertIn('品質点検で停止',message)
        self.assertIn('自動再開しません',message)
        self.assertNotIn('品質確認後に承認依頼を追加します',message)

    def test_error_text_and_round_identifiers_never_enter_notice(self):
        self.store.record_preparation_progress(TARGET,'ogp_capture:breakfast:11111111-1111-4111-8111-111111111111','running',now=NOW)
        self.store.record_preparation_failure(TARGET,'registration',RuntimeError('secret-private-credential'),NOW)
        notice=self.store.claim_notice();message=json.loads(notice['payload'])['content']
        self.assertIn('朝食／共有画像取得',message)
        self.assertNotIn('secret-private',message);self.assertNotIn('11111111',message)

    def test_unknown_notification_is_never_automatically_resent(self):
        error=QualityHold('保留','meal_review:lunch',('context_mismatch',))
        self.store.record_preparation_failure(TARGET,'registration',error,NOW)
        discord=Mock();discord.send.side_effect=RuntimeError('送信不明')
        self.assertEqual(send_notifications(self.store,discord),0)
        self.store.record_preparation_failure(TARGET,'registration',error,NOW+timedelta(minutes=1))
        self.store.queue_delay(TARGET,NOW+timedelta(minutes=1))
        self.assertEqual(send_notifications(self.store,discord),0)
        discord.send.assert_called_once()

    def test_legacy_hold_does_not_promise_automatic_recovery(self):
        with self.store.transaction() as db:db.execute("UPDATE preparation SET state='held:registration'")
        self.store.queue_delay(TARGET,NOW+timedelta(minutes=1))
        notice=self.store.claim_notice();message=json.loads(notice['payload'])['content']
        self.assertIn('確認・対応が必要',message);self.assertIn('自動再開しません',message)
