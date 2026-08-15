"""Discord へ下書きと安全な承認確認 URL を通知するモジュール。"""

import json
from pathlib import Path
from urllib.parse import urlencode, urlparse
import requests
try:
    from src.logging_utils import get_logger, log_event
except ModuleNotFoundError:
    from logging_utils import get_logger, log_event  # type: ignore


logger = get_logger(__name__)


def _validate_webhook_url(url: str) -> None:
    """誤設定で任意の HTTP 宛先に投稿本文を送らない。"""
    parsed = urlparse(url)
    allowed_hosts = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise ValueError("DISCORD_WEBHOOK_URL は Discord の HTTPS webhook URL にしてください")


def _raise_for_retry(response: requests.Response) -> requests.Response:
    """429/5xx を retry_call が分類できる HTTPError に変換する。"""
    response.raise_for_status()
    return response

try:
    from src.config import config
    from src.supabase_client import create_approval_token
except ModuleNotFoundError:
    from config import config  # type: ignore
    from supabase_client import create_approval_token  # type: ignore
try:
    from src.retry import retry_call
except ModuleNotFoundError:
    from retry import retry_call  # type: ignore


def _build_approval_url(post_id: str | int, raw_token: str) -> str:
    """状態を変えない確認画面 URL を作る。トークンは URL 以外へ記録しない。"""
    base_url = config.NEXT_PUBLIC_SITE_URL.rstrip("/")
    if not base_url:
        raise ValueError("NEXT_PUBLIC_SITE_URL を設定してください")
    return f"{base_url}/api/approve?{urlencode({'id': str(post_id), 'token': raw_token})}"


def send_discord_notification(project_name: str, topic: str, content: str, post_id: str | int,
                              media_path: str | None = None) -> bool:
    """一回限りの承認 URL を含む通知を送る。"""
    if not config.DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL が未設定のため通知をスキップします")
        return False
    _validate_webhook_url(config.DISCORD_WEBHOOK_URL)
    approval_url = _build_approval_url(post_id, create_approval_token(post_id))
    message = f"**[{project_name}] 新しい投稿案**\n**テーマ:** {topic}\n```text\n{content}\n```\n確認・承認: {approval_url}\n[ID: {post_id}]"
    media_file = Path(media_path) if media_path else None
    if media_file and media_file.is_file():
        with media_file.open("rb") as image_file:
            response = retry_call(lambda: _raise_for_retry(requests.post(config.DISCORD_WEBHOOK_URL, data={"payload_json": json.dumps({"content": message, "allowed_mentions": {"parse": []}})}, files={"files[0]": (media_file.name, image_file, "image/png")}, timeout=30)))
    else:
        response = retry_call(lambda: _raise_for_retry(requests.post(config.DISCORD_WEBHOOK_URL, json={"content": message, "allowed_mentions": {"parse": []}}, timeout=10)))
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        # 応答本文には token や個人情報が含まれ得るのでログに残さない。
        log_event(logger, "discord_webhook_failed", post_id=post_id,
                  status_code=getattr(response, "status_code", None))
        raise RuntimeError("Discord Webhook への通知に失敗しました") from error
    log_event(logger, "discord_webhook_sent", post_id=post_id, project_name=project_name)
    print(f"投稿 ID {post_id} の承認通知を送信しました")
    return True
