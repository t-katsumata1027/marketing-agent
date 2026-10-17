"""API・既存X候補・写真の一致と、目標欠落時の停止を検証する。"""

from dataclasses import asdict, replace
from contextlib import closing
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PIL import Image

from src.mina.domain import ACCOUNT, Candidate
from src.mina.instagram_domain import digest
from src.mina.instagram_source import load_bundle, verify_bundle
from src.mina.clients import ClockSkewError, MealClient, await_checked_at

NOW = datetime(2026, 10, 16, 1, tzinfo=UTC)
TARGET = '2026-10-17'


class InstagramSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.day = self.base / TARGET
        self.day.mkdir()
        self.database = self.base / 'db.sqlite3'
        self.state = {'contract_version': 'mina-write-v1', 'account': ACCOUNT, 'complete': True,
                      'date': TARGET, 'checked_at': NOW.isoformat(), 'state_version': 'sv1_' + 'a' * 64,
                      'meal_count': 3, 'meals': [], 'nutrition_targets': {'calories': 2000, 'protein': 100, 'fat': 60, 'carbs': 250}}
        self.state['summary'] = {'totalCalories': 1500, 'totalProtein': 90, 'totalFat': 45, 'totalCarbs': 180}
        saved = {'target_date': TARGET, 'images': {}}
        self.candidates = {}
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('CREATE TABLE candidates(target_date TEXT,slot TEXT,payload TEXT,digest TEXT,state TEXT)')
            for slot in ('breakfast', 'lunch', 'dinner', 'daily'):
                daily = slot == 'daily'
                n = {'calories': 1500 if daily else 500, 'protein': 90 if daily else 30,
                     'fat': 45 if daily else 15, 'carbs': 180 if daily else 60}
                result = {'result_version': 'rv1_' + 'b' * 64}
                image_hash = 'c' * 64
                if daily:
                    self.state['daily_share'] = result
                else:
                    path = self.day / (slot + '.jpg')
                    Image.new('RGB', (100, 100), 'white').save(path)
                    image_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                    saved['images'][slot] = {'path': str(path), 'sha256': image_hash,
                                            'drive': {'verified': True, 'sha256': image_hash},
                                            'image_review': {'status': 'image_acceptable', 'reason_codes': []}}
                    result['meal'] = {'meal_type': slot}
                    self.state['meals'].append(result)
                c = Candidate(TARGET, slot, 'おいしい', '本文', 'https://example.com/share', 'ごはん・焼き魚', n,
                              image_hash, 'd' * 64, 'pass', 'publishable', True, True, True, 'e' * 40,
                              meal_count=3 if daily else 1,
                              approval_source={'state_version': self.state['state_version'], 'result_sha256': digest(result)})
                self.candidates[slot] = c
                db.execute('INSERT INTO candidates VALUES(?,?,?,?,?)', (TARGET, slot, json.dumps(asdict(c), ensure_ascii=False), c.digest, 'approved'))
            db.commit()
        (self.day / 'drive.json').write_text(json.dumps(saved), encoding='utf-8')

    def load(self):
        return load_bundle(self.database, self.base, TARGET, self.state, NOW)

    def test_exact_shared_source_and_no_fake_targets(self):
        bundle = self.load()
        self.assertEqual(bundle['targets'], self.state['nutrition_targets'])
        self.assertEqual(bundle['total']['calories'], 1500)
        self.state.pop('nutrition_targets')
        with self.assertRaisesRegex(ValueError, 'logeats_nutrition_targets_not_exposed'):
            self.load()

    def test_null_is_unset_but_zero_or_nan_is_invalid(self):
        self.state['nutrition_targets']['fat'] = None
        self.assertIsNone(self.load()['targets']['fat'])
        for invalid in (0, -1, float('nan'), True):
            self.state['nutrition_targets']['fat'] = invalid
            with self.assertRaises(ValueError):
                self.load()

    def test_state_summary_is_the_total_source(self):
        self.state['summary']['totalCalories'] = 1600
        with self.assertRaises(ValueError):
            self.load()

    def test_registered_targets_are_not_historical_or_hardcoded(self):
        first = self.load()
        self.state['nutrition_targets'] = {'calories': 2000, 'protein': 60, 'fat': 56, 'carbs': 314}
        current = self.load()
        self.assertEqual(current['targets']['protein'], 60)
        self.assertEqual(current['x_digests'], first['x_digests'])
        self.assertNotEqual(current['bundle_sha256'], first['bundle_sha256'])
        self.state['nutrition_targets'] = dict.fromkeys(('calories', 'protein', 'fat', 'carbs'))
        self.assertTrue(all(v is None for v in self.load()['targets'].values()))

    def test_state_get_has_no_browser_credentials_and_requests_no_cache(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return json.dumps({'checked_at': NOW.isoformat()}).encode()
        requests = []
        def open_request(request, timeout):
            requests.append(request)
            return Response()
        with patch('urllib.request.build_opener', return_value=SimpleNamespace(open=open_request)):
            MealClient('https://www.log-eats.com', 'test-only-token-' * 4).day_state(TARGET)
        self.assertEqual(requests[0].get_method(), 'GET')
        self.assertEqual(requests[0].get_header('Cache-control'), 'no-store')
        self.assertIsNone(requests[0].get_header('Cookie'))
        self.assertIsNone(requests[0].get_header('Origin'))

    def test_instagram_waits_real_time_without_accepting_future_or_relaxing_x_default(self):
        result = {'checked_at': (NOW + timedelta(seconds=2)).isoformat()}
        times = [NOW, NOW + timedelta(seconds=2)]
        slept = []
        await_checked_at(result, clock=lambda: times.pop(0), sleep=slept.append, max_ahead=5)
        self.assertEqual(slept, [2])
        with self.assertRaises(ClockSkewError):
            await_checked_at(result, clock=lambda: NOW, sleep=lambda _: None)
        with self.assertRaises(ClockSkewError):
            await_checked_at(result, clock=lambda: NOW, sleep=lambda _: None, max_ahead=5)
        with self.assertRaises(ClockSkewError):
            await_checked_at({'checked_at': (NOW + timedelta(seconds=6)).isoformat()},
                             clock=lambda: NOW, sleep=lambda _: None, max_ahead=5)

    def test_stale_state_or_changed_api_result_is_rejected(self):
        self.state['checked_at'] = (NOW - timedelta(seconds=121)).isoformat()
        with self.assertRaises(ValueError):
            self.load()
        self.state['checked_at'] = NOW.isoformat()
        self.state['meals'][0]['result_version'] = 'rv1_' + 'f' * 64
        with self.assertRaises(ValueError):
            self.load()

    def test_repaired_photo_uses_hash_bound_completed_review(self):
        old = json.loads((self.day / 'drive.json').read_text())
        old['images']['dinner']['sha256'] = 'f' * 64
        (self.day / 'drive.json').write_text(json.dumps(old), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.load()
        image = {'path': str(self.day / 'dinner.jpg'), 'sha256': self.candidates['dinner'].image_sha256}
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('CREATE TABLE preparation_steps(target_date TEXT,step TEXT,state TEXT,result TEXT)')
            db.execute('INSERT INTO preparation_steps VALUES(?,?,?,?)', (TARGET, 'image:dinner:repair', 'complete', json.dumps(image)))
            db.execute('INSERT INTO preparation_steps VALUES(?,?,?,?)', (TARGET, 'image_review:dinner:repair', 'complete', json.dumps({'status': 'hold', 'reason_codes': ['image_analysis_mismatch']})))
            db.commit()
        with self.assertRaises(ValueError):
            self.load()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE preparation_steps SET result=? WHERE step='image_review:dinner:repair'", (json.dumps({'status': 'image_acceptable', 'reason_codes': []}),))
            db.commit()
        self.assertEqual(self.load()['meals']['dinner']['image_sha256'], image['sha256'])

    def test_changed_x_candidate_or_photo_breaks_approval_binding(self):
        bundle = self.load()
        c = replace(self.candidates['lunch'], menu='ごはん・卵焼き')
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('UPDATE candidates SET payload=?,digest=? WHERE slot=?', (json.dumps(asdict(c)), c.digest, 'lunch'))
            db.commit()
        with self.assertRaises(ValueError):
            verify_bundle(bundle, self.database, self.base, self.state, NOW)
        (self.day / 'breakfast.jpg').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.load()


if __name__ == '__main__':
    unittest.main()
