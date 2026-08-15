"""公開済み X 投稿のパフォーマンスを Supabase へ同期するモジュール。"""

try:
    from src.supabase_client import get_recent_published_posts, update_post_metrics
    from src.twitter_client import get_tweet_metrics
except ModuleNotFoundError:
    from supabase_client import get_recent_published_posts, update_post_metrics
    from twitter_client import get_tweet_metrics


def run_analytics_job(days: int = 7) -> int:
    """直近の公開投稿について X メトリクスを取得・保存し、更新件数を返す。"""
    print("--- 分析ジョブを開始します ---")
    posts = get_recent_published_posts(days)
    if not posts:
        print("分析対象の公開済み投稿はありません。")
        return 0

    updated_count = 0
    for post in posts:
        x_tweet_id = post.get("x_tweet_id")
        if not x_tweet_id:
            print(f"投稿 ID {post['id']} に X 投稿 ID がないためスキップします。")
            continue

        try:
            metrics = get_tweet_metrics(str(x_tweet_id))
            if not metrics:
                print(f"投稿 ID {post['id']} のメトリクスを取得できませんでした。")
                continue
            update_post_metrics(post["id"], metrics)
            updated_count += 1
            print(f"投稿 ID {post['id']} のメトリクスを更新しました: {metrics}")
        except Exception as error:
            print(f"投稿 ID {post['id']} の分析に失敗しました: {error}")

    print("--- 分析ジョブが完了しました ---")
    return updated_count


if __name__ == "__main__":
    run_analytics_job()
