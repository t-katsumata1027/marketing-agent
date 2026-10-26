"""本人承認に束縛した昼夕収集。公開holdを解除せずM1送信claimと排他する。"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import re
from datetime import UTC, datetime
from .clients import MealClient
from .publication_hold import exists, PublicationHeld
from .replacement_contract import canonical, digest
from .replacement_client import timestamp
from .rescue_contract import TARGET, SCHEMA_SHA

_scope = ContextVar('rescue_collection', default=None)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rescue_collection_permits (
        target_date TEXT PRIMARY KEY, owner TEXT NOT NULL, epoch INTEGER NOT NULL,
        approval TEXT NOT NULL, approval_digest TEXT NOT NULL, not_after TEXT NOT NULL,
        code_commit TEXT NOT NULL, images TEXT NOT NULL, created TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS rescue_collection_claims (
        target_date TEXT NOT NULL, slot TEXT NOT NULL, operation TEXT NOT NULL,
        request_key TEXT NOT NULL UNIQUE, input_digest TEXT NOT NULL, image_sha256 TEXT NOT NULL,
        PRIMARY KEY(target_date,slot,operation))''')


class CollectionPermit:
    def __init__(self, store, clock):
        self.store, self.clock = store, clock

    def issue(self, approval, owner, epoch, code_commit, images):
        """承認artifactを原本ごと保持。同日許可の再発行で期限を延ばさない。"""
        required = {'target_date','operations','not_after','operator_approval_reference','schema_sha256','hold_owner','hold_epoch','code_commit','images','scope'}
        if (set(approval) != required or approval['target_date'] != TARGET
                or approval['operations'] != {'lunch':['meal_register','meal_correction'],
                                               'dinner':['meal_register','meal_correction']}
                or approval['schema_sha256'] != SCHEMA_SHA or approval['scope']!='same_day_lunch_dinner_collection'
                or approval['hold_owner']!=owner or approval['hold_epoch']!=epoch
                or approval['code_commit']!=code_commit or approval['images']!=images
                or not isinstance(approval['operator_approval_reference'], str)
                or not approval['operator_approval_reference'].strip()
                or not re.fullmatch('[0-9a-f]{40}', code_commit)
                or set(images) != {'lunch','dinner'}
                or any(not re.fullmatch('[0-9a-f]{64}', x) for x in images.values())):
            raise ValueError('rescue_collection_approval_invalid')
        end = timestamp(approval['not_after'])
        from .domain import JST
        if end.astimezone(JST).date().isoformat() != TARGET or not self.clock() < end:
            raise ValueError('rescue_collection_deadline')
        MealClient.request_key(owner)
        with self.store.transaction() as db:
            initialize(db)
            hold = db.execute('SELECT * FROM publication_holds WHERE target_date=?', (TARGET,)).fetchone()
            if not hold or (hold['owner'],hold['epoch'],hold['state']) != (owner,epoch,'held'):
                raise PublicationHeld()
            previous = db.execute('SELECT * FROM rescue_collection_permits WHERE target_date=?', (TARGET,)).fetchone()
            values = (TARGET,owner,epoch,canonical(approval).decode(),digest(approval),approval['not_after'],
                      code_commit,canonical(images).decode())
            if previous:
                if tuple(previous[k] for k in ('target_date','owner','epoch','approval','approval_digest',
                        'not_after','code_commit','images')) != values:
                    raise ValueError('rescue_collection_permit_immutable')
                return
            # 朝食以外が既に存在する許可を推測で作らない。実正本照合は呼出側も実施する。
            if exists(db, 'mina_requests') and db.execute(
                    "SELECT 1 FROM mina_requests WHERE target_date=? AND (slot<>'breakfast' OR phase<>'complete')",
                    (TARGET,)).fetchone():
                raise ValueError('rescue_collection_source_unresolved')
            db.execute('INSERT INTO rescue_collection_permits VALUES(?,?,?,?,?,?,?,?,?)',
                       (*values,self.clock().isoformat()))

    def checkpoint(self, db=None):
        import os
        if any(os.getenv(k,'false').lower()!='true' for k in ('MINA_SAME_DAY_RESCUE_ENABLED','MINA_MEAL_REPLACEMENT_ENABLED','MINA_LIVE_WRITES')):
            raise ValueError('rescue_collection_disabled')
        if db is None:
            with self.store.transaction() as connection:
                return self.checkpoint(connection)
        if not exists(db,'rescue_collection_permits'):
            raise ValueError('rescue_collection_permit_missing')
        p = db.execute('SELECT * FROM rescue_collection_permits WHERE target_date=?', (TARGET,)).fetchone()
        h = db.execute('SELECT * FROM publication_holds WHERE target_date=?', (TARGET,)).fetchone()
        if (not p or not h or (h['owner'],h['epoch'],h['state']) != (p['owner'],p['epoch'],'held')
                or digest(json.loads(p['approval'])) != p['approval_digest']):
            raise PublicationHeld()
        if self.clock() >= timestamp(p['not_after']):
            raise ValueError('rescue_collection_deadline')
        return dict(p)

    @contextmanager
    def scope(self):
        self.checkpoint()
        token = _scope.set(self)
        try: yield
        finally: _scope.reset(token)

    def claim(self, db, request):
        """RequestLedgerのprepared→dispatchingと同じSQLite TXで検査・消費。"""
        p = self.checkpoint(db)
        slot, op = request['slot'], request['operation']
        if request['target_date'] != TARGET or slot not in ('lunch','dinner') or op not in ('meal_register','meal_correction'):
            raise PublicationHeld()
        image_sha = json.loads(p['images'])[slot]
        if op == 'meal_register' and request['image_sha256'] != image_sha:
            raise ValueError('rescue_collection_image_changed')
        if op=='meal_register':
            from .domain import slot_time,date
            payload=request['payload']
            if (payload.get('contract_version')!='mina-write-v1' or payload.get('meal_type')!=slot
                    or payload.get('logged_at')!=slot_time(date.fromisoformat(TARGET),slot).isoformat()
                    or payload.get('filename')!=f'{TARGET}-{slot}.jpg'):
                raise ValueError('rescue_collection_target_changed')

        if op == 'meal_correction':
            registration = db.execute("SELECT r.* FROM rescue_collection_claims c JOIN mina_requests r USING(request_key) "
                "WHERE c.target_date=? AND c.slot=? AND c.operation='meal_register'", (TARGET,slot)).fetchone()
            if (not registration or registration['phase'] != 'complete'
                    or json.loads(registration['observation'] or '{}').get('persistence') != 'saved'):
                raise ValueError('rescue_collection_registration_unconfirmed')
            saved_step=db.execute("SELECT result FROM preparation_steps WHERE target_date=? AND step=? AND state='complete'",
                (TARGET,'meal_register:'+slot)).fetchone()
            result=json.loads(saved_step['result'])['result'] if saved_step else None
            if (not result or result['meal']['meal_type']!=slot or result['image_sha256']!=image_sha
                    or request['payload'].get('share_url')!=result['share']['url']
                    or request['payload'].get('comment')!=result['normalized_comment']
                    or request['payload'].get('reason')!='codex_secondary_review'):
                raise ValueError('rescue_collection_correction_target_changed')

        if not re.fullmatch('sv1_[0-9a-f]{64}', request['payload'].get('expected_state_version','')):
            raise ValueError('rescue_collection_state_required')
        old = db.execute('SELECT * FROM rescue_collection_claims WHERE target_date=? AND slot=? AND operation=?',
                         (TARGET,slot,op)).fetchone()
        values = (TARGET,slot,op,request['request_key'],request['input_digest'],image_sha)
        if old:
            if tuple(old) != values: raise ValueError('rescue_collection_already_consumed')
        else:
            db.execute('INSERT INTO rescue_collection_claims VALUES(?,?,?,?,?,?)', values)


class GuardedPort:
    """AI・保存・GET等の各外部呼出し直前に、許可の期限とholdを再検査する。"""
    def __init__(self, port, permit, policy, before_write=None):
        self.port, self.permit, self.policy, self.before_write = port, permit, policy, before_write

    def __getattr__(self, name):
        method = getattr(self.port, name)
        if not callable(method): return method
        def invoke(*args, **kwargs):
            self.permit.checkpoint()
            if name in ('register','json_write') and self.before_write is not None:
                self.before_write()
                self.permit.checkpoint()
            # 外部操作を共通費用台帳に固定し、不明になった費用を返却しない。
            import uuid
            key = str(uuid.uuid4())
            kind = 'collection_review' if name.startswith('review') else ('collection_structured' if name=='structured' else ('collection_register' if name in ('register','json_write') else 'collection_read'))
            self.policy.reserve(TARGET,key,kind)
            try:
                from .external_guard import managed_call
                result=managed_call(lambda:method(*args,**kwargs))
            except BaseException:
                self.policy.called(key,False)
                raise
            self.policy.called(key,True)
            self.permit.checkpoint()
            return result
        return invoke


def require_claim(db, request):
    scope = _scope.get()
    if scope is None: return False
    scope.claim(db,request)
    return True


def verify_menu_budget(store, draft):
    """登録前に完了した食品別概算の原本を再利用。予定kcalだけで承認しない。"""
    import math
    from .preparation import fingerprint
    slots=('breakfast','lunch','dinner')
    inputs={'foods':{slot:draft['meals'][slot]['foods'] for slot in slots}}
    sha=fingerprint(inputs)
    with store.transaction() as db:
        row=db.execute("SELECT state,result,digest FROM preparation_steps WHERE target_date=? AND step=?",
                       (TARGET,'menu_nutrition:'+sha)).fetchone()
    if not row or row['state']!='complete' or row['digest']!=fingerprint(inputs):
        raise ValueError('rescue_menu_budget_origin_missing')
    values=json.loads(row['result'])
    if set(values)!=set(slots) or any(not isinstance(values[s],list) or len(values[s])!=len(inputs['foods'][s])
            or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=10000 for v in values[s]) for s in slots):
        raise ValueError('rescue_menu_budget_origin_invalid')
    if not 1800<=sum(sum(values[s]) for s in slots)<=2000:
        raise ValueError('nutrition_budget_failed')
    return sha
