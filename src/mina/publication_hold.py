"""固定みなアカウントの日単位公開停止。期限や設定変更だけで解除しない。"""

from contextlib import closing, contextmanager
from datetime import date
import os
import uuid
from contextvars import ContextVar

_replacement_m1=ContextVar('replacement_m1',default=None)

@contextmanager
def replacement_daily_scope(target,owner,epoch):
    """C後に新規作成する日次だけの内部許可。通常の食事登録・訂正には使えない。"""
    token=_replacement_m1.set((target,owner,epoch))
    try:yield
    finally:_replacement_m1.reset(token)

def require_m1_write(db,target,operation):
    if not held(db,target):return
    scope=_replacement_m1.get();h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(target,)).fetchone()
    if operation!='daily_share' or not scope or (target,h['owner'],h['epoch'])!=scope or h['state']!='saved_pending_review':
        raise PublicationHeld()


class PublicationHeld(ValueError):
    def __init__(self):
        super().__init__('meal_replacement_publication_held')


def exists(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def held(db, target):
    """旧DBは読取だけで扱い、停止行があれば機能無効化後も保護する。"""
    if not exists(db, 'publication_holds'):
        return False
    row = db.execute('SELECT state FROM publication_holds WHERE target_date=?', (target,)).fetchone()
    return row is not None and row['state'] != 'released'


def require_clear(db, target):
    if held(db, target):
        raise PublicationHeld()


def require_buffer_mutation(store=None, target=None):
    """調停先のない単体Buffer書込みは新機能有効時に拒否する。読取照会は継続する。"""
    if store is None:
        if os.getenv('MINA_MEAL_REPLACEMENT_ENABLED', 'false').lower() == 'true':
            raise ValueError('buffer_replacement_fence_unavailable')
        from pathlib import Path
        if Path(os.getenv('MINA_DATABASE','/var/lib/mina-agent/mina.sqlite3')).is_file():
            raise ValueError('buffer_replacement_fence_unavailable')
        # 無効化しても既存の停止は解除しない。稼働先の読取だけで確認する。
        if any(row['state'] != 'released' for row in status(os.getenv('MINA_DATABASE', '/var/lib/mina-agent/mina.sqlite3'))):
            raise PublicationHeld()
        return
    with store.transaction() as db:
        if target is not None:
            require_clear(db, target)
        elif exists(db, 'publication_holds') and db.execute(
                "SELECT 1 FROM publication_holds WHERE state<>'released'").fetchone():
            raise PublicationHeld()


def require_claim(store, row, table='candidates'):
    """外部送信直前にも同じ台帳の停止状態と所有版を検査する。"""
    if table not in ('candidates', 'instagram_candidates'):
        raise ValueError('公開台帳が不正です')
    with store.transaction() as db:
        require_clear(db, row['target_date'])
        current = db.execute('SELECT state,digest,revision FROM '+table+' WHERE target_date=? AND slot=?',
                             (row['target_date'], row['slot'])).fetchone()
        if (not current or current['state'] != 'dispatching' or current['digest'] != row['digest']
                or current['revision'] != row['revision']):
            raise ValueError('publication_claim_changed')


def initialize(db):
    """新機能を明示操作した台帳だけを拡張する。既存候補を更新しない。"""
    db.execute('''CREATE TABLE IF NOT EXISTS publication_holds (
        target_date TEXT PRIMARY KEY, epoch INTEGER NOT NULL CHECK(epoch>0), owner TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('held','blocked_external','saved_pending_review','released')),
        created REAL NOT NULL, reason TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS publication_mutation_leases (
        operation_key TEXT PRIMARY KEY, target_date TEXT NOT NULL, operation TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('open','complete','unknown')))''')


def acquire(store, target, owner, now):
    """claim確保と同じSQLite書込排他で停止する。公開済み・送信不明は自動対象外。"""
    if os.getenv('MINA_MEAL_REPLACEMENT_ENABLED', 'false').lower() != 'true':
        raise ValueError('meal_replacement_disabled')
    if date.fromisoformat(target).isoformat() != target or str(uuid.UUID(owner)) != owner:
        raise ValueError('停止対象または所有者が不正です')
    from .domain import instant
    with store.transaction() as db:
        initialize(db)
        previous = db.execute('SELECT * FROM publication_holds WHERE target_date=?', (target,)).fetchone()
        if previous and previous['state']=='released':raise ValueError('publication_hold_already_finalized')
        if previous and previous['state'] != 'released':
            if previous['owner'] != owner:
                raise ValueError('publication_hold_already_owned')
            return dict(previous)
        epoch = previous['epoch'] + 1 if previous else 1
        blockers = (
            ('mina_requests', "phase<>'complete'"),
            ('candidates', "state IN ('dispatching','published','outcome_unknown')"),
            ('instagram_candidates', "state IN ('dispatching','accepted','published','outcome_unknown')"),
            ('buffer_publication', "state IN ('attempting','accepted','sent','outcome_unknown')"),
            ('instagram_attempts', '1=1'),
            ('publication_mutation_leases', '1=1'),
        )
        blocked = any(exists(db, table) and db.execute(
            'SELECT 1 FROM '+table+' WHERE target_date=? AND '+condition, (target,)).fetchone()
                      for table, condition in blockers)
        # 旧下書き台帳は対象日の列がない。不明下書きの対象日を推測しない。
        if exists(db, 'instagram_drafts') and db.execute(
                "SELECT 1 FROM instagram_drafts WHERE state NOT IN ('verified')").fetchone():
            blocked = True
        state = 'blocked_external' if blocked else 'held'
        db.execute('''INSERT INTO publication_holds VALUES(?,?,?,?,?,?)
            ON CONFLICT(target_date) DO UPDATE SET epoch=excluded.epoch,owner=excluded.owner,
            state=excluded.state,created=excluded.created,reason=excluded.reason''',
                   (target, epoch, owner, state, instant(now).timestamp(),
                    'local_delivery_or_draft_unresolved' if blocked else 'external_inventory_required'))
        return dict(db.execute('SELECT * FROM publication_holds WHERE target_date=?', (target,)).fetchone())


@contextmanager
def mutation_lease(store, target, operation):
    """候補のclaimを経ない下書き送信も、停止取得と同じDBで先に記録する。"""
    key = str(uuid.uuid4())
    with store.transaction() as db:
        require_clear(db, target)
        initialize(db)
        db.execute("INSERT INTO publication_mutation_leases VALUES(?,?,?,'open')", (key, target, operation))
    try:
        with store.transaction() as db:
            require_clear(db, target)
        yield
    except BaseException:
        # 送信開始との境界が不明なら再送せず照会対象として残す。
        with store.transaction() as db:
            db.execute("UPDATE publication_mutation_leases SET state='unknown' WHERE operation_key=?", (key,))
        raise
    else:
        with store.transaction() as db:
            db.execute("UPDATE publication_mutation_leases SET state='complete' WHERE operation_key=?", (key,))


def status(database):
    """診断ではDBを作成せず、所有者・候補本文・認証を出さない。"""
    from pathlib import Path
    import sqlite3
    path = Path(database).resolve()
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        if not exists(db, 'publication_holds'):
            return []
        return [dict(zip(('target_date','epoch','state','reason'), row)) for row in db.execute(
            'SELECT target_date,epoch,state,reason FROM publication_holds ORDER BY target_date')]
