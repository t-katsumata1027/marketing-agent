"""生成失敗の再試行禁止と、未レビュー下書きの境界を検証する。"""

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from src.mina.generation import OpenAIClient, draft_day, validate_plan, meal_image_description


def plan():
    return {"daily_comment": "明日の献立をまとめました。",
            "meals": {slot: {"menu": "ご飯・魚・野菜", "comment": slot + "の献立候補です。",
                             "estimated_calories": 600, "foods": [{"name": "ご飯", "amount": "150g"}, {"name": "魚", "amount": "100g"}],
                             "image_description": "ご飯150gと魚100gを別皿に盛る。"}
                      for slot in ("breakfast", "lunch", "dinner")}}


class GenerationTest(unittest.TestCase):
    def test_image_input_preserves_food_amounts_and_style_priority(self):
        meal = plan()['meals']['breakfast']
        meal['image_description'] = '真上から撮影。料理を盛る。'
        prompt = meal_image_description(meal)
        context = json.loads(prompt.split('\n', 1)[1])
        self.assertEqual(context['foods'], meal['foods'])
        self.assertEqual(context['menu'], meal['menu'])
        self.assertIn('固定の撮影スタイルを最優先', prompt)
        self.assertIn('固定スタイルを変更しない', prompt)

    def test_disabled_ai_does_not_connect(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "private-key", "MINA_AI_ENABLED": "false"}), patch("src.mina.generation.urlopen") as request:
            with self.assertRaises(RuntimeError):
                OpenAIClient().request("responses", {})
            request.assert_not_called()

    def test_error_is_redacted_and_not_retried(self):
        error = HTTPError("https://api.openai.com/v1/responses", 429, "private-key", None, io.BytesIO(b'private-body'))
        with patch.dict("os.environ", {"OPENAI_API_KEY": "private-key", "MINA_AI_ENABLED": "true"}), patch("src.mina.generation.urlopen", side_effect=error) as request:
            with self.assertRaisesRegex(RuntimeError, "^openai_http_429$"):
                OpenAIClient().request("responses", {})
            self.assertEqual(request.call_count, 1)

    def test_incomplete_response_is_not_accepted(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "private-key"}):
            client = OpenAIClient()
        client.request = Mock(return_value={"status": "incomplete", "output": []})
        with self.assertRaises(ValueError):
            client.structured("instructions", "context", {}, "test")

    def test_draft_is_not_quality_verified_and_existing_draft_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            history, output = Path(folder) / "history.md", Path(folder) / "draft.json"
            history.write_text("直近の運用履歴。", encoding="utf-8")
            client = Mock()
            client.structured.return_value = plan()
            draft_day("2026-10-08", history, output, client)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["state"], "draft_unreviewed")
            self.assertIs(saved["history_verified"], False)
            self.assertNotIn("secondary_review", saved)
            with self.assertRaises(ValueError):
                draft_day("2026-10-08", history, output, client)
            self.assertEqual(client.structured.call_count, 1)

    def test_bad_totals_and_experiential_claims_are_rejected(self):
        for value in (True, 0, 1001, 500):
            changed = copy.deepcopy(plan())
            for meal in changed["meals"].values():
                meal["estimated_calories"] = value
            with self.assertRaises(ValueError):
                validate_plan(changed)
        changed = plan()
        changed["daily_comment"] = "今日は食べた料理です。"
        with self.assertRaises(ValueError):
            validate_plan(changed)

    def test_ingredient_details_are_kept_within_api_supported_limit(self):
        changed = plan()
        changed["meals"]["breakfast"]["foods"] = [{"name": "食材" + str(i), "amount": "10g"} for i in range(20)]
        validate_plan(changed)
        changed["meals"]["breakfast"]["foods"].append({"name": "上限超過", "amount": "10g"})
        with self.assertRaises(ValueError):
            validate_plan(changed)
