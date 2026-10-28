"""10/28昼食の準備専用例外。提案時刻は公開許可として使わない。"""
import re
from datetime import timedelta
from .domain import JST, SLOTS
from .replacement_client import timestamp


KEY = 'scoped_preparation_only'
APPROVAL_PREFIX='【準備専用・公開停止中】時刻は案です。内容の承認だけではSNS予約・公開されません。\n'


def approval_notice(payload,source):
    """daemonがclaimする前のカード生成時点で、準備専用の表示を固定する。"""
    if KEY in (source or {}) and not payload.get('content','').startswith(APPROVAL_PREFIX):
        payload['content']=APPROVAL_PREFIX+payload.get('content','')
    return payload


def validate(window, target, slot, case, now):
    if (not isinstance(window, dict) or set(window) != {'target_date','target_slot','case_uuid','mode','not_after','proposed_times'}
            or (target,slot) != ('2026-10-28','lunch')
            or (window['target_date'],window['target_slot'],window['case_uuid'],window['mode']) != (target,slot,case,'prepare_only')
            or now.astimezone(JST).date().isoformat() != target):
        raise ValueError('scoped_preparation_scope_invalid')
    from .clients import MealClient
    MealClient.request_key(case)
    deadline=timestamp(window['not_after'])
    times=window['proposed_times']
    if (not isinstance(times,dict) or set(times)!=set(SLOTS) or now>=deadline
            or deadline.astimezone(JST).date().isoformat()!=target):
        raise ValueError('scoped_preparation_deadline')
    values=[timestamp(times[s]) for s in SLOTS]
    if (any(t.astimezone(JST).date().isoformat()!=target for t in values)
            or deadline+timedelta(seconds=60)>values[0] or values!=sorted(set(values))):
        raise ValueError('scoped_preparation_schedule_invalid')
    return deadline


def marker(window, slot):
    from .replacement_contract import digest
    return {'mode':'prepare_only','target_date':window['target_date'],'case_uuid':window['case_uuid'],
            'proposed_at':window['proposed_times'][slot],'window_sha256':digest(window)}


def proposed(source, target):
    """承認候補の表示時刻だけを返す。送信claimは別途常に拒否する。"""
    value=(source or {}).get(KEY)
    if value is None:return None
    if (not isinstance(value,dict) or set(value)!={'mode','target_date','case_uuid','proposed_at','window_sha256'}
            or value['mode']!='prepare_only' or value['target_date']!=target or target!='2026-10-28'
            or not re.fullmatch('[a-f0-9]{64}',value['window_sha256'])):
        raise ValueError('scoped_preparation_marker_invalid')
    from .clients import MealClient
    MealClient.request_key(value['case_uuid'])
    result=timestamp(value['proposed_at'])
    if result.astimezone(JST).date().isoformat()!=target:raise ValueError('scoped_preparation_marker_invalid')
    return result


def blocked(payload):
    """壊れたmarkerも公開を拒否する。公開holdの手動解除だけでは送信できない。"""
    return KEY in (payload.get('approval_source') or {}) or KEY in (payload.get('source') or {})


def locked(db,target=None):
    from .publication_hold import exists
    if not exists(db,'scoped_preparation_locks'):return False
    return db.execute('SELECT 1 FROM scoped_preparation_locks'+(' WHERE target_date=?' if target else ''),
                      (target,) if target else ()).fetchone() is not None


def seal(db,window,owner,epoch,xs,igs,now):
    from .replacement_contract import canonical,digest
    validate(window,window['target_date'],'lunch',window['case_uuid'],now)
    for source,slot,target in [(c.approval_source,c.slot,c.target_date) for c in xs]+[(c.source,c.slot,c.target_date) for c in igs]:
        if target!=window['target_date'] or source.get(KEY)!=marker(window,slot):raise ValueError('scoped_preparation_marker_invalid')
    db.execute('''CREATE TABLE IF NOT EXISTS scoped_preparation_locks (
        target_date TEXT PRIMARY KEY,case_uuid TEXT NOT NULL,owner TEXT NOT NULL,epoch INTEGER NOT NULL,
        window_json TEXT NOT NULL,candidates_digest TEXT NOT NULL)''')
    value=(window['target_date'],window['case_uuid'],owner,epoch,canonical(window).decode(),digest([c.digest for c in [*xs,*igs]]))
    old=db.execute('SELECT * FROM scoped_preparation_locks WHERE target_date=?',(window['target_date'],)).fetchone()
    if old and tuple(old)!=value:raise ValueError('scoped_preparation_marker_invalid')
    if not old:db.execute('INSERT INTO scoped_preparation_locks VALUES(?,?,?,?,?,?)',value)


def registered_marker(db,target,slot,source):
    if not locked(db,target):raise ValueError('scoped_preparation_marker_invalid')
    import json
    row=db.execute('SELECT window_json FROM scoped_preparation_locks WHERE target_date=?',(target,)).fetchone()
    expected=marker(json.loads(row['window_json']),slot)
    if source.get(KEY)!=expected:raise ValueError('scoped_preparation_marker_invalid')
    return expected
