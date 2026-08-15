"""既存スキーマ、RPC権限、heartbeatの回帰を検証する。"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src import heartbeat


class SchemaAndHeartbeatTest(unittest.TestCase):
    def test_existing_schema_columns_and_mentions_are_preserved(self):
        sql = Path("supabase_migration.sql").read_text(encoding="utf-8")
        for column in ("impressions", "likes", "retweets", "replies", "bookmarks", "metrics_updated_at"):
            self.assertIn(f"ADD COLUMN IF NOT EXISTS {column}", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS public.mentions_log", sql)
        self.assertIn("mentions_log_project_status_idx", sql)

    def test_every_security_definer_rpc_is_closed(self):
        sql = Path("supabase_migration.sql").read_text(encoding="utf-8")
        signatures = ("claim_posts_for_publish(INTEGER, INTEGER)", "record_publish_dispatch_started(BIGINT, UUID)", "complete_published_post(BIGINT, UUID, TEXT)", "fail_post_for_review(BIGINT, UUID, TEXT)", "approve_post_with_token(TEXT, TEXT, TEXT)")
        for signature in signatures:
            self.assertIn(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC, anon, authenticated", sql)
            self.assertIn(f"GRANT EXECUTE ON FUNCTION public.{signature} TO service_role", sql)

    def test_service_heartbeats_are_fresh_then_expire(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(heartbeat, "HEARTBEAT_DIRECTORY", Path(directory)):
            for service in ("agent-loop", "publisher", "discord-bot"):
                heartbeat.write_heartbeat(service)
                self.assertTrue(heartbeat.is_heartbeat_fresh(service, 1))
            time.sleep(0.02)
            self.assertFalse(heartbeat.is_heartbeat_fresh("publisher", 0.01))
