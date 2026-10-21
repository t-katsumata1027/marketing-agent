"""保存済み受付IDだけを間隔を空けて照会する。投稿要求は作らない。"""

from datetime import UTC, datetime
import json

from .audit import OperationalFailure, safe_failure
from .domain import instant
from .instagram_domain import InstagramCandidate

BACKOFF = (30, 60, 120, 300, 900, 1800, 3600)
MAX_READS = 24


def reconcile_known(store, client=None, *, scheduled=False, clock=lambda: datetime.now(UTC), client_factory=None):
    from .instagram_publisher import GET, request_for, validate_post
    with store.transaction() as db:
        rows = db.execute('''SELECT c.*,a.post_id,a.digest AS attempt_digest,a.revision AS attempt_revision,
            a.request_json AS attempt_request,a.started AS attempted_at FROM instagram_candidates c
            JOIN instagram_attempts a USING(target_date,slot)
            WHERE c.state='outcome_unknown' AND a.state='outcome_unknown' AND a.post_id IS NOT NULL
            ORDER BY a.started''').fetchall()
        if not rows:
            return 0
        db.execute('''CREATE TABLE IF NOT EXISTS instagram_reconciliation (
            target_date TEXT NOT NULL,slot TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL,
            post_id TEXT NOT NULL,attempts INTEGER NOT NULL,next_at REAL NOT NULL,last_reason TEXT,
            PRIMARY KEY(target_date,slot,revision))''')
    confirmed = 0
    for value in rows:
        row = dict(value)
        key = (row['target_date'], row['slot'], row['revision'])
        ts = instant(clock()).timestamp()
        with store.transaction() as db:
            db.execute('INSERT OR IGNORE INTO instagram_reconciliation VALUES(?,?,?,?,?,0,?,NULL)',
                       (*key, row['digest'], row['post_id'], ts))
            progress = db.execute('SELECT * FROM instagram_reconciliation WHERE target_date=? AND slot=? AND revision=?', key).fetchone()
            if progress['digest'] != row['digest'] or progress['post_id'] != row['post_id']:
                if scheduled:
                    continue
                raise OperationalFailure('binding_changed', 'reconciliation')
            if scheduled and progress['attempts'] >= MAX_READS:
                payload = {'content': f"Instagram {row['target_date']} {row['slot']}：公開結果の自動照会では確認できませんでした。"
                           '再投稿せず保留しています。保存済み受付IDの手動照合が必要です。', 'allowed_mentions': {'parse': []}}
                db.execute("INSERT OR IGNORE INTO instagram_notices(target_date,slot,revision,kind,payload) VALUES(?,?,?,'reconciliation_exhausted',?)",
                           (*key, json.dumps(payload, ensure_ascii=False)))
                continue
            if scheduled and progress['next_at'] > ts:
                continue
            # 外部照会より先に次回時刻を保存し、再起動でも連続照会や期限リセットをしない。
            delay = BACKOFF[min(progress['attempts'], len(BACKOFF) - 1)]
            db.execute('UPDATE instagram_reconciliation SET attempts=attempts+1,next_at=? WHERE target_date=? AND slot=? AND revision=?',
                       (ts + delay, *key))
        try:
            candidate = InstagramCandidate(**json.loads(row['payload']))
            if (candidate.digest != row['digest'] or row['digest'] != row['attempt_digest'] or
                    row['revision'] != row['attempt_revision'] or json.loads(row['attempt_request']) != request_for(candidate)):
                raise OperationalFailure('binding_changed', 'reconciliation')
            if client is None:
                if client_factory is not None:
                    client = client_factory()
                else:
                    from .instagram_buffer_client import InstagramBufferClient
                    client = InstagramBufferClient(store)
            post = client._request(GET, {'input': {'id': row['post_id']}})['post']
            if not isinstance(post, dict) or post.get('id') != row['post_id']:
                raise OperationalFailure('binding_changed', 'reconciliation')
            url = validate_post(candidate, post)
            reason = 'outcome_unknown'
            if url:
                sent = instant(datetime.fromisoformat(post['sentAt'].replace('Z', '+00:00'))).timestamp()
                if not row['attempted_at'] - 5 <= sent <= instant(clock()).timestamp() + 5:
                    raise OperationalFailure('response_invalid', 'reconciliation')
                # 公開済みの事実は当初の待機期限を超えても確定する。新たな送信は行わない。
                store.mark(row, 'published', post, url)
                confirmed += 1
                reason = 'none'
        except Exception as error:
            reason, _ = safe_failure(error)
            if not scheduled:
                raise
        with store.transaction() as db:
            db.execute('UPDATE instagram_reconciliation SET last_reason=? WHERE target_date=? AND slot=? AND revision=?',
                       (reason, *key))
    return confirmed
