"""Instagramの版固定・個別承認・一回送信・実データ合成の境界検証。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.message import Message
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PIL import Image

from src.mina.buffer_limits import record_headers, record_request
from src.mina.buffer_trial import ProbeError
from src.mina.instagram_domain import InstagramCandidate, digest, publication_url
from src.mina.instagram_media import HOSTS, host_asset, verify_public
from src.mina.instagram_publisher import draft_once, publish_once, reconcile_known, request_for, validate_post
from src.mina.instagram_render import Canvas, daily_goals, render_bundle
from src.mina.instagram_store import InstagramStore
from src.mina.interactions import ApprovalPolicy, handle_gateway

NOW = datetime(2026, 10, 16, 1, tzinfo=UTC)


def candidate(slot='breakfast'):
    return InstagramCandidate('2026-10-17', slot, 'channel', 'account', 'post' if slot == 'daily' else 'story',
                              '食事記録', [{'url': 'https://drive.google.com/uc?id=image' + str(i),
                                       'sha256': 'a' * 64, 'width': 1080, 'height': 1350 if slot == 'daily' else 1920,
                                       'mime_type': 'image/jpeg'} for i in range(5 if slot == 'daily' else 1)],
                              {'bundle_sha256': 'b' * 64, 'state_version': 'sv1_' + 'c' * 64,
                               'x_digests': {s: 'd' * 64 for s in ('breakfast', 'lunch', 'dinner', 'daily')}})


def post(c, status='draft'):
    return {'id': 'buffer-post', 'text': c.caption, 'channel': {'id': c.channel_id, 'service': 'instagram'},
            'schedulingType': 'automatic', 'metadata': {'type': c.format, 'isAiGenerated': True,
                                                       'shouldShareToFeed': c.format == 'post'},
            'assets': [{'source': a['url'], 'mimeType': 'image/jpeg'} for a in c.assets],
            'status': status, 'sentAt': c.scheduled_at.isoformat() if status == 'sent' else None,
            'externalLink': ('https://www.instagram.com/stories/log_eats/123/' if c.format == 'story' else
                             'https://www.instagram.com/p/abc/') if status == 'sent' else None}


class API:
    def __init__(self, c, fail=False):
        self.c, self.fail, self.mutations, self.reads = c, fail, [], 0

    def _request(self, query, variables=None):
        self.reads += 1
        if 'organizations' in query:
            return {'account': {'organizations': [{'id': 'org'}]}}
        if 'channels(' in query:
            return {'channels': [{'id': 'channel', 'serviceId': 'account', 'name': 'log_eats',
                                  'service': 'instagram', 'isDisconnected': False, 'isLocked': False, 'isQueuePaused': False}]}
        if 'createPost(' in query:
            self.mutations.append(variables['input'])
            if self.fail:
                raise TimeoutError()
            return {'createPost': {'__typename': 'PostActionSuccess', 'post': post(self.c, 'draft' if variables['input']['saveToDraft'] else 'sent')}}
        return {'post': post(self.c)}


class InstagramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = InstagramStore(Path(self.temp.name) / 'db.sqlite3')

    def approve(self, c):
        self.store.save(c, NOW, HOSTS)
        self.store.queue_approval(self.store.rows()[0])
        with self.store.transaction() as db:
            payload = json.loads(db.execute('SELECT payload FROM instagram_notices').fetchone()[0])
        custom = payload['components'][0]['components'][0]['custom_id']
        self.assertEqual(self.store.decide(custom, 'user', NOW), 'approved')
        with self.assertRaises(ValueError):
            self.store.decide(custom, 'user', NOW)
        return custom

    def accepted(self, c):
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true'}):
            draft_once(self.store, c, API(c))

    def test_formats_and_goal_version_are_fixed(self):
        c = candidate()
        c.validate(NOW, HOSTS)
        for changed in (replace(c, format='post'), replace(c, source={**c.source, 'state_version': 'sv1_a'}),
                        replace(c, assets=[{**c.assets[0], 'url': 'https://evil.example/x.jpg'}])):
            with self.assertRaises(ValueError):
                changed.validate(NOW, HOSTS)
        candidate('daily').validate(NOW, HOSTS)

    def test_changed_version_invalidates_old_approval(self):
        c = candidate()
        old = self.approve(c)
        self.assertEqual(self.store.save(replace(c, caption='献立変更'), NOW, HOSTS), 2)
        with self.assertRaises(ValueError):
            self.store.decide(old, 'user', NOW)
        self.assertEqual(self.store.rows()[0]['state'], 'approval_pending')

    def test_claim_is_exclusive_even_before_external_attempt(self):
        c = candidate()
        self.approve(c)
        self.assertIsNotNone(self.store.claim_due(c.scheduled_at))
        self.assertIsNone(self.store.claim_due(c.scheduled_at))
        self.store.hold_interrupted()
        self.assertIsNone(self.store.claim_due(c.scheduled_at))

    def test_unknown_submission_is_never_retried(self):
        c = candidate()
        self.approve(c)
        self.accepted(c)
        api = API(c, fail=True)
        row = self.store.claim_due(c.scheduled_at)
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true', 'MINA_INSTAGRAM_PUBLISH_ENABLED': 'true'}):
            with self.assertRaises(TimeoutError):
                publish_once(self.store, row, lambda _: None, HOSTS, api, clock=lambda: c.scheduled_at,
                             verify_media=lambda _: True)
        self.assertEqual(len(api.mutations), 1)
        self.assertIsNone(self.store.claim_due(c.scheduled_at + timedelta(seconds=15)))
        self.assertEqual(self.store.rows()[0]['state'], 'outcome_unknown')
        with self.assertRaises(ValueError):
            self.store.save(replace(c, caption='変更しても再送しない'), NOW, HOSTS)

    def test_private_draft_then_approved_publish(self):
        c = candidate('daily')
        api = API(c)
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true'}):
            draft_once(self.store, c, api)
            draft_once(self.store, c, api)
        self.assertEqual(len(api.mutations), 1)
        self.assertTrue(api.mutations[0]['saveToDraft'])
        self.assertEqual(api.mutations[0]['mode'], 'addToQueue')
        self.approve(c)
        row = self.store.claim_due(c.scheduled_at)
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true', 'MINA_INSTAGRAM_PUBLISH_ENABLED': 'true'}):
            url = publish_once(self.store, row, lambda _: None, HOSTS, api, clock=lambda: c.scheduled_at,
                               verify_media=lambda _: True)
        self.assertEqual(url, 'https://www.instagram.com/p/abc/')
        self.assertEqual(api.mutations[-1]['mode'], 'shareNow')
        self.assertEqual(self.store.rows()[0]['state'], 'published')

    def test_notification_mode_or_changed_assets_are_rejected(self):
        c = candidate()
        changed = post(c)
        changed['schedulingType'] = 'notification'
        with self.assertRaises(ProbeError):
            validate_post(c, changed, True)
        changed = post(c)
        changed['assets'][0]['source'] = 'https://different.example/x.jpg'
        with self.assertRaises(ProbeError):
            validate_post(c, changed, True)
        with self.assertRaises(ValueError):
            publication_url('https://www.instagram.com/stories/someone/123/', 'story')

    def test_shared_rolling_quota_and_remote_remainder(self):
        headers = Message()
        headers['RateLimit-Policy'] = '"monthly";q=3000;w=2592000'
        headers['RateLimit'] = '"monthly";r=6;t=1000'
        record_headers(self.store, headers)
        record_request(self.store, 'query {}')
        with self.assertRaises(ProbeError):
            record_request(self.store, 'mutation { createPost() }')

    def test_gateway_checks_actor_and_instagram_switch_before_approval(self):
        c = candidate()
        self.store.save(c, NOW, HOSTS)
        self.store.queue_approval(self.store.rows()[0])
        with self.store.transaction() as db:
            notice = json.loads(db.execute('SELECT payload FROM instagram_notices').fetchone()[0])
        button = notice['components'][0]['components'][0]['custom_id']
        policy = ApprovalPolicy('00' * 32, 'app', 'guild', 'channel', frozenset({'user'}))
        payload = {'application_id': 'app', 'type': 3, 'guild_id': 'guild', 'channel_id': 'channel',
                   'member': {'user': {'id': 'user'}}, 'data': {'custom_id': button}}
        with patch.dict('os.environ', {'MINA_INSTAGRAM_APPROVAL_ENABLED': 'false'}):
            handle_gateway(payload, NOW, policy, SimpleNamespace(path=self.store.path), NOW)
        self.assertEqual(self.store.rows()[0]['state'], 'approval_pending')
        with patch.dict('os.environ', {'MINA_INSTAGRAM_APPROVAL_ENABLED': 'true'}):
            wrong = {**payload, 'member': {'user': {'id': 'outsider'}}}
            self.assertEqual(handle_gateway(wrong, NOW, policy, SimpleNamespace(path=self.store.path), NOW)[0], 403)
            self.assertEqual(handle_gateway(payload, NOW, policy, SimpleNamespace(path=self.store.path), NOW)[0], 200)
        self.assertEqual(self.store.rows()[0]['state'], 'approved')

    def test_disabled_publish_and_source_mismatch_never_submit(self):
        c = candidate()
        self.accepted(c)
        self.approve(c)
        row = self.store.claim_due(c.scheduled_at)
        api = API(c)
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true', 'MINA_INSTAGRAM_PUBLISH_ENABLED': 'true'}):
            with self.assertRaises(ValueError):
                publish_once(self.store, row, lambda _: (_ for _ in ()).throw(ValueError('正本変更')),
                             HOSTS, api, clock=lambda: c.scheduled_at, verify_media=lambda _: True)
        self.assertEqual(api.mutations, [])
        self.assertEqual(self.store.rows()[0]['state'], 'held')

    def test_published_state_cannot_be_overwritten_by_stale_worker(self):
        c = candidate()
        self.accepted(c)
        self.approve(c)
        row = self.store.claim_due(c.scheduled_at)
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true', 'MINA_INSTAGRAM_PUBLISH_ENABLED': 'true'}):
            publish_once(self.store, row, lambda _: None, HOSTS, API(c), clock=lambda: c.scheduled_at,
                         verify_media=lambda _: True)
        self.store.mark(row, 'outcome_unknown')
        self.assertEqual(self.store.rows()[0]['state'], 'published')

    def test_public_media_rejects_nonallowlisted_url_before_network(self):
        with self.assertRaises(ValueError):
            verify_public({'url': 'http://127.0.0.1/private'})

    def test_reused_media_does_not_lock_x_database_during_http(self):
        path = Path(self.temp.name) / 'media.jpg'
        Image.new('RGB', (20, 20), 'white').save(path)
        asset = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        hosted = {**asset, 'url': 'https://drive.google.com/uc?id=test'}
        with self.store.transaction() as db:
            db.execute('CREATE TABLE instagram_media(sha256 TEXT PRIMARY KEY,state TEXT,file_id TEXT,asset_json TEXT)')
            db.execute("INSERT INTO instagram_media VALUES(?,'verified','test',?)", (asset['sha256'], json.dumps(hosted)))
        def verify(_):
            with self.store.transaction() as db:
                db.execute("INSERT INTO buffer_api_usage(requested,operation) VALUES(0,'read')")
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true'}):
            self.assertEqual(host_asset(asset, None, self.store, allow_public=True, verify=verify), hosted)

    def test_goals_show_over_one_hundred_and_null_without_zero_substitution(self):
        recorded = []
        original = Canvas.text
        def capture(canvas, text, *args, **kwargs):
            recorded.append(text)
            return original(canvas, text, *args, **kwargs)
        bundle = {'total': {'calories': 1972.3, 'protein': 87.11, 'fat': 55.71, 'carbs': 300.87},
                  'targets': {'calories': 2000, 'protein': 60, 'fat': None, 'carbs': 314}}
        with patch.object(Canvas, 'text', capture):
            daily_goals(bundle, Path(self.temp.name) / 'goals.jpg')
        self.assertIn('145%', recorded)
        self.assertIn('99%', recorded)
        self.assertIn('96%', recorded)
        self.assertIn('目標未設定', recorded)
        self.assertNotIn('0%', recorded)

    def test_reconcile_reads_known_post_without_new_submission(self):
        c = candidate()
        self.approve(c)
        row = self.store.claim_due(c.scheduled_at)
        self.store.begin(row, request_for(c), c.scheduled_at)
        self.store.mark(row, 'accepted', post(c, 'scheduled'))
        self.store.mark(row, 'outcome_unknown')
        class Reader:
            mutations = 0
            def _request(self, query, variables):
                if 'createPost(' in query:
                    self.mutations += 1
                    raise AssertionError('再送は禁止です')
                return {'post': post(c, 'sent')}
        api = Reader()
        self.assertEqual(reconcile_known(self.store, api), 1)
        self.assertEqual(api.mutations, 0)
        self.assertEqual(self.store.rows()[0]['state'], 'published')

    def test_render_is_deterministic_and_keeps_full_photo(self):
        path = Path(self.temp.name) / 'meal.jpg'
        photo = Image.new('RGB', (400, 200), 'red')
        photo.paste('blue', (0, 0, 30, 200))
        photo.paste('green', (370, 0, 400, 200))
        photo.save(path)
        meal = {'image_path': str(path), 'image_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'menu': '焼き魚・ごはん・味噌汁', 'nutrition': {'calories': 500, 'protein': 30, 'fat': 20, 'carbs': 60}}
        bundle = {'meals': {s: meal for s in ('breakfast', 'lunch', 'dinner')},
                  'total': {'calories': 1500, 'protein': 90, 'fat': 60, 'carbs': 180},
                  'targets': {'calories': 2000, 'protein': 100, 'fat': None, 'carbs': 240}, 'bundle_sha256': 'a' * 64}
        first = render_bundle(bundle, Path(self.temp.name) / 'render')
        self.assertEqual(first, render_bundle(bundle, Path(self.temp.name) / 'render'))
        self.assertEqual(sum(map(len, first['assets'].values())), 8)
        c = Canvas(1350)
        c.photo(meal, (0, 0, 400, 400))
        self.assertGreater(c.image.getpixel((5, 200))[2], 200)
        self.assertGreater(c.image.getpixel((395, 200))[1], 100)
        with self.assertRaises(ValueError):
            c.lines('献立' * 100, 0, 0, 100, 30, max_lines=2)
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            c.photo(meal, (0, 0, 400, 400))


if __name__ == '__main__':
    unittest.main()
