"""通常候補と通知の到達を分離し、一度限りの全操作を実Discordへ照合する。"""
import copy,hashlib,json
from datetime import UTC,datetime
from .domain import Candidate,SLOTS
from .preparation import fingerprint
from .routine_recovery import admitted


def assert_candidates(store,target,payloads):
    expected={p['slot']:Candidate(**p) for p in payloads}
    if set(expected)!=set(SLOTS):raise ValueError('通常承認の四候補が不足しています')
    with store.transaction() as db:
        rows=list(db.execute('SELECT * FROM candidates WHERE target_date=?',(target,)))
        if len(rows)!=4:raise ValueError('通常承認台帳の四枠が不足しています')
        from .scoped_preparation import blocked
        for r in rows:
            if r['state']!='approval_pending' or r['digest']!=expected[r['slot']].digest or json.loads(r['payload'])!=payloads[[p['slot'] for p in payloads].index(r['slot'])] or blocked(json.loads(r['payload'])):raise ValueError('準備専用/別版の候補を完成と扱いません')

def buttons(value):
    return [{'type':row.get('type'),'components':[{k:button.get(k,False if k=='disabled' else None) for k in ('type','style','label','custom_id','disabled')} for button in row.get('components',[])]} for row in value.get('components',[])]

def visible(value):
    """Discordが付ける寸法/プロキシ等を除き、表示に影響する送信項目を固定する。"""
    embeds=[]
    for e in value.get('embeds',[]):
        item={k:e[k] for k in ('title','description','url','color','timestamp') if k in e}
        for key in ('image','thumbnail'):
            if key in e:item[key]={'url':e[key].get('url')}
        if 'footer' in e:item['footer']={k:e['footer'][k] for k in ('text','icon_url') if k in e['footer']}
        if 'author' in e:item['author']={k:e['author'][k] for k in ('name','url','icon_url') if k in e['author']}
        if 'fields' in e:item['fields']=[{'name':f.get('name'),'value':f.get('value'),'inline':f.get('inline',False)} for f in e['fields']]
        embeds.append(item)
    return {'content':value.get('content',''),'embeds':embeds,'attachments':[{'filename':a['filename']} for a in value.get('attachments',[])],'components':buttons(value)}

def matches(message,wire,author,channel):
    if not isinstance(message,dict) or message.get('author',{}).get('id')!=author or message.get('channel_id')!=channel or visible(message)!=visible(wire):return False
    if message.get('nonce') is not None and str(message['nonce'])!=wire['nonce']:return False
    # parse=[]は送信前条件。応答では実際のmention一覧が空であることを確認する。
    return not message.get('mentions') and not message.get('mention_roles') and message.get('mention_everyone',False) is False

def current_notice(db,notice,platform):
    from .replacement_bundle import notice_allowed
    table='candidates' if platform=='x' else 'instagram_candidates'
    row=db.execute('SELECT * FROM '+table+' WHERE target_date=? AND slot=?',(notice['target_date'],notice['slot'])).fetchone()
    if not row or row['revision']!=notice['revision'] or row['state']!='approval_pending' or not notice_allowed(db,notice,platform):raise ValueError('通知対象の承認候補/公開排他が変わりました')

