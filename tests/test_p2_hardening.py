"""P2 の状態保護、秘密情報マスキング、メディア検査を確認する。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import image_generator
from src.logging_utils import mask_sensitive


class P2HardeningTest(unittest.TestCase):
    def test_sensitive_values_are_masked(self):
        value = mask_sensitive({"api_key": "secret", "nested": "Bearer abc.def", "url": "https://x/?token=raw"})
        self.assertEqual(value["api_key"], "***")
        self.assertNotIn("abc.def", value["nested"])
        self.assertNotIn("raw", value["url"])

    def test_mock_image_has_valid_dimensions_and_safe_path(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(image_generator, "OUTPUT_DIRECTORY", Path(directory)):
            path = Path(image_generator.generate_promotional_image("test", "../../Log Eats"))
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent, Path(directory).resolve())
            image_generator.validate_image_file(path)

    def test_webhook_requires_discord_https_host(self):
        from src.discord_client import _validate_webhook_url
        _validate_webhook_url("https://discord.com/api/webhooks/1/token")
        with self.assertRaises(ValueError):
            _validate_webhook_url("https://example.invalid/webhook")
        with self.assertRaises(ValueError):
            _validate_webhook_url("http://discord.com/api/webhooks/1/token")

    def test_schema_has_safe_recovery_and_retry_limits(self):
        sql = Path("supabase_migration.sql").read_text(encoding="utf-8")
        self.assertIn("recover_failed_post", sql)
        self.assertIn("p_confirmed_not_published", sql)
        self.assertIn("retry_count BETWEEN 0 AND 5", sql)
        self.assertIn("SET search_path = public", sql)
        self.assertIn("operations_audit_log", sql)

    def test_service_role_variable_is_documented(self):
        contents = Path(".env.example").read_text(encoding="utf-8")
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY", contents)
        self.assertNotIn("\nSUPABASE_KEY=", contents)


if __name__ == "__main__":
    unittest.main()
