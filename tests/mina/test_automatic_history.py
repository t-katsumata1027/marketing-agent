"""日々の手入力なしで公開台帳を照合し、不明投稿を保留する。"""

import tempfile
import json
from dataclasses import replace
import unittest
from datetime import datetime
from pathlib import Path

from src.mina.automatic_history import build_automatic_history, refresh_history, prepare_history
from src.mina.domain import ACCOUNT, JST
from src.mina.store import Store
from tests.mina.test_daily import candidate, NOW, HOST
from src.mina.x_history import collect_timeline
from unittest.mock import Mock


class AutomaticHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'db')
        self.now = datetime(2026, 10, 9, 19, tzinfo=JST)
        self.cutover = {'account': ACCOUNT, 'old_reservations_zero': True, 'old_generation_stopped': True,
                        'confirmed_by': '運用者', 'confirmed_at': '2026-10-09T18:00:00+09:00'}
        self.bootstrap = {'account': ACCOUNT, 'reviewed': True, 'reviewer': '運用者', 'entries': []}

    def snapshot(self, posts):
        return collect_timeline(lambda *a, **k: {'data': posts, 'meta': {'result_count': len(posts)}}, '99', '2026-10-10')

    def test_published_ledger_and_actual_timeline_produce_readiness(self):
        c = candidate()
        self.store.save(c, HOST, NOW)
        with self.store.transaction() as db:
            db.execute("UPDATE candidates SET state='published',x_id='123'")
        snapshot = self.snapshot([{'id': '123', 'author_id': '99', 'created_at': '2026-10-08T07:30:00+09:00', 'text': c.content}])
        evidence = build_automatic_history(self.root/'db', snapshot, self.bootstrap, self.cutover, self.now)
        self.assertTrue(evidence['history_complete'])
        self.assertEqual(evidence['entries'][0]['menu'], c.menu)

    def test_external_post_requires_review_then_bootstrap_matches(self):
        snapshot = self.snapshot([{'id': '456', 'author_id': '99', 'created_at': '2026-10-08T07:30:00+09:00', 'text': '旧投稿'}])
        with self.assertRaises(ValueError):
            build_automatic_history(self.root/'db', snapshot, self.bootstrap, self.cutover, self.now)
        self.bootstrap['entries'] = [{'x_id': '456', 'date': '2026-10-08', 'menu': '確認済み献立', 'comment': '確認済み一言', 'publish_text': '旧投稿'}]
        self.assertTrue(build_automatic_history(self.root/'db', snapshot, self.bootstrap, self.cutover, self.now)['history_complete'])
        self.bootstrap['entries'][0]['date'] = '2026-10-07'
        with self.assertRaises(ValueError):
            build_automatic_history(self.root/'db', snapshot, self.bootstrap, self.cutover, self.now)

    def test_unresolved_publish_and_cutover_not_confirmed_hold(self):
        self.store.save(candidate(), HOST, NOW)
        with self.store.transaction() as db:
            db.execute("UPDATE candidates SET state='outcome_unknown'")
        with self.assertRaises(ValueError):
            build_automatic_history(self.root/'db', self.snapshot([]), self.bootstrap, self.cutover, self.now)
        self.cutover['old_reservations_zero'] = False
        with self.assertRaises(ValueError):
            build_automatic_history(self.root/'db', self.snapshot([]), self.bootstrap, self.cutover, self.now)

    def test_refresh_output_and_read_failure_no_output(self):
        client = Mock()
        client.read_history.return_value = self.snapshot([])
        output = self.root/'history.json'
        refresh_history(self.root/'db', '2026-10-10', output, self.bootstrap, self.cutover, client, lambda: self.now)
        self.assertTrue(output.is_file())
        client.read_history.side_effect = RuntimeError('読取停止')
        with self.assertRaises(RuntimeError):
            refresh_history(self.root/'db', '2026-10-10', self.root/'failed', self.bootstrap, self.cutover, client, lambda: self.now)
        self.assertFalse((self.root/'failed').exists())

    def test_failed_history_queues_one_safe_notice_without_generation(self):
        c = replace(candidate(), target_date='2026-10-10')
        self.store.save(c, HOST, NOW)
        (self.root/'bootstrap').write_text(json.dumps(self.bootstrap), encoding='utf-8')
        (self.root/'cutover').write_text(json.dumps(self.cutover), encoding='utf-8')
        client = Mock()
        client.read_history.side_effect = RuntimeError('秘密値を含む応答原文')
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, '生成・登録を開始していません'):
                prepare_history(self.store, self.root/'db', '2026-10-10', self.root/'history.json',
                                self.root/'bootstrap', self.root/'cutover', client, lambda: self.now)
        notice = self.store.claim_notice()
        self.assertEqual(notice['kind'], 'history_hold')
        self.assertNotIn('秘密値', notice['payload'])
        self.assertIsNone(self.store.claim_notice())
        self.assertFalse((self.root/'history.json').exists())
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM preparation').fetchone()[0], 0)
            row = db.execute('SELECT state,nonce_hash,actor FROM candidates').fetchone()
            self.assertEqual(tuple(row), ('changes_requested', None, None))
