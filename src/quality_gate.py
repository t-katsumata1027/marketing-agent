"""投稿・返信を保存又は送信する前の保守的な品質ゲート。"""

import re
from urllib.parse import urlparse

URL = re.compile(r"https?://[^\s]+")
MENTION = re.compile(r"(?<!\w)@([A-Za-z0-9_]{1,15})")


def validate_content(content: str, policy: dict | None = None, reply_to_username: str | None = None, recent_contents: list[str] | None = None) -> list[str]:
    """違反理由を返す。URLは現在のX短縮URL長を23文字として見積もる。"""
    policy = policy or {}; errors: list[str] = []
    if not content or not content.strip(): errors.append("本文が空白です")
    normalized = URL.sub(lambda value: "x" * 23, content)
    if len(normalized) > int(policy.get("max_x_characters", 280)): errors.append("X文字数の上限を超えています")
    allowed = set(policy.get("allowed_domains", []))
    for value in URL.findall(content):
        if allowed and urlparse(value).hostname not in allowed: errors.append("許可されていないURLドメインです")
    if content.count("#") > int(policy.get("max_hashtags", 3)): errors.append("ハッシュタグが多すぎます")
    mentions = MENTION.findall(content)
    if len(mentions) > int(policy.get("max_mentions", 1)): errors.append("メンションが多すぎます")
    if reply_to_username and any(name.lower() != reply_to_username.lstrip("@").lower() for name in mentions): errors.append("返信先以外へのメンションがあります")
    if any(word.lower() in content.lower() for word in policy.get("banned_words", [])): errors.append("禁止語が含まれます")
    if any(content.strip() == item.strip() for item in recent_contents or []): errors.append("最近の投稿と同一です")
    return errors
