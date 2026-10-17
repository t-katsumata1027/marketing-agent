"""単一サブPCで検証するSQLite台帳。外部DBとは接続しない。"""

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from .domain import Candidate, JST, LABELS, approval_time, date, instant
from .publication_hold import held, require_clear


class Store:
    """トランザクションで候補の版、承認、送信状態を固定する。"""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mina_recovery_guard'").fetchone():
                raise ValueError('復旧台帳は外部履歴照合と承認失効の確認が必要です。稼働開始を停止します')
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
                CREATE TABLE IF NOT EXISTS cycle_health (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), checked REAL NOT NULL,
                    last_success REAL, consecutive_failures INTEGER NOT NULL, mode TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS publication_exclusions (
                    target_date TEXT NOT NULL, slot TEXT NOT NULL, reason TEXT NOT NULL,
                    PRIMARY KEY(target_date,slot));
                CREATE TABLE IF NOT EXISTS publication_exceptions (
                    target_date TEXT NOT NULL, slot TEXT NOT NULL, until_at TEXT NOT NULL,
                    reason TEXT NOT NULL, PRIMARY KEY(target_date,slot));
                CREATE TABLE IF NOT EXISTS preparation_progress (
                    target_date TEXT PRIMARY KEY, step TEXT NOT NULL, status TEXT NOT NULL,
                    reasons TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS buffer_publication (
                    singleton INTEGER PRIMARY KEY, target_date TEXT NOT NULL,
                    slot TEXT NOT NULL, revision INTEGER NOT NULL,
                    digest TEXT NOT NULL, channel_id TEXT NOT NULL, service_id TEXT NOT NULL,
                    state TEXT NOT NULL, post_id TEXT, request_json TEXT, response_json TEXT,
                    attempted REAL, proof_json TEXT, UNIQUE(target_date,slot));
                CREATE TABLE IF NOT EXISTS buffer_policy (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), date_from TEXT NOT NULL,
                    review_until TEXT NOT NULL, channel_id TEXT NOT NULL, service_id TEXT NOT NULL,
                    state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS buffer_api_usage (
                    id INTEGER PRIMARY KEY, requested REAL NOT NULL, operation TEXT NOT NULL);
            """)
            from .audit import SCHEMA, AuditLog
            db.executescript(SCHEMA)
            if 'invocation' not in {row[1] for row in db.execute('PRAGMA table_info(operation_events)')}:
                db.execute("ALTER TABLE operation_events ADD COLUMN invocation TEXT NOT NULL DEFAULT ''")
            # 朝食一件限定だった旧表を、実公開の行をそのまま保って複数枠へ拡張する。
            db.execute('BEGIN IMMEDIATE')
            schema = db.execute("SELECT sql FROM sqlite_master WHERE name='buffer_publication'").fetchone()[0]
            if 'CHECK(singleton=1)' in schema.replace(' ', ''):
                db.execute('ALTER TABLE buffer_publication RENAME TO buffer_publication_single_legacy')
                db.execute('''CREATE TABLE buffer_publication (
                    singleton INTEGER PRIMARY KEY, target_date TEXT NOT NULL, slot TEXT NOT NULL,
                    revision INTEGER NOT NULL, digest TEXT NOT NULL, channel_id TEXT NOT NULL,
                    service_id TEXT NOT NULL, state TEXT NOT NULL, post_id TEXT, request_json TEXT,
                    response_json TEXT, attempted REAL, proof_json TEXT, UNIQUE(target_date,slot))''')
                db.execute('INSERT INTO buffer_publication SELECT * FROM buffer_publication_single_legacy')
                db.execute('DROP TABLE buffer_publication_single_legacy')
        self.audit = AuditLog(self)

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
                require_clear(db, candidate.target_date)
                until = (candidate.approval_source or {}).get('publish_after_approval_until')
                if until:
                    approval = db.execute('SELECT until_at FROM publication_exceptions WHERE target_date=? AND slot=?',
                                          (candidate.target_date,candidate.slot)).fetchone()
                    if not approval or approval['until_at'] != until:
                        raise ValueError('当日食事の臨時公開に本人の指定記録が必要です')
                if db.execute('SELECT 1 FROM publication_exclusions WHERE target_date=? AND slot=?',
                              (candidate.target_date, candidate.slot)).fetchone():
                    raise ValueError('本人指定により公開しない枠です')
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

    def issue_cards(self, target_date: str, now: datetime, slots=None) -> list[dict]:
        """20時までは通知を作らず、同じ版のカードを一度だけ作る。"""
        timestamp = instant(now).timestamp()
        if instant(now) < approval_time(date.fromisoformat(target_date)):
            return []
        issued = []
        with self.transaction() as db:
            require_clear(db, target_date)
            rows = db.execute("""SELECT * FROM candidates WHERE target_date=? AND state='approval_pending' AND scheduled>?
                ORDER BY CASE slot WHEN 'breakfast' THEN 1 WHEN 'lunch' THEN 2 WHEN 'dinner' THEN 3 ELSE 4 END""", (target_date, timestamp)).fetchall()
            for row in rows:
                if slots is not None and row['slot'] not in slots:
                    continue
                if row["nonce_hash"]:
                    continue
                nonce = secrets.token_urlsafe(24)
                key = f"{target_date}|{row['slot']}|{row['revision']}|{nonce}"
                c = Candidate(**json.loads(row["payload"]))
                timing = ('本人指定：本日の' + LABELS[c.slot] + 'は承認後に臨時公開（受付期限 JST '
                          + c.scheduled_at.astimezone(JST).strftime('%H:%M') + '）') if (c.approval_source or {}).get('publish_after_approval_until') else f'JST {c.scheduled_at.astimezone(JST):%H:%M}'
                card = {"content": f"━━━━━━━━━━━━━━━━━━━━\n**{LABELS[c.slot]}｜{target_date}**\n投稿予定：{timing}\n━━━━━━━━━━━━━━━━━━━━\n\n{c.content}",
                        "allowed_mentions": {"parse": []},
                        "embeds": [{"title": f"{LABELS[c.slot]}｜品質確認済みの候補", "description": c.menu,
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

    def requeue_failed_dinner(self, candidate, allowed_host, now, previous_digest, round_key):
        """送信前停止の夕食だけを新承認版へ戻し、旧候補と未送信経路を保全する。"""
        from .clients import MealClient
        MealClient.request_key(round_key)
        candidate.validate(allowed_host, now)
        until = (candidate.approval_source or {}).get('publish_after_approval_until')
        if candidate.slot != 'dinner' or not until:
            raise ValueError('当日夕食の明示的な再承認だけを扱います')
        with self.transaction() as db:
            require_clear(db, candidate.target_date)
            old = db.execute("SELECT * FROM candidates WHERE target_date=? AND slot='dinner'", (candidate.target_date,)).fetchone()
            route = db.execute("SELECT * FROM buffer_publication WHERE target_date=? AND slot='dinner'", (candidate.target_date,)).fetchone()
            if (not old or old['state'] != 'changes_requested' or old['reason'] != 'preflight_failed' or
                    old['digest'] != previous_digest or old['x_id'] is not None or not route or
                    route['state'] != 'armed' or route['revision'] != old['revision'] or route['digest'] != old['digest'] or
                    any(route[name] is not None for name in ('attempted','post_id','request_json','response_json','proof_json'))):
                raise ValueError('夕食の未送信を確認できません。自動再公開しません')
            original = Candidate(**json.loads(old['payload']))
            old_data, new_data = asdict(original), asdict(candidate)
            for value in (old_data, new_data):
                value.pop('source_commit')
                binding = value['approval_source']
                for name in ('review_round','readiness_sha256','publish_after_approval_until'):
                    binding.pop(name, None)
            if old_data != new_data or original.digest != old['digest']:
                raise ValueError('再承認する夕食の本文・原本・正本が変更されています')
            db.execute('''CREATE TABLE IF NOT EXISTS publication_recoveries (
                round_key TEXT PRIMARY KEY, target_date TEXT NOT NULL, slot TEXT NOT NULL,
                previous_candidate TEXT NOT NULL, previous_route TEXT NOT NULL, created REAL NOT NULL)''')
            db.execute('INSERT INTO publication_recoveries VALUES(?,?,?,?,?,?)',
                (round_key, candidate.target_date, 'dinner', json.dumps(dict(old),ensure_ascii=False),
                 json.dumps(dict(route),ensure_ascii=False), instant(now).timestamp()))
            db.execute("INSERT INTO publication_exceptions VALUES(?,'dinner',?,'本人指示：公開前停止した本日夕食を再承認後に公開')",
                       (candidate.target_date, until))
            revision = old['revision'] + 1
            db.execute("""UPDATE candidates SET revision=?,payload=?,digest=?,scheduled=?,state='approval_pending',
                nonce_hash=NULL,actor=NULL,approved=NULL,dispatch_started=NULL,x_id=NULL,reason=NULL
                WHERE target_date=? AND slot='dinner'""",
                (revision,json.dumps(asdict(candidate),ensure_ascii=False),candidate.digest,
                 candidate.scheduled_at.timestamp(),candidate.target_date))
            db.execute("UPDATE buffer_publication SET revision=?,digest=? WHERE target_date=? AND slot='dinner'",
                       (revision,candidate.digest,candidate.target_date))
            db.execute("UPDATE notices SET state='obsolete' WHERE target_date=? AND slot='dinner' AND state='pending'",
                       (candidate.target_date,))
        return revision

    def decide(self, custom_id: str, actor: str, now: datetime):
        """許可済みの署名検証層からの判断だけを原子的に反映する。"""
        action, target, slot, revision, nonce = custom_id.split("|")
        states = {"approve": "approved", "changes": "changes_requested", "cancel": "cancelled"}
        if action not in states:
            raise ValueError("不正な判断です")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM candidates WHERE target_date=? AND slot=?", (target, slot)).fetchone()
            require_clear(db, target)
            valid = row and row["state"] == "approval_pending" and row["revision"] == int(revision)
            if not valid or not secrets.compare_digest(row["nonce_hash"] or "", hashlib.sha256(nonce.encode()).hexdigest()):
                raise ValueError("使用済みまたは古い承認です")
            if row["scheduled"] <= instant(now).timestamp():
                raise ValueError("承認期限を過ぎています")
            candidate = Candidate(**json.loads(row["payload"]))
            if candidate.digest != row["digest"]:
                raise ValueError("承認対象が変更されています")
            db.execute("UPDATE candidates SET state=?,actor=?,approved=?,nonce_hash=NULL WHERE target_date=? AND slot=?", (states[action], actor, instant(now).timestamp(), target, slot))
            if action == 'approve' and (candidate.approval_source or {}).get('publish_after_approval_until'):
                until = candidate.approval_source['publish_after_approval_until']
                record = db.execute('SELECT until_at FROM publication_exceptions WHERE target_date=? AND slot=?',(target,slot)).fetchone()
                if not record or record['until_at'] != until:
                    raise ValueError('臨時公開の指定記録が不一致です')
                db.execute('UPDATE candidates SET scheduled=? WHERE target_date=? AND slot=?',(instant(now).timestamp(),target,slot))
            return states[action]

    def claim_due(self, now: datetime, grace_seconds: int = 120) -> list[dict]:
        """期限内の承認済み投稿を確保。送信不明は再確保しない。"""
        ts = instant(now).timestamp()
        with self.transaction() as db:
            self.expire_due(db,now,grace_seconds)
            rows = db.execute("""SELECT * FROM candidates c WHERE state='approved' AND scheduled<=? AND scheduled>=?
                AND NOT EXISTS(SELECT 1 FROM publication_retries r WHERE r.target_date=c.target_date AND r.slot=c.slot AND r.revision=c.revision AND r.next_at>?) ORDER BY scheduled""", (ts, ts - grace_seconds,ts)).fetchall()
            rows = [row for row in rows if not held(db, row['target_date'])]
            for row in rows:
                db.execute("UPDATE candidates SET state='dispatching',dispatch_started=? WHERE target_date=? AND slot=?", (ts, row["target_date"], row["slot"]))
                self.audit.emit('publication',row['target_date'],row['slot'],row['revision'],'preparation','started',
                    details={'before':'approved','after':'dispatching'},now=now,db=db)
            return [dict(r) for r in rows]

    def has_due(self, now: datetime, grace_seconds: int = 120) -> bool:
        """期限超過を整理し、公開枠がない巡回でX認証照会をしない。確保はしない。"""
        ts = instant(now).timestamp()
        with self.transaction() as db:
            self.expire_due(db,now,grace_seconds)
            rows = db.execute("""SELECT target_date FROM candidates c WHERE state='approved' AND scheduled BETWEEN ? AND ?
                AND NOT EXISTS(SELECT 1 FROM publication_retries r WHERE r.target_date=c.target_date AND r.slot=c.slot AND r.revision=c.revision AND r.next_at>?)""",
                              (ts-grace_seconds, ts,ts)).fetchall()
            return any(not held(db, row['target_date']) for row in rows)

    def expire_due(self,db,now,grace_seconds):
        rows=db.execute("SELECT * FROM candidates WHERE state IN ('approval_pending','approved') AND scheduled<?",(instant(now).timestamp()-grace_seconds,)).fetchall()
        for row in rows:
            db.execute("UPDATE candidates SET state='expired',nonce_hash=NULL WHERE target_date=? AND slot=?",(row['target_date'],row['slot']))
            self.audit.emit('publication',row['target_date'],row['slot'],row['revision'],'freshness','stopped','deadline_exceeded',
                details={'matched':row['state']=='approved','after':'expired','write_started':False},now=now,db=db)
            if row['state']=='approved':
                payload={'content':f'{row["target_date"]} {row["slot"]}：公開期限を超過したため送信せず停止しました。','allowed_mentions':{'parse':[]}}
                db.execute("INSERT OR IGNORE INTO notices(target_date,slot,revision,kind,payload,created) VALUES(?,?,?,'expiry',?,?)",
                    (row['target_date'],row['slot'],row['revision'],json.dumps(payload,ensure_ascii=False),now.timestamp()))

    def record_cycle_health(self, success: bool, now: datetime, mode='failed'):
        """例外原文・本文・認証を保存せず、再起動後も連続失敗を検知する。"""
        if type(success) is not bool or mode not in ('dry_run', 'live', 'failed') or (success == (mode == 'failed')):
            raise ValueError('巡回状態の分類が不正です')
        ts = instant(now).timestamp()
        with self.transaction() as db:
            previous = db.execute('SELECT * FROM cycle_health WHERE singleton=1').fetchone()
            last = ts if success else (previous['last_success'] if previous else None)
            failures = 0 if success else min(1000000, (previous['consecutive_failures'] if previous else 0)+1)
            db.execute('INSERT INTO cycle_health VALUES(1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET checked=excluded.checked,last_success=excluded.last_success,consecutive_failures=excluded.consecutive_failures,mode=excluded.mode',
                       (ts, last, failures, mode))

    def business_health(self,now):
        """現在の作成停止・投稿停止を、過去の保全履歴と巡回の生存から分離する。"""
        today=instant(now).astimezone(JST).date().isoformat()
        with self.transaction() as db:
            creation=db.execute("SELECT count(*) FROM preparation WHERE target_date>=? AND state LIKE 'held:%'",(today,)).fetchone()[0]
            publication=db.execute("SELECT count(*) FROM candidates WHERE target_date=? AND (state IN ('outcome_unknown','expired') OR (state='changes_requested' AND reason='preflight_failed'))",(today,)).fetchone()[0]
            retry=db.execute("SELECT count(*) FROM publication_retries r JOIN candidates c ON c.target_date=r.target_date AND c.slot=r.slot AND c.revision=r.revision WHERE c.target_date=? AND c.state='approved' AND r.attempts>0",(today,)).fetchone()[0]
        return {'healthy':not(creation or publication or retry),'creation_holds':creation,'publication_holds':publication,'retry_pending':retry}

    def finish(self, row: dict, x_id: str | None, now: datetime):
        """送信後は成功または成否不明へ固定し、結果通知を積む。"""
        state = "published" if x_id else "outcome_unknown"
        with self.transaction() as db:
            result = db.execute("UPDATE candidates SET state=?,x_id=? WHERE target_date=? AND slot=? AND revision=? AND state='dispatching'", (state, x_id, row["target_date"], row["slot"], row["revision"]))
            if result.rowcount != 1:
                raise ValueError("送信状態が一致しません")
            self.audit.emit('publication',row['target_date'],row['slot'],row['revision'],'delivery',
                'completed' if x_id else 'failed','none' if x_id else 'outcome_unknown',
                {'before':'dispatching','after':state,'write_started':True},now=now,db=db)
            text = f"{row['target_date']} {row['slot']}：{'公開済み https://x.com/' + Candidate(**json.loads(row['payload'])).account + '/status/' + x_id if x_id else '公開結果不明。確認まで再送を停止しています。'}"
            db.execute("INSERT OR IGNORE INTO notices(target_date,slot,revision,kind,payload,created) VALUES(?,?,?,'result',?,?)", (row["target_date"], row["slot"], row["revision"], json.dumps({"content": text, "allowed_mentions": {"parse": []}}, ensure_ascii=False), instant(now).timestamp()))

    def claim_notice(self) -> dict | None:
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM notices WHERE state='pending' ORDER BY id").fetchall()
            row = next((r for r in rows if r['kind'] != 'approval' or not held(db, r['target_date'])), None)
            if row:
                db.execute("UPDATE notices SET state='sending' WHERE id=?", (row["id"],))
            return dict(row) if row else None

    def finish_notice(self, notice_id: int, state: str):
        if state not in ("sent", "pending", "outcome_unknown"):
            raise ValueError("通知状態が不正です")
        with self.transaction() as db:
            db.execute("UPDATE notices SET state=? WHERE id=? AND state='sending'", (state, notice_id))

    def stop_before_publish(self, row, now, stage='source', reason='unclassified', details=None):
        """Xへ送る前の照合失敗は、送信結果不明と区別して承認を失効する。"""
        with self.transaction() as db:
            changed = db.execute("UPDATE candidates SET state='changes_requested',actor=NULL,approved=NULL,nonce_hash=NULL,reason='preflight_failed' WHERE target_date=? AND slot=? AND revision=? AND state='dispatching'",
                                 (row['target_date'], row['slot'], row['revision']))
            if changed.rowcount != 1:
                raise ValueError('公開前停止の対象版が一致しません')
            self.audit.emit('publication',row['target_date'],row['slot'],row['revision'],stage,'stopped',reason,
                {**(details or {}),'before':'dispatching','after':'changes_requested','write_started':False},now=now,db=db)
            labels={'route':'公開経路','connection':'Buffer接続照会','candidate':'承認内容','source':'正本・共有画像','freshness':'時計・公開期限','recovery':'再起動後の照合'}
            payload = {'content': f"{row['target_date']} {row['slot']}：公開前の照合を完了できず停止しました。停止工程：{labels.get(stage,'公開前確認')}。理由分類：{reason}。再確認・再レビュー後の候補を承認してください。", 'allowed_mentions': {'parse': []}}
            db.execute("INSERT OR IGNORE INTO notices(target_date,slot,revision,kind,payload,created) VALUES(?,?,?,'preflight',?,?)",
                       (row['target_date'], row['slot'], row['revision'], json.dumps(payload, ensure_ascii=False), instant(now).timestamp()))

    def queue_delay(self, target_date: str, now: datetime):
        """20時に不足する枠を一度だけ通知する。"""
        from .domain import SLOTS
        if instant(now) < approval_time(date.fromisoformat(target_date)):
            return
        present = {r["slot"] for r in self.list_day(target_date)}
        with self.transaction() as db:
            excluded = {r[0] for r in db.execute('SELECT slot FROM publication_exclusions WHERE target_date=?', (target_date,))}
        missing = [LABELS[slot] for slot in SLOTS if slot not in present and slot not in excluded]
        if not missing:
            return
        with self.transaction() as db:
            progress=db.execute('SELECT * FROM preparation_progress WHERE target_date=?',(target_date,)).fetchone()
            prep=db.execute('SELECT state FROM preparation WHERE target_date=?',(target_date,)).fetchone()
        if progress and progress['status'] in ('quality_hold','reconciliation_required','stopped'):
            self.queue_preparation_hold(target_date,now)
            return
        from .preparation_status import describe_step
        if progress and prep and prep['state'] in ('started','resuming:registration','read_recovery_started'):
            label={'correcting':'訂正・再点検中','reviewing':'点検中'}.get(progress['status'],'処理中')
            fresh=0 <= instant(now).timestamp()-progress['updated'] <= 180
            message=(f'翌日{target_date}の候補は{label}です。最終進捗：{describe_step(progress["step"])}。'
                     if fresh else f'翌日{target_date}の準備は進捗更新の確認が必要です。最終進捗：{describe_step(progress["step"])}。')
            message+=f'未完成枠：{"、".join(missing)}。完了・合格した場合に承認依頼へ進みます。停止後の自動再開は行いません。'
        else:
            message=f'翌日{target_date}の候補準備は確認・対応が必要です。未完成枠：{"、".join(missing)}。自動再開しません。実行記録を照合してください。'
        payload = {"content": message, "allowed_mentions": {"parse": []}}
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO notices(target_date,slot,revision,kind,payload,created) VALUES(?,'all',0,'delay',?,?)", (target_date, json.dumps(payload, ensure_ascii=False), instant(now).timestamp()))

    def record_preparation_progress(self,target,step,status,reasons=(),now=None):
        """要求台帳を変更せず、通知用の工程状態だけを記録する。"""
        from datetime import UTC
        from .preparation_status import REASONS
        if status not in ('running','reviewing','correcting','quality_hold','reconciliation_required','stopped','complete'):
            raise ValueError('準備状態の分類が不正です')
        safe_reasons=[code for code in reasons if code in REASONS or code in ('deadline_exceeded','unconfirmed')]
        with self.transaction() as db:
            db.execute('INSERT INTO preparation_progress VALUES(?,?,?,?,?) ON CONFLICT(target_date) DO UPDATE SET step=excluded.step,status=excluded.status,reasons=excluded.reasons,updated=excluded.updated',
                (target,step,status,json.dumps(safe_reasons),instant(now or datetime.now(UTC)).timestamp()))

    def record_preparation_failure(self,target,stage,error,now):
        """品質保留は理由を残し、その他の未確認工程は安全側で停止して即時通知する。"""
        from .preparation_status import QualityHold,PreparationDeadline
        from .audit import safe_failure,step_context
        with self.transaction() as db:
            progress=db.execute('SELECT step FROM preparation_progress WHERE target_date=?',(target,)).fetchone()
        # 以前の工程が完了した後の例外を、その以前の工程へ誤って結び付けない。
        parents={'daily_draft':'draft','image':'images','image_review':'images','automatic_image_repair':'images',
                 'drive_upload':'drive','drive_verify':'drive','source_state':'registration','source_request':'registration',
                 'meal_register':'registration','meal_request_key':'registration','meal_review':'registration',
                 'correction_plan':'registration','correction_key':'registration','meal_correction':'registration',
                 'ogp_capture':'registration','ogp_review':'registration','daily_share':'registration','daily_request_key':'registration'}
        previous=progress['step'] if progress else stage
        prefix=previous.split(':')[0]
        step=previous if prefix==stage or parents.get(prefix)==stage else stage
        reason,details=safe_failure(error)
        if isinstance(error,QualityHold):
            step,status,reasons=error.step,'quality_hold',error.reason_codes
        elif isinstance(error,PreparationDeadline):
            status,reasons='stopped',('deadline_exceeded',)
        else:
            from .clients import OutcomeUnknown
            status='stopped' if not isinstance(error,OutcomeUnknown) and step.split(':')[0] in ('source_state','source_request') else 'reconciliation_required'
            from .preparation_status import REASONS
            reasons=(reason,) if reason in REASONS else ('unconfirmed',)
        event_stage,slot=step_context(step)
        # ラッパー例外へ置き換える前に、元の分類・位置・時計差を追記する。
        self.audit.emit('creation',target,slot,None,event_stage,'held' if isinstance(error,QualityHold) else 'stopped',reason,details,now=now)
        self.record_preparation_progress(target,step,status,reasons,now)
        self.queue_preparation_hold(target,now)

    def queue_preparation_hold(self,target,now):
        """同じ停止は一度だけ通知し、本文・資格情報・UUIDを含めない。"""
        from .preparation_status import REASONS,describe_step
        with self.transaction() as db:
            row=db.execute('SELECT * FROM preparation_progress WHERE target_date=?',(target,)).fetchone()
            if not row or row['status'] not in ('quality_hold','reconciliation_required','stopped'):
                return
            reasons=json.loads(row['reasons'])
            label={'quality_hold':'品質点検で停止','reconciliation_required':'成否・正本の照合が必要','stopped':'準備を停止'}[row['status']]
            explanation='、'.join(REASONS.get(code,{'deadline_exceeded':'準備期限を超過','unconfirmed':'工程の完了を確認できない'}.get(code,'確認が必要')) for code in reasons) or '品質の確認を完了できない'
            message=f'{target}の候補準備：{label}。対象：{describe_step(row["step"])}。理由：{explanation}。自動再開しません。保存済み記録を再登録せず、照合・対応が必要です。'
            revision=int(hashlib.sha256(json.dumps([row['step'],row['status'],reasons]).encode()).hexdigest()[:8],16)
            db.execute("INSERT OR IGNORE INTO notices(target_date,slot,revision,kind,payload,created) VALUES(?,'all',?,'preparation_hold',?,?)",
                (target,revision,json.dumps({'content':message,'allowed_mentions':{'parse':[]}},ensure_ascii=False),instant(now).timestamp()))

    def queue_history_hold(self, target_date: str, now: datetime):
        """履歴の不明点をまとめ、秘密値を含めず一度だけ通知待ちへ保存する。"""
        if date.fromisoformat(target_date).isoformat() != target_date:
            raise ValueError('対象日が不正です')
        payload = {'content': f'翌日{target_date}の準備を履歴確認で保留しました。X履歴の取得、台帳にない投稿、本文変更・消失、送信成否不明のいずれかを確認する必要があります。確認が済むまで生成・登録・公開へ進みません。',
                   'allowed_mentions': {'parse': []}}
        with self.transaction() as db:
            # 既存の未送信候補がある場合も、履歴不明のまま旧承認で公開しない。
            db.execute("UPDATE candidates SET state='changes_requested',actor=NULL,approved=NULL,nonce_hash=NULL,reason='history_hold' WHERE target_date=? AND state IN ('approved','approval_pending')", (target_date,))
            db.execute("UPDATE notices SET state='obsolete' WHERE target_date=? AND state='pending' AND kind='approval'", (target_date,))
            db.execute("INSERT OR IGNORE INTO notices(target_date,slot,revision,kind,payload,created) VALUES(?,'all',0,'history_hold',?,?)",
                       (target_date, json.dumps(payload, ensure_ascii=False), instant(now).timestamp()))

    def claim_preparation(self, target_date: str, now: datetime) -> bool:
        """生成中に再起動しても自動再登録しない。再開は人が照合する。"""
        with self.transaction() as db:
            return db.execute("INSERT OR IGNORE INTO preparation VALUES(?,'started',?)", (target_date, instant(now).timestamp())).rowcount == 1

    def preparation_done(self, target_date: str):
        with self.transaction() as db:
            db.execute("UPDATE preparation SET state='complete' WHERE target_date=?", (target_date,))
            db.execute("UPDATE preparation_progress SET status='complete' WHERE target_date=?",(target_date,))

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
