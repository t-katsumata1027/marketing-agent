"""旧不明を保持した四案送付専用case。食事/AI/画像生成/SNS操作を持たない。"""
import hashlib,json,os
from datetime import timedelta
from pathlib import Path
from .current_day_origin import TARGET
from .current_day_checkpoint import rows,file_record
from .preparation import fingerprint
from .replacement_client import timestamp
from .replacement_contract import stamp
from .domain import JST,LABELS,Candidate
from .clients import DiscordAttachmentRejected

VERSION='mina-discord-delivery-recovery-v1'
SELECTED={(p,s) for p in ('x','instagram') for s in ('dinner','daily')}

def content(payload):
    original=timestamp(payload['original_scheduled_at']).astimezone(JST).strftime('%m/%d %H:%M JST')
    return f"【準備専用・公開停止】{TARGET} {LABELS[payload['slot']]} / {payload['platform']}\n公開時刻：未定。元の予定は{original}。遅延公開は未承認です。\n{payload['body']}"

def capture(db,config):
    parent=rows(db,'current_day_dinner_continuations')
    if len(parent)!=1 or parent[0]['state']!='held':raise ValueError('停止した完成案の親caseが必要です')
    owner=parent[0]['recovery_uuid'];cards=[dict(r) for r in db.execute('SELECT * FROM current_day_cards WHERE recovery_uuid=? ORDER BY platform,slot',(owner,))]
    if len(cards)!=8 or {(r['platform'],r['slot']) for r in cards}!={(p,s) for p in ('x','instagram') for s in ('breakfast','lunch','dinner','daily')}:raise ValueError('既存八案が必要です')
    selected=[r for r in cards if (r['platform'],r['slot']) in SELECTED]
    if [(r['platform'],r['slot']) for r in selected if r['state']=='unknown']!=[('instagram','daily')] or any(r['state'] not in ('prepared','unknown') or r['message_id'] is not None for r in cards):raise ValueError('旧不明一件/未送信三件以外は扱いません')
    steps={r['step']:r for r in rows(db,'preparation_steps')}
    for step in ('meal_review:breakfast:corrected','ogp_review:breakfast','meal_review:lunch:corrected','ogp_review:lunch','meal_review:dinner:corrected','ogp_review:dinner','ogp_review:daily'):
        if steps[step]['state']!='complete' or json.loads(steps[step]['result'])!={'status':'publishable','reason_codes':[]}:raise ValueError('全点検の合格原本が必要です')
    files={}
    for row in selected:
        payload=json.loads(row['payload'])
        if fingerprint(payload)!=row['digest'] or payload['scheduled_at'] is not None:raise ValueError('元カードの版が不一致です')
        for asset in payload['assets']:
            record=file_record(asset['path'])
            if record['sha256']!=asset['sha256']:raise ValueError('元添付の実体が不一致です')
            files[record['path']]=record
    return {'spec':VERSION,'target_date':TARGET,'parent':parent,'owner':owner,'cards':cards,'files':list(files.values()),'old_lookup':config}

