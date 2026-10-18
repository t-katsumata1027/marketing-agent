"""Bufferの非公開試験が公開・自動再送・秘密の出力へ進まないことを確認する。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.mina.buffer_trial import BufferTrialClient, ProbeError, TEST_TEXT, CREATE, probe


class BufferTrialTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'buffer-trial.sqlite3'
        self.channel = {'id': 'channel', 'serviceId': 'x-native-id', 'service': 'twitter',
            'name': 'EatsLog88161', 'isDisconnected': False, 'isLocked': False,
            'isQueuePaused': False, 'timezone': 'Asia/Tokyo'}
        self.post = {'id': 'post', 'text': TEST_TEXT, 'status': 'draft', 'dueAt': None,
            'sentAt': None, 'externalLink': None, 'channel': {'id': 'channel', 'service': 'twitter'}}
        self.transport = Mock(side_effect=self.reply)
        self.client = BufferTrialClient('synthetic-test-secret', self.transport)

    def reply(self, query, variables):
        if query.startswith('query { account'):
            return {'data': {'account': {'organizations': [{'id': 'org'}]}}}
        if 'channels(input' in query:
            return {'data': {'channels': [self.channel]}}
        if query == CREATE:
            data = variables['input']
            self.assertIs(data['saveToDraft'], True)
            self.assertNotIn('dueAt', data)
            self.assertEqual(data['mode'], 'addToQueue')
            return {'data': {'createPost': {'__typename': 'PostActionSuccess', 'post': dict(self.post)}}}
        return {'data': {'post': dict(self.post)}}

    def creates(self):
        return sum(call.args[0] == CREATE for call in self.transport.call_args_list)

    def test_inspect_makes_no_mutation_and_no_database(self):
        report = probe(self.client, self.path)
        self.assertTrue(report['target_account_matches'])
        self.assertEqual(self.creates(), 0)
        self.assertFalse(self.path.exists())

    def test_draft_readback_and_restart_do_not_duplicate(self):
        first = probe(self.client, self.path, True)
        second = probe(self.client, self.path, True)
        self.assertTrue(first['draft_verified'])
        self.assertFalse(second['draft_created_this_run'])
        self.assertEqual(self.creates(), 1)
        self.assertNotIn('synthetic-test-secret', str(first))
        self.assertNotIn('post', first)

    def test_response_loss_stops_after_restart(self):
        reply = self.reply
        def lost(query, variables):
            if query == CREATE:
                raise TimeoutError('synthetic-test-secret')
            return reply(query, variables)
        self.transport.side_effect = lost
        with self.assertRaises(ProbeError):
            probe(self.client, self.path, True)
        with self.assertRaisesRegex(ProbeError, 'reconciliation_required'):
            probe(self.client, self.path, True)
        self.assertEqual(self.creates(), 1)

    def test_http_200_graphql_error_is_not_success(self):
        self.transport.side_effect = lambda *_: {'data': {}, 'errors': [{'message': 'synthetic-test-secret'}]}
        with self.assertRaisesRegex(ProbeError, '^buffer_graphql_error$'):
            probe(self.client, self.path)

    def test_unexpected_account_never_writes(self):
        self.channel['name'] = 'another-account'
        with self.assertRaisesRegex(ProbeError, 'target_unconfirmed'):
            probe(self.client, self.path, True)
        self.assertEqual(self.creates(), 0)

    def test_existing_production_database_is_not_opened(self):
        with self.assertRaisesRegex(ProbeError, 'isolated_trial_database_required'):
            probe(self.client, self.path.with_name('mina.sqlite3'), True)
        self.assertEqual(self.creates(), 0)

    def test_scheduled_response_is_not_private_success_or_retried(self):
        self.post['status'] = 'scheduled'
        with self.assertRaisesRegex(ProbeError, 'not_private'):
            probe(self.client, self.path, True)
        self.post['status'] = 'draft'
        report = probe(self.client, self.path, True)
        self.assertTrue(report['draft_verified'])
        self.assertEqual(self.creates(), 1)

    def test_text_change_and_read_failure_never_create_again(self):
        probe(self.client, self.path, True)
        self.post['text'] += '変更'
        with self.assertRaisesRegex(ProbeError, 'text_changed'):
            probe(self.client, self.path, True)
        self.assertEqual(self.creates(), 1)

    def test_channel_binding_change_is_rejected(self):
        probe(self.client, self.path, True)
        self.channel['serviceId'] = 'different-native-id'
        with self.assertRaisesRegex(ProbeError, 'input_changed'):
            probe(self.client, self.path, True)
        self.assertEqual(self.creates(), 1)


if __name__ == '__main__':
    unittest.main()
