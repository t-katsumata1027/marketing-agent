"""救済専用の不変原本・操作台帳。通常台帳を上書きせずUUIDの衝突を拒否する。"""
import hashlib
import json
from .clients import MealClient
from .replacement_contract import canonical, digest, stamp
from .publication_hold import exists
from .rescue_contract import TARGET


def reserve_uuid(db, key, kind, owner):
    MealClient.request_key(key)
    db.execute('CREATE TABLE IF NOT EXISTS rescue_uuid_registry (uuid TEXT PRIMARY KEY,kind TEXT NOT NULL,owner TEXT NOT NULL)')
    for table in ('mina_requests','meal_replacement_requests'):
        if exists(db,table) and db.execute('SELECT 1 FROM '+table+' WHERE request_key=?',(key,)).fetchone():
            raise ValueError('rescue_uuid_collision')
    row = db.execute('SELECT kind,owner FROM rescue_uuid_registry WHERE uuid=?',(key,)).fetchone()
    if row and tuple(row) != (kind,owner): raise ValueError('rescue_uuid_collision')
    if not row: db.execute('INSERT INTO rescue_uuid_registry VALUES(?,?,?)',(key,kind,owner))


def reject_reserved_uuid(db, key):
    if exists(db,'rescue_uuid_registry') and db.execute('SELECT 1 FROM rescue_uuid_registry WHERE uuid=?',(key,)).fetchone():
        raise ValueError('rescue_uuid_collision')


