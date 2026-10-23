"""XとInstagramで同じBuffer API予算を原子的に管理する。"""

from datetime import UTC, datetime
import re

from .buffer_trial import ProbeError


def record_request(store, query):
    operation = 'publish' if 'createPost(' in query else 'read'
    ts = datetime.now(UTC).timestamp()
    with store.transaction() as db:
        for seconds, limit in ((900, 90), (86400, 240), (2592000, 2900)):
            if db.execute('SELECT count(*) FROM buffer_api_usage WHERE requested>=?', (ts - seconds,)).fetchone()[0] >= limit:
                raise ProbeError('buffer_local_quota_stop')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='buffer_api_remaining'").fetchone():
            for row in db.execute('SELECT * FROM buffer_api_remaining WHERE expires>?', (ts,)):
                later = db.execute('SELECT count(*) FROM buffer_api_usage WHERE id>?', (row['checkpoint'],)).fetchone()[0]
                if row['remaining'] - later <= 5:
                    raise ProbeError('buffer_remote_quota_stop')
        db.execute('INSERT INTO buffer_api_usage(requested,operation) VALUES(?,?)', (ts, operation))


def record_headers(store, headers):
    """秘密のpartition keyを保存せず、残量と期限だけを残す。"""
    values = headers.get_all('RateLimit', [])
    policies = headers.get_all('RateLimit-Policy', [])
    found = {}
    for line in policies:
        for match in re.finditer(r'"([^"]+)"\s*;\s*q=(\d+)\s*;\s*w=(\d+)', line):
            found[match[1]] = int(match[3])
    ts = datetime.now(UTC).timestamp()
    with store.transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS buffer_api_remaining
            (window INTEGER PRIMARY KEY,remaining INTEGER NOT NULL,expires REAL NOT NULL,checkpoint INTEGER NOT NULL)''')
        checkpoint = db.execute('SELECT COALESCE(max(id),0) FROM buffer_api_usage').fetchone()[0]
        for line in values:
            for match in re.finditer(r'"([^"]+)"\s*;\s*r=(\d+)\s*;\s*t=(\d+)', line):
                window = found.get(match[1])
                if window in (900, 86400, 2592000):
                    db.execute('INSERT OR REPLACE INTO buffer_api_remaining VALUES(?,?,?,?)',
                               (window, int(match[2]), ts + int(match[3]), checkpoint))
