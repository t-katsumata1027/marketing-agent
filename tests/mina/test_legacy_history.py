"""移行前の実文面から、献立や確認済み判定を捏造しないことを確認する。"""

import unittest

from src.mina.legacy_history import bootstrap_draft
from src.mina.domain import ACCOUNT


class LegacyHistoryTest(unittest.TestCase):
    def test_comment_is_exact_and_other_has_no_invented_menu(self):
        posts = [{'id': '1', 'created_at': '2026-10-01T23:30:00Z', 'text': '魚とご飯の朝。\n\n今日の食事解析結果 500kcal'},
                 {'id': '2', 'created_at': '2026-10-02T01:00:00Z', 'text': '@someone Thanks!'}]
        draft = bootstrap_draft({'account': ACCOUNT, 'timeline_pages_complete': True, 'posts': posts})
        self.assertFalse(draft['reviewed'])
        self.assertEqual(draft['entries'][0]['comment'], '魚とご飯の朝。')
        self.assertEqual(draft['entries'][0]['date'], '2026-10-02')
        self.assertEqual(draft['entries'][0]['kind'], 'pending')
        self.assertEqual(draft['entries'][1]['menu'], '')
        self.assertEqual(draft['entries'][1]['comment'], '')
        self.assertEqual(draft['entries'][1]['publish_text'], posts[1]['text'])

    def test_incomplete_and_duplicate_posts_rejected(self):
        with self.assertRaises(ValueError):
            bootstrap_draft({'account': ACCOUNT, 'timeline_pages_complete': False, 'posts': []})
        post = {'id': '1', 'created_at': '2026-10-02T01:00:00Z', 'text': '文面'}
        with self.assertRaises(ValueError):
            bootstrap_draft({'account': ACCOUNT, 'timeline_pages_complete': True, 'posts': [post, post]})
