"""X の最近の投稿を検索して見込み顧客候補として記録するモジュール。"""

from typing import Any

try:
    from src.supabase_client import log_mention
    from src.twitter_client import get_twitter_client
except ModuleNotFoundError:
    from supabase_client import log_mention
    from twitter_client import get_twitter_client


def run_social_listening(project_name: str, query: str, max_results: int = 10) -> int:
    """検索語に一致する最近の X 投稿を取得し、Supabase へ保存する。"""
    if not project_name.strip():
        raise ValueError("プロジェクト名を空にすることはできません。")
    if not query.strip():
        raise ValueError("検索クエリを空にすることはできません。")
    if not 10 <= max_results <= 100:
        raise ValueError("max_results は 10 から 100 の範囲で指定してください。")

    print(f"--- [{project_name}] のソーシャルリスニングを開始します ---")
    print(f"検索クエリ: {query}")
    response = get_twitter_client().search_recent_tweets(
        query=query,
        max_results=max_results,
        expansions=["author_id"],
        user_fields=["username"],
    )

    if not response.data:
        print("この検索クエリに一致する新しい投稿はありません。")
        return 0

    includes: dict[str, list[Any]] = response.includes or {}
    users = {
        user.id: user.username
        for user in includes.get("users", [])
        if user.username is not None
    }

    logged_count = 0
    for tweet in response.data:
        author_username = users.get(tweet.author_id, "unknown")
        print(f"@{author_username} の投稿を検出しました: {tweet.text[:50]}...")
        # upsertで既存メンションは例外にせず安全にスキップする。
        inserted = log_mention(
            project_name=project_name,
            tweet_id=str(tweet.id),
            author_username=author_username,
            content=tweet.text,
        )
        logged_count += int(bool(inserted))

    return logged_count


if __name__ == "__main__":
    print("ソーシャルリスニングモジュールを読み込みました。")