class RescueJournal:
    def __init__(self, store, clock):
        self.store,self.clock = store,clock
        with store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS rescue_jobs (
                target_date TEXT PRIMARY KEY,owner TEXT NOT NULL,epoch INTEGER NOT NULL,
                issue_json TEXT NOT NULL,case_uuid TEXT NOT NULL UNIQUE,prepare_uuid TEXT NOT NULL UNIQUE,
                case_observation TEXT,stage TEXT NOT NULL,deadline TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS rescue_requests (
                request_key TEXT PRIMARY KEY,operation TEXT NOT NULL,metadata TEXT NOT NULL,
                body BLOB NOT NULL,body_sha256 TEXT NOT NULL,content_type TEXT NOT NULL,
                prepare_uuid TEXT NOT NULL,phase TEXT NOT NULL,observation TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS rescue_events (
                id INTEGER PRIMARY KEY,kind TEXT NOT NULL,reference TEXT NOT NULL,
                document TEXT NOT NULL,observed_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS rescue_calls (
                call_key TEXT PRIMARY KEY,kind TEXT NOT NULL,state TEXT NOT NULL,
                encoding TEXT,value BLOB,finished_at TEXT)''')

    def job(self):
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM rescue_jobs WHERE target_date=?',(TARGET,)).fetchone()
            if not row: raise ValueError('rescue_job_missing')
            return dict(row)

    def stage(self, stage):
        with self.store.transaction() as db:
            current=db.execute('SELECT stage FROM rescue_jobs WHERE target_date=?',(TARGET,)).fetchone()
            if current and current['stage']=='all_meals_reviewed': return
            saved=any(json.loads(r[0] or '{}').get('persistence')=='saved' for r in db.execute("SELECT observation FROM rescue_requests WHERE operation='meal_replace'"))
            if saved and stage!='all_meals_reviewed': stage='saved'
            db.execute('UPDATE rescue_jobs SET stage=? WHERE target_date=?',(stage,TARGET))

    def event(self, kind, reference, value, db=None):
        if db is None:
            with self.store.transaction() as connection: return self.event(kind,reference,value,connection)
        db.execute('INSERT INTO rescue_events(kind,reference,document,observed_at) VALUES(?,?,?,?)',
                   (kind,reference,canonical(value).decode(),stamp(self.clock())))

    def prepare(self, metadata, operation, body, content_type):
        key=metadata['request_uuid'];job=self.job()
        if operation == 'candidate_prepare' and key != job['prepare_uuid']:
            raise ValueError('rescue_planned_prepare_required')
        if metadata['rescue']['case_uuid'] != job['case_uuid']:
            raise ValueError('rescue_case_changed')
        sha=hashlib.sha256(body).hexdigest()
        values=(key,operation,canonical(metadata).decode(),body,sha,content_type,job['prepare_uuid'])
        with self.store.transaction() as db:
            reserve_uuid(db,key,'candidate_prepare' if operation=='candidate_prepare' else operation,job['case_uuid'])
            old=db.execute('SELECT * FROM rescue_requests WHERE request_key=?',(key,)).fetchone()
            if old:
                if tuple(old[k] for k in ('request_key','operation','metadata','body','body_sha256','content_type','prepare_uuid')) != values:
                    raise ValueError('rescue_request_immutable')
                return
            if db.execute('SELECT 1 FROM rescue_requests WHERE operation=?',(operation,)).fetchone() and operation!='replacement_resume':
                raise ValueError('rescue_operation_already_reserved')
            db.execute("INSERT INTO rescue_requests VALUES(?,?,?,?,?,?,?,'prepared',NULL)",values)

    def request(self, key):
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM rescue_requests WHERE request_key=?',(key,)).fetchone()
            if not row or hashlib.sha256(row['body']).hexdigest()!=row['body_sha256']:
                raise ValueError('rescue_request_integrity_invalid')
            return dict(row)

    def start(self, key, checkpoint):
        with self.store.transaction() as db:
            checkpoint(db)
            row=db.execute('SELECT * FROM rescue_requests WHERE request_key=?',(key,)).fetchone()
            if (not row or row['phase']!='prepared'
                    or hashlib.sha256(row['body']).hexdigest()!=row['body_sha256']):
                raise ValueError('rescue_lookup_only')
            db.execute("UPDATE rescue_requests SET phase='started' WHERE request_key=?",(key,))
            return dict(row)

    def observe(self, key, value):
        row=self.request(key);request=value['request'];metadata=json.loads(row['metadata'])
        if (request['request_uuid']!=key or request['operation']!=row['operation']
                or request['input_hash']!=digest({'operation':row['operation'],**{k:v for k,v in metadata.items() if k!='request_uuid'}},'ih1_')):
            raise ValueError('rescue_response_input_changed')
        field='persistence' if row['operation']=='meal_replace' else 'outcome'
        positive='saved' if field=='persistence' else 'applied'
        observed={field:request[field]}
        old=json.loads(row['observation'] or '{}')
        effective=dict(observed)
        if old.get(field)==positive: effective[field]=positive
        if old.get('conflict') or old.get(field) in ('saved','not_saved','applied','rejected') and observed[field] in ('saved','not_saved','applied','rejected') and old[field]!=observed[field]:
            effective['conflict']=True
        with self.store.transaction() as db:
            latest=db.execute('SELECT observation FROM rescue_requests WHERE request_key=?',(key,)).fetchone()
            old=json.loads(latest['observation'] or '{}')
            if old.get(field)==positive: effective[field]=positive
            if old.get('conflict') or (old.get(field) in ('saved','not_saved','applied','rejected')
                    and observed[field] in ('saved','not_saved','applied','rejected') and old[field]!=observed[field]):
                effective['conflict']=True
            self.event('response',key,value,db)
            db.execute("UPDATE rescue_requests SET phase='lookup_only',observation=? WHERE request_key=?",
                       (canonical(effective).decode(),key))
            if field=='persistence' and effective[field]=='saved' and old.get(field)!='saved':
                from .replacement_bundle import invalidate_saved
                invalidate_saved(db,TARGET)
                job=db.execute('SELECT owner,epoch FROM rescue_jobs WHERE target_date=?',(TARGET,)).fetchone()
                changed=db.execute("UPDATE publication_holds SET state='saved_pending_review',reason='rescue_new_review_required' WHERE target_date=? AND owner=? AND epoch=? AND state='held'",(TARGET,job['owner'],job['epoch'])).rowcount
                if changed!=1: raise ValueError('rescue_hold_changed')
        return effective
