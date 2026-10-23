"""本文・秘密値を含めず、作成と公開の経緯を追記する運用記録。"""

import hashlib
import json
import math
import os
import re
import time
import traceback
import uuid
from contextlib import contextmanager,closing
from datetime import UTC, date, datetime, timedelta

SCHEMA = '''
CREATE TABLE IF NOT EXISTS operation_events (
 id INTEGER PRIMARY KEY, run TEXT NOT NULL, category TEXT NOT NULL,
 target_date TEXT, slot TEXT, revision INTEGER, stage TEXT NOT NULL,
 event TEXT NOT NULL, reason TEXT NOT NULL, occurred REAL NOT NULL, details TEXT NOT NULL,
 invocation TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS operation_events_run ON operation_events(run,id);
CREATE TABLE IF NOT EXISTS publication_retries (
 target_date TEXT NOT NULL,slot TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL,
 attempts INTEGER NOT NULL,next_at REAL NOT NULL, PRIMARY KEY(target_date,slot,revision));
CREATE TABLE IF NOT EXISTS audit_maintenance (day TEXT PRIMARY KEY);
'''
STAGES = frozenset(('draft','images','drive','registration','candidates','daily_draft','image','image_review',
 'drive_upload','drive_verify','meal_register','meal_request_key','meal_review','correction_plan','correction_key',
 'meal_correction','ogp_capture','ogp_review','daily_share','daily_request_key','automatic_image_repair',
 'route','connection','candidate','source','source_state','source_request','source_image','source_final',
 'freshness','submission','delivery','notification','preparation','request','reconciliation','launch','cycle',
 'review_diagnosis','recovery','meal_replan','menu_nutrition','other'))
REASONS = frozenset(('none','unclassified','validation_failed','post_text_too_long','io_failed','timeout','http_error','response_invalid',
 'contract_invalid','quality_hold','quality_rejected_not_saved','deadline_exceeded','outcome_unknown',
 'lookup_unavailable','clock_ahead','state_changed','source_changed','image_changed','ogp_expired',
 'connection_network','connection_timeout','connection_http_transient','retry_pending','retry_exhausted','usage_limit','configuration','sandbox','execution',
 'retry_deadline','binding_changed','buffer_unavailable','buffer_quota','authentication','audit_unavailable',
 'image_analysis_mismatch','nutrition_semantic_mismatch','context_mismatch','share_ogp_mismatch',
 'experiential_claim','health_claim','copy_repetition','daily_energy_out_of_range','recovered','legacy_hold'))
EVENTS = frozenset(('started','completed','failed','held','stopped','retry_wait','recovered','observed'))


class OperationalFailure(RuntimeError):
    """外部のエラー文を持たず、許可した分類だけを上位へ伝える。"""
    def __init__(self, reason, stage=None, http_status=None):
        if reason not in REASONS or stage is not None and stage not in STAGES:
            raise ValueError('運用エラーの分類が不正です')
        self.audit_reason, self.audit_stage, self.http_status = reason, stage, http_status
        super().__init__(reason)


class AuditUnavailable(OperationalFailure):
    def __init__(self):
        super().__init__('audit_unavailable')


def safe_failure(error):
    """例外の本文やlocalsは読まず、型と許可属性から診断する。"""
    reason = getattr(error, 'audit_reason', None)
    if not isinstance(reason, str) or reason not in REASONS:
        reason = ('timeout' if isinstance(error, TimeoutError) else 'io_failed' if isinstance(error, OSError)
                  else 'validation_failed' if isinstance(error, (ValueError, TypeError, KeyError)) else 'unclassified')
    frames = []
    for frame in traceback.extract_tb(error.__traceback__):
        path = frame.filename.replace('\\','/')
        match = re.search(r'(src/mina/[a-z0-9_]+\.py)$', path)
        if match and re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]{0,63}',frame.name):
            frames.append(f'{match[1]}:{frame.name}:{frame.lineno}')
    details = {'frames': frames[-5:]}
    from .x_text import PostLengthError
    if isinstance(error,PostLengthError):
        details.update(expected=280,observed=error.post_length)
    status = getattr(error,'http_status',None)
    if type(status) is int and 100 <= status <= 599:
        details['http_status'] = status
    ahead=getattr(error,'clock_ahead_ms',None)
    if type(ahead) in (int,float) and math.isfinite(ahead) and abs(ahead)<=1e12:
        details['clock_ahead_ms']=ahead
    return reason, details


def checked_details(details):
    """未定義の項目を捨てず拒否し、秘密の混入を構造で防ぐ。"""
    numbers={'elapsed_ms','clock_ahead_ms','attempt','next_at','requested_at','received_at','expected','observed'}
    hashes={'input_sha256','image_sha256','result_sha256','step_sha256','policy_sha256','source_commit'}
    bools={'matched','write_started','saved','retryable'}
    enums={'operation':{'channel_organizations','channel_list','post_create','post_lookup','meal_register','meal_correction','daily_share'},
           'persistence':{'saved','not_saved','unknown'},'phase':{'prepared','dispatching','response_received','complete','held'},
           'before':{'approved','dispatching','changes_requested','published','outcome_unknown','started','held'},
           'after':{'approved','dispatching','changes_requested','published','outcome_unknown','complete','held','expired'}}
    result={}
    for key,value in (details or {}).items():
        if key in numbers and type(value) in (int,float) and math.isfinite(value) and abs(value)<=1e12:
            result[key]=value
        elif key in hashes and isinstance(value,str) and re.fullmatch(r'[0-9a-f]{40}' if key=='source_commit' else r'[0-9a-f]{64}',value):
            result[key]=value
        elif key in bools and type(value) is bool:
            result[key]=value
        elif key=='http_status' and type(value) is int and 100<=value<=599:
            result[key]=value
        elif key in enums and isinstance(value,str) and value in enums[key]:
            result[key]=value
        elif key=='frames' and isinstance(value,list) and len(value)<=5 and all(isinstance(v,str) and re.fullmatch(r'src/mina/[a-z0-9_]+\.py:[A-Za-z_][A-Za-z_0-9]{0,63}:[0-9]{1,6}',v) for v in value):
            result[key]=value
        elif key=='findings':
            from .review_diagnostics import validate_findings
            result[key]=validate_findings(value)
        elif key=='quality_codes' and isinstance(value,list) and len(value)<=12 and all(v in (
                'meal_calories_out_of_range','macro_out_of_range','food_calories_out_of_range',
                'atwater_mismatch','nutrition_invalid','ambiguous_foods') for v in value):
            result[key]=value
        else:
            raise ValueError('運用記録の項目・値が許可されていません')
    raw=json.dumps(result,ensure_ascii=False,allow_nan=False)
    if len(raw.encode())>8192:
        raise ValueError('運用記録の容量上限を超えました')
    return raw


