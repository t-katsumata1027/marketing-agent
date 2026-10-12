"""共通プロンプト変更後に旧生成結果を再利用・上書きしないことを確認する。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.preparation import prepare_images
from src.mina.store import Store
from tests.mina.test_generation import plan
from src.mina.generation import review_schema


class ImagePromptRevisionTests(unittest.TestCase):
    def prepared(self,root,review=None):
        draft=root/'draft.json'
        draft.write_text(json.dumps({'state':'draft_unreviewed','target_date':'2026-10-13',**plan()}))
        store=Store(root/'state.sqlite3');client=Mock()
        client.image.return_value=b'\xff\xd8\xfftest-only'
        client.review_image.return_value=review or {'status':'image_acceptable','reason_codes':[]}
        return draft,store,client,root/'images'

    def test_schema_change_preserves_old_results_without_calls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);draft,store,client,directory=self.prepared(root)
            prepare_images(store,draft,directory,client)
            with store.transaction() as db:
                before=[tuple(row) for row in db.execute('SELECT * FROM preparation_steps ORDER BY step')]
            original={path.name:path.read_bytes() for path in directory.iterdir()}
            changed=review_schema('image_acceptable')
            changed['properties']['reason_codes']['items']['enum'].append('future_validation_failure')
            client.reset_mock()
            with patch('src.mina.generation.review_schema',return_value=changed):
                with self.assertRaisesRegex(ValueError,'版が変わ'):
                    prepare_images(store,draft,directory,client)
            client.image.assert_not_called();client.review_image.assert_not_called()
            self.assertEqual(original,{path.name:path.read_bytes() for path in directory.iterdir()})
            with store.transaction() as db:
                self.assertEqual(before,[tuple(row) for row in db.execute('SELECT * FROM preparation_steps ORDER BY step')])

    def test_changed_image_stops_before_new_review_or_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);draft,store,client,directory=self.prepared(root)
            prepare_images(store,draft,directory,client)
            path=directory/'2026-10-13-breakfast.jpg';path.write_bytes(b'changed')
            client.reset_mock()
            with self.assertRaisesRegex(ValueError,'保存した画像の版が変わ'):
                prepare_images(store,draft,directory,client)
            client.image.assert_not_called();client.review_image.assert_not_called()
            self.assertEqual(path.read_bytes(),b'changed')

    def test_hold_preserves_first_image_and_blocks_later_slots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            draft,store,client,directory=self.prepared(root,{'status':'hold','reason_codes':['image_analysis_mismatch']})
            with self.assertRaisesRegex(ValueError,'保留'):
                prepare_images(store,draft,directory,client)
            self.assertEqual([p.name for p in directory.iterdir()],['2026-10-13-breakfast.jpg'])
            original=(directory/'2026-10-13-breakfast.jpg').read_bytes()
            client.reset_mock()
            with self.assertRaisesRegex(ValueError,'保留'):
                prepare_images(store,draft,directory,client)
            client.image.assert_not_called();client.review_image.assert_not_called()
            self.assertEqual((directory/'2026-10-13-breakfast.jpg').read_bytes(),original)
            with store.transaction() as db:
                rows={row['step']:json.loads(row['result']) for row in db.execute('SELECT * FROM preparation_steps')}
            self.assertEqual(set(rows),{'image:breakfast','image_review:breakfast'})
            self.assertEqual(rows['image_review:breakfast']['status'],'hold')

    def test_review_only_change_preserves_images_and_refuses_cached_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);draft=root/'draft.json'
            draft.write_text(json.dumps({'state':'draft_unreviewed','target_date':'2026-10-13',**plan()}))
            store=Store(root/'state.sqlite3');client=Mock()
            client.image.return_value=b'\xff\xd8\xfftest-only'
            client.review_image.return_value={'status':'image_acceptable','reason_codes':[]}
            directory=root/'images'
            prepare_images(store,draft,directory,client)
            original={path.name:path.read_bytes() for path in directory.iterdir()}
            client.reset_mock()
            with patch('src.mina.generation.REVIEW_INSTRUCTIONS','別の点検基準'):
                with self.assertRaisesRegex(ValueError,'版が変わ'):
                    prepare_images(store,draft,directory,client)
            client.image.assert_not_called();client.review_image.assert_not_called()
            self.assertEqual(original,{path.name:path.read_bytes() for path in directory.iterdir()})

    def test_changed_style_preserves_old_images_and_requires_reconciliation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);draft=root/'draft.json'
            draft.write_text(json.dumps({'state':'draft_unreviewed','target_date':'2026-10-13',**plan()}))
            store=Store(root/'state.sqlite3');client=Mock()
            client.image.return_value=b'\xff\xd8\xfftest-only'
            client.review_image.return_value={'status':'image_acceptable','reason_codes':[]}
            directory=root/'images'
            prepare_images(store,draft,directory,client)
            original={path.name:path.read_bytes() for path in directory.iterdir()}
            client.reset_mock()
            with patch('src.mina.preparation.IMAGE_STYLE','別の共通スタイル'), patch('src.mina.generation.IMAGE_STYLE','別の共通スタイル'):
                with self.assertRaisesRegex(ValueError,'版が変わ'):
                    prepare_images(store,draft,directory,client)
            client.image.assert_not_called();client.review_image.assert_not_called()
            self.assertEqual(original,{path.name:path.read_bytes() for path in directory.iterdir()})
