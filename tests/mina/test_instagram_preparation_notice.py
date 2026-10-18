"""画像完成前の停止通知と、送信成否不明時の重複防止を検証する。"""

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.mina.instagram_runtime import InstagramRuntime
from src.mina.instagram_store import InstagramStore


class PreparationNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.store = InstagramStore(base / 'state.sqlite3')
        self.runtime = InstagramRuntime(base / 'state.sqlite3', base, base / 'images', store=self.store)

    def notices(self):
        with self.store.transaction() as db:
            return [dict(r) for r in db.execute('SELECT * FROM instagram_notices')]

    def test_missing_source_notifies_once_without_candidates_or_approval(self):
        error = ValueError('外部応答の秘密を含む例外')
        self.runtime.prepare = Mock(side_effect=error)
        client = Mock()
        with patch.dict('os.environ', {'MINA_INSTAGRAM_PREPARE_ENABLED': 'true',
                                      'MINA_INSTAGRAM_NOTIFY_ENABLED': 'true'}), \
                patch('src.mina.instagram_runtime.DiscordClient', return_value=client):
            for _ in range(2):
                with self.assertRaises(ValueError) as caught:
                    self.runtime.prepare_next()
                self.assertIs(caught.exception, error)
        self.assertEqual(client.send.call_count, 1)
        notices = self.notices()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['state'], 'sent')
        self.assertEqual(notices[0]['kind'], 'preparation_held')
        self.assertNotIn(str(error), notices[0]['payload'])
        self.assertNotIn('components', json.loads(notices[0]['payload']))
        self.assertEqual(self.store.rows(), [])
        self.assertIsNone(self.store.claim_due(datetime.now(UTC)))

    def test_unknown_notice_is_not_resent_after_failure_or_restart(self):
        self.runtime.prepare = Mock(side_effect=ValueError('準備失敗'))
        client = Mock()
        client.send.side_effect = TimeoutError()
        with patch.dict('os.environ', {'MINA_INSTAGRAM_PREPARE_ENABLED': 'true',
                                      'MINA_INSTAGRAM_NOTIFY_ENABLED': 'true'}), \
                patch('src.mina.instagram_runtime.DiscordClient', return_value=client):
            with self.assertRaises(ValueError):
                self.runtime.prepare_next()
            self.store.hold_interrupted()
            with self.assertRaises(ValueError):
                self.runtime.prepare_next()
        self.assertEqual(client.send.call_count, 1)
        self.assertEqual(self.notices()[0]['state'], 'outcome_unknown')

    def test_disabled_preparation_does_not_raise_operational_alarm(self):
        self.runtime.prepare = Mock()
        with patch.dict('os.environ', {'MINA_INSTAGRAM_PREPARE_ENABLED': 'false'}):
            with self.assertRaises(ValueError):
                self.runtime.prepare_next()
        self.runtime.prepare.assert_not_called()
        self.assertEqual(self.notices(), [])

    def test_notice_failure_keeps_original_preparation_error(self):
        error = ValueError('元の準備失敗')
        self.runtime.prepare = Mock(side_effect=error)
        with patch.dict('os.environ', {'MINA_INSTAGRAM_PREPARE_ENABLED': 'true'}), \
                patch.object(self.store, 'queue_preparation_hold', side_effect=OSError()):
            with self.assertRaises(ValueError) as caught:
                self.runtime.prepare_next()
        self.assertIs(caught.exception, error)

    def test_normal_preparation_still_delivers_approval_without_failure_notice(self):
        self.runtime.prepare = Mock()
        self.runtime.notify = Mock(return_value=4)
        with patch.dict('os.environ', {'MINA_INSTAGRAM_PREPARE_ENABLED': 'true'}):
            result = self.runtime.prepare_next()
        self.assertEqual(result['notices_sent'], 4)
        self.runtime.prepare.assert_called_once_with(result['target_date'], allow_public=True)
        self.assertEqual(self.notices(), [])


if __name__ == '__main__':
    unittest.main()
