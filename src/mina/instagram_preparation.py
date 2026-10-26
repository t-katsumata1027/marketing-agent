"""食事準備の復旧を追跡し、日付をまたいでも未来の4枠を一度だけ準備する。"""
from datetime import UTC,datetime,timedelta
import json,os
from pathlib import Path
from .domain import JST,SLOTS,instant,slot_time,date
from .instagram_domain import InstagramCandidate,digest
from .publication_hold import require_clear
from contextlib import nullcontext


class PreparationWaiting(ValueError):
    """未完成の依存データだけを待つ。外部書込みの成否不明ではない。"""


def owner():
    """再起動やPID再利用を、時間切れで推測せずプロセス実体で区別する。"""
    pid=os.getpid()
    if os.name!='posix':return f'local:{pid}'
    start=Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19]
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()+':'+str(pid)+':'+start


def alive(value):
    if os.name!='posix':return True
    try:
        boot,pid,start=value.split(':');current=Path(f'/proc/{int(pid)}/stat')
        return (boot==Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                and current.read_text().split(') ',1)[1].split()[19]==start)
    except (OSError,ValueError,IndexError):return False


def initialize(store):
    with store.transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS instagram_preparations (
            target_date TEXT PRIMARY KEY,state TEXT NOT NULL,owner TEXT,source_marker TEXT,
            next_check REAL NOT NULL DEFAULT 0,reason TEXT NOT NULL,updated REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS instagram_preparation_events (
            id INTEGER PRIMARY KEY,target_date TEXT NOT NULL,stage TEXT NOT NULL,event TEXT NOT NULL,
            reason TEXT NOT NULL,details TEXT NOT NULL,created REAL NOT NULL)''')


def record(store,target,stage,event,error=None):
    """外部応答の原文を出さず、停止工程・分類・安全な呼出し位置を残す。"""
    from .audit import safe_failure,checked_details
    reason,details=safe_failure(error) if error is not None else ('none',{})
    with store.transaction() as db:
        db.execute('INSERT INTO instagram_preparation_events(target_date,stage,event,reason,details,created) VALUES(?,?,?,?,?,?)',
                   (target,stage,event,reason,checked_details(details),datetime.now(UTC).timestamp()))


def prepare(runtime,target,allow_public=False,clock=lambda:datetime.now(UTC)):
    """依存読取は再開できるが、画像保存を開始した不明工程は再実行しない。"""
    initialize(runtime.store);now=instant(clock());identity=owner()
    with runtime.store.transaction() as db:
        previous=db.execute('SELECT * FROM instagram_preparations WHERE target_date=?',(target,)).fetchone()
        existing=list(db.execute('SELECT * FROM instagram_candidates WHERE target_date=?',(target,)))
        if previous and previous['state']=='reconciliation_required':raise ValueError('instagram_preparation_reconciliation_required')
        if previous and previous['state'] in ('checking_source','external_preparing'):
            if alive(previous['owner']):raise PreparationWaiting('instagram_preparation_running')
            if previous['state']=='external_preparing':
                db.execute("UPDATE instagram_preparations SET state='reconciliation_required',reason='interrupted_external' WHERE target_date=?",(target,))
                # 例外をtransaction外で投げ、停止記録をcommitする。
                interrupted=True
            else:interrupted=False
        else:interrupted=False
        if interrupted:pass
        elif previous and previous['state']=='complete' and len(existing)!=4:raise ValueError('instagram_completed_candidates_missing')
        elif existing:
            if len(existing)!=4 or {r['slot'] for r in existing}!=set(SLOTS):raise ValueError('instagram_partial_candidates_require_reconciliation')
            return [InstagramCandidate(**json.loads(r['payload'])) for r in existing]
        elif slot_time(date.fromisoformat(target),'breakfast')<=now:raise ValueError('instagram_preparation_deadline')
        else:
            require_clear(db,target)
            rows=list(db.execute('SELECT slot,digest FROM candidates WHERE target_date=?',(target,)))
            ready=len(rows)==4 and {r['slot'] for r in rows}==set(SLOTS)
            marker=digest({r['slot']:r['digest'] for r in rows}) if ready else None
            if not ready:
                db.execute("INSERT INTO instagram_preparations VALUES(?,'waiting_source',NULL,NULL,?,'source_not_ready',?) ON CONFLICT(target_date) DO UPDATE SET state='waiting_source',owner=NULL,source_marker=NULL,next_check=excluded.next_check,reason=excluded.reason,updated=excluded.updated",(target,now.timestamp()+60,now.timestamp()))
                waiting=True
            elif previous and previous['state']=='waiting_source' and previous['source_marker']==marker and previous['next_check']>now.timestamp():
                waiting=True
            else:
                db.execute("INSERT INTO instagram_preparations VALUES(?,'checking_source',?,?,0,'none',?) ON CONFLICT(target_date) DO UPDATE SET state='checking_source',owner=excluded.owner,source_marker=excluded.source_marker,reason='none',updated=excluded.updated",(target,identity,marker,now.timestamp()))
                waiting=False
    if interrupted:raise ValueError('instagram_preparation_reconciliation_required')
    if waiting:raise PreparationWaiting('instagram_source_not_ready')
    def check(connection=None):
        if instant(clock())>=slot_time(date.fromisoformat(target),'breakfast'):raise ValueError('instagram_preparation_deadline')
        with (runtime.store.transaction() if connection is None else nullcontext(connection)) as db:
            require_clear(db,target)
            row=db.execute('SELECT state,owner FROM instagram_preparations WHERE target_date=?',(target,)).fetchone()
            current=list(db.execute('SELECT slot,digest FROM candidates WHERE target_date=?',(target,)))
            if (row['state'] not in ('checking_source','external_preparing') or row['owner']!=identity
                    or digest({r['slot']:r['digest'] for r in current})!=marker):raise ValueError('instagram_preparation_source_changed')
    def begin_write():
        with runtime.store.transaction() as db:
            check(db)
            changed=db.execute("UPDATE instagram_preparations SET state='external_preparing',updated=? WHERE target_date=? AND state='checking_source'",(instant(clock()).timestamp(),target)).rowcount
        if changed:record(runtime.store,target,'external_write','started')
    old_guard=getattr(runtime,'preparation_guard',None);old_begin=getattr(runtime,'preparation_begin_write',None)
    runtime.preparation_guard=check;runtime.preparation_begin_write=begin_write
    try:
        # 再読取・BufferチャネルGETも最初の不可逆操作までは読取工程のまま保持する。
        runtime.render(target);check()
        result=runtime._prepare(target,allow_public)
        with runtime.store.transaction() as db:
            db.execute("UPDATE instagram_preparations SET state='complete',reason='none',updated=? WHERE target_date=? AND owner=?",(instant(clock()).timestamp(),target,identity))
        record(runtime.store,target,'candidates','completed')
        return result
    except BaseException as error:
        with runtime.store.transaction() as db:
            row=db.execute('SELECT state FROM instagram_preparations WHERE target_date=?',(target,)).fetchone()
            readonly=row['state']=='checking_source'
            db.execute("UPDATE instagram_preparations SET state=?,owner=NULL,next_check=?,reason=?,updated=? WHERE target_date=? AND owner=?",
                       ('waiting_source' if readonly else 'reconciliation_required',instant(clock()).timestamp()+60,
                        'source_validation_failed' if readonly else 'external_step_unconfirmed',instant(clock()).timestamp(),target,identity))
        record(runtime.store,target,'source' if readonly else 'artwork','failed' if readonly else 'stopped',error)
        raise
    finally:runtime.preparation_guard=old_guard;runtime.preparation_begin_write=old_begin


def catch_up(runtime,now=None):
    """20:10以降と翌朝食前だけを対象に、常駐巡回から完成依存へ追従する。"""
    now=instant(now or datetime.now(UTC));local=now.astimezone(JST)
    if os.getenv('MINA_INSTAGRAM_PREPARE_ENABLED','false').lower()!='true':return 'disabled'
    target=local.date() if now<slot_time(local.date(),'breakfast') else local.date()+timedelta(days=1)
    start=datetime.combine(target-timedelta(days=1),datetime.min.time(),tzinfo=JST).replace(hour=20,minute=10)
    if now<start:return 'not_due'
    target=target.isoformat()
    if len(runtime.store.rows(target))==4:
        try:runtime.notify(target)
        except Exception:return 'notification_held'
        return 'already_prepared'
    try:
        prepare(runtime,target,allow_public=True)
        try:runtime.notify(target)
        except Exception as error:
            record(runtime.store,target,'notification','stopped',error)
            return 'notification_held'
        return 'prepared'
    except PreparationWaiting:return 'waiting_source'
    except Exception:
        runtime.store.queue_preparation_hold(target)
        return 'held'
