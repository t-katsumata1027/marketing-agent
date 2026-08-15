"""原子的に確保した投稿だけを X へ送信する公開ワーカー。"""

import time
from typing import Any

try:
    from src.supabase_client import (
        claim_posts_for_publish,
        mark_publish_dispatch_started,
        mark_post_published,
        mark_post_requires_review,
    )
    from src.twitter_client import post_tweet
    from src.heartbeat import write_heartbeat
    from src.quality_gate import validate_content
    from src.crew_setup import load_project_config
    from src.paths import project_config_path
except ModuleNotFoundError:
    from supabase_client import (  # type: ignore
        claim_posts_for_publish, mark_publish_dispatch_started, mark_post_published,
        mark_post_requires_review,
    )
    from twitter_client import post_tweet  # type: ignore
    from heartbeat import write_heartbeat  # type: ignore
    from quality_gate import validate_content  # type: ignore
    from crew_setup import load_project_config  # type: ignore
    from paths import project_config_path  # type: ignore


def run_publisher_once(limit: int = 10, lease_seconds: int = 300) -> int:
    """DB で lease を取得済みの投稿のみを送信し、成功数を返す。

    X API は呼出しタイムアウト時に「未投稿」を証明できない。このため、送信開始後に
    例外又は DB 更新失敗が起きた投稿は自動再送せず、``failed`` として運用者確認へ回す。
    """
    posts = claim_posts_for_publish(limit=limit, lease_seconds=lease_seconds)
    published_count = 0
    for post in posts:
        post_id = post["id"]
        lease_token = post["publish_lease_token"]
        try:
            # 既存データの公開を設定ファイル不在だけで停止させない。設定があれば厳格化する。
            try:
                project_name = post.get("project_name")
                policy = load_project_config(project_config_path(project_name)).get("quality_policy") if project_name else None
            except FileNotFoundError:
                policy = None
            errors = validate_content(post["content"], policy)
            if errors:
                raise ValueError("品質ゲートで公開を停止しました: " + "、".join(errors))
            # 外部副作用より先に永続化する。ここで失敗した場合は X を呼ばない。
            mark_publish_dispatch_started(post_id, lease_token)
            response = post_tweet(
                text=post["content"],
                in_reply_to_tweet_id=post.get("in_reply_to_id"),
                media_paths=[post["media_path"]] if post.get("media_path") else None,
            )
            x_tweet_id = response.get("id")
            if not x_tweet_id:
                raise RuntimeError("X API 応答に投稿 ID がありません")
            mark_post_published(post_id, lease_token, str(x_tweet_id))
            published_count += 1
        except Exception as error:
            # 成功後の DB 障害も含め、投稿可否が不明なため安全側で停止する。
            try:
                mark_post_requires_review(post_id, lease_token, str(error))
            except Exception:
                # DB 障害時は publishing のまま残る。lease 回収ではなく運用者確認対象である。
                pass
            print(f"投稿 ID {post_id} は自動再送せず運用者確認へ移しました")
    return published_count


def run_publisher_daemon(poll_interval_seconds: int = 60) -> None:
    """公開ワーカーを常駐実行する。"""
    if poll_interval_seconds <= 0:
        raise ValueError("ポーリング間隔は 1 秒以上にしてください")
    while True:
        try:
            write_heartbeat("publisher")
            run_publisher_once()
        except Exception as error:
            print(f"公開ワーカーのループでエラーが発生しました: {error}")
        time.sleep(poll_interval_seconds)


if __name__ == "__main__":
    run_publisher_daemon()