class AuditLog:
    def __init__(self,store):
        self.store=store
        self.invocation=uuid.uuid4().hex

    def emit(self,category,target,slot,revision,stage,event,reason='none',details=None,now=None,db=None):
        if category not in ('creation','publication','system') or stage not in STAGES or event not in EVENTS or reason not in REASONS:
            raise ValueError('運用記録の分類が不正です')
        if target is not None and date.fromisoformat(target).isoformat()!=target:
            raise ValueError('運用記録の対象日が不正です')
        if slot not in (None,'breakfast','lunch','dinner','daily') or revision is not None and (type(revision) is not int or revision<0):
            raise ValueError('運用記録の枠・版が不正です')
        details=dict(details or {})
        commit=os.getenv('MINA_SOURCE_COMMIT','')
        if re.fullmatch(r'[0-9a-f]{40}',commit):details.setdefault('source_commit',commit)
        raw=checked_details(details)
        run=f'{category}:{target or "system"}:{slot or "all"}:{revision or 0}'
        values=(run,category,target,slot,revision,stage,event,reason,(now or datetime.now(UTC)).timestamp(),raw,self.invocation)
        try:
            if db is not None:
                db.execute('INSERT INTO operation_events(run,category,target_date,slot,revision,stage,event,reason,occurred,details,invocation) VALUES(?,?,?,?,?,?,?,?,?,?,?)',values)
            else:
                with self.store.transaction() as connection:
                    connection.execute('INSERT INTO operation_events(run,category,target_date,slot,revision,stage,event,reason,occurred,details,invocation) VALUES(?,?,?,?,?,?,?,?,?,?,?)',values)
        except Exception:
            # DB障害時にも本文や例外原文を出さない。送信前ならここで停止する。
            print('{"event":"mina_audit_unavailable"}',flush=True)
            raise AuditUnavailable() from None

    @contextmanager
    def operation(self,category,target,slot,revision,stage,details=None):
        start=time.monotonic()
        self.emit(category,target,slot,revision,stage,'started',details=details)
        try:
            yield
        except Exception as error:
            reason,info=safe_failure(error)
            self.emit(category,target,slot,revision,stage,'failed',reason,{**info,'elapsed_ms':round((time.monotonic()-start)*1000,3)})
            raise
        else:
            self.emit(category,target,slot,revision,stage,'completed',details={'elapsed_ms':round((time.monotonic()-start)*1000,3)})

    def maintain(self,now):
        """30日以内と未解決事案を保持し、完了した古い詳細だけを日一回整理する。"""
        day=now.date().isoformat();cutoff=(now-timedelta(days=30)).timestamp()
        with self.store.transaction() as db:
            if db.execute('SELECT 1 FROM audit_maintenance WHERE day=?',(day,)).fetchone():return
            db.execute('''DELETE FROM operation_events WHERE occurred<? AND
                ((category='publication' AND EXISTS(SELECT 1 FROM candidates c WHERE c.target_date=operation_events.target_date AND c.slot=operation_events.slot AND c.state IN ('published','cancelled')))
                OR (category='creation' AND EXISTS(SELECT 1 FROM preparation p WHERE p.target_date=operation_events.target_date AND p.state='complete')
                AND NOT EXISTS(SELECT 1 FROM mina_requests r WHERE r.target_date=operation_events.target_date AND r.phase IN ('held','dispatching'))))''',(cutoff,)) if db.execute("SELECT 1 FROM sqlite_master WHERE name='mina_requests'").fetchone() else None
            db.execute('INSERT INTO audit_maintenance VALUES(?)',(day,))


def step_context(step):
    parts=step.split(':');prefix=parts[0]
    slot=parts[1].split('-')[0] if len(parts)>1 else None
    return prefix if prefix in STAGES else 'other',slot if slot in ('breakfast','lunch','dinner','daily') else None


def read_events(database,target,slot=None):
    """外部接続・DB作成なしで、許可済みの詳細を最大200件読む。"""
    import sqlite3
    from pathlib import Path
    date.fromisoformat(target)
    if slot not in (None,'breakfast','lunch','dinner','daily'):raise ValueError('対象枠が不正です')
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='operation_events'").fetchone():return []
        rows=db.execute('SELECT * FROM operation_events WHERE target_date=? AND (? IS NULL OR slot=?) ORDER BY id DESC LIMIT 200',(target,slot,slot)).fetchall()
        result=[]
        for row in reversed(rows):
            item=dict(row);item['details']=json.loads(checked_details(json.loads(item['details'])))
            item['occurred_at_utc']=datetime.fromtimestamp(item.pop('occurred'),UTC).isoformat()
            result.append(item)
        return result