def prepare(store,notice,platform):
    payload=json.loads(notice['payload']);target=notice['target_date']
    with store.transaction() as db:
        policy=admitted(db,target)
        if not policy:
            from .next_saved import handoff_policy
            policy=handoff_policy(db,target)
        if not policy:
            from .next_meals import handoff_policy
            policy=handoff_policy(db,target)
        if not policy:return None
        if notice['kind']!='approval':return None
        current_notice(db,notice,platform)
        if payload.get('allowed_mentions')!={'parse':[]} or len(buttons(payload))!=1 or len(buttons(payload)[0]['components'])!=3 or any(not b['custom_id'] for b in buttons(payload)[0]['components']):raise ValueError('通常承認の操作/mention条件が不正です')
        db.execute('CREATE TABLE IF NOT EXISTS routine_notice_receipts(target_date TEXT,platform TEXT,notice_id INTEGER,policy_id TEXT,payload TEXT,payload_digest TEXT,nonce TEXT UNIQUE,token_binding TEXT UNIQUE,state TEXT,author TEXT,channel TEXT,message_id TEXT,dispatch_started REAL,receipt TEXT,PRIMARY KEY(platform,notice_id))')
        existing=db.execute('SELECT * FROM routine_notice_receipts WHERE platform=? AND notice_id=?',(platform,notice['id'])).fetchone()
        if existing:
            stored=json.loads(existing['payload'])
            original={k:v for k,v in stored.items() if k not in ('nonce','enforce_nonce')}
            if existing['state']=='prepared' and existing['author'] is None and existing['message_id'] is None and existing['dispatch_started'] is None and original==payload and fingerprint(stored)==existing['payload_digest']:return stored
            raise ValueError('通知要求を再送しません。元受領を照会してください')
        token_binding=fingerprint(buttons(payload));nonce=fingerprint({'policy_id':policy['policy_id'],'platform':platform,'notice_id':notice['id'],'payload_digest':fingerprint(payload)})[:24]
        wire={**payload,'nonce':nonce,'enforce_nonce':True}
        db.execute("INSERT INTO routine_notice_receipts VALUES(?,?,?,?,?,?,?,?,'prepared',NULL,NULL,NULL,NULL,NULL)",(target,platform,notice['id'],policy['policy_id'],json.dumps(wire,ensure_ascii=False),fingerprint(wire),nonce,token_binding))
        return wire

def transmit(store,notice,platform,client):
    wire=prepare(store,notice,platform)
    if wire is None:return client.send(json.loads(notice['payload']))
    author=client.identity();channel=client.channel
    with store.transaction() as db:
        current_notice(db,notice,platform)
        row=db.execute('SELECT * FROM routine_notice_receipts WHERE platform=? AND notice_id=?',(platform,notice['id'])).fetchone()
        if row['state']!='prepared' or fingerprint(wire)!=row['payload_digest']:raise ValueError('通知送信の固定版が変わりました')
        db.execute("UPDATE routine_notice_receipts SET state='dispatching',author=?,channel=?,dispatch_started=? WHERE platform=? AND notice_id=?",(author,channel,datetime.now(UTC).timestamp(),platform,notice['id']))
    result=None
    try:
        result=client.send(wire)
        key=result.get('id') if isinstance(result,dict) else None
        if not isinstance(key,str) or not key.isdigit():raise ValueError('通知の受領IDが不明です')
        with store.transaction() as db:db.execute('UPDATE routine_notice_receipts SET message_id=? WHERE platform=? AND notice_id=?',(key,platform,notice['id']))
        actual=client.message(key)
        if actual.get('id')!=key or not matches(result,wire,author,channel) or not matches(actual,wire,author,channel):raise ValueError('通知の同ID/全内容/操作が不一致です')
        receipt={'message_id':key,'visible_digest':fingerprint(visible(actual)),'nonce_echo_present':actual.get('nonce') is not None,'token_binding':fingerprint(buttons(actual)),'verification':'same_id_get'}
        with store.transaction() as db:db.execute("UPDATE routine_notice_receipts SET state='sent',receipt=? WHERE platform=? AND notice_id=?",(json.dumps(receipt),platform,notice['id']))
        return result
    except BaseException:
        with store.transaction() as db:db.execute("UPDATE routine_notice_receipts SET state='unknown' WHERE platform=? AND notice_id=?",(platform,notice['id']))
        raise

