"""常駐系と既存ワークフローの公開import契約を検証する。"""

import importlib
import unittest


class ImportContractTest(unittest.TestCase):
    def test_agent_publisher_discord_and_workflows_import(self):
        modules = (
            "src.agent_loop", "src.publisher", "src.discord_bot", "src.analytics",
            "src.rewrite_workflow", "src.reply_workflow", "src.listener", "src.workflow",
        )
        for name in modules:
            with self.subTest(module=name):
                self.assertIsNotNone(importlib.import_module(name))

    def test_supabase_public_api_is_available(self):
        client = importlib.import_module("src.supabase_client")
        for name in (
            "log_post", "log_mention", "get_unprocessed_mentions", "update_mention_status",
            "get_post", "update_draft_content", "get_recent_published_posts", "update_post_metrics",
        ):
            with self.subTest(function=name):
                self.assertTrue(callable(getattr(client, name)))
