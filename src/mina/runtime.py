"""日次候補の取り込み、20時通知、承認済み予約の公開。"""

import json
import math
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .domain import ACCOUNT, Candidate, JST, SLOTS, instant
from .store import Store


def import_manifest(store: Store, path: Path, host: str, now: datetime, expected_digests=None, excluded_slots=()):
    """独立レビュー済み成果物を取り込み、元の版を保存する。"""
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if excluded_slots:
        if set(excluded_slots) != {'breakfast'}:
            raise ValueError('当日朝食以外の公開除外は許可されていません')
        today = instant(now).astimezone(JST).date().isoformat()
        with store.transaction() as db:
            if not db.execute("SELECT 1 FROM publication_exclusions WHERE target_date=? AND slot='breakfast'", (today,)).fetchone():
                raise ValueError('当日の朝食非公開指定が必要です')
    expected_slots = set(SLOTS) - set(excluded_slots)
    if not isinstance(payload, list) or len(payload) != len(expected_slots):
        raise ValueError("候補ファイルには翌日4枠が必要です")
    candidates = [Candidate(**entry) for entry in payload]
    if len({c.target_date for c in candidates}) != 1 or {c.slot for c in candidates} != expected_slots:
        raise ValueError("同じ日付の4枠が必要です")
    target = candidates[0].target_date
    if excluded_slots and target != today:
        raise ValueError('当日以外へ朝食除外を適用できません')
    history_start = instant(now) - timedelta(days=14)
    with store.transaction() as db:
        history = db.execute("SELECT payload FROM candidates WHERE target_date<>? AND scheduled>=? AND scheduled<?", (target, history_start.timestamp(), candidates[0].scheduled_at.timestamp())).fetchall()
    comments = [json.loads(r["payload"])["comment"] for r in history]
    weekly = [json.loads(r['payload'])['comment'] for r in history
              if datetime.fromisoformat(json.loads(r['payload'])['target_date']) >= datetime.fromisoformat(target) - timedelta(days=7)]
    for c in candidates:
        c.validate(host, now, comments, weekly)
        comments.append(c.comment)
        weekly.append(c.comment)
    # 全件を検証後に原子的に反映し、一部だけの差し替えを防ぐ。
    store.save_many(candidates, host, now, expected_digests=expected_digests)
    return target


def notify_day(store: Store, target: str, now: datetime):
    store.issue_cards(target, now)
    store.queue_delay(target, now)


def send_notifications(store: Store, client):
    """通知成否不明を保留し、同じカードを自動重複送信しない。"""
    count = 0
    while (notice := store.claim_notice()) is not None:
        try:
            client.send(json.loads(notice["payload"]))
        except Exception:
            store.finish_notice(notice["id"], "outcome_unknown")
            break
        store.finish_notice(notice["id"], "sent")
        count += 1
    return count


def publish_due(store: Store, client, now: datetime, host: str, preflight, clock=lambda: datetime.now(UTC), timer=time.monotonic):
    """公開先・OGPを照合後、排他的に一度だけ送信する。"""
    if not store.has_due(now):
        return 0
    from .buffer_publisher import requires_x_verification
    if requires_x_verification(store, now):
        client.verify_account(ACCOUNT)
    count = 0
    for row in store.claim_due(now):
        try:
            from .buffer_publisher import publication_client
            publisher = publication_client(store, row, client)
            if publisher is not client:
                publisher.verify_account(ACCOUNT)
            c = Candidate(**json.loads(row["payload"]))
            if c.digest != row["digest"]:
                raise ValueError("成果物ハッシュが一致しません")
            # 到達時刻の検証済み候補を、予定時刻直前として品質再検査する。
            immediate = bool((c.approval_source or {}).get('publish_after_approval_until'))
            c.validate(host, clock() if immediate else c.scheduled_at - timedelta(seconds=1))
            if immediate and row['scheduled'] != row['approved']:
                raise ValueError('臨時公開の起点が本人承認時刻と一致しません')
            proof = preflight(c)
            sending_at = instant(clock())
            due_at = datetime.fromtimestamp(row['scheduled'], UTC) if immediate else c.scheduled_at
            if not 0 <= (sending_at - due_at).total_seconds() <= 120 or (immediate and sending_at >= c.scheduled_at):
                raise ValueError('送信直前に公開猶予を超えています')
            if c.approval_source is not None:
                if not isinstance(proof, dict):
                    raise ValueError('公開直前の照合時刻証跡が必要です')
                sending_monotonic = timer()
                start, observed = proof['started_monotonic'], proof['observed_monotonic']
                if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                       for value in (start, observed, sending_monotonic)):
                    raise ValueError('公開直前の単調時計証跡が不正です')
                if not start <= observed <= sending_monotonic or sending_monotonic - start > 120:
                    raise ValueError('最初のGET開始から送信直前まで120秒を超えています')
                for field in ('started_at', 'checked_at', 'observed_at'):
                    age = (sending_at - instant(datetime.fromisoformat(proof[field]))).total_seconds()
                    if not 0 <= age <= 120:
                        raise ValueError('公開直前の照合証跡が古いか不正です')
                if instant(datetime.fromisoformat(proof['expires_at'])) <= sending_at:
                    raise ValueError('送信直前にOGPが失効しました')
            if publisher is not client:
                publisher.proof = proof
        except Exception:
            store.stop_before_publish(row, now)
            continue
        try:
            x_id = publisher.publish(c.content)
            if not isinstance(x_id, str) or not x_id.isdigit():
                raise ValueError("公開IDが不正です")
            store.finish(row, x_id, now)
            count += 1
        except Exception:
            store.finish(row, None, now)
    return count