def reconcile(store,platform,notice_id,client):
    """GETだけで採用し、履歴0件を未受付の証明にしない。"""
    with store.transaction() as db:r=db.execute('SELECT * FROM routine_notice_receipts WHERE platform=? AND notice_id=?',(platform,notice_id)).fetchone()
    if not r or r['state'] not in ('unknown','dispatching','sent'):return False
    old=dict(r);wire=json.loads(r['payload'])
    if fingerprint(wire)!=r['payload_digest'] or fingerprint(buttons(wire))!=r['token_binding'] or wire['nonce']!=r['nonce'] or client.identity()!=r['author'] or client.channel!=r['channel']:raise ValueError('通知照会の版/著者/宛先が不一致です')
    if r['message_id']:
        message=client.message(r['message_id'])
        matched=[message] if message.get('id')==r['message_id'] and matches(message,wire,r['author'],r['channel']) else []
        complete=True
    else:
        snapshot=client.history_since(datetime.fromtimestamp(r['dispatch_started'],UTC))
        complete=snapshot['complete'];matched=[]
        for message in snapshot['messages']:
            created=datetime.fromisoformat(message['timestamp'].replace('Z','+00:00'))
            if created.tzinfo is None:raise ValueError('通知履歴の時刻が不正です')
            if created.timestamp()>=r['dispatch_started'] and matches(message,wire,r['author'],r['channel']):matched.append(message)
    if not complete or len(matched)!=1:return False
    message=matched[0];receipt={'message_id':message['id'],'visible_digest':fingerprint(visible(message)),'nonce_echo_present':message.get('nonce') is not None,'token_binding':fingerprint(buttons(message)),'verification':'same_id_get' if r['message_id'] else 'complete_history_get'}
    with store.transaction() as db:
        current=db.execute('SELECT * FROM routine_notice_receipts WHERE platform=? AND notice_id=?',(platform,notice_id)).fetchone()
        if dict(current)!=old:raise ValueError('通知照会結果の採用が競合しました')
        db.execute('CREATE TABLE IF NOT EXISTS routine_notice_reconciliations(platform TEXT,notice_id INTEGER,old_record TEXT,receipt TEXT,PRIMARY KEY(platform,notice_id))')
        db.execute('INSERT INTO routine_notice_reconciliations VALUES(?,?,?,?)',(platform,notice_id,json.dumps(old),json.dumps(receipt)))
        table='notices' if platform=='x' else 'instagram_notices'
        notice=db.execute('SELECT * FROM '+table+' WHERE id=?',(notice_id,)).fetchone()
        if not notice or notice['target_date']!=r['target_date'] or notice['state']!='outcome_unknown' or json.loads(notice['payload'])!={k:v for k,v in wire.items() if k not in ('nonce','enforce_nonce')}:raise ValueError('元通知の版/状態が変わりました')
        db.execute("UPDATE routine_notice_receipts SET state='sent',message_id=?,receipt=? WHERE platform=? AND notice_id=?",(message['id'],json.dumps(receipt),platform,notice_id));db.execute("UPDATE "+table+" SET state='sent' WHERE id=?",(notice_id,))
    return True


def maintain_notices(store,platform,client):
    """新方針の不明通知だけ最大三回GET。送信前失敗以外は再POSTしない。"""
    from .routine_recovery import exists
    with store.transaction() as db:
        if not exists(db,'routine_notice_receipts'):return
        rows=[dict(r) for r in db.execute("SELECT * FROM routine_notice_receipts WHERE platform=? AND state IN ('prepared','unknown','dispatching','sent')",(platform,))]
        if not rows:return
        db.execute('CREATE TABLE IF NOT EXISTS routine_notice_lookups(platform TEXT,notice_id INTEGER,count INTEGER,PRIMARY KEY(platform,notice_id))')
    for row in rows:
        table='notices' if platform=='x' else 'instagram_notices'
        with store.transaction() as db:
            n=db.execute('SELECT * FROM '+table+' WHERE id=?',(row['notice_id'],)).fetchone()
            if not n or n['state']!='outcome_unknown':continue
            count=db.execute('SELECT count FROM routine_notice_lookups WHERE platform=? AND notice_id=?',(platform,row['notice_id'])).fetchone()
            if count and count[0]>=3:continue
            db.execute('INSERT INTO routine_notice_lookups VALUES(?,?,1) ON CONFLICT(platform,notice_id) DO UPDATE SET count=count+1',(platform,row['notice_id']))
        try:
            if row['state']=='prepared' and row['dispatch_started'] is None and row['author'] is None:
                client.identity()
                with store.transaction() as db:
                    live=db.execute('SELECT * FROM routine_notice_receipts WHERE platform=? AND notice_id=?',(platform,row['notice_id'])).fetchone()
                    if dict(live)!=row:raise ValueError('送信前照会が競合しました')
                    original={k:v for k,v in json.loads(row['payload']).items() if k not in ('nonce','enforce_nonce')}
                    current=db.execute('SELECT * FROM '+table+' WHERE id=?',(row['notice_id'],)).fetchone()
                    if current['state']!='outcome_unknown' or json.loads(current['payload'])!=original:raise ValueError('通知原本が変わりました')
                    db.execute("UPDATE "+table+" SET state='pending' WHERE id=?",(row['notice_id'],))
            else:reconcile(store,platform,row['notice_id'],client)
        except Exception:
            # 生の応答やBot資格をログに出さず、不明を維持する。
            continue
