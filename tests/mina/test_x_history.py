"""ページ全体・外部投稿・本文改変を確認し、予約確認と混同しない。"""

import unittest
import os
from datetime import datetime, UTC
from unittest.mock import patch, Mock

from src.mina.x_history import collect_timeline, reconcile_timeline


def post(key='1', text='本文 https://t.co/abc'):
    return {'id': key, 'author_id': '99', 'created_at': '2026-10-10T12:00:00+00:00',
            'text': text, 'entities': {'urls': [{'url': 'https://t.co/abc', 'expanded_url': 'https://www.log-eats.com/s/a'}]}}


def page(data, token=None):
    meta = {'result_count': len(data)}
    if token is not None:
        meta['next_token'] = token
    return {'data': data, 'meta': meta}


class XHistoryTest(unittest.TestCase):
    def test_next_day_query_ends_before_observation_time(self):
        calls = []
        def fetch(*a, **params):
            calls.append(params)
            return page([])
        result = collect_timeline(fetch, '99', '2026-10-13', as_of=datetime(2026, 10, 12, 10, microsecond=123456, tzinfo=UTC))
        self.assertEqual(calls[0]['end_time'], '2026-10-12T09:59:30Z')
        self.assertEqual(result['read_until'], '2026-10-12T09:59:30+00:00')
    def test_disabled_reader_does_not_contact_sdk(self):
        from src.mina.clients import XClient
        client = XClient.__new__(XClient)
        client.client = Mock()
        with patch.dict(os.environ, {'MINA_X_HISTORY_READ_ENABLED': 'false'}):
            with self.assertRaises(RuntimeError):
                client.read_history('EatsLog88161', '2026-10-12')
        client.client.get_me.assert_not_called()

    def test_pages_and_exact_url_expansion(self):
        calls = []
        def fetch(account, **params):
            calls.append(params.copy())
            return page([post()], 'next') if len(calls) == 1 else page([post('2')])
        result = collect_timeline(fetch, '99', '2026-10-12')
        self.assertEqual(calls[1]['pagination_token'], 'next')
        self.assertEqual(calls[0]['start_time'], '2026-09-27T15:00:00Z')
        rows = [{'x_id': key, 'publish_text': '本文 https://www.log-eats.com/s/a'} for key in ('1', '2')]
        checked = reconcile_timeline(result, rows)
        self.assertTrue(checked['timeline_matches_ledger'])
        self.assertFalse(checked['legacy_schedule_verified'])

    def test_unknown_missing_changed(self):
        result = collect_timeline(lambda *a, **k: page([post(), post('2')]), '99', '2026-10-12')
        checked = reconcile_timeline(result, [{'x_id': '1', 'publish_text': '改変'}, {'x_id': '3', 'publish_text': '不存在'}])
        self.assertEqual(checked['external_post_ids'], ['2'])
        self.assertEqual(checked['missing_post_ids'], ['3'])
        self.assertEqual(checked['changed_post_ids'], ['1'])

    def test_partial_response_and_failure_never_return_success(self):
        for bad in ({'errors': [{}], **page([])}, {'data': [], 'meta': {'result_count': 1}},
                    page([], 'next'), page([post(), post()]), page([{**post(), 'author_id': '88'}]),
                    page([{**post(), 'created_at': '2026-10-12T00:00:00Z'}]), page([{**post(), 'note_tweet': {'text': '長文'}}])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                collect_timeline(lambda *a, **k: bad, '99', '2026-10-12')
        def fail(*a, **k):
            raise RuntimeError('secret')
        with self.assertRaisesRegex(ValueError, '^X履歴を完全に取得できません$'):
            collect_timeline(fail, '99', '2026-10-12')

    def test_repeated_token_and_page_cap(self):
        with self.assertRaises(ValueError):
            collect_timeline(lambda *a, **k: page([post()], 'again'), '99', '2026-10-12')
        with self.assertRaisesRegex(ValueError, 'ページ上限'):
            collect_timeline(lambda *a, **k: page([post()], 'next'), '99', '2026-10-12', max_pages=1)

    def test_empty_is_only_timeline_empty(self):
        result = collect_timeline(lambda *a, **k: page([]), '99', '2026-10-12')
        self.assertTrue(reconcile_timeline(result, [])['timeline_matches_ledger'])
        self.assertFalse(result['legacy_schedule_verified'])
