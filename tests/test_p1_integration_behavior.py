"""P1で追加したclaim、品質ゲート、再試行境界の実フロー接続を検証する。"""

import unittest
from unittest.mock import MagicMock, patch

from src import agent_loop, publisher, reply_workflow, twitter_client, workflow


class ReplyWorkflowIntegrationTest(unittest.TestCase):
    """メンションの排他処理と品質失敗時の停止を検証する。"""

    def test_claimed_mentions_continue_after_one_failure(self):
        mentions = [
            {"id": "a", "reply_lease_token": "la"},
            {"id": "b", "reply_lease_token": "lb"},
        ]
        with patch.object(reply_workflow, "claim_mentions_for_reply", return_value=mentions), \
             patch.object(reply_workflow.reply_app, "invoke", side_effect=[RuntimeError("生成失敗"), {"db_response": [{"id": "post-b"}]}]) as invoke, \
             patch.object(reply_workflow, "fail_mention") as fail:
            result = reply_workflow.run_reply_processor("LogEats", limit=5)
        self.assertEqual(invoke.call_count, 2)
        fail.assert_called_once_with("a", "la", "生成失敗")
        self.assertEqual(result["processed"], 1)

    def test_quality_failure_does_not_save_or_notify_and_marks_failed(self):
        mention = {"id": "a", "tweet_id": "tweet", "author_username": "user", "reply_lease_token": "lease"}
        state = {"project_name": "LogEats", "project_config": {"quality_policy": {}}, "current_mention": mention, "draft_reply": "不適切", "unprocessed_mentions": [], "db_response": None}
        with patch.object(reply_workflow, "validate_content", return_value=["禁止語"]), \
             patch.object(reply_workflow, "log_post") as log_post, \
             patch.object(reply_workflow, "send_discord_notification") as notify, \
             patch.object(reply_workflow, "complete_mention") as complete, \
             patch.object(reply_workflow, "fail_mention") as fail:
            result = reply_workflow.save_and_notify_node(state)
        self.assertEqual(result, {"db_response": []})
        log_post.assert_not_called()
        notify.assert_not_called()
        complete.assert_not_called()
        fail.assert_called_once()

    def test_successful_reply_completes_mention(self):
        mention = {"id": "a", "tweet_id": "tweet", "author_username": "user", "reply_lease_token": "lease"}
        state = {"project_name": "LogEats", "project_config": {"quality_policy": {}}, "current_mention": mention, "draft_reply": "@user 返信", "unprocessed_mentions": [], "db_response": None}
        with patch.object(reply_workflow, "validate_content", return_value=[]), \
             patch.object(reply_workflow, "log_post", return_value=[{"id": "post"}]), \
             patch.object(reply_workflow, "send_discord_notification"), \
             patch.object(reply_workflow, "complete_mention") as complete:
            reply_workflow.save_and_notify_node(state)
        complete.assert_called_once_with("a", "lease")


class ScheduledJobIntegrationTest(unittest.TestCase):
    """ジョブleaseが実行と完了記録を確実に支配することを検証する。"""

    def test_proactive_skips_when_claim_is_unavailable(self):
        project = {"name": "LogEats", "query": "q", "promo_interval_hours": 6}
        with patch.object(agent_loop, "claim_job", return_value=False), patch.object(agent_loop.promo_workflow, "invoke") as invoke:
            self.assertFalse(agent_loop.run_proactive_generation(project))
        invoke.assert_not_called()

    def test_proactive_finishes_success_or_failure(self):
        project = {"name": "LogEats", "query": "q", "promo_interval_hours": 6}
        with patch.object(agent_loop, "claim_job", return_value="lease-a"), \
             patch.object(agent_loop, "load_project_config", return_value={"promo_topics": ["topic"]}), \
             patch.object(agent_loop.promo_workflow, "invoke"), \
             patch.object(agent_loop, "finish_job") as finish:
            self.assertTrue(agent_loop.run_proactive_generation(project))
        finish.assert_called_once_with("LogEats", "proactive_generation", "lease-a", True)
        with patch.object(agent_loop, "claim_job", return_value="lease-b"), \
             patch.object(agent_loop, "load_project_config", side_effect=RuntimeError("設定失敗")), \
             patch.object(agent_loop, "finish_job") as finish:
            with self.assertRaises(RuntimeError):
                agent_loop.run_proactive_generation(project)
        self.assertEqual(finish.call_args.args[:3], ("LogEats", "proactive_generation", "lease-b"))
        self.assertFalse(finish.call_args.args[3])

    def test_analytics_skips_and_records_success_or_failure(self):
        with patch.object(agent_loop, "claim_job", return_value=False), patch.object(agent_loop, "run_analytics_job") as job:
            self.assertFalse(agent_loop.run_periodic_analytics())
        job.assert_not_called()
        with patch.object(agent_loop, "claim_job", return_value="lease-c"), patch.object(agent_loop, "run_analytics_job"), patch.object(agent_loop, "finish_job") as finish:
            self.assertTrue(agent_loop.run_periodic_analytics())
        finish.assert_called_once_with("__system__", "analytics", "lease-c", True)


class QualityAndRetryBoundaryTest(unittest.TestCase):
    """品質不合格とX投稿の非リトライ境界を検証する。"""

    def test_workflow_quality_rejection_stops_database_write(self):
        state = {"project_name": "LogEats", "topic": "t", "project_config": {}, "draft_content": "不適切", "media_path": None, "db_response": None}
        with patch.object(workflow, "validate_content", return_value=["禁止語"]), patch.object(workflow, "log_post") as log_post:
            with self.assertRaises(ValueError):
                workflow.save_to_db_node(state)
        log_post.assert_not_called()

    def test_publisher_quality_rejection_stops_x_post(self):
        post = {"id": "p", "publish_lease_token": "lease", "content": "不適切", "project_name": "LogEats"}
        with patch.object(publisher, "claim_posts_for_publish", return_value=[post]), \
             patch.object(publisher, "validate_content", return_value=["禁止語"]), \
             patch.object(publisher, "post_tweet") as post_tweet, \
             patch.object(publisher, "mark_post_requires_review") as review:
            self.assertEqual(publisher.run_publisher_once(), 0)
        post_tweet.assert_not_called()
        review.assert_called_once()

    def test_create_tweet_is_never_retried(self):
        client = MagicMock()
        client.create_tweet.side_effect = RuntimeError("接続断")
        with patch.object(twitter_client, "get_twitter_client", return_value=client):
            with self.assertRaises(RuntimeError):
                twitter_client.post_tweet("本文")
        client.create_tweet.assert_called_once_with(text="本文")


if __name__ == "__main__":
    unittest.main()
