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
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_cost_reservations (
                call_key TEXT PRIMARY KEY,target_date TEXT NOT NULL,kind TEXT NOT NULL,units INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('reserved','complete','unknown')))''')
            db.execute('''CREATE TABLE IF NOT EXISTS replacement_attempts (
                target_date TEXT NOT NULL,slot TEXT NOT NULL,ordinal INTEGER NOT NULL,first_started REAL NOT NULL,
                deadline REAL NOT NULL,receipt TEXT NOT NULL,PRIMARY KEY(target_date,slot,ordinal))''')
            db.execute('''CREATE TABLE IF NOT EXISTS preparation_steps (
                target_date TEXT NOT NULL,step TEXT NOT NULL,digest TEXT NOT NULL,state TEXT NOT NULL,
                result TEXT,started REAL NOT NULL,PRIMARY KEY(target_date,step))''')

    def _day(self,db,target):
        old=db.execute('SELECT policy_digest FROM replacement_budget_days WHERE target_date=?',(target,)).fetchone()
        if old and old[0]!=self.policy_digest:raise ValueError('replacement_budget_policy_changed')
        if not old:
            db.execute('INSERT INTO replacement_budget_days VALUES(?,?,?)',(target,self.policy_digest,json.dumps(self.policy)))
            db.execute("INSERT INTO replacement_cost_reservations VALUES(?,?,?,?,'reserved')",('server-allocation:'+target,target,'server_allocation',self.policy['server_allocation_units']))
            db.execute("INSERT INTO replacement_cost_reservations VALUES(?,?,?,?,'unknown')",('opening-exposure:'+target,target,'opening_exposure',self.policy['opening_exposure_units']))
        if db.execute('SELECT sum(units) FROM replacement_cost_reservations WHERE target_date=?',(target,)).fetchone()[0]>self.policy['limit_units']:
            raise ValueError('budget_exceeded')

    def reserve(self,target,call_key,kind):
        if kind not in self.policy['call_bounds']:raise ValueError('budget_unconfigured')
        with self.store.transaction() as db:
            self._day(db,target)
            if db.execute('SELECT 1 FROM replacement_cost_reservations WHERE call_key=?',(call_key,)).fetchone():raise ValueError('replacement_call_lookup_only')
            total=db.execute('SELECT sum(units) FROM replacement_cost_reservations WHERE target_date=?',(target,)).fetchone()[0]
            units=self.policy['call_bounds'][kind]
            if total+units>self.policy['limit_units']:raise ValueError('budget_exceeded')
            db.execute("INSERT INTO replacement_cost_reservations VALUES(?,?,?,?,'reserved')",(call_key,target,kind,units))

    def called(self,key,success):
        with self.store.transaction() as db:
            db.execute('UPDATE replacement_cost_reservations SET state=? WHERE call_key=?',('complete' if success else 'unknown',key))

    def attempt(self,target,slot):
        now=self.clock();existing_deadline=approval_time(date.fromisoformat(target))
        if slot not in ('breakfast','lunch','dinner') or now>=existing_deadline:raise ValueError('deadline_exceeded')
        with self.store.transaction() as db:
            self._day(db,target)
            rows=[]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='preparation_steps'").fetchone():
                rows=list(db.execute('SELECT step,state,started FROM preparation_steps WHERE target_date=? AND step LIKE ?',(target,'meal_replan:'+slot+':%')))
            old_ordinals=[];starts=[]
            for r in rows:
                n=r['step'].split(':')[-1]
                if n not in ('1','2') or r['state']!='complete':raise ValueError('migration_unverified')
                old_ordinals.append(int(n));starts.append(r['started'])
            attempts=list(db.execute('SELECT ordinal,first_started,deadline FROM replacement_attempts WHERE target_date=? AND slot=?',(target,slot)))
            used=sorted(set(old_ordinals)|{r['ordinal'] for r in attempts})
            if used and used!=list(range(1,max(used)+1)):raise ValueError('migration_unverified')
            if len(used)>=2:raise ValueError('replan_limit_exceeded')
            first=min(starts+[r['first_started'] for r in attempts]+[now.timestamp()])
            deadline=min(existing_deadline.timestamp(),first+900)
            if now.timestamp()+60>=deadline:raise ValueError('deadline_exceeded')
            ordinal=len(used)+1;step=f'meal_replan:{slot}:{ordinal}'
            receipt={'prior_consumed_ordinals':used,'attempt_ordinal':ordinal,'agent_step_key':step,
                'first_replan_started_at':stamp(now.fromtimestamp(first,now.tzinfo)),
                'existing_preparation_deadline_at':stamp(existing_deadline),'policy_version':self.policy['policy_version'],
                'budget_policy_digest':self.policy_digest,'fee_reservation_digest':digest({'target':target,'allocation':self.policy['server_allocation_units'],'policy':self.policy_digest})}
            receipt['receipt_digest']=digest(receipt)
            db.execute('INSERT INTO replacement_attempts VALUES(?,?,?,?,?,?)',(target,slot,ordinal,first,deadline,json.dumps(receipt)))
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",(target,step,digest(receipt),json.dumps({'reservation_only':True,'receipt':receipt}),now.timestamp()))
            return receipt,deadline
