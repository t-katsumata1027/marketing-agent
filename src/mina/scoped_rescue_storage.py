"""v3の所有鍵を全行へ固定し、旧救済と別日の結果を混在させない。"""
import hashlib
import json
from .replacement_contract import canonical, digest, stamp
from .rescue_storage import reserve_uuid
from .scoped_rescue_contract import VERSION, target_scope


def execution_seal(db,target):
    """旧業務行・原画像・入力資料の実体を固定する。新しい救済出力は含めない。"""
    from pathlib import Path
    from .publication_hold import exists
    tables={};paths=set()
    for table in ('preparation_steps','mina_requests','operation_events','replacement_runtime_context','replacement_episodes',
                  'replacement_attempts','replacement_origin_receipts','replacement_external_calls','replacement_waived_calls','replacement_cost_reservations','meal_replacement_requests','scoped_rescue_calls'):
        if not exists(db,table):continue
        suffix=" AND stage IN ('image','meal_replan')" if table=='operation_events' else ''
        rows=[]
        for record in db.execute('SELECT rowid AS __seal_rowid,* FROM '+table+' WHERE target_date=?'+suffix+' ORDER BY rowid',(target,)):
            row=dict(record)
            for k,v in list(row.items()):
                if isinstance(v,bytes):row[k]={'byte_length':len(v),'sha256':hashlib.sha256(v).hexdigest()}
            rows.append(row)
            if table=='preparation_steps' and row['step'].startswith('image:'):
                value=json.loads(row['result'] or '{}')
                if value.get('path'):paths.add(value['path'])
            if table=='replacement_runtime_context':
                context=json.loads(row['context']);base=Path(context['directory'])
                paths.update(str(base/n) for n in ('draft.json','drive.json'))
                paths.update(i['path'] for i in context['saved']['images'].values())
        tables[table]=rows
    files={}
    for name in sorted(paths):
        p=Path(name)
        if p.is_symlink() or not p.is_file():raise ValueError('scoped_rescue_origin_file_changed')
        files[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    return {'tables':tables,'files':files}


def verify_execution_seal(db,target,seal,case_uuid):
    """旧行の変更・削除と無関係な追加を拒否し、自己の新費用と日次だけを区別する。"""
    current=execution_seal(db,target)
    if current['files']!=seal['files']:raise ValueError('scoped_rescue_origin_file_changed')
    for table,rows in seal['tables'].items():
        now={r['__seal_rowid']:r for r in current['tables'].get(table,[])}
        for r in rows:
            actual=now.get(r['__seal_rowid'])
            if actual==r:continue
            if table=='replacement_episodes' and actual=={**r,'stage':'new_bundle_pending_approval'}:
                job=db.execute("SELECT 1 FROM scoped_rescue_jobs WHERE contract=? AND target_date=? AND case_uuid=? AND owner=? AND epoch=? AND stage='all_meals_reviewed'",(VERSION,target,case_uuid,r['owner'],r['epoch'])).fetchone()
                if job:continue
            raise ValueError('scoped_rescue_origin_changed')
        original={r['__seal_rowid'] for r in rows}
        for row in now.values():
            if row['__seal_rowid'] in original:continue
            if table in ('replacement_waived_calls','replacement_cost_reservations') and row['call_key'].startswith(case_uuid+':'):continue
            if table=='scoped_rescue_calls' and row['case_uuid']==case_uuid and row['contract']==VERSION and row['call_key'].startswith(case_uuid+':'):continue
            if table=='mina_requests' and row['operation']=='daily_share':
                found=db.execute('SELECT 1 FROM replacement_finalization_requests WHERE contract=? AND target_date=? AND request_key=?',(VERSION,target,row['request_key'])).fetchone()
                if found:continue
            raise ValueError('scoped_rescue_origin_changed')
    if set(current['tables'])-set(seal['tables']):
        for table in set(current['tables'])-set(seal['tables']):
            if current['tables'][table]:raise ValueError('scoped_rescue_origin_changed')


class ScopedRescueJournal:
    def __init__(self, store, clock, target, slot, case_uuid):
        from .clients import MealClient
        target_scope(target, slot); MealClient.request_key(case_uuid)
        self.store, self.clock = store, clock
        self.scope = (VERSION, target, slot, case_uuid)
        with store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS scoped_preparation_locks (
                target_date TEXT PRIMARY KEY,case_uuid TEXT NOT NULL,owner TEXT NOT NULL,epoch INTEGER NOT NULL,
                window_json TEXT NOT NULL,candidates_digest TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS scoped_rescue_jobs (
                contract TEXT NOT NULL,target_date TEXT NOT NULL,slot TEXT NOT NULL,case_uuid TEXT NOT NULL,
                owner TEXT NOT NULL,epoch INTEGER NOT NULL,issue_json TEXT NOT NULL,prepare_uuid TEXT NOT NULL UNIQUE,
                case_observation TEXT,stage TEXT NOT NULL,deadline TEXT,
                PRIMARY KEY(contract,target_date,slot,case_uuid),UNIQUE(target_date,slot))''')
            db.execute('''CREATE TABLE IF NOT EXISTS scoped_rescue_requests (
                contract TEXT NOT NULL,target_date TEXT NOT NULL,slot TEXT NOT NULL,case_uuid TEXT NOT NULL,
                request_key TEXT PRIMARY KEY,operation TEXT NOT NULL,metadata TEXT NOT NULL,body BLOB NOT NULL,
                body_sha256 TEXT NOT NULL,content_type TEXT NOT NULL,phase TEXT NOT NULL,observation TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS scoped_rescue_events (
                id INTEGER PRIMARY KEY,contract TEXT NOT NULL,target_date TEXT NOT NULL,slot TEXT NOT NULL,
                case_uuid TEXT NOT NULL,kind TEXT NOT NULL,reference TEXT NOT NULL,document TEXT NOT NULL,observed_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS scoped_rescue_calls (
                contract TEXT NOT NULL,target_date TEXT NOT NULL,slot TEXT NOT NULL,case_uuid TEXT NOT NULL,
                call_key TEXT PRIMARY KEY,kind TEXT NOT NULL,state TEXT NOT NULL,input_digest TEXT NOT NULL,
                encoding TEXT,value BLOB,finished_at TEXT)''')

    def rows(self, table, suffix='', args=(), db=None):
        if table not in ('jobs', 'requests', 'events', 'calls'): raise ValueError('scoped_rescue_table_invalid')
        if db is None:
            with self.store.transaction() as connection: return self.rows(table, suffix, args, connection)
        return [dict(r) for r in db.execute('SELECT * FROM scoped_rescue_' + table +
            ' WHERE contract=? AND target_date=? AND slot=? AND case_uuid=? ' + suffix, (*self.scope, *args))]

    def job(self, db=None):
        rows = self.rows('jobs', db=db)
        if len(rows) != 1: raise ValueError('scoped_rescue_job_missing')
        return rows[0]

    def issue(self, value, owner, epoch, evidence):
        from .scoped_rescue_contract import ScopedRescueContract
        ScopedRescueContract(self.clock, self.scope[1], self.scope[2]).validate('GrantIssueInput', value)
        if value['case_uuid'] != self.scope[3]: raise ValueError('scoped_rescue_case_changed')
        with self.store.transaction() as db:
            from .publication_hold import exists
            h = db.execute('SELECT * FROM publication_holds WHERE target_date=?', (self.scope[1],)).fetchone()
            if not h or (h['owner'], h['epoch'], h['state']) != (owner, epoch, 'held'):
                raise ValueError('scoped_rescue_hold_changed')
            # 同日同枠の旧caseがあっても、新UUIDで救済回数を戻さない。
            old = db.execute('SELECT * FROM scoped_rescue_jobs WHERE target_date=? AND slot=?', self.scope[1:3]).fetchone()
            if old:
                if tuple(old[k] for k in ('contract','target_date','slot','case_uuid')) != self.scope or old['issue_json'] != canonical(value).decode():
                    raise ValueError('scoped_rescue_issue_immutable')
                return
            if exists(db, 'rescue_jobs') and db.execute('SELECT 1 FROM rescue_jobs WHERE target_date=?', (self.scope[1],)).fetchone():
                raise ValueError('scoped_rescue_existing_case')
            reserve_uuid(db, self.scope[3], 'case', self.scope[3])
            reserve_uuid(db, value['planned_prepare_uuid'], 'candidate_prepare', self.scope[3])
            db.execute("INSERT INTO scoped_rescue_jobs VALUES(?,?,?,?,?,?,?,?,NULL,'awaiting_operator',NULL)",
                (*self.scope, owner, epoch, canonical(value).decode(), value['planned_prepare_uuid']))
            self.event('issue_evidence', self.scope[3], evidence, db)
            self.event('execution_seal',self.scope[3],execution_seal(db,self.scope[1]),db)

    def event(self, kind, reference, value, db=None):
        if db is None:
            with self.store.transaction() as connection: return self.event(kind, reference, value, connection)
        db.execute('INSERT INTO scoped_rescue_events(contract,target_date,slot,case_uuid,kind,reference,document,observed_at) VALUES(?,?,?,?,?,?,?,?)',
            (*self.scope, kind, reference, canonical(value).decode(), stamp(self.clock())))

    def saved(self, db=None):
        return any(json.loads(r['observation'] or '{}').get('persistence') == 'saved'
                   for r in self.rows('requests', "AND operation='meal_replace'", db=db))

    def stage(self, value):
        with self.store.transaction() as db:
            if self.job(db)['stage'] == 'all_meals_reviewed': return
            if self.saved(db): value = 'saved' if value != 'all_meals_reviewed' else value
            db.execute('UPDATE scoped_rescue_jobs SET stage=? WHERE contract=? AND target_date=? AND slot=? AND case_uuid=?', (value, *self.scope))

    def bind(self, c):
        with self.store.transaction() as db:
            job = self.job(db); old = json.loads(job['case_observation'] or 'null')
            issue = json.loads(job['issue_json'])
            if any(c[k] != issue[k] for k in ('case_uuid', 'basis', 'planned_prepare_uuid', 'attempt_ordinal')):
                raise ValueError('scoped_rescue_case_changed')
            if old and (c['authorization_digest'] != old['authorization_digest']
                        or c['first_activated_at'] != old['first_activated_at'] or c['hard_deadline_at'] != old['hard_deadline_at']
                        or c['authorization_revision'] < old['authorization_revision']):
                raise ValueError('scoped_rescue_case_regressed')
            db.execute('UPDATE scoped_rescue_jobs SET case_observation=?,deadline=? WHERE contract=? AND target_date=? AND slot=? AND case_uuid=?',
                (canonical(c).decode(), c['hard_deadline_at'], *self.scope))

    def prepare(self, metadata, operation, body, content_type):
        key = metadata['request_uuid']; job = self.job()
        if metadata['rescue']['case_uuid'] != self.scope[3] or operation == 'candidate_prepare' and key != job['prepare_uuid']:
            raise ValueError('scoped_rescue_planned_request_required')
        values = (key, operation, canonical(metadata).decode(), body, hashlib.sha256(body).hexdigest(), content_type)
        with self.store.transaction() as db:
            reserve_uuid(db, key, operation, self.scope[3])
            old = db.execute('SELECT * FROM scoped_rescue_requests WHERE request_key=?', (key,)).fetchone()
            if old:
                if tuple(old[k] for k in ('contract','target_date','slot','case_uuid')) != self.scope or tuple(old[k] for k in ('request_key','operation','metadata','body','body_sha256','content_type')) != values:
                    raise ValueError('scoped_rescue_request_immutable')
                return
            if operation != 'replacement_resume' and self.rows('requests', 'AND operation=?', (operation,), db):
                raise ValueError('scoped_rescue_operation_already_reserved')
            db.execute("INSERT INTO scoped_rescue_requests VALUES(?,?,?,?,?,?,?,?,?,?,'prepared',NULL)", (*self.scope, *values))

    def request(self, key, db=None):
        rows = self.rows('requests', 'AND request_key=?', (key,), db)
        if len(rows) != 1 or hashlib.sha256(rows[0]['body']).hexdigest() != rows[0]['body_sha256']:
            raise ValueError('scoped_rescue_request_integrity_invalid')
        return rows[0]

    def start(self, key, checkpoint):
        with self.store.transaction() as db:
            checkpoint(db); row = self.request(key, db)
            if row['phase'] != 'prepared': raise ValueError('scoped_rescue_lookup_only')
            db.execute("UPDATE scoped_rescue_requests SET phase='started' WHERE request_key=?", (key,))
            return row

    def observe(self, key, value):
        row = self.request(key); r = value['request']; meta = json.loads(row['metadata'])
        if (r['request_uuid'] != key or r['operation'] != row['operation']
                or r['input_hash'] != digest({'operation': row['operation'], **{k:v for k,v in meta.items() if k != 'request_uuid'}}, 'ih1_')):
            raise ValueError('scoped_rescue_response_input_changed')
        field = 'persistence' if row['operation'] == 'meal_replace' else 'outcome'
        positive = 'saved' if field == 'persistence' else 'applied'
        with self.store.transaction() as db:
            old = json.loads(self.request(key, db)['observation'] or '{}')
            terminal=(r[field]=='saved' or r[field]=='not_saved' and r.get('status')=='failed') if field=='persistence' else r[field] in ('applied','rejected')
            old_terminal=old.get('terminal',old.get(field)==positive)
            effective={field:r[field],'terminal':terminal,'last_status':r.get('status'),'last_stage':r.get('stage')}
            # processing/not_savedは未保存の確定ではない。確定した結果は未知応答で降格しない。
            if old_terminal:effective.update({field:old[field],'terminal':True})
            if r[field]==positive:effective.update({field:positive,'terminal':True})
            if old.get('conflict') or old_terminal and terminal and old[field] != r[field]:
                effective['conflict'] = True
            self.event('response', key, value, db)
            db.execute("UPDATE scoped_rescue_requests SET phase='lookup_only',observation=? WHERE request_key=?", (canonical(effective).decode(), key))
            if field == 'persistence' and effective[field] == 'saved' and old.get(field) != 'saved':
                from .replacement_bundle import invalidate_saved
                invalidate_saved(db, self.scope[1]); job = self.job(db)
                changed = db.execute("UPDATE publication_holds SET state='saved_pending_review',reason='scoped_rescue_new_review_required' WHERE target_date=? AND owner=? AND epoch=? AND state='held'", (self.scope[1],job['owner'],job['epoch'])).rowcount
                if changed != 1: raise ValueError('scoped_rescue_hold_changed')
            if effective.get('conflict'):
                # 正本savedは維持し、矛盾が後から判明した新版も承認解除へ進めない。
                from .replacement_bundle import invalidate_saved
                invalidate_saved(db,self.scope[1]);job=self.job(db)
                db.execute("UPDATE replacement_bundles SET state='reconciliation_required' WHERE target_date=?",(self.scope[1],))
                db.execute("UPDATE publication_holds SET state=?,reason='scoped_rescue_reconciliation_required' WHERE target_date=? AND owner=? AND epoch=?",
                    ('saved_pending_review' if self.saved(db) else 'held',self.scope[1],job['owner'],job['epoch']))
        return effective
