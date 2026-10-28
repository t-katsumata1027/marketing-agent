"""日・枠の共通試行と日内費用を送信前に予約する。仮の承認額を作らない。"""
import json
from datetime import timedelta
from .domain import approval_time,date
from .replacement_contract import digest,stamp

class ReplacementPolicy:
    def __init__(self,store,policy,clock):
        self.store,self.policy,self.clock=store,policy,clock
        required=('approval_digest','currency','limit_units','opening_exposure_units','server_allocation_units','call_bounds',
                  'cost_bounds_verified','existing_usage_verified','server_allocation_verified','policy_version')
        self.waived = policy.get('cost_control_mode') == 'waived'
        if self.waived:
            import re
            if (any(k not in policy for k in required)
                    or any(policy[k] is not None for k in ('currency','limit_units','opening_exposure_units','server_allocation_units'))
                    or policy['cost_bounds_verified'] is not False
                    or policy.get('approval_reference_digest') != policy['approval_digest']
                    or not re.fullmatch('[0-9a-f]{64}',policy['approval_digest'])
                    or policy.get('analysis_provider') != 'gemini' or policy.get('model') != 'gemini-3.1-flash-lite'
                    or policy.get('max_output_tokens') != 2048 or not policy['policy_version']
                    or not isinstance(policy['call_bounds'],dict) or not policy['call_bounds']
                    or any(v is not None for v in policy['call_bounds'].values())):
                raise ValueError('budget_unconfigured')
        else:
            if any(k not in policy for k in required) or any(policy[k] is not True for k in ('cost_bounds_verified','existing_usage_verified','server_allocation_verified')):
                raise ValueError('budget_unconfigured')
            for key in ('limit_units','opening_exposure_units','server_allocation_units'):
                if type(policy[key]) is not int or policy[key]<0:raise ValueError('budget_unconfigured')
            if len(policy['approval_digest'])!=64 or not policy['currency'] or not policy['policy_version']:
                raise ValueError('budget_unconfigured')
            if any(type(v) is not int or v<0 for v in policy['call_bounds'].values()):raise ValueError('budget_unconfigured')
        self.policy_digest=digest(policy)
        with store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_budget_days (
                target_date TEXT PRIMARY KEY, policy_digest TEXT NOT NULL, policy TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_waived_calls (
                call_key TEXT PRIMARY KEY,target_date TEXT NOT NULL,kind TEXT NOT NULL,max_units INTEGER CHECK(max_units IS NULL),
                state TEXT NOT NULL CHECK(state IN ('reserved','complete','unknown')))''')
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_cost_reservations (
                call_key TEXT PRIMARY KEY,target_date TEXT NOT NULL,kind TEXT NOT NULL,units INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('reserved','complete','unknown')))''')
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_attempts (
                target_date TEXT NOT NULL,slot TEXT NOT NULL,ordinal INTEGER NOT NULL,first_started REAL NOT NULL,
                deadline REAL NOT NULL,receipt TEXT NOT NULL,PRIMARY KEY(target_date,slot,ordinal))''')
            db.execute('''CREATE TABLE IF NOT EXISTS preparation_steps (
                target_date TEXT NOT NULL,step TEXT NOT NULL,digest TEXT NOT NULL,state TEXT NOT NULL,
                result TEXT,started REAL NOT NULL,PRIMARY KEY(target_date,step))''')
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_origin_receipts (
                target_date TEXT NOT NULL,slot TEXT NOT NULL,ordinal INTEGER NOT NULL,
                owner TEXT NOT NULL,epoch INTEGER NOT NULL,origin TEXT NOT NULL,origin_digest TEXT NOT NULL,
                receipt TEXT NOT NULL,PRIMARY KEY(target_date,slot,ordinal))''')
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_probe_claims (
                retry_at TEXT PRIMARY KEY,owner TEXT NOT NULL,epoch INTEGER NOT NULL,target_date TEXT NOT NULL)''')

    def _day(self,db,target):
        old=db.execute('SELECT policy_digest FROM replacement_budget_days WHERE target_date=?',(target,)).fetchone()
        if old and old[0]!=self.policy_digest:raise ValueError('replacement_budget_policy_changed')
        if self.waived:
            if not old:db.execute('INSERT INTO replacement_budget_days VALUES(?,?,?)',(target,self.policy_digest,json.dumps(self.policy)))
            return
        if not old:
            db.execute('INSERT INTO replacement_budget_days VALUES(?,?,?)',(target,self.policy_digest,json.dumps(self.policy)))
            db.execute("INSERT INTO replacement_cost_reservations VALUES(?,?,?,?,'reserved')",('server-allocation:'+target,target,'server_allocation',self.policy['server_allocation_units']))
            db.execute("INSERT INTO replacement_cost_reservations VALUES(?,?,?,?,'unknown')",('opening-exposure:'+target,target,'opening_exposure',self.policy['opening_exposure_units']))
        if db.execute('SELECT sum(units) FROM replacement_cost_reservations WHERE target_date=?',(target,)).fetchone()[0]>self.policy['limit_units']:
            raise ValueError('budget_exceeded')

    def reserve(self,target,call_key,kind):
        rescue_kinds={'collection_register','collection_read','collection_review','collection_capture','collection_structured'}
        if kind not in self.policy['call_bounds'] and not (self.waived and kind in rescue_kinds):raise ValueError('budget_unconfigured')
        with self.store.transaction() as db:
            self._day(db,target)
            if self.waived:
                if db.execute('SELECT 1 FROM replacement_waived_calls WHERE call_key=?',(call_key,)).fetchone():raise ValueError('replacement_call_lookup_only')
                db.execute("INSERT INTO replacement_waived_calls VALUES(?,?,?,NULL,'reserved')",(call_key,target,kind))
                return
            if db.execute('SELECT 1 FROM replacement_cost_reservations WHERE call_key=?',(call_key,)).fetchone():raise ValueError('replacement_call_lookup_only')
            total=db.execute('SELECT sum(units) FROM replacement_cost_reservations WHERE target_date=?',(target,)).fetchone()[0]
            units=self.policy['call_bounds'][kind]
            if total+units>self.policy['limit_units']:raise ValueError('budget_exceeded')
            db.execute("INSERT INTO replacement_cost_reservations VALUES(?,?,?,?,'reserved')",(call_key,target,kind,units))

    def called(self,key,success):
        with self.store.transaction() as db:
            table='replacement_waived_calls' if self.waived else 'replacement_cost_reservations'
            db.execute('UPDATE '+table+' SET state=? WHERE call_key=?',('complete' if success else 'unknown',key))

    def attempt(self,target,slot,*,binding=None):
        now=self.clock();existing_deadline=approval_time(date.fromisoformat(target))
        if slot not in ('breakfast','lunch','dinner') or now>=existing_deadline:raise ValueError('deadline_exceeded')
        with self.store.transaction() as db:
            from .publication_hold import exists
            if exists(db,'rescue_jobs') and db.execute('SELECT 1 FROM rescue_jobs WHERE target_date=?',(target,)).fetchone():
                raise ValueError('rescue_existing_reservation')
            if exists(db,'scoped_rescue_jobs') and db.execute('SELECT 1 FROM scoped_rescue_jobs WHERE target_date=?',(target,)).fetchone():
                raise ValueError('rescue_existing_reservation')
            self._day(db,target)
            origin=None
            if binding is not None:
                from .replacement_origin import originals
                origin=originals(db,target,slot,binding)
                origin['server_allocation_units']=self.policy['server_allocation_units']
                circuit=binding['preconditions'].get('circuit')
                if circuit and circuit['state']=='half_open' and not circuit['recovery_required']:
                    row=db.execute('SELECT circuit FROM replacement_circuit_observations WHERE singleton=1').fetchone()
                    old=json.loads(row[0]) if row else None
                    if not old or old['state']!='open' or old['generation']!=circuit['generation']:
                        raise ValueError('replacement_recovery_required')
                    circuit=old
                if circuit and circuit['state']=='open':
                    from .replacement_contract import timestamp
                    if circuit['recovery_required'] or not circuit['retry_at'] or timestamp(circuit['retry_at'])>now:
                        raise ValueError('replacement_cooldown')
                    if db.execute('SELECT 1 FROM replacement_probe_claims WHERE retry_at=?',(circuit['retry_at'],)).fetchone():
                        raise ValueError('replacement_recovery_required')
                    db.execute('INSERT INTO replacement_probe_claims VALUES(?,?,?,?)',
                        (circuit['retry_at'],binding['owner'],binding['epoch'],target))
            rows=[]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='preparation_steps'").fetchone():
                rows=list(db.execute('SELECT * FROM preparation_steps WHERE target_date=? AND step LIKE ?',(target,'meal_replan:'+slot+':%')))
            old_ordinals=[];starts=[]
            for r in rows:
                n=r['step'].split(':')[-1]
                if n not in ('1','2') or r['state'] not in ('complete','known_rejected'):raise ValueError('migration_unverified')
                if r['state']=='known_rejected':
                    from .generation_receipts import rejection
                    rejection(r)
                if not 0<r['started']<=now.timestamp():raise ValueError('migration_unverified')
                old_ordinals.append(int(n));starts.append(r['started'])
            attempts=list(db.execute('SELECT ordinal,first_started,deadline,receipt FROM replacement_attempts WHERE target_date=? AND slot=?',(target,slot)))
            for r in rows:
                value=db.execute('SELECT result FROM preparation_steps WHERE target_date=? AND step=?',(target,r['step'])).fetchone()
                if json.loads(value[0] or '{}').get('reservation_only') and int(r['step'].split(':')[-1]) not in {a['ordinal'] for a in attempts}:
                    raise ValueError('migration_unverified')
            if any(not 0<r['first_started']<=now.timestamp() or r['ordinal'] not in old_ordinals for r in attempts):
                raise ValueError('migration_unverified')
            for r in attempts:
                receipt=json.loads(r['receipt'])
                step=db.execute('SELECT digest,result FROM preparation_steps WHERE target_date=? AND step=?',(target,f"meal_replan:{slot}:{r['ordinal']}")).fetchone()
                if step['digest']!=digest(receipt) or json.loads(step['result']).get('receipt')!=receipt:
                    raise ValueError('migration_unverified')
                if binding is not None:
                    from .replacement_origin import verify_receipt
                    verify_receipt(db,target,slot,receipt,binding['owner'],binding['epoch'])
            used=sorted(set(old_ordinals)|{r['ordinal'] for r in attempts})
            if used and used!=list(range(1,max(used)+1)):raise ValueError('migration_unverified')
            if len(used)>=2:raise ValueError('replan_limit_exceeded')
            first=min(starts+[r['first_started'] for r in attempts]) if used else now.timestamp()
            from .replacement_contract import timestamp
            first=timestamp(stamp(now.fromtimestamp(first,now.tzinfo))).timestamp()
            prior_deadlines=[timestamp(json.loads(r['receipt'])['existing_preparation_deadline_at']).timestamp() for r in attempts]
            existing_deadline=now.fromtimestamp(min([existing_deadline.timestamp(),*prior_deadlines]),now.tzinfo)
            deadline=min(existing_deadline.timestamp(),first+900)
            if attempts and any(r['first_started']!=first or r['deadline']!=deadline for r in attempts):
                raise ValueError('migration_unverified')
            if now.timestamp()+60>deadline:raise ValueError('deadline_exceeded')
            ordinal=len(used)+1;step=f'meal_replan:{slot}:{ordinal}'
            receipt={'prior_consumed_ordinals':used,'attempt_ordinal':ordinal,'agent_step_key':step,
                'first_replan_started_at':stamp(now.fromtimestamp(first,now.tzinfo)),
                'existing_preparation_deadline_at':stamp(existing_deadline),'policy_version':self.policy['policy_version'],
                'budget_policy_digest':self.policy_digest,'fee_reservation_digest':digest({'target':target,'allocation':self.policy['server_allocation_units'],'policy':self.policy_digest})}
            if origin is not None:
                # Schemaのreceipt形状を変えず、実原本・所有版を既存の予約digestへ結び付ける。
                receipt['fee_reservation_digest']=digest({'origin_digest':digest(origin),'policy':self.policy_digest,
                    'target':target,'slot':slot,'ordinal':ordinal,'owner':binding['owner'],'epoch':binding['epoch'],
                    'allocation':self.policy['server_allocation_units']})
            receipt['receipt_digest']=digest(receipt)
            db.execute('INSERT INTO replacement_attempts VALUES(?,?,?,?,?,?)',(target,slot,ordinal,first,deadline,json.dumps(receipt)))
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",(target,step,digest(receipt),json.dumps({'reservation_only':True,'receipt':receipt}),now.timestamp()))
            if origin is not None:
                db.execute('INSERT INTO replacement_origin_receipts VALUES(?,?,?,?,?,?,?,?)',
                    (target,slot,ordinal,binding['owner'],binding['epoch'],json.dumps(origin,sort_keys=True),digest(origin),json.dumps(receipt,sort_keys=True)))
            return receipt,deadline
