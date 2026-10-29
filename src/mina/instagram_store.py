"""Instagramだけの承認・公開台帳。Xの候補や承認行を変更しない。"""

from contextlib import contextmanager,nullcontext
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3

from .domain import JST, instant
from .instagram_domain import InstagramCandidate
from .publication_hold import held, require_clear
from .replacement_bundle import notice_allowed


class InstagramStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mina_recovery_guard'").fetchone():
                raise ValueError('復元後の外部照合が必要です')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS instagram_candidates (
                    target_date TEXT NOT NULL, slot TEXT NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, digest TEXT NOT NULL, scheduled REAL NOT NULL,
                    state TEXT NOT NULL, actor TEXT, approved REAL, nonce_hash TEXT,
                    dispatch_started REAL, reason TEXT, PRIMARY KEY(target_date,slot));
                CREATE TABLE IF NOT EXISTS instagram_attempts (
                    target_date TEXT NOT NULL, slot TEXT NOT NULL, revision INTEGER NOT NULL,
                    digest TEXT NOT NULL, state TEXT NOT NULL, request_json TEXT NOT NULL,
                    started REAL NOT NULL, post_id TEXT, response_json TEXT, external_url TEXT,
                    PRIMARY KEY(target_date,slot));
                CREATE TABLE IF NOT EXISTS instagram_notices (
                    id INTEGER PRIMARY KEY, target_date TEXT NOT NULL, slot TEXT NOT NULL,
                    revision INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', UNIQUE(target_date,slot,revision,kind));
                CREATE TABLE IF NOT EXISTS buffer_api_usage (
                    id INTEGER PRIMARY KEY, requested REAL NOT NULL, operation TEXT NOT NULL);
            ''')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def save(self, candidate, now, hosts, connection=None):
        if 'recovery' in candidate.source:
            raise ValueError('復旧候補は未送信の旧版照合が必要です')
        candidate.validate(now, hosts)
        with (self.transaction() if connection is None else nullcontext(connection)) as db:
            require_clear(db, candidate.target_date)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='publication_exclusions'").fetchone() and db.execute('SELECT 1 FROM publication_exclusions WHERE target_date=? AND slot=?',(candidate.target_date,candidate.slot)).fetchone():raise ValueError('本人指定により公開しない枠です')
            previous = db.execute('SELECT * FROM instagram_candidates WHERE target_date=? AND slot=?',
                                  (candidate.target_date, candidate.slot)).fetchone()
            if previous and previous['digest'] == candidate.digest:
                return previous['revision']
            if db.execute('SELECT 1 FROM instagram_attempts WHERE target_date=? AND slot=?',
                          (candidate.target_date, candidate.slot)).fetchone():
                raise ValueError('送信開始済みの枠は新版でも送信できません')
            if previous and previous['state'] == 'dispatching':
                raise ValueError('公開処理中の候補は変更できません')
            revision = previous['revision'] + 1 if previous else 1
            db.execute('''INSERT INTO instagram_candidates
                (target_date,slot,revision,payload,digest,scheduled,state) VALUES(?,?,?,?,?,?,'approval_pending')
                ON CONFLICT(target_date,slot) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,
                digest=excluded.digest,scheduled=excluded.scheduled,state='approval_pending',actor=NULL,
                approved=NULL,nonce_hash=NULL,reason=NULL''',
                (candidate.target_date, candidate.slot, revision, json.dumps(asdict(candidate), ensure_ascii=False),
                 candidate.digest, candidate.scheduled_at.timestamp()))
            db.execute("UPDATE instagram_notices SET state='obsolete' WHERE target_date=? AND slot=? AND state='pending'",
                       (candidate.target_date, candidate.slot))
            return revision

    def save_many(self,candidates,now,hosts,guard=None):
        """4枠の全検査後に同じtransactionへ保存し、一部だけの承認候補を残さない。"""
        from .domain import SLOTS
        if len(candidates)!=4 or {c.slot for c in candidates}!=set(SLOTS) or len({c.target_date for c in candidates})!=1:raise ValueError('instagram_four_candidates_required')
        for c in candidates:c.validate(now,hosts)
        with self.transaction() as db:
            if guard:guard(db)
            for c in candidates:self.save(c,now,hosts,connection=db)
            if guard:guard(db)

    def rows(self, target=None):
        with self.transaction() as db:
            rows = db.execute('SELECT * FROM instagram_candidates' + (' WHERE target_date=?' if target else '')
                              + ' ORDER BY scheduled', (target,) if target else ()).fetchall()
        return [dict(r) for r in rows]

    def queue_preparation_hold(self, target):
        """候補がまだない準備失敗も、対象日ごとに一度だけ通知する。"""
        # 例外原文には認証情報や外部応答が含まれ得るため転記しない。
        payload = {'content': f'【Instagram画像準備：保留】{target}\n'
                              '食事データの確認・画像作成・保存の途中で停止しました。'
                              '承認用画像の配信は完了していません。処理台帳の確認が必要です。',
                   'allowed_mentions': {'parse': []}}
        with self.transaction() as db:
            db.execute('''INSERT OR IGNORE INTO instagram_notices
                (target_date,slot,revision,kind,payload) VALUES(?,'prepare',1,'preparation_held',?)''',
                       (target, json.dumps(payload, ensure_ascii=False)))

    def queue_approval(self, row):
        """承認ボタンはInstagram専用の一度だけ使える乱数に結び付ける。"""
        token = secrets.token_urlsafe(24)
        hashed = hashlib.sha256(token.encode()).hexdigest()
        c = InstagramCandidate(**json.loads(row['payload']))
        types = 'ストーリーズ' if c.format == 'story' else 'カルーセル'
        timing = f'予定 {c.scheduled_at.astimezone(JST).isoformat()}'
        if 'recovery' in c.source:
            from .instagram_recovery import publication_window
            _, until = publication_window(self, row, c)
            timing = f'【復旧投稿】承認後に投稿します。受付期限 {until.astimezone(JST).isoformat()}'
        from .scoped_preparation import KEY,approval_notice
        if KEY in c.source:timing=f'提案時刻 JST {c.scheduled_at.astimezone(JST).isoformat()}'
        payload = {'content': f'【Instagram／{types}】{c.target_date} {c.slot}\n'
                              f'{timing}\n{c.caption}',
                   'embeds': [{'image': {'url': a['url']}} for a in c.assets],
                   'allowed_mentions': {'parse': []},
                   'components': [{'type': 1, 'components': [
                       {'type': 2, 'style': style, 'label': label, 'custom_id': 'minaig:' + action + ':' + token}
                       for action, label, style in (('approve', 'Instagramを承認', 3), ('reject', '差戻し', 2), ('skip', '見送り', 4))]}]}
        approval_notice(payload,c.source)
        with self.transaction() as db:
            actual = db.execute('SELECT * FROM instagram_candidates WHERE target_date=? AND slot=?',
                                (c.target_date, c.slot)).fetchone()
            from .replacement_bundle import approval_allowed
            if not actual or actual['digest'] != c.digest or actual['state'] != 'approval_pending' or actual['revision']!=row['revision']:
                raise ValueError('承認候補が変わっています')
            approval_allowed(db,c.target_date,'instagram',c.slot,actual['revision'],c.digest)
            if db.execute("SELECT 1 FROM instagram_notices WHERE target_date=? AND slot=? AND revision=? AND kind='approval'",
                          (c.target_date, c.slot, row['revision'])).fetchone():
                return
            db.execute('UPDATE instagram_candidates SET nonce_hash=? WHERE target_date=? AND slot=?',
                       (hashed, c.target_date, c.slot))
            db.execute("INSERT INTO instagram_notices(target_date,slot,revision,kind,payload) VALUES(?,?,?,'approval',?)",
                       (c.target_date, c.slot, row['revision'], json.dumps(payload, ensure_ascii=False)))

    def decide(self, custom_id, actor, now):
        parts = custom_id.split(':')
        if len(parts) != 3 or parts[0] != 'minaig' or parts[1] not in ('approve', 'reject', 'skip'):
            raise ValueError('Instagramの操作ではありません')
        hashed = hashlib.sha256(parts[2].encode()).hexdigest()
        decision = {'approve': 'approved', 'reject': 'changes_requested', 'skip': 'cancelled'}[parts[1]]
        # 読取照合を別接続で行い、書込transaction内では同じ行かを再確認する。
        with self.transaction() as db:
            checked = db.execute('SELECT * FROM instagram_candidates WHERE nonce_hash=?', (hashed,)).fetchone()
        if checked:
            from .instagram_recovery import publication_window
            publication_window(self, dict(checked), InstagramCandidate(**json.loads(checked['payload'])))
        with self.transaction() as db:
            row = db.execute('SELECT * FROM instagram_candidates WHERE nonce_hash=?', (hashed,)).fetchone()
            if (not row or not checked or dict(row) != dict(checked) or
                    row['state'] != 'approval_pending' or row['scheduled'] <= instant(now).timestamp()):
                raise ValueError('古い版・使用済み・期限切れです')
            from .replacement_bundle import approval_allowed
            approval_allowed(db,row['target_date'],'instagram',row['slot'],row['revision'],row['digest'])
            if db.execute('SELECT 1 FROM instagram_attempts WHERE target_date=? AND slot=?',
                          (row['target_date'], row['slot'])).fetchone():
                raise ValueError('送信開始済みです')
            recovery = 'recovery' in json.loads(row['payload'])['source']
            scheduled = instant(now).timestamp() if recovery and decision == 'approved' else row['scheduled']
            db.execute('UPDATE instagram_candidates SET state=?,actor=?,approved=?,scheduled=?,nonce_hash=NULL WHERE target_date=? AND slot=?',
                       (decision, actor, instant(now).timestamp(), scheduled, row['target_date'], row['slot']))
            from .replacement_bundle import maybe_release
            maybe_release(db,row['target_date'],instant(now))
        return decision

    def claim_due(self, now):
        ts = instant(now).timestamp()
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM instagram_attempts WHERE state IN ('attempting','accepted','outcome_unknown')").fetchone():
                return None
            if db.execute("SELECT 1 FROM instagram_candidates WHERE state IN ('dispatching','accepted','outcome_unknown')").fetchone():
                return None
            db.execute("UPDATE instagram_candidates SET state='expired',nonce_hash=NULL WHERE state IN ('approved','approval_pending') AND scheduled<?", (ts - 120,))
            rows = db.execute("SELECT * FROM instagram_candidates WHERE state='approved' AND scheduled<=? AND scheduled>=? ORDER BY scheduled", (ts, ts - 120)).fetchall()
            from .scoped_preparation import blocked
            row = next((r for r in rows if not held(db, r['target_date']) and not blocked(json.loads(r['payload']))), None)
            if not row:
                return None
            c = InstagramCandidate(**json.loads(row['payload']))
            if c.digest != row['digest'] or not row['actor'] or row['approved'] is None:
                raise ValueError('承認の版が一致しません')
            db.execute("UPDATE instagram_candidates SET state='dispatching',dispatch_started=? WHERE target_date=? AND slot=? AND state='approved'", (ts, row['target_date'], row['slot']))
            return dict(row)

    def begin(self, row, request, now):
        with self.transaction() as db:
            require_clear(db, row['target_date'])
            current = db.execute('SELECT * FROM instagram_candidates WHERE target_date=? AND slot=?',
                                 (row['target_date'], row['slot'])).fetchone()
            if (not current or current['state'] != 'dispatching' or current['digest'] != row['digest'] or
                    current['revision'] != row['revision'] or current['scheduled'] != row['scheduled'] or
                    current['actor'] != row['actor'] or current['approved'] != row['approved']):
                raise ValueError('送信枠を所有していません')
            db.execute("INSERT INTO instagram_attempts(target_date,slot,revision,digest,state,request_json,started) VALUES(?,?,?,?,'attempting',?,?)",
                       (row['target_date'], row['slot'], row['revision'], row['digest'], json.dumps(request, ensure_ascii=False), instant(now).timestamp()))

    def mark(self, row, state, post=None, external_url=None, failure=None):
        if state not in ('accepted', 'published', 'outcome_unknown', 'held'):
            raise ValueError('公開状態が不正です')
        with self.transaction() as db:
            current = db.execute('SELECT state,digest,revision FROM instagram_candidates WHERE target_date=? AND slot=?',
                                 (row['target_date'], row['slot'])).fetchone()
            allowed = {'accepted': ('dispatching',), 'published': ('accepted', 'outcome_unknown'),
                       'outcome_unknown': ('dispatching', 'accepted'), 'held': ('dispatching',)}
            if (not current or current['digest'] != row['digest'] or current['revision'] != row['revision']
                    or current['state'] not in allowed[state]):
                return
            db.execute('UPDATE instagram_candidates SET state=?,reason=? WHERE target_date=? AND slot=? AND digest=?',
                       (state, None if state == 'published' else state, row['target_date'], row['slot'], row['digest']))
            db.execute('UPDATE instagram_attempts SET state=?,post_id=COALESCE(?,post_id),response_json=COALESCE(?,response_json),external_url=? WHERE target_date=? AND slot=? AND digest=?',
                       (state, post.get('id') if post else None, json.dumps(post, ensure_ascii=False) if post else None,
                        external_url, row['target_date'], row['slot'], row['digest']))
            if state == 'published':
                message = ('Instagramの公開を確認できました。先ほどの確認待ち・成否不明を解消しました。' if current['state'] == 'outcome_unknown'
                           else 'Instagramへ公開しました。')
                payload = {'content': message + '\n' + external_url, 'allowed_mentions': {'parse': []}}
                db.execute("UPDATE instagram_notices SET state='obsolete' WHERE target_date=? AND slot=? AND revision=? AND state='pending' AND kind IN ('outcome_unknown','reconciliation_exhausted')",
                           (row['target_date'], row['slot'], row['revision']))
                db.execute("INSERT OR IGNORE INTO instagram_notices(target_date,slot,revision,kind,payload) VALUES(?,?,?,'published',?)", (row['target_date'], row['slot'], row['revision'], json.dumps(payload, ensure_ascii=False)))
            elif state in ('held', 'outcome_unknown'):
                message = ('公開前の検査で保留しました。' if state == 'held' else
                           '送信の成否を確認できないため停止しました。自動再送せず照合が必要です。')
                if state == 'outcome_unknown':
                    attempt = db.execute('SELECT post_id FROM instagram_attempts WHERE target_date=? AND slot=?',
                                         (row['target_date'], row['slot'])).fetchone()
                    if attempt and attempt['post_id']:
                        message = '公開完了は確認待ちです。再投稿せず、保存済み受付IDでしばらく自動照会を続けます。'
                payload = {'content': f"Instagram {row['target_date']} {row['slot']}：{message}",
                           'allowed_mentions': {'parse': []}}
                if failure:
                    # 安全化した分類だけを残し、外部応答や例外原文は通知へ出さない。
                    payload['content'] += f"\n停止工程：{failure['stage']}／分類：{failure['reason']}"
                    db.execute('UPDATE instagram_candidates SET reason=? WHERE target_date=? AND slot=?',
                               (json.dumps(failure, ensure_ascii=False), row['target_date'], row['slot']))
                db.execute('INSERT OR IGNORE INTO instagram_notices(target_date,slot,revision,kind,payload) VALUES(?,?,?,?,?)',
                           (row['target_date'], row['slot'], row['revision'], state, json.dumps(payload, ensure_ascii=False)))

    def send_notices(self, client):
        from .routine_handoff import maintain_notices,transmit
        maintain_notices(self,'instagram',client)
        from .replacement_bundle import notice_allowed
        sent = 0
        while True:
            with self.transaction() as db:
                rows = db.execute("SELECT * FROM instagram_notices WHERE state='pending' ORDER BY id").fetchall()
                row = next((r for r in rows if r['kind'] != 'approval' or notice_allowed(db,r,'instagram')), None)
                if not row:
                    break
                db.execute("UPDATE instagram_notices SET state='sending' WHERE id=?", (row['id'],))
            try:
                transmit(self,row,'instagram',client)
            except Exception:
                with self.transaction() as db:
                    db.execute("UPDATE instagram_notices SET state='outcome_unknown' WHERE id=?", (row['id'],))
                break
            with self.transaction() as db:
                db.execute("UPDATE instagram_notices SET state='sent' WHERE id=?", (row['id'],))
            sent += 1
        return sent

    def hold_interrupted(self):
        """再起動後に送信開始状態を自動再送しない。"""
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM instagram_candidates WHERE state IN ('dispatching','accepted')").fetchall()
            db.execute("UPDATE instagram_notices SET state='outcome_unknown' WHERE state='sending'")
        for row in rows:
            self.mark(dict(row), 'outcome_unknown')
