"""単一サブPCで検証するSQLite台帳。外部DBとは接続しない。"""

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from .domain import Candidate, JST, approval_time, date, instant


class Store:
    """トランザクションで候補の版、承認、送信状態を固定する。"""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS candidates (
                    target_date TEXT NOT NULL, slot TEXT NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, digest TEXT NOT NULL, scheduled REAL NOT NULL,
                    state TEXT NOT NULL, nonce_hash TEXT, actor TEXT, approved REAL,
                    dispatch_started REAL, x_id TEXT, reason TEXT,
                    PRIMARY KEY(target_date, slot));
                CREATE TABLE IF NOT EXISTS notices (
                    id INTEGER PRIMARY KEY, target_date TEXT NOT NULL, slot TEXT NOT NULL,
                    revision INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL,
                    UNIQUE(target_date, slot, revision, kind));
                CREATE TABLE IF NOT EXISTS preparation (
                    target_date TEXT PRIMARY KEY, state TEXT NOT NULL, started REAL NOT NULL);
            """)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, target_date: str, slot: str) -> dict | None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM candidates WHERE target_date=? AND slot=?", (target_date, slot)).fetchone()
            return dict(row) if row else None

    def list_day(self, target_date: str) -> list[dict]:
        with self.transaction() as db:
            return [dict(r) for r in db.execute("SELECT * FROM candidates WHERE target_date=? ORDER BY scheduled", (target_date,))]

    def save(self, candidate: Candidate, allowed_host: str, now: datetime):
        """差し替えで旧承認を失効し、送信開始後の変更を拒否する。"""
        return self.save_many([candidate], allowed_host, now)[0]

    def save_many(self, candidates, allowed_host: str, now: datetime, expected_digests=None):
        """全枠を一つのトランザクションで反映する。"""
        for candidate in candidates:
            candidate.validate(allowed_host, now)
        with self.transaction() as db:
            revisions = []
            for candidate in candidates:
                row = db.execute("SELECT * FROM candidates WHERE target_date=? AND slot=?", (candidate.target_date, candidate.slot)).fetchone()
                if expected_digests is not None and (not row or row['digest'] != expected_digests.get(candidate.slot) or
                        row['state'] not in ('changes_requested', 'approval_pending')):
                    raise ValueError('再点検中に候補または判断が変更されています')
                if row and row["digest"] == candidate.digest:
                    revisions.append(row["revision"])
                    continue
                if row and row["state"] in ("dispatching", "published", "outcome_unknown"):
                    raise ValueError("送信開始済みの投稿は差し替えできません")
                revision = row["revision"] + 1 if row else 1
                db.execute("""INSERT INTO candidates(target_date,slot,revision,payload,digest,scheduled,state)
                    VALUES(?,?,?,?,?,?,'approval_pending') ON CONFLICT(target_date,slot) DO UPDATE SET
                    revision=excluded.revision,payload=excluded.payload,digest=excluded.digest,
                    scheduled=excluded.scheduled,state='approval_pending',nonce_hash=NULL,actor=NULL,
                    approved=NULL,reason=NULL""", (candidate.target_date, candidate.slot, revision,
                    json.dumps(asdict(candidate), ensure_ascii=False, allow_nan=False), candidate.digest, candidate.scheduled_at.timestamp()))
                db.execute("UPDATE notices SET state='obsolete' WHERE target_date=? AND slot=? AND state='pending'", (candidate.target_date, candidate.slot))
                revisions.append(revision)
            return revisions

    def issue_cards(self, target_date: str, now: datetime) -> list[dict]:
        """20時までは通知を作らず、同じ版のカードを一度だけ作る。"""
        timestamp = instant(now).timestamp()
        if instant(now) < approval_time(date.fromisoformat(target_date)):
            return []
        issued = []
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM candidates WHERE target_date=? AND state='approval_pending' AND scheduled>?", (target_date, timestamp)).fetchall()
            for row in rows:
                if row["nonce_hash"]:
                    continue
                nonce = secrets.token_urlsafe(24)
                key = f"{target_date}|{row['slot']}|{row['revision']}|{nonce}"
                c = Candidate(**json.loads(row["payload"]))
                card = {"content": f"翌日投稿の承認依頼：{target_date} {c.slot}（JST {c.scheduled_at.astimezone(JST):%H:%M}）\n{c.content}",
                        "allowed_mentions": {"parse": []},
                        "embeds": [{"title": "品質確認済みの候補", "description": c.menu,
                                    "fields": [{"name": "栄養の目安", "value": json.dumps(c.nutrition, ensure_ascii=False)},
                                               {"name": "確認", "value": "API・二次レビュー・OGP：合格"}]}],
                        "components": [{"type": 1, "components": [
                            {"type": 2, "style": style, "label": label, "custom_id": f"{action}|{key}"}
                            for action, label, style in (("approve", "承認", 3), ("changes", "差戻し", 1), ("cancel", "見送り", 4))]}]}
                if c.approval_source and c.approval_source.get('ogp_image_url'):
                    card['embeds'][0]['image'] = {'url': c.approval_source['ogp_image_url']}
                    card['embeds'][0]['fields'].append({'name': '承認対象の版', 'value': c.digest[:16]})
                db.execute("UPDATE candidates SET nonce_hash=? WHERE target_date=? AND slot=?", (hashlib.sha256(nonce.encode()).hexdigest(), target_date, row["slot"]))
                db.execute("INSERT INTO notices(target_date,slot,revision,kind,payload,created) VALUES(?,?,?,'approval',?,?)", (target_date, row["slot"], row["revision"], json.dumps(card, ensure_ascii=False), timestamp))
                issued.append(card)
        return issued

    def decide(self, custom_id: str, actor: str, now: datetime):
        """許可済みの署名検証層からの判断だけを原子的に反映する。"""
        action, target, slot, revision, nonce = custom_id.split("|")
        states = {"approve": "approved", "changes": "changes_requested", "cancel": "cancelled"}
        if action not in states:
            raise ValueError("不正な判断です")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM candidates WHERE target_date=? AND slot=?", (target, slot)).fetchone()
            valid = row and row["state"] == "approval_pending" and row["revision"] == int(revision)
            if not valid or not secrets.compare_digest(row["nonce_hash"] or "", hashlib.sha256(nonce.encode()).hexdigest()):
                raise ValueError("使用済みまたは古い承認です")
            if row["scheduled"] <= instant(now).timestamp():
                raise ValueError("承認期限を過ぎています")
            candidate = Candidate(**json.loads(row["payload"]))
            if candidate.digest != row["digest"]:
                raise ValueError("承認対象が変更されています")
            db.execute("UPDATE candidates SET state=?,actor=?,approved=?,nonce_hash=NULL WHERE target_date=? AND slot=?", (states[action], actor, instant(now).timestamp(), target, slot))
            return states[action]

    def claim_due(self, now: datetime, grace_seconds: int = 120) -> list[dict]:
        """期限内の承認済み投稿を確保。送信不明は再確保しない。"""
        ts = instant(now).timestamp()
        with self.transaction() as db:
            db.execute("UPDATE candidates SET state='expired',nonce_hash=NULL WHERE state IN ('approval_pending','approved') AND scheduled<?", (ts - grace_seconds,))
            rows = db.execute("SELECT * FROM candidates WHERE state='approved' AND scheduled<=? AND scheduled>=? ORDER BY scheduled", (ts, ts - grace_seconds)).fetchall()
            for row in rows:
                db.execute("UPDATE candidates SET state='dispatching',dispatch_started=? WHERE target_date=? AND slot=?", (ts, row["target_date"], row["slot"]))
            return [dict(r) for r in rows]

    def finish(self, row: dict, x_id: str | None, now: datetime):
        """送信後は成功または成否不明へ固定し、結果通知を積む。"""
        state = "published" if x_id else "outcome_unknown"
        with self.transaction() as db:
            result = db.execute("UPDATE candidates SET state=?,x_id=? WHERE target_date=? AND slot=? AND revision=? AND state='dispatching'", (state, x_id, row["target_date"], row["slot"], row["revision"]))
            if result.rowcount != 1:
                raise ValueError("送信状態が一致しません")
            text = f"{row['target_date']} {row['slot']}：{'公開済み https://x.com/' + Candidate(**json.loads(row['payload'])).account + '/status/' + x_id if x_id else '公開結果不明。確認まで再送を停止しています。'}"
            db.execute("INSERT OR IGNORE INTO notices(target_date,slot,revision,kind,payload,created) VALUES(?,?,?,'result',?,?)", (row["target_date"], row["slot"], row["revision"], json.dumps({"content": text, "allowed_mentions": {"parse": []}}, ensure_ascii=False), instant(now).timestamp()))

    def claim_notice(self) -> dict | None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM notices WHERE state='pending' ORDER BY id LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE notices SET state='sending' WHERE id=?", (row["id"],))
            return dict(row) if row else None

    def finish_notice(self, notice_id: int, state: str):
        if state not in ("sent", "pending", "outcome_unknown"):
            raise ValueError("通知状態が不正です")
        with self.transaction() as db:
            db.execute("UPDATE notices SET state=? WHERE id=? AND state='sending'", (state, notice_id))

    def stop_before_publish(self, row, now):
        """Xへ送る前の照合失敗は、送信結果不明と区別して承認を失効する。"""
        with self.transaction() as db:
            changed = db.execute("UPDATE candidates SET state='changes_requested',actor=NULL,approved=NULL,nonce_hash=NULL,reason='preflight_failed' WHERE target_date=? AND slot=? AND revision=? AND state='dispatching'",
                                 (row['target_date'], row['slot'], row['revision']))
            if changed.rowcount != 1:
                raise ValueError('公開前停止の対象版が一致しません')
            payload = {'content': f"{row['target_date']} {row['slot']}：公開前の照合を完了できず停止しました。再確認・再レビュー後の候補を承認してください。", 'allowed_mentions': {'parse': []}}
            db.execute("INSERT OR IGNORE INTO notices(target_date,slot,revision,kind,payload,created) VALUES(?,?,?,'preflight',?,?)",
                       (row['target_date'], row['slot'], row['revision'], json.dumps(payload, ensure_ascii=False), instant(now).timestamp()))

    def queue_delay(self, target_date: str, now: datetime):
        """20時に不足する枠を一度だけ通知する。"""
        from .domain import SLOTS
        if instant(now) < approval_time(date.fromisoformat(target_date)):
            return
        present = {r["slot"] for r in self.list_day(target_date)}
        missing = sorted(set(SLOTS) - present)
        if not missing:
            return
        payload = {"content": f"翌日{target_date}の候補準備を保留しています。未完成枠：{'、'.join(missing)}。品質確認後に承認依頼を追加します。", "allowed_mentions": {"parse": []}}
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO notices(target_date,slot,revision,kind,payload,created) VALUES(?,'all',0,'delay',?,?)", (target_date, json.dumps(payload, ensure_ascii=False), instant(now).timestamp()))

    def claim_preparation(self, target_date: str, now: datetime) -> bool:
        """生成中に再起動しても自動再登録しない。再開は人が照合する。"""
        with self.transaction() as db:
            return db.execute("INSERT OR IGNORE INTO preparation VALUES(?,'started',?)", (target_date, instant(now).timestamp())).rowcount == 1

    def preparation_done(self, target_date: str):
        with self.transaction() as db:
            db.execute("UPDATE preparation SET state='complete' WHERE target_date=?", (target_date,))

    def hold_day(self, target_date, expected_digests):
        """再点検前に旧承認を失効する。送信開始済みや別版は変更しない。"""
        from .domain import SLOTS
        with self.transaction() as db:
            rows = list(db.execute('SELECT * FROM candidates WHERE target_date=?', (target_date,)))
            if len(rows) != 4 or set(expected_digests) != set(SLOTS) or any(
                    row['digest'] != expected_digests.get(row['slot']) or
                    row['state'] not in ('approved', 'approval_pending', 'changes_requested') for row in rows):
                raise ValueError('再点検対象の版・送信状態が不一致です')
            db.execute("UPDATE candidates SET state='changes_requested',actor=NULL,approved=NULL,nonce_hash=NULL,reason='replacement_review' WHERE target_date=?", (target_date,))
            db.execute("UPDATE notices SET state='obsolete' WHERE target_date=? AND state='pending' AND kind='approval'", (target_date,))
