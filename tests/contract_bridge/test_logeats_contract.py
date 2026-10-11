"""LogEats実ハンドラーの応答をPython接続・工程・公開前検証へ渡す。"""

import base64
import io
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from PIL import Image
from src.mina.clients import MealClient, OutcomeUnknown
from src.mina.domain import Candidate
from src.mina.m1_contract import request_report, verify_source
from src.mina.ogp import capture_ogp
from src.mina.registration import register_day
from src.mina.store import Store


HOST = 'www.log-eats.com'
TARGET = '2026-10-08'
NOW = datetime(2026, 10, 7, 11, tzinfo=UTC)


class TestClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


class Bridge:
    def __init__(self, source, directory):
        node = shutil.which('node')
        tsx = source / 'node_modules/tsx/dist/cli.mjs'
        if not node or not tsx.is_file():
            raise RuntimeError('Node.jsとLogEatsのインストール済みtsxが必要です')
        jpeg = io.BytesIO()
        Image.new('RGB', (600, 600), 'ivory').save(jpeg, format='JPEG')
        self.image = jpeg.getvalue()
        image_path, png_path = directory / 'input.jpg', directory / 'ogp.png'
        image_path.write_bytes(self.image)
        Image.new('RGB', (1200, 630), 'ivory').save(png_path, format='PNG')
        # 本番接続情報を子プロセスへ継承しない。環境ファイルも読み込まない。
        allowed = ('PATH', 'SYSTEMROOT', 'SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT',
                   'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'LANG')
        environment = {key: os.environ[key] for key in allowed if key in os.environ}
        environment.update(NODE_ENV='test', NEXT_PUBLIC_SITE_URL='https://' + HOST)
        # 既存route入口の認証も実行する。値は実サービスと無関係な試験専用値。
        environment.update(MINA_AGENT_API_TOKEN='m' * 40, MINA_AGENT_ACCOUNT_EMAIL='mina@example.test')
        self.error_file = (directory / 'bridge-stderr.log').open('wb')
        self.process = subprocess.Popen([node, str(tsx), '--tsconfig', str(source / 'tsconfig.json'),
                                         str(Path(__file__).with_name('logeats_bridge.mts')), str(source), str(image_path), str(png_path)],
                                        cwd=source, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.error_file, text=True, encoding='utf-8')
        self.lines = queue.Queue()
        def read():
            for line in self.process.stdout:
                self.lines.put(line)
            self.lines.put(None)
        self.thread = threading.Thread(target=read, daemon=True)
        self.thread.start()
        self.drop_next_meal_response = False

    def rpc(self, message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()
        try:
            line = self.lines.get(timeout=25)
        except queue.Empty:
            raise RuntimeError('契約ブリッジが応答しません') from None
        if line is None:
            raise RuntimeError('契約ブリッジが停止しました')
        result = json.loads(line)
        if result.get('ok') is not True:
            raise RuntimeError('契約ブリッジ内の処理が失敗しました')
        return result['result']

    def open(self, request, timeout=None):
        result = self.rpc({'op': 'request', 'url': request.full_url, 'method': request.get_method(),
                           'headers': dict(request.header_items()),
                           'body': base64.b64encode(request.data).decode() if request.data is not None else None})
        raw = base64.b64decode(result['body'])
        if self.drop_next_meal_response and request.get_method() == 'POST' and request.full_url.endswith('/mina/meals'):
            self.drop_next_meal_response = False
            raise URLError('synthetic_response_lost')
        if result['status'] >= 400:
            raise HTTPError(request.full_url, result['status'], 'synthetic_http_error', result['headers'], io.BytesIO(raw))
        response = io.BytesIO(raw)
        response.headers = result['headers']
        # urllibのHTTPMessageと同じ大文字小文字非依存の参照を模擬する。
        from email.message import Message
        response.headers = Message()
        for key, value in result['headers'].items():
            response.headers[key] = value
        return response

    def close(self):
        # 起動失敗・切断済みpipeでも、ログとプロセスの後始末を省略しない。
        try:
            try:
                self.process.stdin.close()
            except OSError:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        finally:
            self.process.stdout.close()
            self.error_file.close()
            self.thread.join(timeout=2)


class ContractBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = os.getenv('LOGEATS_CONTRACT_SOURCE')
        if not source:
            raise RuntimeError('明示したLogEatsソースのパスが必要です。専用ランナーを使用してください')
        cls.source = Path(source).resolve()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bridge = Bridge(self.source, self.directory)
        self.addCleanup(self.bridge.close)
        # HTTPクライアント自体は実装を使用し、通信の出口だけをインメモリへ差し替える。
        for name in ('src.mina.clients.urlopen', 'src.mina.clients.build_opener', 'urllib.request.build_opener', 'src.mina.ogp.build_opener'):
            replacement = self.bridge.open if name.endswith('urlopen') else lambda *_: self.bridge
            patcher = patch(name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {'MINA_LIVE_WRITES': 'true'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = MealClient('https://' + HOST, 'm' * 40)
        for module in ('src.mina.registration.datetime', 'src.mina.m1_contract.datetime'):
            patcher = patch(module, TestClock)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = Store(self.directory / 'state.db')
        self.ai = Mock()
        self.ai.review_meal.return_value = self.ai.review_ogp.return_value = {'status': 'publishable', 'reason_codes': []}
        self.draft, self.saved = self.inputs()
        self.addCleanup(self.assert_no_external_network)

    def assert_no_external_network(self):
        self.assertEqual(self.bridge.rpc({'op': 'stats'})['network_attempts'], 0)

    def inputs(self):
        import hashlib
        meals, images = {}, {}
        for slot in ('breakfast', 'lunch', 'dinner'):
            meals[slot] = {'menu': 'ご飯と魚', 'comment': slot + 'の献立を紹介します。', 'estimated_calories': 600,
                'foods': [{'name': 'ご飯', 'amount': '150g'}, {'name': '魚', 'amount': '100g'}], 'image_description': 'ご飯と魚を別皿に盛る。'}
            path = self.directory / (slot + '.jpg')
            path.write_bytes(self.bridge.image)
            sha = hashlib.sha256(self.bridge.image).hexdigest()
            images[slot] = {'path': str(path), 'sha256': sha, 'size': len(self.bridge.image),
                'image_review': {'status': 'image_acceptable', 'reason_codes': []}, 'drive': {'verified': True, 'sha256': sha}}
        return ({'target_date': TARGET, 'state': 'draft_unreviewed', 'meals': meals, 'daily_comment': '1日の3食を振り返ります。'},
                {'target_date': TARGET, 'state': 'images_drive_verified', 'images': images})

    def prepare(self):
        return register_day(self.store, self.draft, self.saved, self.directory / 'ogp', self.client, self.ai, HOST)

    def candidate(self, result, slot='daily'):
        post = result['posts'][slot]
        return Candidate(TARGET, slot, post['comment'], post['api_text'], post['share_url'], post['menu'], post['nutrition'],
            post['image_sha256'], post['ogp_sha256'], 'pass', 'publishable', True, True, True, 'a' * 40,
            meal_count=3 if slot == 'daily' else 1, publish_text=post['publish_text'], approval_source=post['approval_source'])

    def test_full_registration_resume_and_four_preflights(self):
        prepared = self.prepare()
        self.assertEqual(len(prepared['posts']), 4)
        for slot in prepared['posts']:
            candidate = self.candidate(prepared, slot)
            candidate.validate(HOST, NOW)
            verify_source(candidate, self.client, HOST, capture_ogp, NOW)
        self.assertEqual(prepared, self.prepare())
        counters = self.bridge.rpc({'op': 'stats'})
        self.assertEqual(counters['analyze'], 3)
        self.assertEqual(counters['image'], 3)
        self.assertEqual(counters['render'], 4)
        self.assertEqual(self.ai.review_meal.call_count, 3)
        self.assertEqual(self.ai.review_ogp.call_count, 4)

    def test_actual_results_import_and_discord_approval_round(self):
        from src.mina.readiness import build_candidates
        from src.mina.runtime import import_manifest, notify_day
        from src.mina.domain import JST
        prepared = self.prepare()
        evidence = {'account': 'EatsLog88161', 'target_date': TARGET, 'checked_at': NOW.isoformat(),
                    'reviewer': '試験担当', 'history_source': '試験履歴', 'schedule_source': '試験予約',
                    'history_complete': True, 'legacy_schedule_clear': True,
                    'history_from': '2026-09-24', 'history_to': '2026-10-07', 'entries': []}
        candidates = build_candidates(prepared, evidence, 'a' * 40, HOST, NOW)
        path = self.directory / 'candidates.json'
        path.write_text(json.dumps(candidates), encoding='utf-8')
        import_manifest(self.store, path, HOST, NOW)
        notify = datetime(2026, 10, 7, 20, tzinfo=JST)
        notify_day(self.store, TARGET, notify)
        with self.store.transaction() as db:
            cards = [json.loads(row['payload']) for row in db.execute("SELECT payload FROM notices WHERE kind='approval'")]
        self.assertEqual(len(cards), 4)
        for card in cards:
            self.assertTrue(card['embeds'][0]['image']['url'].startswith('https://' + HOST))
            custom_id = card['components'][0]['components'][0]['custom_id']
            self.assertEqual(self.store.decide(custom_id, '試験承認者', notify), 'approved')
            with self.assertRaises(ValueError):
                self.store.decide(custom_id, '試験承認者', notify)
        self.assertEqual({row['state'] for row in self.store.list_day(TARGET)}, {'approved'})

    def test_daily_comment_change_blocks_preflight_without_changing_day_version(self):
        prepared = self.prepare()
        candidate = self.candidate(prepared)
        state = self.client.day_state(TARGET)
        self.bridge.rpc({'op': 'change-daily-comment'})
        self.assertEqual(state['state_version'], self.client.day_state(TARGET)['state_version'])
        response = self.client.request_state(candidate.approval_source['request_key'], 'daily_share')
        self.assertEqual(response['result_status'], 'modified')
        with self.assertRaises(ValueError):
            verify_source(candidate, self.client, HOST, capture_ogp, NOW)

    def test_same_url_changed_png_is_rejected(self):
        prepared = self.prepare()
        self.bridge.rpc({'op': 'corrupt-ogp'})
        with self.assertRaises(ValueError):
            verify_source(self.candidate(prepared), self.client, HOST, capture_ogp, NOW)

    def test_ogp_failure_is_saved_and_never_registered_twice(self):
        self.bridge.rpc({'op': 'fail-render'})
        for _ in range(2):
            with self.assertRaises(OutcomeUnknown):
                self.prepare()
        with self.store.transaction() as db:
            row = db.execute("SELECT result FROM preparation_steps WHERE step='meal_request_key:breakfast'").fetchone()
        key = json.loads(row['result'])['key']
        report = request_report(self.client.request_state(key, 'meal_register'), key, 'meal_register')
        self.assertEqual(report['persistence'], 'saved')
        self.assertFalse(report['automatic_retry_allowed'])
        with self.store.transaction() as db:
            observed = json.loads(db.execute('SELECT observation FROM mina_requests WHERE request_key=?', (key,)).fetchone()['observation'])
        self.assertEqual(observed['persistence'], 'saved')
        self.assertFalse(observed['automatic_retry_allowed'])
        self.assertEqual(self.bridge.rpc({'op': 'stats'})['analyze'], 1)

    def test_successful_registration_with_lost_response_is_not_retried(self):
        self.bridge.drop_next_meal_response = True
        for _ in range(2):
            with self.assertRaises(OutcomeUnknown):
                self.prepare()
        with self.store.transaction() as db:
            row = db.execute("SELECT result FROM preparation_steps WHERE step='meal_request_key:breakfast'").fetchone()
        key = json.loads(row['result'])['key']
        response = self.client.request_state(key, 'meal_register')
        report = request_report(response, key, 'meal_register')
        self.assertEqual(report['status'], 'succeeded')
        self.assertEqual(report['persistence'], 'saved')
        with self.store.transaction() as db:
            observed = json.loads(db.execute('SELECT observation FROM mina_requests WHERE request_key=?', (key,)).fetchone()['observation'])
        self.assertEqual(observed['persistence'], 'saved')
        self.assertEqual(self.bridge.rpc({'op': 'stats'})['analyze'], 1)

    def test_missing_uuid_and_wrong_operation_do_not_allow_retry(self):
        key = '11111111-1111-4111-8111-111111111111'
        with self.assertRaisesRegex(RuntimeError, '^meal_request_http_404$'):
            self.client.request_state(key, 'meal_register')
        prepared = self.prepare()
        key = prepared['posts']['breakfast']['approval_source']['request_key']
        with self.assertRaisesRegex(RuntimeError, '^meal_request_http_404$'):
            self.client.request_state(key, 'daily_share')

    def test_correction_and_daily_refresh_are_rebound_without_agent_writes(self):
        from src.mina.readiness import build_candidates
        from src.mina.domain import Candidate
        from src.mina.rebinding import rebind_day
        import copy
        prepared = self.prepare()
        evidence = {'account': 'EatsLog88161', 'target_date': TARGET, 'checked_at': NOW.isoformat(),
                    'reviewer': '試験担当', 'history_source': '試験履歴', 'schedule_source': '試験予約',
                    'history_complete': True, 'legacy_schedule_clear': True,
                    'history_from': '2026-09-24', 'history_to': '2026-10-07', 'entries': []}
        old = build_candidates(prepared, evidence, 'a' * 40, HOST, NOW)
        self.store.save_many([Candidate(**item) for item in old], HOST, NOW)
        draft = copy.deepcopy(self.draft)
        draft['meals']['breakfast']['comment'] = '朝の献立候補を見直します。'
        draft['daily_comment'] = '更新した1日分の献立候補です。'
        correction_key = '55555555-5555-4555-8555-555555555555'
        daily_key = '66666666-6666-4666-8666-666666666666'
        before = self.client.day_state(TARGET)
        corrected = self.client.json_write('/api/internal/mina/meals', {
            'contract_version': 'mina-write-v1', 'reason': 'codex_secondary_review',
            'share_url': prepared['posts']['breakfast']['share_url'],
            'foods': before['meals'][0]['analysis']['foods'], 'comment': draft['meals']['breakfast']['comment'],
            'expected_state_version': before['state_version']}, method='PATCH', request_key=correction_key)
        self.assertEqual(corrected['request']['operation'], 'meal_correction')
        updated = self.client.day_state(TARGET)
        self.assertNotEqual(before['state_version'], updated['state_version'])
        old_daily_key = prepared['posts']['daily']['approval_source']['request_key']
        old_daily = self.client.request_state(old_daily_key, 'daily_share')
        self.assertEqual(old_daily['result_status'], 'modified')
        self.assertEqual(old_daily['persistence'], 'saved')
        self.client.json_write('/api/internal/mina/daily-share', {
            'contract_version': 'mina-write-v1', 'date': TARGET, 'comment': draft['daily_comment'],
            'expected_state_version': updated['state_version'], 'expected_meal_count': 3}, request_key=daily_key)
        bindings = {'target_date': TARGET, 'requests': {
            slot: {'request_key': post['approval_source']['request_key'], 'operation': 'daily_share' if slot == 'daily' else 'meal_register'}
            for slot, post in prepared['posts'].items()}}
        bindings['requests']['breakfast'] = {'request_key': correction_key, 'operation': 'meal_correction'}
        bindings['requests']['daily'] = {'request_key': daily_key, 'operation': 'daily_share'}
        counters = self.bridge.rpc({'op': 'stats'})
        self.ai.reset_mock()
        with patch('src.mina.rebinding.release_commit', return_value='a' * 40), patch('src.mina.rereview.release_commit', return_value='a' * 40):
            rebind_day(self.store, TARGET, '77777777-7777-4777-8777-777777777777', bindings, evidence,
                       {slot: info['path'] for slot, info in self.saved['images'].items()}, draft, self.ai, self.client,
                       HOST, self.directory / 'rebound.json', lambda: NOW, capture_ogp, 'a' * 40, self.directory)
        rows = self.store.list_day(TARGET)
        self.assertEqual({row['revision'] for row in rows}, {2})
        self.assertEqual({row['state'] for row in rows}, {'approval_pending'})
        for row in rows:
            candidate = Candidate(**json.loads(row['payload']))
            verify_source(candidate, self.client, HOST, capture_ogp, NOW)
        after = self.bridge.rpc({'op': 'stats'})
        self.assertEqual(after['analyze'], counters['analyze'])
        self.assertEqual(after['image'], counters['image'])
        self.assertEqual(after['writeCount'], counters['writeCount'])
        self.assertEqual(self.ai.review_meal.call_count, 3)
        self.assertEqual(self.ai.review_ogp.call_count, 4)
