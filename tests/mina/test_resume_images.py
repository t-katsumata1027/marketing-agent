"""画像復旧で既存朝食と旧保留を保持し、重複登録・無限再生成を防ぐ。"""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from src.mina.domain import JST
from src.mina.preparation import PreparationJournal, prepare_images
from src.mina.resume_images import resume_image_hold
from src.mina.store import Store
from test_generation import plan
from test_readiness import evidence, reviewed
from test_daily import TARGET, HOST

KEY = '11111111-1111-4111-8111-111111111111'
NOW = datetime(2026, 10, 7, 19, 10, tzinfo=JST)


class ImageResumeTests(unittest.TestCase):
    def setup_flow(self, root):
        store = Store(root / 'db')
        journal = PreparationJournal(store)
        with store.transaction() as db:
            db.execute("INSERT INTO preparation VALUES(?,'held:images',?)", (TARGET, NOW.timestamp()))
            db.execute('CREATE TABLE mina_requests (target_date TEXT)')
        base = root / 'daily' / TARGET
        base.mkdir(parents=True)
        draft = base / 'draft.json'
        draft.write_text(json.dumps({'state': 'draft_unreviewed', 'target_date': TARGET, **plan()}), encoding='utf-8')
        ai = Mock()
        ai.image.return_value = b'\xff\xd8\xffimage'
        ai.review_image.side_effect = [{'status': 'image_acceptable', 'reason_codes': []},
                                      {'status': 'hold', 'reason_codes': ['image_analysis_mismatch']}]
        with self.assertRaises(ValueError):
            prepare_images(store, draft, base / 'images', ai)
        ai.reset_mock(side_effect=True)
        ai.image.return_value = b'\xff\xd8\xffnew-image'
        ai.review_image.return_value = {'status': 'image_acceptable', 'reason_codes': []}
        history = root / 'history'
        history.mkdir()
        proof = {**evidence(), 'checked_at': NOW.isoformat()}
        (history / '1.json').write_text(json.dumps(proof))
        return store, base, ai, history

    def invoke(self, root, store, ai, history, register, now=NOW):
        with patch('src.mina.resume_images.release_commit', return_value='a' * 40), \
             patch('src.mina.resume_images.save_images_to_drive', return_value={'saved': True}), \
             patch('src.mina.resume_images.register_day', register), \
             patch('src.mina.resume_images.verify_source'):
            return resume_image_hold(store, root / 'daily', TARGET, KEY, root, 'a' * 40,
                                     HOST, ai, Mock(), Mock(), history, clock=lambda: now)

    def test_replaces_only_lunch_and_preserves_original_hold_then_imports_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, base, ai, history = self.setup_flow(root)
            originals = {p.name: p.read_bytes() for p in (base / 'images').glob('*.jpg')}
            register = Mock(return_value=reviewed())
            self.assertEqual(self.invoke(root, store, ai, history, register)['post_count'], 4)
            self.assertEqual(ai.image.call_count, 2)
            self.assertEqual(ai.review_image.call_count, 2)
            self.assertIn('右端', ai.image.call_args_list[0].args[0])
            self.assertEqual(originals, {p.name: p.read_bytes() for p in (base / 'images').glob('*.jpg') if 'dinner' not in p.name})
            saved = json.loads((base / 'images-reviewed.json').read_text(encoding='utf-8'))
            self.assertIn(KEY, saved['images']['lunch']['path'])
            with store.transaction() as db:
                old = json.loads(db.execute("SELECT result FROM preparation_steps WHERE step='image_review:lunch'").fetchone()[0])
                self.assertEqual(old['status'], 'hold')
            self.assertEqual(len(store.list_day(TARGET)), 4)
            with self.assertRaises(ValueError):
                self.invoke(root, store, ai, history, register)
            self.assertEqual(register.call_count, 1)

    def test_second_hold_never_retries_or_registers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, base, ai, history = self.setup_flow(root)
            ai.review_image.return_value = {'status': 'hold', 'reason_codes': ['image_analysis_mismatch']}
            register = Mock()
            with self.assertRaises(RuntimeError):
                self.invoke(root, store, ai, history, register)
            with self.assertRaises(ValueError):
                self.invoke(root, store, ai, history, register)
            self.assertEqual(ai.image.call_count, 1)
            register.assert_not_called()

    def test_changed_original_unknown_step_and_existing_request_stop_before_generation(self):
        for mode in ('changed', 'unknown', 'request', 'expired'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                store, base, ai, history = self.setup_flow(root)
                if mode == 'changed':
                    (base / 'images' / (TARGET + '-breakfast.jpg')).write_bytes(b'changed')
                with store.transaction() as db:
                    if mode == 'unknown':
                        db.execute("UPDATE preparation_steps SET state='outcome_unknown' WHERE step='image:lunch'")
                    if mode == 'request':
                        db.execute('INSERT INTO mina_requests VALUES(?)', (TARGET,))
                register = Mock()
                now = NOW.replace(hour=23) if mode == 'expired' else NOW
                with self.assertRaises(ValueError):
                    self.invoke(root, store, ai, history, register, now)
                ai.image.assert_not_called()
                register.assert_not_called()
