"""作成障害の追跡用集計。本文・UUID・画像・認証を診断応答へ含めない。"""
import json,re

def summarize(db,now):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'replacement_episodes' not in tables:return []
    def code(value):return value if isinstance(value,str) and re.fullmatch('[a-z_]+(?::[a-z_]+)?',value) else 'unconfirmed'
    result=[]
    for target,slot,stage,deadline,reason in db.execute('SELECT target_date,slot,stage,deadline,reason FROM replacement_episodes ORDER BY target_date DESC LIMIT 30'):
        item={'target_date':target,'slot':slot,'category':'creation','stage':code(stage),'reason':code(reason),
              'deadline_remaining_seconds':round(deadline-now.timestamp())}
        if 'replacement_attempts' in tables:item['attempt_ordinal']=db.execute('SELECT coalesce(max(ordinal),0) FROM replacement_attempts WHERE target_date=? AND slot=?',(target,slot)).fetchone()[0]
        if 'replacement_cost_reservations' in tables:item['cost_reservations']={state:{'count':count,'maximum_units':units} for state,count,units in db.execute('SELECT state,count(*),sum(units) FROM replacement_cost_reservations WHERE target_date=? GROUP BY state',(target,))}
        if 'meal_replacement_requests' in tables:
            item['requests']=[]
            for op,phase,observation in db.execute('SELECT operation,phase,observation FROM meal_replacement_requests WHERE target_date=?',(target,)):
                value=json.loads(observation) if observation else {}
                item['requests'].append({'operation':code(op),'phase':code(phase),'last_confirmed':code(value.get('persistence',value.get('outcome'))),
                    'conflict':value.get('conflict',False),'next_operation':'lookup' if phase!='prepared' else 'verify_unsent'})
        if 'replacement_stop_events' in tables:item['recent_stops']=[{'operation':code(op),'stage':code(st),'reason':code(r),'occurred_at':at} for op,st,r,at in db.execute('SELECT operation,stage,reason,occurred_at FROM replacement_stop_events WHERE target_date=? ORDER BY id DESC LIMIT 8',(target,))]
        result.append(item)
    return result