class Delivery:
    def __init__(self,store,approval,snapshot,clock,check_code,own_author,channel):
        self.store,self.a,self.s,self.clock,self.check_code,self.author,self.channel=store,approval,snapshot,clock,check_code,own_author,channel
    def source(self,db):
        self.check_code()
        reference=self.s['old_lookup'].get('lookup')
        if reference and file_record(reference['path'])['sha256']!=reference['sha256']:raise ValueError('旧履歴照会の原本が変わりました')
        if rows(db,'current_day_dinner_continuations')!=self.s['parent']:raise ValueError('旧held/期限が変わりました')
        hold=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone();lock=db.execute('SELECT * FROM scoped_preparation_locks WHERE target_date=?',(TARGET,)).fetchone()
        if not hold or not lock or (hold['owner'],hold['epoch'],hold['state'])!=(self.s['owner'],1,'held') or (lock['owner'],lock['epoch'])!=(self.s['owner'],1):raise ValueError('公開停止の所有が変わりました')
        for file in self.s['files']:
            if file_record(file['path'])!=file:raise ValueError('添付原本が変わりました')
        for row in self.s['cards']:
            actual=db.execute('SELECT payload,digest FROM current_day_cards WHERE recovery_uuid=? AND platform=? AND slot=?',(self.s['owner'],row['platform'],row['slot'])).fetchone()
            if not actual or (actual['payload'],actual['digest'])!=(row['payload'],row['digest']):raise ValueError('送付内容が変わりました')
    def check(self,db=None,sending=False):
        if db is None:
            with self.store.transaction() as connection:return self.check(connection,sending)
        self.source(db);row=db.execute('SELECT * FROM current_day_discord_delivery_jobs WHERE target_date=?',(TARGET,)).fetchone()
        if not row or row['case_uuid']!=self.a['delivery_uuid'] or row['approval_digest']!=fingerprint(self.a) or row['state'] not in ('issued','running'):raise ValueError('送付専用権限の段階/版が不一致です')
        now=self.clock()
        if now<timestamp(row['activated_at']) or now>=timestamp(row['deadline']) or sending and (timestamp(row['deadline'])-now).total_seconds()<60:raise ValueError('送付専用期限を超過しました')
    def issue(self):
        from .clients import MealClient
        self.check_code();now=self.clock();a=self.a
        if os.getenv('MINA_DISCORD_DELIVERY_RECOVERY_ENABLED','false').lower()!='true' or a['spec']!=VERSION or a['target_date']!=TARGET or a['mode']!='discord_only' or a['allow_unknown_resend'] is not True or fingerprint(self.s)!=a['checkpoint_digest'] or not self.author.isdigit() or not self.channel.isdigit():raise ValueError('送付専用承認が不正です')
        MealClient.request_key(a['delivery_uuid'])
        end=timestamp(a['not_after'])
        if now.astimezone(JST).date().isoformat()!=TARGET or not now<end<=timestamp('2026-10-29T08:30:00Z') or (end-now).total_seconds()<60:raise ValueError('送付専用日時が不正です')
        with self.store.transaction() as db:
            self.source(db)
            db.execute('CREATE TABLE IF NOT EXISTS current_day_discord_delivery_jobs(target_date TEXT PRIMARY KEY,case_uuid TEXT NOT NULL UNIQUE,approval_digest TEXT NOT NULL,activated_at TEXT NOT NULL,deadline TEXT NOT NULL,state TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS current_day_discord_delivery_attempts(case_uuid TEXT NOT NULL,platform TEXT NOT NULL,slot TEXT NOT NULL,original TEXT NOT NULL,input TEXT NOT NULL,input_digest TEXT NOT NULL,nonce TEXT NOT NULL UNIQUE,state TEXT NOT NULL,message_id TEXT,receipt TEXT,PRIMARY KEY(case_uuid,platform,slot))')
            if db.execute('SELECT 1 FROM current_day_discord_delivery_jobs WHERE target_date=?',(TARGET,)).fetchone():raise ValueError('送付専用caseを再発行しません')
            deadline=min(end,now+timedelta(minutes=15))
            db.execute('INSERT INTO current_day_discord_delivery_jobs VALUES(?,?,?,?,?,?)',(TARGET,a['delivery_uuid'],fingerprint(a),stamp(now),stamp(deadline),'issued'))
            for row in self.s['cards']:
                if (row['platform'],row['slot']) not in SELECTED:continue
                payload=json.loads(row['payload']);nonce=fingerprint({'case':a['delivery_uuid'],'digest':row['digest'],'channel':self.channel,'author':self.author})[:24]
                inputs={'content':content(payload),'assets':payload['assets'],'channel':self.channel,'author':self.author,'nonce':nonce}
                db.execute("INSERT INTO current_day_discord_delivery_attempts VALUES(?,?,?,?,?,?,?,'prepared',NULL,NULL)",(a['delivery_uuid'],row['platform'],row['slot'],json.dumps(row),json.dumps(inputs),fingerprint(inputs),nonce))
        return deadline
    def run(self,discord,reader,verify):
        with self.store.transaction() as db:
            self.check(db,True)
            if db.execute("UPDATE current_day_discord_delivery_jobs SET state='running' WHERE case_uuid=? AND state='issued'",(self.a['delivery_uuid'],)).rowcount!=1:raise ValueError('送付専用の二重実行を拒否します')
        try:
            for old in self.s['cards']:
                if (old['platform'],old['slot']) not in SELECTED:continue
                payload=json.loads(old['payload']);verify(payload);self.check(sending=True)
                with self.store.transaction() as db:
                    self.check(db,True);row=db.execute('SELECT * FROM current_day_discord_delivery_attempts WHERE case_uuid=? AND platform=? AND slot=?',(self.a['delivery_uuid'],old['platform'],old['slot'])).fetchone();inputs=json.loads(row['input'])
                    if row['state']!='prepared' or fingerprint(inputs)!=row['input_digest']:raise ValueError('送付入力/段階が変わりました')
                    if db.execute("UPDATE current_day_discord_delivery_attempts SET state='sending' WHERE case_uuid=? AND platform=? AND slot=? AND state='prepared'",(self.a['delivery_uuid'],old['platform'],old['slot'])).rowcount!=1:raise ValueError('送付claim競合')
                message={'content':inputs['content'],'allowed_mentions':{'parse':[]},'embeds':[{'image':{'url':'attachment://'+Path(a['path']).name}} for a in inputs['assets']],'nonce':row['nonce'],'enforce_nonce':True}
                result=None
                try:
                    from .external_guard import guard
                    token=guard.set(lambda:self.check(sending=True))
                    try:result=discord.send_files(message,[Path(a['path']) for a in inputs['assets']])
                    finally:guard.reset(token)
                    receipt=verify_receipt(result,inputs,reader)
                except BaseException as error:
                    returned=result.get('id') if isinstance(result,dict) and isinstance(result.get('id'),str) and result['id'].isdigit() else None
                    failure={'http_status':getattr(error,'http_status',None),'discord_code':getattr(error,'discord_code',None),'response_sha256':getattr(error,'response_sha256',None),'returned_message_id':returned}
                    with self.store.transaction() as db:db.execute('UPDATE current_day_discord_delivery_attempts SET state=?,message_id=?,receipt=? WHERE case_uuid=? AND platform=? AND slot=?',('rejected' if isinstance(error,DiscordAttachmentRejected) else 'unknown',returned,json.dumps(failure),self.a['delivery_uuid'],old['platform'],old['slot']))
                    raise
                with self.store.transaction() as db:
                    self.source(db)
                    db.execute("UPDATE current_day_discord_delivery_attempts SET state='sent',message_id=?,receipt=? WHERE case_uuid=? AND platform=? AND slot=?",(receipt['message_id'],json.dumps(receipt),self.a['delivery_uuid'],old['platform'],old['slot']))
                    if db.execute("UPDATE current_day_cards SET state='sent',message_id=? WHERE recovery_uuid=? AND platform=? AND slot=? AND digest=? AND state IN ('prepared','unknown')",(receipt['message_id'],self.s['owner'],old['platform'],old['slot'],old['digest'])).rowcount!=1:raise ValueError('元カードの受領反映が競合しました')
            with self.store.transaction() as db:db.execute("UPDATE current_day_discord_delivery_jobs SET state='complete' WHERE case_uuid=?",(self.a['delivery_uuid'],))
        except BaseException:
            with self.store.transaction() as db:db.execute("UPDATE current_day_discord_delivery_jobs SET state='held' WHERE case_uuid=?",(self.a['delivery_uuid'],))
            raise
        return {'target_date':TARGET,'discord_sent':4,'sns_published':0,'food_writes':0,'ai_calls':0,'old_unknown_origin_preserved':True}

