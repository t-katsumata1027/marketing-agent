"""Supabase の投稿状態、承認トークン、公開 lease を扱うモジュール。"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from supabase import Client, create_client

try:
    from src.config import config
except ModuleNotFoundError:
    from config import config  # type: ignore


def get_supabase_client() -> Client:
    """service_role 用クライアントを生成する。"""
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_ROLE_KEY:
        raise ValueError("SUPABASE_URL と SUPABASE_KEY を設定してください")
    return create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def log_post(project_name: str, content: str, platform: str = "X", status: str = "draft",
             in_reply_to_id: str | None = None, media_path: str | None = None) -> list[dict[str, Any]]:
    """下書きを保存する。"""
    if not project_name.strip() or not content.strip():
        raise ValueError("プロジェクト名と投稿本文は必須です")
    data: dict[str, Any] = {"project_name": project_name, "content": content,
                            "platform": platform, "status": status}
    if in_reply_to_id:
        data["in_reply_to_id"] = in_reply_to_id
    if media_path:
        data["media_path"] = media_path
    return get_supabase_client().table("posts_log").insert(data).execute().data


def create_approval_token(post_id: str | int, expires_in_hours: int = 24) -> str:
    """投稿単位・一回限りの承認トークンを発行し、平文は呼出元へ一度だけ返す。"""
    if expires_in_hours <= 0:
        raise ValueError("有効期限は 1 時間以上にしてください")
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = (datetime.now(UTC) + timedelta(hours=expires_in_hours)).isoformat()
    client = get_supabase_client()
    # 再発行時は既存の未使用トークンを無効化する。トークン平文をログへ出してはならない。
    client.table("post_approval_tokens").update({"revoked_at": datetime.now(UTC).isoformat(),
        "revoked_reason": "reissued"}).eq("post_id", post_id).is_("used_at", "null").is_("revoked_at", "null").execute()
    client.table("post_approval_tokens").insert({"post_id": post_id, "token_hash": token_hash,
        "expires_at": expires_at}).execute()
    return raw_token


def claim_posts_for_publish(limit: int = 10, lease_seconds: int = 300) -> list[dict[str, Any]]:
    """SQL 関数で approved から publishing へ原子的に移し、lease を返す。"""
    if limit <= 0 or lease_seconds <= 0:
        raise ValueError("limit と lease_seconds は正の値にしてください")
    return get_supabase_client().rpc("claim_posts_for_publish", {
        "p_limit": limit, "p_lease_seconds": lease_seconds}).execute().data or []


def mark_publish_dispatch_started(post_id: str | int, lease_token: str) -> dict[str, Any]:
    """X API 呼出し直前の送信開始記録を保存する。"""
    response = get_supabase_client().rpc("record_publish_dispatch_started", {
        "p_post_id": post_id, "p_lease_token": lease_token}).execute()
    if not response.data:
        raise RuntimeError("送信開始記録の保存に失敗しました")
    return response.data[0]


def mark_post_published(post_id: str | int, lease_token: str, x_tweet_id: str) -> dict[str, Any]:
    """同じ lease の送信成功を確定する。"""
    response = get_supabase_client().rpc("complete_published_post", {
        "p_post_id": post_id, "p_lease_token": lease_token, "p_x_tweet_id": x_tweet_id}).execute()
    if not response.data:
        raise RuntimeError("公開完了の保存に失敗しました")
    return response.data[0]


def mark_post_requires_review(post_id: str | int, lease_token: str, error: str) -> None:
    """投稿成否が不明な投稿を失敗として固定し、運用者確認待ちにする。"""
    get_supabase_client().rpc("fail_post_for_review", {"p_post_id": post_id,
        "p_lease_token": lease_token, "p_error": error[:2000]}).execute()


def recover_failed_post(post_id: str | int, operator_note: str,
                        confirmed_not_published: bool) -> dict[str, Any]:
    """運用者が X 未投稿を確認した failed 投稿だけを draft に戻す。"""
    if not operator_note.strip() or not confirmed_not_published:
        raise ValueError("復旧理由と X 未投稿の確認が必要です")
    response = get_supabase_client().rpc("recover_failed_post", {
        "p_post_id": post_id,
        "p_operator_note": operator_note[:2000],
        "p_confirmed_not_published": confirmed_not_published,
    }).execute()
    if not response.data:
        raise RuntimeError("投稿を安全に復旧できませんでした")
    return response.data[0]


def get_approved_posts() -> list[dict[str, Any]]:
    """互換性のため残す。公開ワーカーは使用してはいけない。"""
    return get_supabase_client().table("posts_log").select("*").eq("status", "approved").execute().data


def get_recent_published_posts(days: int = 7) -> list[dict[str, Any]]:
    """指定期間に公開済みとなった投稿を返す。"""
    if days <= 0:
        raise ValueError("days は 1 以上にしてください")
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    return get_supabase_client().table("posts_log").select("*").eq("status", "published").gte("published_at", since).execute().data


def update_post_metrics(post_id: str | int, metrics: dict[str, int]) -> dict[str, Any]:
    """X の公開メトリクスを既存投稿へ保存する。"""
    response = get_supabase_client().table("posts_log").update({"impressions": metrics.get("impression_count", 0), "likes": metrics.get("like_count", 0), "retweets": metrics.get("retweet_count", 0), "replies": metrics.get("reply_count", 0), "bookmarks": metrics.get("bookmark_count", 0), "metrics_updated_at": datetime.now(UTC).isoformat()}).eq("id", post_id).execute()
    if not response.data:
        raise ValueError("メトリクス更新対象の投稿がありません")
    return response.data[0]


def get_post(post_id: str | int) -> dict[str, Any]:
    """投稿を ID で取得する。"""
    response = get_supabase_client().table("posts_log").select("*").eq("id", post_id).execute()
    if not response.data:
        raise ValueError("投稿がありません")
    return response.data[0]


def update_draft_content(post_id: str | int, content: str) -> dict[str, Any]:
    """下書き状態の投稿本文だけを更新する。"""
    if not content.strip():
        raise ValueError("投稿本文は必須です")
    response = get_supabase_client().table("posts_log").update({"content": content}).eq("id", post_id).eq("status", "draft").execute()
    if not response.data:
        raise ValueError("下書き以外は更新できないか、投稿がありません")
    return response.data[0]


def log_mention(project_name: str, tweet_id: str, author_username: str, content: str) -> list[dict[str, Any]]:
    """検出したメンションを重複を防ぐ tweet_id とともに記録する。"""
    if not all(value.strip() for value in (project_name, tweet_id, author_username, content)):
        raise ValueError("メンションの必須項目が不足しています")
    response = get_supabase_client().table("mentions_log").upsert({"project_name": project_name, "tweet_id": tweet_id, "author_username": author_username, "content": content, "status": "unprocessed"}, on_conflict="project_name,tweet_id", ignore_duplicates=True).execute()
    return response.data or []


def claim_mentions_for_reply(project_name: str, limit: int = 5, lease_seconds: int = 300) -> list[dict[str, Any]]:
    """未処理メンションを原子的に processing として確保する。"""
    return get_supabase_client().rpc("claim_mentions_for_reply", {"p_project_name": project_name, "p_limit": limit, "p_lease_seconds": lease_seconds}).execute().data or []


def complete_mention(mention_id: str | int, lease_token: str) -> None:
    """同一leaseのメンションだけを処理済みにする。"""
    get_supabase_client().rpc("complete_mention_reply", {"p_mention_id": mention_id, "p_lease_token": lease_token}).execute()


def fail_mention(mention_id: str | int, lease_token: str, error: str) -> None:
    """失敗を安全に記録して再試行待ちへ戻す。"""
    get_supabase_client().rpc("fail_mention_reply", {"p_mention_id": mention_id, "p_lease_token": lease_token, "p_error": error[:2000]}).execute()


def claim_job(project_name: str, job_name: str, interval_seconds: int, lease_seconds: int = 900) -> str | None:
    """定期ジョブを確保し、所有者だけが持つ lease トークンを返す。"""
    response = get_supabase_client().rpc("claim_scheduled_job", {"p_project_name": project_name, "p_job_name": job_name, "p_interval_seconds": interval_seconds, "p_lease_seconds": lease_seconds}).execute()
    return str(response.data) if response.data else None


def finish_job(project_name: str, job_name: str, lease_token: str, success: bool, error: str | None = None) -> None:
    """同じ lease 所有者だけが定期ジョブの成功又は失敗を記録する。"""
    response = get_supabase_client().rpc("finish_scheduled_job", {"p_project_name": project_name, "p_job_name": job_name, "p_lease_token": lease_token, "p_success": success, "p_error": (error or "")[:2000]}).execute()
    if response.data is not True:
        raise RuntimeError("定期ジョブ lease が失効又は他ワーカーへ移管済みです")


def get_unprocessed_mentions(project_name: str, limit: int = 5) -> list[dict[str, Any]]:
    """未処理メンションを取得する。"""
    if not project_name.strip() or limit <= 0:
        raise ValueError("プロジェクト名と正の limit が必要です")
    return get_supabase_client().table("mentions_log").select("*").eq("project_name", project_name).eq("status", "unprocessed").limit(limit).execute().data


def update_mention_status(mention_id: str | int, status: str) -> list[dict[str, Any]]:
    """メンションの処理状態を更新する。"""
    if not status.strip():
        raise ValueError("状態は必須です")
    return get_supabase_client().table("mentions_log").update({"status": status}).eq("id", mention_id).execute().data
