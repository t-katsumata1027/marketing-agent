"""将来日の欠けた枠だけを通常登録し、既存品質保留と日公開holdを保持する。"""
from contextvars import ContextVar
from datetime import timedelta
import hashlib,json,os,re
from pathlib import Path
from .domain import JST,approval_time,date,slot_time
from .preparation import fingerprint
from .publication_hold import PublicationHeld

_scope=ContextVar('future_replacement_collection',default=None)


class Collection:
    def __init__(self,store,base,draft,saved,meal,ai,host,policy,inventory,clock,admit):
        self.store,self.base,self.draft,self.saved=store,Path(base),draft,saved
        self.meal,self.ai,self.host,self.policy,self.inventory,self.clock=meal,ai,host,policy,inventory,clock
        self.e=None
        self.admit=admit

    def checkpoint(self,db=None):
        if db is None:
            with self.store.transaction() as connection:return self.checkpoint(connection)
        e=self.e;target=e['target_date'];now=self.clock()
        if (os.getenv('MINA_REPLACEMENT_COLLECTION_ENABLED','false').lower()!='true'
                or any(os.getenv(k,'false').lower()!='true' for k in ('MINA_MEAL_REPLACEMENT_ENABLED','MINA_LIVE_WRITES'))
                or target!=(now.astimezone(JST).date()+timedelta(days=1)).isoformat()):
            raise ValueError('replacement_collection_disabled')
        end=min(e['deadline'],approval_time(date.fromisoformat(target)).timestamp())
        rows=list(db.execute("SELECT step,state,started FROM preparation_steps WHERE target_date=? AND step LIKE ?",(target,'meal_replan:'+e['slot']+':%')))
        ordinals=[]
        for r in rows:
            n=r['step'].split(':')[-1]
            if n not in ('1','2') or r['state']!='complete' or not 0<r['started']<=now.timestamp():
                raise ValueError('migration_unverified')
            ordinals.append(int(n));end=min(end,r['started']+900)
        if sorted(ordinals)!=list(range(1,len(ordinals)+1)) or len(ordinals)>=2:
            raise ValueError('replan_limit_exceeded')
        if now.timestamp()>=end:raise ValueError('deadline_exceeded')
        h=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(target,)).fetchone()
        episode=db.execute('SELECT * FROM replacement_episodes WHERE target_date=?',(target,)).fetchone()
        if (not h or (h['owner'],h['epoch'],h['state'])!=(e['owner'],e['epoch'],'held')
                or not episode or episode['stage']!='checking' or episode['old_source']!='{}'
                or any(episode[k] is not None for k in ('prepare_uuid','discard_uuid','commit_uuid'))):raise PublicationHeld()
        for path,sha in self.files.items():
            p=Path(path)
            if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:
                raise ValueError('replacement_collection_source_changed')
        for step,original in self.original_steps.items():
            current=db.execute('SELECT * FROM preparation_steps WHERE target_date=? AND step=?',(target,step)).fetchone()
            if not current or dict(current)!=original:raise ValueError('replacement_collection_source_changed')
        for original in self.original_requests:
            current=db.execute('SELECT request_key,operation,slot,input_digest,image_sha256,phase,observation FROM mina_requests WHERE request_key=?',(original['request_key'],)).fetchone()
            if not current or dict(current)!=original:raise ValueError('replacement_collection_source_changed')
        return end

    def claim(self,db,request):
        """通常M1の送信claimと同じTXで、欠けていた枠の操作を各1回だけ拘束する。"""
        self.checkpoint(db);target=self.e['target_date'];slot=request['slot'];op=request['operation']
        if request['target_date']!=target or slot not in self.missing or op not in ('meal_register','meal_correction'):
            raise PublicationHeld()
        payload=request['payload'];image=self.saved['images'][slot]
        if (payload.get('contract_version')!='mina-write-v1' or payload.get('comment')!=self.draft['meals'][slot]['comment']
                or not re.fullmatch('sv1_[0-9a-f]{64}',payload.get('expected_state_version',''))):raise PublicationHeld()
        if op=='meal_register':
            if (request['image_sha256']!=image['sha256'] or payload.get('meal_type')!=slot
                    or payload.get('logged_at')!=slot_time(date.fromisoformat(target),slot).isoformat()
                    or payload.get('filename')!=f'{target}-{slot}.jpg'):raise PublicationHeld()
        else:
            registered=db.execute("SELECT r.phase,r.observation FROM replacement_collection_claims c JOIN mina_requests r USING(request_key) WHERE c.target_date=? AND c.slot=? AND c.operation='meal_register'",(target,slot)).fetchone()
            step=db.execute("SELECT result FROM preparation_steps WHERE target_date=? AND step=? AND state='complete'",(target,'meal_register:'+slot)).fetchone()
            result=json.loads(step[0])['result'] if step else None
            if (not registered or registered['phase']!='complete' or json.loads(registered['observation'] or '{}').get('persistence')!='saved'
                    or not result or result['image_sha256']!=image['sha256'] or payload.get('share_url')!=result['share']['url']
                    or payload.get('reason')!='codex_secondary_review'):raise PublicationHeld()
        old=db.execute('SELECT request_key,input_digest FROM replacement_collection_claims WHERE target_date=? AND slot=? AND operation=?',(target,slot,op)).fetchone()
        if old is not None:raise ValueError('replacement_collection_already_claimed')
        db.execute('INSERT INTO replacement_collection_claims VALUES(?,?,?,?,?)',(target,slot,op,request['request_key'],request['input_digest']))

    def __call__(self,e):
        """全ページ予約確認後に呼ぶ。保存済み枠の再登録・保留解除・日次生成を行わない。"""
        from .registration import assert_day_state,register_day
        from .m1_contract import validate_write,completed_request
        from .request_ledger import RequestLedger
        self.e=e;target=e['target_date'];completed={}
        from .readiness import read_json
        if (read_json(self.base/'draft.json')!=self.draft or read_json(self.base/'drive.json')!=self.saved
                or self.draft.get('target_date')!=target or self.saved.get('target_date')!=target):
            raise ValueError('replacement_collection_source_changed')
        self.files={str(self.base/'draft.json'):hashlib.sha256((self.base/'draft.json').read_bytes()).hexdigest(),
                    str(self.base/'drive.json'):hashlib.sha256((self.base/'drive.json').read_bytes()).hexdigest()}
        for image in self.saved['images'].values():self.files[image['path']]=image['sha256']
        with self.store.transaction() as db:
            rows={r['step']:dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=?',(target,))}
            requests=[dict(r) for r in db.execute('SELECT request_key,operation,slot,input_digest,image_sha256,phase,observation FROM mina_requests WHERE target_date=?',(target,))]
        self.original_steps,self.original_requests=rows,requests
        if any(r['phase']!='complete' for r in requests) or any(r['state']!='complete' for r in rows.values()):
            raise ValueError('replacement_existing_request_unresolved')
        ledger=RequestLedger(self.store)
        for slot in ('breakfast','lunch','dinner'):
            step=rows.get('meal_correction:'+slot) or rows.get('meal_register:'+slot)
            if not step:continue
            response=json.loads(step['result']);op='meal_correction' if 'meal_correction:'+slot in rows else 'meal_register'
            key=json.loads(rows[('correction_key:' if op=='meal_correction' else 'meal_request_key:')+slot]['result'])['key']
            ledger.restore(key)
            validate_write(response,slot,target,self.host,operation=op,logged_at=slot_time(date.fromisoformat(target),slot).isoformat(),input_comment=self.draft['meals'][slot]['comment'])
            completed_request(self.meal.request_state(key,op),key,op,response['result']);completed[slot]=response
        assert_day_state(self.meal.day_state(target),target,completed,self.clock())
        self.missing=tuple(s for s in ('breakfast','lunch','dinner') if s not in completed)
        self.verify_target(rows,completed)
        self.verify_others(rows,completed)
        if not self.missing:
            self.expected_results={slot:value['result']['result_version'] for slot,value in completed.items()}
            return
        effective_deadline=self.checkpoint()
        if self.clock().timestamp()+180>=effective_deadline:raise ValueError('deadline_exceeded')
        # boundedへの未検証の課金割当を追加しない。既承認waivedの操作予約を維持する。
        if not self.policy.waived:raise ValueError('budget_unconfigured')
        self.admit()
        self.checkpoint()
        binding={'owner':e['owner'],'epoch':e['epoch'],'missing':self.missing,'files':self.files,
                 'steps_sha256':fingerprint(rows),'requests_sha256':fingerprint(requests)}
        with self.store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS replacement_collection_jobs (target_date TEXT PRIMARY KEY,binding TEXT NOT NULL,state TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS replacement_collection_claims (target_date TEXT,slot TEXT,operation TEXT,request_key TEXT UNIQUE,input_digest TEXT,PRIMARY KEY(target_date,slot,operation))')
            if db.execute('SELECT 1 FROM replacement_collection_jobs WHERE target_date=?',(target,)).fetchone():
                raise ValueError('replacement_collection_reconciliation_required')
            self.checkpoint(db)
            db.execute("INSERT INTO replacement_collection_jobs VALUES(?,?,'started')",(target,json.dumps(binding,sort_keys=True)))
        token=_scope.set(self)
        try:
            def guard():self.checkpoint()
            def port(client):return CollectionPort(client,self)
            def capture(url,host):
                from .ogp import capture_ogp
                guard();return capture_ogp(url,host)
            register_day(self.store,self.draft,self.saved,self.base/'ogp',port(self.meal),port(self.ai),self.host,
                         capture=capture,allow_correction=True,collection_slots=self.missing)
            self.checkpoint()
            with self.store.transaction() as db:db.execute("UPDATE replacement_collection_jobs SET state='complete' WHERE target_date=?",(target,))
        except BaseException:
            with self.store.transaction() as db:db.execute("UPDATE replacement_collection_jobs SET state='held' WHERE target_date=?",(target,))
            raise
        finally:_scope.reset(token)
        # 新規保存も同じ正本・元UUIDと再照合し、正式preconditionとの比較値へ固定する。
        self(e)

    def verify_preconditions(self,p):
        expected={m['expected']['target_slot']:m['expected']['target_result_version'] for m in p['meals']}
        if expected!=self.expected_results:raise ValueError('replacement_source_changed')

    def verify_target(self,rows,completed):
        """保存済み対象の現在の品質保留を、同じ正本・画像・点検入力へ束縛する。"""
        from .generation import meal_review_policy_sha256
        slot=self.e['slot'];response=completed.get(slot)
        corrected='meal_correction:'+slot in rows
        review=rows.get('meal_review:'+slot+(':corrected' if corrected else ''))
        captured=rows.get('ogp_capture:'+slot+('-corrected' if corrected else ''))
        if not response or slot in self.missing or not review or not captured:raise ValueError('replacement_quality_hold_required')
        verdict=json.loads(review['result']);capture=json.loads(captured['result'])
        expected={'image_sha256':self.saved['images'][slot]['sha256'],'meal':self.draft['meals'][slot],
                  'response':response,'ogp_digest':capture['digest'],'policy_sha256':meal_review_policy_sha256()}
        if (review['state']!='complete' or captured['state']!='complete' or verdict.get('status')!='hold'
                or not verdict.get('reason_codes') or not set(verdict['reason_codes'])<={'image_analysis_mismatch','nutrition_semantic_mismatch'}
                or review['digest']!=fingerprint(expected)):
            raise ValueError('replacement_quality_hold_required')

    def verify_others(self,rows,completed):
        """置換対象の保留は維持し、他の保存済み枠の実点検を正本へ束縛する。"""
        from .generation import meal_review_policy_sha256
        from .m1_contract import validate_write
        for slot,response in completed.items():
            if slot==self.e['slot']:continue
            corrected='meal_correction:'+slot in rows
            review=rows.get('meal_review:'+slot+(':corrected' if corrected else ''))
            captured=rows.get('ogp_capture:'+slot+('-corrected' if corrected else ''))
            ogp=rows.get('ogp_review:'+slot)
            if not review or not captured or not ogp:raise ValueError('replacement_other_review_unverified')
            capture=json.loads(captured['result'])
            path=Path(capture['path'])
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=capture['preview_sha256']:
                raise ValueError('replacement_other_review_unverified')
            expected={'image_sha256':self.saved['images'][slot]['sha256'],'meal':self.draft['meals'][slot],
                      'response':response,'ogp_digest':capture['digest'],'policy_sha256':meal_review_policy_sha256()}
            normalized=validate_write(response,slot,self.e['target_date'],self.host,operation='meal_correction' if corrected else 'meal_register',
                        logged_at=slot_time(date.fromisoformat(self.e['target_date']),slot).isoformat(),input_comment=self.draft['meals'][slot]['comment'])
            expected_ogp={'image_sha256':self.saved['images'][slot]['sha256'],'normalized':normalized,'ogp_digest':capture['digest']}
            if (any(r['state']!='complete' or json.loads(r['result'])!={'status':'publishable','reason_codes':[]} for r in (review,ogp))
                    or review['digest']!=fingerprint(expected) or ogp['digest']!=fingerprint(expected_ogp)):
                raise ValueError('replacement_other_review_unverified')


class CollectionPort:
    """費用不明を返却せず、各外部操作前後に同じ許可を検査する。"""
    def __init__(self,client,collection):self.client,self.collection=client,collection
    def __getattr__(self,name):
        value=getattr(self.client,name)
        if not callable(value):return value
        def invoke(*args,**kwargs):
            import uuid
            from .external_guard import managed_call
            c=self.collection;c.checkpoint();key=str(uuid.uuid4())
            kind='collection_review' if name.startswith('review') else 'collection_structured' if name=='structured' else 'collection_register' if name in ('register','json_write') else 'collection_read'
            c.policy.reserve(c.e['target_date'],key,kind)
            try:result=managed_call(lambda:value(*args,**kwargs))
            except BaseException:c.policy.called(key,False);raise
            c.policy.called(key,True);c.checkpoint();return result
        return invoke


def require_claim(db,request):
    scope=_scope.get()
    if scope is None:return False
    scope.claim(db,request);return True
