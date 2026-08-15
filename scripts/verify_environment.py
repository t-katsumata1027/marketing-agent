"""外部接続せずに、起動前の必須設定と構成を検証する。"""

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUIRED = (
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "NEXT_PUBLIC_SITE_URL",
    "DISCORD_WEBHOOK_URL", "DISCORD_BOT_TOKEN", "X_API_KEY", "X_API_SECRET",
    "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET", "ANTHROPIC_API_KEY",
    "DISCORD_ALLOWED_GUILD_ID", "DISCORD_ALLOWED_CHANNEL_ID", "DISCORD_ALLOWED_ROLE_ID",
)


def main() -> int:
    """秘密値を表示せず、設定漏れと危険な本番設定を報告する。"""
    expected_python = (3, 12)
    if sys.version_info[:2] != expected_python:
        print("Python 3.12 が必要です", file=sys.stderr)
        return 1
    missing = [name for name in REQUIRED if not os.getenv(name, "").strip()]
    if missing:
        print("必須環境変数が未設定です: " + ", ".join(missing), file=sys.stderr)
        return 1
    if os.getenv("GENERATE_PROMOTIONAL_IMAGES", "false").lower() in {"1", "true", "yes"} and os.getenv("IMAGE_PROVIDER", "mock") == "mock":
        print("本番画像生成で IMAGE_PROVIDER=mock は利用できません", file=sys.stderr)
        return 1
    if not any((ROOT / "projects").glob("*.yaml")):
        print("projects 配下にプロジェクト設定がありません", file=sys.stderr)
        return 1
    print("起動前設定の検証に成功しました（秘密値は表示しません）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
