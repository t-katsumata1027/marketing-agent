"""XとInstagramで同じBuffer API予算を原子的に管理する。"""

from datetime import UTC, datetime
import re

from .buffer_trial import ProbeError


def record_request(store, query, subject=None):
    operation = 'publish' if 'createPost(' in query else 'read'
    ts = datetime.now(UTC).timestamp()
    with store.transaction() as db:
        from .buffer_budget import check,PROBE,RESERVATION,REFRESH,REFRESH_TABLE,reservation,continuation_refresh_check
        grant=PROBE.get();budget=RESERVATION.get();probe=False
        if grant:
            from .buffer_trial import ORGANIZATIONS
            row=db.execute('SELECT * FROM buffer_quota_probes WHERE grant_id=?',(grant,)).fetchone()
            if query!=ORGANIZATIONS or not row or row['subject']!=subject or row['state']!='reserved' or row['deadline']<=ts:raise ProbeError('buffer_probe_unavailable')
            probe=True
        if budget:
            budget_table,row=reservation(db,budget)
            from .buffer_trial import ORGANIZATIONS,CHANNELS
            from .replacement_inventory import POSTS
            if query not in (ORGANIZATIONS,CHANNELS,POSTS) or not row or row['subject']!=subject or row['state']!='active' or row['deadline']<=ts or row['remaining']<=0:raise ProbeError('buffer_reservation_unavailable')
        refresh=REFRESH.get()
        if refresh:
            from .buffer_trial import ORGANIZATIONS
            refresh_table=REFRESH_TABLE.get()
            if refresh_table not in ('buffer_continuation_refresh','buffer_lunch_refresh','buffer_dinner_refresh'):raise ProbeError('buffer_refresh_scope_invalid')
            row=db.execute('SELECT * FROM '+refresh_table+' WHERE grant_id=?',(refresh,)).fetchone()
            if probe or budget or query!=ORGANIZATIONS or not row or row['subject']!=subject or row['state']!='reserved' or row['deadline']<=ts:raise ProbeError('buffer_refresh_unavailable')
            continuation_refresh_check(db,subject,ts)
        else:check(db,subject,ts,exclude=budget,probe=probe)
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='buffer_api_remaining'").fetchone():
            for row in db.execute('SELECT * FROM buffer_api_remaining WHERE expires>?', (ts,)):
                later = db.execute('SELECT count(*) FROM buffer_api_usage WHERE id>?', (row['checkpoint'],)).fetchone()[0]
                if row['remaining'] - later <= 5:
                    raise ProbeError('buffer_remote_quota_stop')
        key=db.execute('INSERT INTO buffer_api_usage(requested,operation) VALUES(?,?)', (ts, operation)).lastrowid
        if probe:db.execute("UPDATE buffer_quota_probes SET state='started',request_id=? WHERE grant_id=? AND state='reserved'",(key,grant))
        if budget:db.execute('UPDATE '+budget_table+' SET remaining=remaining-1 WHERE budget_id=?',(budget,))
        if refresh:db.execute('UPDATE '+refresh_table+" SET state='started',request_id=? WHERE grant_id=?",(key,refresh))
        return key


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
