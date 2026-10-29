"""M1の応答喪失を元UUIDの正式照会だけで照合し、明示した互換投影へ結ぶ。"""
import copy,hashlib,json,time,re
from datetime import UTC,datetime
from urllib.parse import urlsplit,unquote
from .preparation import fingerprint
from .m1_contract import CONTRACT,completed_request,normalized_comment,validate_result,validate_state
from .replacement_contract import canonical
from .routine_recovery import CURRENT

PROJECTION='mina-m1-get-compatibility-projection-v1'
# 読取確認した本体固定版。計算規則の変更時は新しい受入を要求する。
CONTRACT_COMMIT='f4aee73ecc9ad2c784c6196fb4d04009812037d6'
JS_SPACE='\u0009\u000a\u000b\u000c\u000d \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'

def foods(value):
    return [{**{k:f[k] for k in ('calories','protein','fat','carbs')},'name':f['name'].strip(JS_SPACE),'amount':(f.get('amount') or '').strip(JS_SPACE) or None} for f in value]

def expected_hash(request):
    p=request['payload'];op=request['operation'];common={'contract_version':CONTRACT,'operation':op,'comment':normalized_comment(p['comment']),'expected_state_version':p['expected_state_version']}
    if op=='meal_register':
        at=datetime.fromisoformat(p['logged_at']).astimezone(UTC).isoformat(timespec='milliseconds').replace('+00:00','Z')
        image=request['image'];mime='image/png' if image.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg'
        common.update(strict_meal_type=True,mime=mime,image_sha256=request['image_sha256'],meal_type=p['meal_type'],logged_at=at)
    elif op=='meal_correction':
        parsed=urlsplit(p['share_url']);short=unquote(parsed.path.rsplit('/',1)[-1],errors='strict')
        if parsed.scheme!='https' or parsed.hostname!='www.log-eats.com' or parsed.username or parsed.password or parsed.query or parsed.fragment or not re.fullmatch('/s/[^/]+',parsed.path) or not re.fullmatch('[a-z0-9_-]{4,32}',short):raise ValueError('訂正先短縮URLの形式が不正です')
        common.update(reason=p['reason'],share_short_id=short,foods=foods(p['foods']))
    elif op=='daily_share':common.update(date=p['date'],expected_meal_count=3)
    else:raise ValueError('M1投影の操作範囲外です')
    return 'ih1_'+hashlib.sha256(canonical(common)).hexdigest()

def archive_failure(ledger,key):
    run=CURRENT.get()
    if run is None:return
    r=ledger.restore(key)
    with ledger.store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS routine_request_failures(request_key TEXT PRIMARY KEY,target_date TEXT,policy_id TEXT,input_digest TEXT,original_phase TEXT,original_observation TEXT,created REAL)')
        db.execute('INSERT OR IGNORE INTO routine_request_failures VALUES(?,?,?,?,?,?,?)',(key,r['target_date'],run.id,r['input_digest'],r['phase'],r['observation'],run.clock().timestamp()))

def lookup(ledger,key,client):
    """通常復旧の全照会に共通する、送信前予約と応答原本の保存。"""
    run=CURRENT.get();request=ledger.restore(key)
    if run is None:return client.request_state(key,request['operation'])
    run.check()
    with ledger.store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS routine_request_lookups(request_key TEXT,ordinal INTEGER,raw TEXT,digest TEXT,classification TEXT,PRIMARY KEY(request_key,ordinal))')
        ordinal=db.execute('SELECT count(*) FROM routine_request_lookups WHERE request_key=?',(key,)).fetchone()[0]+1
        if ordinal>3:raise ValueError('元UUIDの照会上限です')
        db.execute("INSERT INTO routine_request_lookups VALUES(?,?,NULL,NULL,'started')",(key,ordinal))
    try:
        value=client.request_state(key,request['operation'])
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False)
        if len(raw.encode())>131072:raise ValueError('M1照会の原本が過大です')
    except Exception as error:
        with ledger.store.transaction() as db:db.execute('UPDATE routine_request_lookups SET classification=? WHERE request_key=? AND ordinal=?',('unavailable:'+type(error).__name__,key,ordinal))
        raise
    with ledger.store.transaction() as db:db.execute("UPDATE routine_request_lookups SET raw=?,digest=?,classification='observed' WHERE request_key=? AND ordinal=?",(raw,fingerprint(value),key,ordinal))
    return value

