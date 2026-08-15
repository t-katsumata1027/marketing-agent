"""公開ワーカーの重複防止と不確実な障害時の安全側動作を検証する。"""

import importlib
import sys
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from unittest.mock import patch


def load_publisher():
    """外部 SDK を読み込まずに対象モジュールをロードする。"""
    database = types.ModuleType("src.supabase_client")
    for name in ("claim_posts_for_publish", "mark_publish_dispatch_started", "mark_post_published", "mark_post_requires_review"):
        setattr(database, name, lambda *args, **kwargs: None)
    twitter = types.ModuleType("src.twitter_client")
    twitter.post_tweet = lambda **kwargs: {"id": "unused"}
    sys.modules["src.supabase_client"] = database
    sys.modules["src.twitter_client"] = twitter
    sys.modules.pop("src.publisher", None)
    return importlib.import_module("src.publisher")


class PublisherTest(unittest.TestCase):
    def test_parallel_publishers_claim_a_post_once(self):
        publisher = load_publisher()
        lock, claimed, sent = Lock(), False, []
        post = {"id": 1, "content": "本文", "publish_lease_token": "lease-1"}
        def claim(**kwargs):
            nonlocal claimed
            with lock:
                if claimed:
                    return []
                claimed = True
                return [post]
        with patch.object(publisher, "claim_posts_for_publish", side_effect=claim), \
             patch.object(publisher, "mark_publish_dispatch_started"), \
             patch.object(publisher, "post_tweet", side_effect=lambda **kwargs: sent.append(1) or {"id": "x1"}), \
             patch.object(publisher, "mark_post_published"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda _: publisher.run_publisher_once(), range(2)))
        self.assertEqual(sent, [1])

    def test_db_failure_after_x_success_never_reposts(self):
        publisher = load_publisher()
        post = {"id": 2, "content": "本文", "publish_lease_token": "lease-2"}
        with patch.object(publisher, "claim_posts_for_publish", return_value=[post]), \
             patch.object(publisher, "mark_publish_dispatch_started"), \
             patch.object(publisher, "post_tweet", return_value={"id": "x2"}) as send, \
             patch.object(publisher, "mark_post_published", side_effect=RuntimeError("DB 障害")), \
             patch.object(publisher, "mark_post_requires_review") as review:
            self.assertEqual(publisher.run_publisher_once(), 0)
        send.assert_called_once()
        review.assert_called_once()
