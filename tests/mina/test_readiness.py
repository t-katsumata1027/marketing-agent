"""未確認の履歴・旧予約・実装版が承認候補へ進まないことを検証する。"""

import json
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from src.mina.daily_pipeline import DeadlineClient, prepare_day
from src.mina.readiness import build_candidates, check_evidence, release_commit
from src.mina.store import Store
from tests.mina.test_daily import HOST, NOW, TARGET, candidate


def evidence():
    return {'account': 'EatsLog88161', 'target_date': TARGET, 'checked_at': NOW.isoformat(),
            'reviewer': '確認担当者', 'history_source': '投稿履歴一覧', 'schedule_source': 'X予約一覧',
            'history_complete': True, 'legacy_schedule_clear': True,
            'history_from': '2026-09-24', 'history_to': '2026-10-07', 'entries': []}


def reviewed():
    posts = {}
    for slot in ('breakfast', 'lunch', 'dinner', 'daily'):
        c = asdict(candidate(slot))
        c['publish_text'] = f"{c['comment']}\n\n{c['api_text']}\n{c['share_url']}"
        c['approval_source'] = {'request_key': '11111111-1111-4111-8111-111111111111',
                                'input_hash': 'ih1_' + 'a' * 64, 'result_sha256': 'b' * 64,
                                'state_version': 'sv1_' + 'c' * 64}
        c['ogp'] = {'image_url': 'https://' + HOST + '/share/' + slot + '/og-image', 'image_sha256': 'e' * 64}
        posts[slot] = c
    return {'target_date': TARGET, 'state': 'posts_reviewed', 'posts': posts}