def project(request,lookup,state,host,now):
    key=request['request_key'];op=request['operation'];p=request['payload'];slot=request['slot'];target=request['target_date']
    result=lookup.get('current_result')
    if not isinstance(result,dict) or lookup.get('result')!=result or lookup.get('input_hash')!=expected_hash(request):raise ValueError('M1照会の保存原本/入力hashが一致しません')
    completed_request(lookup,key,op,result)
    current=validate_state(state,target,now)
    if lookup.get('current_state_version')!=current:raise ValueError('M1照会と現在の日版が違います')
    meals=None
    if slot=='daily':
        if result.get('source_state_version')!=p['expected_state_version'] or p.get('expected_meal_count')!=3:raise ValueError('日次の生成元版が違います')
        meals={}
        for value in state['meals']:
            kind=value['meal']['meal_type'];meals[kind]=validate_result(value,kind,target,host,logged_at=value['meal']['logged_at'],input_comment=value['normalized_comment'],now=now)
        actual=state.get('daily_share')
    else:
        actuals=[v for v in state['meals'] if v['meal']['meal_type']==slot]
        if len(actuals)!=1:raise ValueError('食事の現在正本を一意に確認できません')
        actual=actuals[0]
        if op=='meal_register' and (result['image_sha256']!=request['image_sha256'] or lookup.get('image_sha256')!=request['image_sha256']):raise ValueError('登録原画像の版が違います')
        if op=='meal_correction' and (result['share']['url']!=p['share_url'] or foods(result['analysis']['foods'])!=foods(p['foods'])):raise ValueError('訂正先/全食品が違います')
    if not isinstance(actual,dict) or any(k not in actual or actual[k]!=v for k,v in result.items()):raise ValueError('保存時原本と現在正本が違います')
    validate_result(result,slot,target,host,logged_at=p.get('logged_at',result.get('meal',{}).get('logged_at')),input_comment=p['comment'],meals=meals,now=now)
    # 互換キーはGET実値のコピー。仮のstatus/時刻/栄養を補完しない。
    envelope={**copy.deepcopy(result),'contract_version':CONTRACT,'result':copy.deepcopy(result),'agent_projection':{'version':PROJECTION,'source_lookup_digest':fingerprint(lookup)}}
    return envelope

def completed_lookup(ledger,key,client,operation):
    """採用した投影の同一GET原本を後続契約検査へ渡す。再GETで上限を超えない。"""
    from .routine_recovery import exists
    if CURRENT.get() is not None:
        with ledger.store.transaction() as db:
            if exists(db,'routine_request_projections'):
                p=db.execute('SELECT * FROM routine_request_projections WHERE request_key=?',(key,)).fetchone()
                if p:
                    r=db.execute("SELECT raw FROM routine_request_lookups WHERE request_key=? AND digest=? AND classification='observed'",(key,p['lookup_digest'])).fetchone()
                    if not r or fingerprint(json.loads(r[0]))!=p['lookup_digest']:raise ValueError('投影のGET原本がありません')
                    return json.loads(r[0])
    return client.request_state(key,operation)

def recover(ledger,key,client,seed=None,seed_attempted=False,sleep=None):
    sleep=time.sleep if sleep is None else sleep
    run=CURRENT.get()
    if run is None:return None
    request=ledger.restore(key)
    if request['target_date']!=run.target:return None
    run.check()
    with ledger.store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS routine_request_lookups(request_key TEXT,ordinal INTEGER,raw TEXT,digest TEXT,classification TEXT,PRIMARY KEY(request_key,ordinal))')
        db.execute('CREATE TABLE IF NOT EXISTS routine_request_projections(request_key TEXT PRIMARY KEY,version TEXT,lookup_digest TEXT,envelope TEXT,envelope_digest TEXT,policy_id TEXT)')
        count=db.execute('SELECT count(*) FROM routine_request_lookups WHERE request_key=?',(key,)).fetchone()[0]
    # 先行reconcileの原本も採用検査する。既に消費したGETを再計上しない。
    attempts=([0] if isinstance(seed,dict) else [])+list(range(count+1,4))
    for ordinal in attempts:
        run.check();value=None
        if ordinal>1:sleep(2 if ordinal==2 else 5);run.check()
        try:value=seed if ordinal==0 else lookup(ledger,key,client)
        except Exception:classification='lookup_unavailable'
        else:classification='observed'
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False)
        if len(raw.encode())>131072:raise ValueError('M1照会の原本が過大です')
        run.event('request:'+request['slot'],'lookup',{'ordinal':ordinal,'request_key_sha256':hashlib.sha256(key.encode()).hexdigest(),'classification':classification})
        run.check()
        if not isinstance(value,dict):continue
        try:
            envelope=project(request,value,client.day_state(request['target_date']),run.host,run.clock())
            if request['operation']=='meal_correction':
                with ledger.store.transaction() as db:
                    originals=[json.loads(r[0])['result'] for r in db.execute("SELECT result FROM preparation_steps WHERE target_date=? AND step LIKE ? AND state='complete'",(run.target,'meal_register:'+request['slot']+'%'))]
                if not any(r['image_sha256']==envelope['result']['image_sha256'] and r['stored_image_sha256']==envelope['result']['stored_image_sha256'] for r in originals):raise ValueError('訂正前の画像を保持していません')
        except Exception:continue
        run.check()
        with ledger.store.transaction() as db:
            current=db.execute('SELECT input_digest,phase FROM mina_requests WHERE request_key=?',(key,)).fetchone()
            if current['input_digest']!=request['input_digest'] or current['phase']!='held':raise ValueError('M1投影の採用が競合しました')
            db.execute('INSERT INTO routine_request_projections VALUES(?,?,?,?,?,?)',(key,PROJECTION,fingerprint(value),json.dumps(envelope,ensure_ascii=False),fingerprint(envelope),run.id))
            db.execute("UPDATE mina_requests SET phase='response_received',observation=?,updated=? WHERE request_key=? AND phase='held'",(json.dumps({'persistence':'saved','stage':'completed','projection_version':PROJECTION,'lookup_digest':fingerprint(value)}),run.clock().timestamp(),key))
        run.event('request:'+request['slot'],'saved_get_projection',{'input_digest':request['input_digest'],'lookup_digest':fingerprint(value),'projection_version':PROJECTION})
        return envelope
    return None
