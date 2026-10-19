"""秘密値と投稿本文を表示しない、読み取り専用の運用診断。"""

import math
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from .domain import JST, SLOTS


SETTING_GROUPS = {
    "drive_storage": ("MINA_GOOGLE_CLIENT_ID", "MINA_GOOGLE_CLIENT_SECRET", "MINA_GOOGLE_REFRESH_TOKEN", "MINA_DRIVE_FOLDER_ID"),
    "discord_notification": ("DISCORD_BOT_TOKEN", "DISCORD_ALLOWED_CHANNEL_ID"),
    "discord_approval": ("MINA_DISCORD_APPLICATION_ID", "MINA_DISCORD_PUBLIC_KEY",
                         "DISCORD_ALLOWED_GUILD_ID", "DISCORD_ALLOWED_CHANNEL_ID", "MINA_APPROVER_USER_IDS"),
    "x_publish": ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET", "MINA_SHARE_HOST"),
    "logeats_registration": ("MINA_AGENT_API_TOKEN", "MINA_MEAL_API_BASE_URL"),
}
CANDIDATE_STATES = ("approval_pending", "approved", "changes_requested", "cancelled",
                    "dispatching", "published", "outcome_unknown", "expired")
NOTICE_STATES = ("pending", "sending", "sent", "outcome_unknown", "obsolete")
STOP_FLAGS = ('MINA_AI_ENABLED', 'MINA_LIVE_WRITES', 'MINA_PUBLISH_ENABLED',
              'MINA_APPROVAL_ENABLED', 'MINA_PREPARE_ENABLED', 'MINA_READ_REVIEW_ENABLED',
              'MINA_X_HISTORY_READ_ENABLED', 'MINA_HISTORY_AUTO_ENABLED')


def diagnose(database: str | Path, heartbeat: str | Path, now: datetime, env=None):
    """DBを新規作成せず、許可した集計項目だけを返す。"""
    env = os.environ if env is None else env
    groups = dict(SETTING_GROUPS)
    backend = env.get('MINA_AI_BACKEND', 'codex_cli')
    groups['ai_generation'] = ('MINA_CODEX_BIN', 'MINA_CODEX_HOME') if backend == 'codex_cli' else ('OPENAI_API_KEY',)
    target = (now.astimezone(JST).date() + timedelta(days=1)).isoformat()
    report = {
        "target_date": target,
        "switches": {key: env.get(key, "false").lower() == "true" for key in STOP_FLAGS},
        "connections": {name: {"configured": all(env.get(key, "").strip() for key in keys),
                               "missing": [key for key in keys if not env.get(key, "").strip()]}
                        for name, keys in groups.items()},
        'ai_backend': backend,
        "database": {"readable": False},
        "heartbeat": {"fresh": False},
        "cycle_health": {"measured": False, "healthy": False},
        # 設定済みだけでは日次運用の完成を意味しない。
        "daily_automation_complete": False,
        "remaining_pipeline": ["本番M1接続と全工程の実測", "履歴・旧予約の確認証跡",
                               "配布コードのcommit証跡", "Discord承認の実操作検証", "X API実公開と無人復帰"],
    }
    try:
        stamp = float(Path(heartbeat).read_text(encoding="ascii"))
        age = now.timestamp() - stamp
        if math.isfinite(age) and age >= 0:
            report["heartbeat"] = {"fresh": age <= 90, "age_seconds": round(age)}
    except (OSError, ValueError):
        pass
    try:
        # mode=roは不存在時もDBを作らず、権限不足や破損も診断結果に留める。
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            db.execute("PRAGMA query_only=ON")
            present = {row[0] for row in db.execute("SELECT slot FROM candidates WHERE target_date=?", (target,))}
            candidates = {state: db.execute("SELECT COUNT(*) FROM candidates WHERE state=?", (state,)).fetchone()[0]
                          for state in CANDIDATE_STATES}
            notices = {state: db.execute("SELECT COUNT(*) FROM notices WHERE state=?", (state,)).fetchone()[0]
                       for state in NOTICE_STATES}
            report["database"] = {"readable": True, "tomorrow_candidate_count": len(present & set(SLOTS)),
                                  "tomorrow_missing_slots": [slot for slot in SLOTS if slot not in present],
                                  "candidate_states": candidates, "notice_states": notices}
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            today=now.astimezone(JST).date().isoformat()
            current_creation=db.execute("SELECT count(*) FROM preparation WHERE target_date>=? AND state LIKE 'held:%'",(today,)).fetchone()[0] if 'preparation' in tables else 0
            current_publication=db.execute("SELECT count(*) FROM candidates WHERE target_date=? AND (state IN ('outcome_unknown','expired') OR (state='changes_requested' AND reason='preflight_failed'))",(today,)).fetchone()[0]
            retry=db.execute("SELECT count(*) FROM publication_retries r JOIN candidates c ON c.target_date=r.target_date AND c.slot=r.slot AND c.revision=r.revision WHERE c.target_date=? AND c.state='approved' AND r.attempts>0",(today,)).fetchone()[0] if 'publication_retries' in tables else 0
            report['business_health']={'healthy':not(current_creation or current_publication or retry),'creation_holds':current_creation,'publication_holds':current_publication,'retry_pending':retry}
            report['database']['recovery_reconciliation_required'] = 'mina_recovery_guard' in tables
            if 'cycle_health' in tables:
                row = db.execute('SELECT checked,last_success,consecutive_failures,mode FROM cycle_health WHERE singleton=1').fetchone()
                if row:
                    age = now.timestamp() - row[0]
                    valid = (math.isfinite(age) and 0 <= age <= 90 and type(row[2]) is int and row[2] >= 0 and row[3] in ('dry_run','live','failed'))
                    report['cycle_health'] = {'measured': True, 'healthy': valid and row[2] == 0 and row[3] != 'failed',
                                              'consecutive_failures': max(0, row[2]) if type(row[2]) is int else None,
                                              'fresh': valid, 'mode': row[3] if row[3] in ('dry_run','live','failed') else 'unrecognized'}
            if 'preparation' in tables:
                report['database']['preparation_holds'] = db.execute(
                    "SELECT COUNT(*) FROM preparation WHERE state LIKE 'held:%' OR (state='started' AND started<?)",
                    (now.timestamp() - 3300,)).fetchone()[0]
            if 'preparation_steps' in tables:
                report['database']['unknown_preparation_steps'] = db.execute(
                    "SELECT COUNT(*) FROM preparation_steps WHERE state='outcome_unknown' OR (state='started' AND started<?)",
                    (now.timestamp() - 3300,)).fetchone()[0]
            if 'mina_requests' in tables:
                report['database']['request_holds'] = db.execute(
                    "SELECT COUNT(*) FROM mina_requests WHERE phase IN ('held','dispatching')").fetchone()[0]
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError):
        pass
    report["manual_reconciliation_needed"] = bool(report['database'].get('recovery_reconciliation_required', False) or report['database'].get('preparation_holds', 0) or
        report['database'].get('unknown_preparation_steps', 0) or report['database'].get('request_holds', 0)) or any(
        report["database"].get(section, {}).get(state, 0)
        for section, states in (("candidate_states", ("dispatching", "outcome_unknown")),
                                ("notice_states", ("sending", "outcome_unknown")))
        for state in states)
    return report