class ReadinessTest(unittest.TestCase):
    def test_complete_proof_produces_four_bound_candidates(self):
        result = build_candidates(reviewed(), evidence(), 'a' * 40, HOST, NOW)
        self.assertEqual(len(result), 4)
        self.assertTrue(all(c['history_verified'] and c['approval_source']['readiness_sha256'] for c in result))

    def test_missing_expired_future_and_partial_proof_rejected(self):
        for changes in ({'history_complete': False}, {'legacy_schedule_clear': False},
                        {'checked_at': (NOW - timedelta(hours=25)).isoformat()},
                        {'checked_at': (NOW + timedelta(seconds=1)).isoformat()},
                        {'history_from': '2026-10-02'}, {'reviewer': ''}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                check_evidence({**evidence(), **changes}, TARGET, NOW)

    def test_weekly_phrase_limit_does_not_extend_to_two_weeks(self):
        result = reviewed()
        result['posts']['breakfast']['comment'] = '朝の献立が気になります。'
        post = result['posts']['breakfast']
        post['publish_text'] = f"{post['comment']}\n\n{post['api_text']}\n{post['share_url']}"
        old = {'date': '2026-09-25', 'menu': '別献立', 'comment': '以前の献立が気になります。'}
        self.assertEqual(len(build_candidates(result, {**evidence(), 'entries': [old]}, 'a' * 40, HOST, NOW)), 4)
        with self.assertRaises(ValueError):
            build_candidates(result, {**evidence(), 'entries': [{**old, 'date': '2026-10-07'}]}, 'a' * 40, HOST, NOW)

    def test_repeated_menu_comment_and_divergent_state_are_rejected(self):
        old = {'date': '2026-10-07', 'menu': '別の献立', 'comment': '別のコメント'}
        for key in ('menu', 'comment'):
            history = {**evidence(), 'entries': [{**old, key: reviewed()['posts']['breakfast'][key]}]}
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_candidates(reviewed(), history, 'a' * 40, HOST, NOW)
        result = reviewed()
        result['posts']['daily']['approval_source']['state_version'] = 'sv1_' + 'd' * 64
        with self.assertRaises(ValueError):
            build_candidates(result, evidence(), 'a' * 40, HOST, NOW)

    def test_commit_verifies_real_repository_and_detects_modified_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src/mina').mkdir(parents=True)
            module = root / 'src/mina/example.py'
            module.write_text('# 確認用\n', encoding='utf-8')
            (root / 'requirements-mina.txt').write_text('', encoding='utf-8')
            def git(*args):
                return subprocess.run(['git', '-C', tmp, *args], capture_output=True, check=True).stdout.decode().strip()
            git('init')
            git('add', '.')
            git('-c', 'user.name=検証', '-c', 'user.email=test@example.test', 'commit', '-m', '検証用')
            commit = git('rev-parse', 'HEAD')
            self.assertEqual(release_commit(root, commit), commit)
            module.write_text('# 変更\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                release_commit(root, commit)

    def test_deadline_prevents_next_external_operation(self):
        check, client = Mock(), Mock()
        proxy = DeadlineClient(client, check)
        proxy.register()
        check.side_effect = ValueError('期限超過')
        with self.assertRaises(ValueError):
            proxy.register()
        self.assertEqual(client.register.call_count, 1)

    def test_commit_rejects_missing_added_and_changed_deployment_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'src/mina').mkdir(parents=True)
            (root / 'deploy').mkdir()
            module = root / 'src/mina/example.py'
            module.write_text('# 検証\n', encoding='utf-8')
            missing = root / 'src/mina/required.py'
            missing.write_text('# 必須\n', encoding='utf-8')
            deployment = root / 'deploy/run.sh'
            deployment.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
            (root / 'requirements-mina.txt').write_text('', encoding='utf-8')
            def git(*args):
                return subprocess.run(['git', '-C', tmp, *args], capture_output=True, check=True).stdout.decode().strip()
            git('init')
            git('add', '.')
            git('-c', 'user.name=検証', '-c', 'user.email=test@example.test', 'commit', '-m', '検証')
            commit = git('rev-parse', 'HEAD')
            self.assertEqual(release_commit(root, commit), commit)
            missing.unlink()
            with self.assertRaises(ValueError):
                release_commit(root, commit)
            missing.write_text('# 必須\n', encoding='utf-8')
            added = root / 'src/mina/untracked.py'
            added.write_text('# 追加\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                release_commit(root, commit)
            added.unlink()
            deployment.write_text('#!/bin/sh\nexit 1\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                release_commit(root, commit)

    def test_paused_prepare_does_not_create_database_or_clients(self):
        import os
        from src.mina.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'missing.sqlite3'
            with patch.dict(os.environ, {'MINA_PREPARE_ENABLED': 'false'}), \
                 patch('sys.argv', ['mina', 'prepare', '--database', str(path)]), \
                 patch('src.mina.codex_client.generation_client') as client:
                with self.assertRaises(ValueError):
                    main()
            client.assert_not_called()
            self.assertFalse(path.exists())

    def test_read_commands_are_paused_before_client_or_database_creation(self):
        import os
        from src.mina.__main__ import main
        for command in ('request-status', 'request-reconcile', 'rereview', 'rebind-review'):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'missing.sqlite3'
                with patch.dict(os.environ, {'MINA_READ_REVIEW_ENABLED': 'false'}), \
                     patch('sys.argv', ['mina', command, '--database', str(path)]), \
                     patch('src.mina.clients.MealClient') as client:
                    with self.assertRaises(ValueError):
                        main()
                client.assert_not_called()
                self.assertFalse(path.exists())

    def test_pipeline_success_is_atomic_and_repeated_run_skips_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / 'state.sqlite3')
            history = root / 'history.json'
            history.write_text(json.dumps(evidence()), encoding='utf-8')
            def draft(target, history, output, ai):
                output.write_text(json.dumps({'target_date': target}), encoding='utf-8')
                return {'state': 'draft_unreviewed'}
            with patch('src.mina.daily_pipeline.release_commit', return_value='a' * 40), \
                 patch('src.mina.daily_pipeline.draft_day', side_effect=draft) as generation, \
                 patch('src.mina.daily_pipeline.prepare_images', return_value={}), \
                 patch('src.mina.daily_pipeline.save_images_to_drive', return_value={}), \
                 patch('src.mina.daily_pipeline.register_day', return_value=reviewed()), \
                 patch('src.mina.m1_contract.verify_source'):
                args = (store, root / 'daily', history, history, 'a' * 40, root, HOST, Mock(), Mock(), Mock())
                self.assertEqual(prepare_day(*args, clock=lambda: NOW)['state'], 'approval_ready')
                self.assertEqual(len(store.list_day(TARGET)), 4)
                self.assertEqual(prepare_day(*args, clock=lambda: NOW)['state'], 'already_complete')
                self.assertEqual(generation.call_count, 1)

    def test_failure_is_held_without_candidates_or_automatic_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / 'state.sqlite3')
            history = root / 'history.json'
            history.write_text(json.dumps(evidence()), encoding='utf-8')
            with patch('src.mina.daily_pipeline.release_commit', return_value='a' * 40), \
                 patch('src.mina.daily_pipeline.draft_day', side_effect=RuntimeError('秘密を含む原文')) as generation:
                args = (store, root / 'daily', history, history, 'a' * 40, root, HOST, Mock(), Mock(), Mock())
                with self.assertRaisesRegex(RuntimeError, '^daily_preparation_held:draft$'):
                    prepare_day(*args, clock=lambda: NOW)
                with self.assertRaises(ValueError):
                    prepare_day(*args, clock=lambda: NOW)
                self.assertEqual(generation.call_count, 1)
                self.assertEqual(store.list_day(TARGET), [])
