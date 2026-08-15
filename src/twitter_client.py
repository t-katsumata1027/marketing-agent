"""X API v2 と連携するための Tweepy クライアント。"""

from os import PathLike
from pathlib import Path
from typing import Any, Sequence

import tweepy

try:
    from src.config import config
except ModuleNotFoundError:
    from config import config


def get_twitter_client() -> tweepy.Client:
    """Doppler の認証情報で初期化した X API v2 クライアントを返す。"""
    required_keys = (
        config.X_API_KEY,
        config.X_API_SECRET,
        config.X_ACCESS_TOKEN,
        config.X_ACCESS_TOKEN_SECRET,
    )
    if not all(required_keys):
        raise ValueError("X API の認証情報が未設定です。Doppler 経由で実行してください。")

    return tweepy.Client(
        consumer_key=config.X_API_KEY,
        consumer_secret=config.X_API_SECRET,
        access_token=config.X_ACCESS_TOKEN,
        access_token_secret=config.X_ACCESS_TOKEN_SECRET,
    )


def post_tweet(
    text: str,
    in_reply_to_tweet_id: str | None = None,
    media_paths: Sequence[str | PathLike[str]] | None = None,
) -> dict[str, Any]:
    """画像付きの通常投稿または指定投稿へのリプライを公開する。"""
    if not text.strip():
        raise ValueError("投稿内容を空にすることはできません。")

    request_data: dict[str, str] = {"text": text}
    if in_reply_to_tweet_id:
        request_data["in_reply_to_tweet_id"] = in_reply_to_tweet_id
    if media_paths:
        auth = tweepy.OAuth1UserHandler(
            config.X_API_KEY,
            config.X_API_SECRET,
            config.X_ACCESS_TOKEN,
            config.X_ACCESS_TOKEN_SECRET,
        )
        api_v1 = tweepy.API(auth)
        media_ids: list[str | int] = []
        for media_path in media_paths:
            path = Path(media_path)
            if not path.is_file():
                raise FileNotFoundError(f"添付するメディアファイルが見つかりません: {path}")
            uploaded_media = api_v1.media_upload(filename=str(path))
            media_ids.append(uploaded_media.media_id)
        request_data["media_ids"] = media_ids

    response = get_twitter_client().create_tweet(**request_data)
    if response.data is None:
        raise RuntimeError("X API から投稿結果が返されませんでした。")

    return response.data


def get_tweet_metrics(tweet_id: str) -> dict[str, int]:
    """指定した X 投稿の公開メトリクスを取得する。"""
    if not tweet_id.strip():
        raise ValueError("X 投稿 ID を空にすることはできません。")

    response = get_twitter_client().get_tweet(
        id=tweet_id,
        tweet_fields=["public_metrics"],
    )
    if response.data is None or response.data.public_metrics is None:
        return {}
    return dict(response.data.public_metrics)


if __name__ == "__main__":
    print("X API クライアントモジュールを読み込みました。")
    print("実際の投稿は Doppler 経由で post_tweet() を呼び出して実行します。")
