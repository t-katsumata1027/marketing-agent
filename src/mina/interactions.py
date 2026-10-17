"""Discordの署名と許可リストを検証する承認受付。"""

import json
import os
from dataclasses import dataclass
from datetime import datetime

from .domain import instant
from .store import Store


@dataclass(frozen=True)
class ApprovalPolicy:
    public_key: str
    application_id: str
    guild_id: str
    channel_id: str
    user_ids: frozenset[str]

    def __post_init__(self):
        if not all((self.public_key, self.application_id, self.guild_id, self.channel_id, self.user_ids)):
            raise ValueError("Discord承認の許可リストが必要です")
        if len(bytes.fromhex(self.public_key)) != 32:
            raise ValueError("Discord公開鍵が不正です")


def handle(body: bytes, signature: str, timestamp: str, policy: ApprovalPolicy,
           store: Store, now: datetime) -> tuple[int, dict]:
    """原文バイト列のEd25519署名を確認してから承認を記録する。"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if len(body) > 65536:
        return 413, {"error": "request_too_large"}
    try:
        if abs(instant(now).timestamp() - int(timestamp)) > 300:
            raise ValueError("期限切れ署名です")
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(policy.public_key))
        key.verify(bytes.fromhex(signature), timestamp.encode("ascii") + body)
    except (ValueError, InvalidSignature, UnicodeError):
        return 401, {"error": "invalid_signature"}
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return 400, {"error": "invalid_payload"}
    return _handle_verified(payload, policy, store, now)


def handle_gateway(payload: dict, created_at: datetime, policy: ApprovalPolicy,
                   store: Store, now: datetime) -> tuple[int, dict]:
    """認証済みSDK接続からだけ呼ぶ。HTTPから署名を省略して呼ばない。"""
    if abs((instant(now) - instant(created_at)).total_seconds()) > 300:
        return 403, {"error": "stale_interaction"}
    return _handle_verified(payload, policy, store, now)


def _handle_verified(payload, policy, store, now):
    """検証済みの受信経路に対して、共通の操作権限と候補の版を検査する。"""
    try:
        if not isinstance(payload, dict) or str(payload.get("application_id")) != policy.application_id:
            return 403, {"error": "wrong_application"}
        if payload.get("type") == 1:
            return 200, {"type": 1}
        if payload.get("type") != 3:
            return 400, {"error": "unsupported_interaction"}
        user = str(payload.get("member", {}).get("user", {}).get("id", ""))
        if str(payload.get("guild_id")) != policy.guild_id or str(payload.get("channel_id")) != policy.channel_id or user not in policy.user_ids:
            return 403, {"error": "actor_not_allowed"}
        custom_id = payload["data"]["custom_id"]
        if custom_id.startswith('minaig:'):
            if os.getenv('MINA_INSTAGRAM_APPROVAL_ENABLED', 'false').lower() != 'true':
                raise ValueError('Instagram承認受付は無効です')
            from .instagram_store import InstagramStore
            decision = InstagramStore(store.path).decide(custom_id, user, now)
        else:
            decision = store.decide(custom_id, user, now)
        messages = {"approved": "この版を承認しました。指定時刻に公開します。",
                    "changes_requested": "差戻しを記録しました。修正後に再承認が必要です。",
                    "cancelled": "この投稿を見送りました。"}
        return 200, {"type": 4, "data": {"content": messages[decision], "flags": 64,
                                         "allowed_mentions": {"parse": []}}}
    except (ValueError, KeyError, TypeError, AttributeError):
        return 200, {"type": 4, "data": {"content": "古い版・使用済み・期限切れの操作です。最新カードを確認してください。", "flags": 64}}