def verify_receipt(result,inputs,reader):
    """同IDの実読取と添付bytesを照合。nonce省略は省略として記録する。"""
    if not isinstance(result,dict) or not isinstance(result.get('id'),str) or not result['id'].isdigit():raise ValueError('Discord受領IDが不明です')
    current=reader.message(result['id'])
    for value in (result,current):
        if value.get('id')!=result['id'] or value.get('channel_id')!=inputs['channel'] or value.get('author',{}).get('id')!=inputs['author'] or value.get('content')!=inputs['content'] or value.get('nonce') is not None and str(value['nonce'])!=inputs['nonce']:raise ValueError('Discord受領の宛先/著者/本文/nonceが不一致です')
        if sorted(a['filename'] for a in value.get('attachments',[]))!=sorted(Path(a['path']).name for a in inputs['assets']):raise ValueError('Discord受領の添付集合が不一致です')
    expected={Path(a['path']).name:a for a in inputs['assets']}
    for asset in current['attachments']:
        raw=reader.asset(asset['url'])
        if hashlib.sha256(raw).hexdigest()!=expected[asset['filename']]['sha256']:raise ValueError('Discord添付実バイトが不一致です')
    return {'message_id':result['id'],'channel':inputs['channel'],'author':inputs['author'],'content_sha256':hashlib.sha256(inputs['content'].encode()).hexdigest(),'nonce_requested':inputs['nonce'],'nonce_echo_present':current.get('nonce') is not None,'assets_verified':True}

class DiscordReader:
    """Bot APIはGETだけ、添付CDNには認証を転送しない。"""
    def __init__(self,token,channel):self.token,self.channel=token,channel
    def api(self,path):
        from urllib.request import Request,build_opener
        from .drive import NoRedirect
        if path!='users/@me' and not path.startswith('channels/'+self.channel+'/messages/'):raise ValueError('Discord読取先が不正です')
        with build_opener(NoRedirect()).open(Request('https://discord.com/api/v10/'+path,headers={'Authorization':'Bot '+self.token,'User-Agent':'DiscordBot (https://github.com/t-katsumata1027/marketing-agent, 1.0)','Cache-Control':'no-store'}),timeout=20) as response:return json.loads(response.read(131072))
    def identity(self):return self.api('users/@me')['id']
    def message(self,key):
        if not isinstance(key,str) or not key.isdigit():raise ValueError('Discord読取IDが不正です')
        return self.api('channels/'+self.channel+'/messages/'+key)
    def asset(self,url):
        from urllib.parse import urlsplit
        from urllib.request import Request,build_opener
        from .drive import NoRedirect
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname!='cdn.discordapp.com' or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('Discord添付の読取先が不正です')
        with build_opener(NoRedirect()).open(Request(url,headers={'User-Agent':'DiscordBot (https://github.com/t-katsumata1027/marketing-agent, 1.0)'}),timeout=20) as response:raw=response.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:raise ValueError('Discord添付が大きすぎます')
        return raw
