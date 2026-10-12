"""毎回の実履歴と公開台帳を照合し、日次準備の確認資料を自動作成する。"""

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from .artifact_backup import checked_path
from .domain import ACCOUNT, Candidate, JST, instant
from .encrypted_backup import save_new
from .readiness import check_evidence
from .x_history import reconcile_timeline


def build_automatic_history(database, snapshot, bootstrap, cutover, now):
    """移行前の確認済み履歴と、新運用の公開台帳から対応を確定する。"""
    target = snapshot['target_date']
    day = date.fromisoformat(target)
    first = day - timedelta(days=14)
    if cutover.get('account') != ACCOUNT or cutover.get('old_reservations_zero') is not True or cutover.get('old_generation_stopped') is not True or not isinstance(cutover.get('confirmed_by'), str) or not cutover['confirmed_by'].strip():
        raise ValueError('旧予約・旧生成停止の切替確認が必要です')
    confirmed = instant(datetime.fromisoformat(cutover['confirmed_at']))
    if confirmed > instant(now):
        raise ValueError('切替確認の時刻が不正です')
    if bootstrap.get('account') != ACCOUNT or bootstrap.get('reviewed') is not True or not isinstance(bootstrap.get('reviewer'), str) or not bootstrap['reviewer'].strip() or not isinstance(bootstrap.get('entries'), list):
        raise ValueError('移行前の履歴確認が必要です')
    records = {}
    def add(row):
        key, recorded = row['x_id'], date.fromisoformat(row['date'])
        if not isinstance(key, str) or not key.isdigit() or not all(isinstance(row.get(name), str) and row[name].strip() for name in ('menu', 'comment', 'publish_text')):
            raise ValueError('公開履歴の対応が不正です')
        if first <= recorded < day:
            if key in records and records[key] != row:
                raise ValueError('公開履歴の対応が競合しています')
            records[key] = row
    for row in bootstrap['entries']:
        add(row)
    source = checked_path(database)
    if not source.is_file():
        raise ValueError('公開台帳がありません')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute("SELECT COUNT(*) FROM candidates WHERE state IN ('dispatching','outcome_unknown') AND target_date BETWEEN ? AND ?", (first.isoformat(), (day-timedelta(days=1)).isoformat())).fetchone()[0]:
            raise ValueError('公開成否の照合が必要です')
        rows = db.execute("SELECT target_date,slot,payload,digest,x_id FROM candidates WHERE state='published' AND target_date BETWEEN ? AND ?", (first.isoformat(), (day-timedelta(days=1)).isoformat())).fetchall()
        for recorded, slot, payload, digest, key in rows:
            candidate = Candidate(**json.loads(payload))
            if candidate.account != ACCOUNT or candidate.target_date != recorded or candidate.slot != slot or candidate.digest != digest:
                raise ValueError('公開台帳が変更されています')
            add({'x_id': key, 'date': recorded, 'menu': candidate.menu,
                 'comment': candidate.comment, 'publish_text': candidate.content})
    comparison = reconcile_timeline(snapshot, list(records.values()))
    if not comparison['timeline_matches_ledger']:
        raise ValueError('外部投稿・消失・本文変更の確認が必要です')
    for post in snapshot['posts']:
        if instant(datetime.fromisoformat(post['created_at'])).astimezone(JST).date().isoformat() != records[post['id']]['date']:
            raise ValueError('公開台帳と実投稿の日付が不一致です')
    evidence = {'account': ACCOUNT, 'target_date': target, 'checked_at': instant(now).isoformat(),
                'reviewer': 'Marketing Agent履歴照合', 'history_source': '公式X API全ページと公開台帳・移行前確認履歴の照合',
                'schedule_source': '旧予約ゼロ・旧生成停止の切替確認',
                'history_complete': True, 'legacy_schedule_clear': True,
                'history_from': first.isoformat(), 'history_to': (day-timedelta(days=1)).isoformat(),
                'entries': [{k: row[k] for k in ('date','menu','comment','x_id')} for row in records.values()],
                'timeline_sha256': hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                'cutover_sha256': hashlib.sha256(json.dumps(cutover, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    check_evidence(evidence, target, now)
    return evidence


def refresh_history(database, target, output, bootstrap, cutover, client, clock):
    """取得後の時刻で期限を記録する。異常時は候補の生成へ進めない。"""
    before = instant(clock())
    snapshot = client.read_history(ACCOUNT, target)
    after = instant(clock())
    if snapshot.get('target_date') != target:
        raise ValueError('履歴取得の対象日が不一致です')
    if not timedelta(0) <= after - before <= timedelta(minutes=2):
        raise ValueError('履歴取得の時間が不正です')
    evidence = build_automatic_history(database, snapshot, bootstrap, cutover, after)
    save_new(output, json.dumps(evidence, ensure_ascii=False, indent=2).encode())
    return Path(output)
