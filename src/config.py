"""Doppler から注入される設定値を扱うモジュール。"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """実行時の環境変数から読み込む接続設定。"""

    # API キーは Doppler から実行時に注入する。
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    HELICONE_API_KEY: str = os.getenv("HELICONE_API_KEY", "")
    DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
    IMAGE_PROVIDER: str = os.getenv("IMAGE_PROVIDER", "mock")
    GENERATE_PROMOTIONAL_IMAGES: str = os.getenv(
        "GENERATE_PROMOTIONAL_IMAGES", "false"
    )
    NEXT_PUBLIC_SITE_URL: str = os.getenv("NEXT_PUBLIC_SITE_URL", "")
    X_API_KEY: str = os.getenv("X_API_KEY", "")
    X_API_SECRET: str = os.getenv("X_API_SECRET", "")
    X_ACCESS_TOKEN: str = os.getenv("X_ACCESS_TOKEN", "")
    X_ACCESS_TOKEN_SECRET: str = os.getenv("X_ACCESS_TOKEN_SECRET", "")
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    # 旧 SUPABASE_KEY は移行期間だけ読み取り互換として受け入れる。
    # service role をブラウザや公開環境変数へ渡してはいけない。
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv(
        "SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_KEY", "")
    )
    DISCORD_ALLOWED_GUILD_ID: str = os.getenv("DISCORD_ALLOWED_GUILD_ID", "")
    DISCORD_ALLOWED_CHANNEL_ID: str = os.getenv("DISCORD_ALLOWED_CHANNEL_ID", "")
    DISCORD_ALLOWED_ROLE_ID: str = os.getenv("DISCORD_ALLOWED_ROLE_ID", "")
    REPLY_PROCESS_LIMIT: int = int(os.getenv("REPLY_PROCESS_LIMIT", "5"))


config = Config()
