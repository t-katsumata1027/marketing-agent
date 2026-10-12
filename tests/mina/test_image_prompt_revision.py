"""共通プロンプト変更後に旧生成結果を再利用・上書きしないことを確認する。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.preparation import prepare_images
from src.mina.store import Store
from tests.mina.test_generation import plan


class ImagePromptRevisionTests(unittest.TestCase):
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
            with patch('src.mina.preparation.IMAGE_STYLE','別の共通スタイル'):
                with self.assertRaisesRegex(ValueError,'版が変わ'):
                    prepare_images(store,draft,directory,client)
            client.image.assert_not_called();client.review_image.assert_not_called()
            self.assertEqual(original,{path.name:path.read_bytes() for path in directory.iterdir()})
