"""新契約だけの送信前台帳。M1要求台帳や既存の保存成否を再定義しない。"""

import hashlib
import json

from .clients import MealClient


class ReplacementJournal:
    """確定Schemaから生成した送信バイトを保持する。外部書込みは実装しない。"""
    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS meal_replacement_requests (
                request_key TEXT PRIMARY KEY, operation TEXT NOT NULL, target_date TEXT NOT NULL,
                slot TEXT NOT NULL, hold_epoch INTEGER NOT NULL, owner TEXT NOT NULL,
                body BLOB NOT NULL, body_sha256 TEXT NOT NULL, phase TEXT NOT NULL,
                observation TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS meal_replacement_observations (
                id INTEGER PRIMARY KEY, request_key TEXT NOT NULL, observation TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS meal_replacement_envelopes (
                request_key TEXT NOT NULL, sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)''')

    def _owned(self, db, row):
        row=dict(row)
        hold = db.execute('SELECT * FROM publication_holds WHERE target_date=?', (row['target_date'],)).fetchone()
        if (not hold or hold['owner'] != row['owner'] or hold['epoch'] != row['hold_epoch'] or hold['state'] not in (('held','saved_pending_review') if row.get('operation')=='replacement_resume' else ('held',))):
            raise ValueError('replacement_hold_not_owned_or_blocked')

    def prepare(self, key, operation, target, slot, hold_epoch, owner, body):
        MealClient.request_key(key)
        if operation not in ('candidate_prepare', 'candidate_discard', 'meal_replace', 'replacement_resume') or slot not in ('breakfast','lunch','dinner'):
            raise ValueError('replacement_request_kind_invalid')
        if not isinstance(body, bytes) or not 0 < len(body) <= 5*1024*1024:
            raise ValueError('replacement_wire_body_invalid')
        MealClient.request_key(owner)
        if type(hold_epoch) is not int or hold_epoch <= 0:
            raise ValueError('replacement_hold_epoch_invalid')
        row = {'target_date':target,'owner':owner,'hold_epoch':hold_epoch,'operation':operation}
        digest = hashlib.sha256(body).hexdigest()
        with self.store.transaction() as db:
            self._owned(db, row)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='mina_requests'").fetchone() and db.execute('SELECT 1 FROM mina_requests WHERE request_key=?',(key,)).fetchone():
                raise ValueError('replacement_uuid_m1_collision')
            from .rescue_storage import reject_reserved_uuid
            reject_reserved_uuid(db,key)
            previous = db.execute('SELECT * FROM meal_replacement_requests WHERE request_key=?', (key,)).fetchone()
            binding = (operation,target,slot,hold_epoch,owner,body,digest)
            if previous:
                if tuple(previous[k] for k in ('operation','target_date','slot','hold_epoch','owner','body','body_sha256')) != binding:
                    raise ValueError('replacement_uuid_input_changed')
                return
            db.execute("INSERT INTO meal_replacement_requests VALUES(?,?,?,?,?,?,?,?,'prepared',NULL)", (key,*binding))

    def start(self, key):
        """送信予定UUIDを一度だけ確保。再起動後もstartedをpreparedへ戻さない。"""
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM meal_replacement_requests WHERE request_key=?', (key,)).fetchone()
            if not row:
                raise ValueError('replacement_request_missing')
            self._owned(db, row)
            if row['phase'] != 'prepared' or hashlib.sha256(row['body']).hexdigest() != row['body_sha256']:
                raise ValueError('replacement_request_lookup_only')
            db.execute("UPDATE meal_replacement_requests SET phase='started' WHERE request_key=?", (key,))
            return bytes(row['body'])

    def get(self,key):
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM meal_replacement_requests WHERE request_key=?',(key,)).fetchone()
            if not row or hashlib.sha256(row['body']).hexdigest()!=row['body_sha256']:
                raise ValueError('replacement_request_integrity_invalid')
            return dict(row)

    def envelope(self,key,value):
        """Schema検証済みの対象要求だけを追記し、入力hashの変化を拒否する。"""
        row=self.get(key);request=value['request']
        if request['request_uuid']!=key or request['operation']!=row['operation']:
            raise ValueError('replacement_lookup_target_changed')
        with self.store.transaction() as db:
            old=db.execute('SELECT payload FROM meal_replacement_envelopes WHERE request_key=? ORDER BY sequence LIMIT 1',(key,)).fetchone()
            if old and json.loads(old[0])['request']['input_hash']!=request['input_hash']:
                raise ValueError('replacement_lookup_input_hash_changed')
            db.execute('INSERT INTO meal_replacement_envelopes(request_key,payload) VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False,allow_nan=False)))
        return self.observe(key,**({'persistence':request['persistence']} if row['operation']=='meal_replace' else {'outcome':request['outcome']}))

    def observe(self, key, *, outcome=None, persistence=None):
        """Schema検証後の内部分類だけを記録。不明照会で既知のsaved/appliedを失わない。"""
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM meal_replacement_requests WHERE request_key=?', (key,)).fetchone()
            if not row or row['phase'] == 'prepared':
                raise ValueError('replacement_request_not_started')
            commit = row['operation'] == 'meal_replace'
            if (commit and (outcome is not None or persistence not in ('saved','not_saved','unknown'))
                    or not commit and (persistence is not None or outcome not in ('applied','rejected','processing','unknown'))):
                raise ValueError('replacement_observation_kind_invalid')
            field, value = ('persistence', persistence) if commit else ('outcome', outcome)
            observed = {field:value}
            previous = json.loads(row['observation']) if row['observation'] else {}
            known = ('saved','not_saved') if commit else ('applied','rejected')
            positive = 'saved' if commit else 'applied'
            effective = dict(previous if previous.get(field) in known else observed)
            if value == positive:
                effective[field] = positive
            if previous.get('conflict') or (previous.get(field) in known and value in known and previous[field] != value):
                effective['conflict'] = True
            db.execute('INSERT INTO meal_replacement_observations(request_key,observation) VALUES(?,?)',
                       (key, json.dumps(observed)))
            db.execute("UPDATE meal_replacement_requests SET phase='lookup_only',observation=? WHERE request_key=?",
                       (json.dumps(effective),key))
            if commit and effective.get('persistence') == 'saved' and previous.get('persistence')!='saved':
                from .replacement_bundle import invalidate_saved
                invalidate_saved(db,row['target_date'])
                db.execute("""UPDATE publication_holds SET state='saved_pending_review',reason='new_source_review_required'
                    WHERE target_date=? AND owner=? AND epoch=? AND state='held'""",
                           (row['target_date'],row['owner'],row['hold_epoch']))
            return effective
