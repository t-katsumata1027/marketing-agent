"""期限切れの旧caseを保持し、同ID照会と残三案だけを別権限で継続する。"""
import json,os
from datetime import timedelta
from .discord_delivery_recovery import Delivery,verify_receipt
from .current_day_origin import TARGET
from .preparation import fingerprint
from .replacement_client import timestamp
from .replacement_contract import stamp
from .domain import JST

VERSION='mina-discord-delivery-continuation-v1'

class Continuation(Delivery):
    jobs='current_day_discord_delivery_continuations'
    def __init__(self,*args,continuation):
        super().__init__(*args);self.n=continuation
    def parent(self,db):
        self.source(db)
        old=db.execute('SELECT * FROM current_day_discord_delivery_jobs WHERE case_uuid=?',(self.a['delivery_uuid'],)).fetchone()
        if not old or old['state']!='held' or old['approval_digest']!=fingerprint(self.a):raise ValueError('旧停止caseの版が不一致です')
        return old
    def issue(self):
        from .clients import MealClient
        self.check_code();n=self.n;now=self.clock();end=timestamp(n['not_after'])
        if os.getenv('MINA_DISCORD_DELIVERY_RECOVERY_ENABLED','false').lower()!='true' or set(n)!={'spec','target_date','continuation_uuid','parent_delivery_uuid','not_after','approval_reference','mode'} or n['spec']!=VERSION or n['target_date']!=TARGET or n['parent_delivery_uuid']!=self.a['delivery_uuid'] or n['mode']!='remaining_three_only':raise ValueError('残三案の承認が不正です')
        MealClient.request_key(n['continuation_uuid'])
        if now.astimezone(JST).date().isoformat()!=TARGET or not now<end<=timestamp('2026-10-29T09:15:00.000Z') or (end-now).total_seconds()<60:raise ValueError('別継続日時が不正です')
        with self.store.transaction() as db:
            old=self.parent(db)
            if now<timestamp(old['deadline']):raise ValueError('旧期限内に別継続を作りません')
            attempts=[dict(r) for r in db.execute('SELECT * FROM current_day_discord_delivery_attempts WHERE case_uuid=?',(self.a['delivery_uuid'],))]
            if len(attempts)!=4 or sum(r['state']=='prepared' for r in attempts)!=3 or sum(r['state']=='unknown' and bool(r['message_id']) for r in attempts)!=1:raise ValueError('既存一件/残三件以外は継続しません')
            for row in attempts:
                if fingerprint(json.loads(row['input']))!=row['input_digest']:raise ValueError('旧送付入力が変わりました')
            db.execute('CREATE TABLE IF NOT EXISTS current_day_discord_delivery_continuations(target_date TEXT PRIMARY KEY,case_uuid TEXT NOT NULL UNIQUE,continuation_uuid TEXT NOT NULL UNIQUE,approval_digest TEXT NOT NULL,activated_at TEXT NOT NULL,deadline TEXT NOT NULL,state TEXT NOT NULL,inputs_digest TEXT NOT NULL)')
            deadline=min(end,now+timedelta(minutes=15))
            inputs=fingerprint([{k:r[k] for k in ('platform','slot','input','input_digest','nonce')} for r in sorted(attempts,key=lambda r:(r['platform'],r['slot']))])
            db.execute('INSERT INTO current_day_discord_delivery_continuations VALUES(?,?,?,?,?,?,?,?)',(TARGET,self.a['delivery_uuid'],n['continuation_uuid'],fingerprint(n),stamp(now),stamp(deadline),'issued',inputs))
        return deadline
    def check(self,db=None,sending=False):
        if db is None:
            with self.store.transaction() as connection:return self.check(connection,sending)
        self.parent(db)
        row=db.execute('SELECT * FROM current_day_discord_delivery_continuations WHERE case_uuid=?',(self.a['delivery_uuid'],)).fetchone()
        if not row or row['continuation_uuid']!=self.n['continuation_uuid'] or row['approval_digest']!=fingerprint(self.n) or row['state'] not in ('issued','running'):raise ValueError('別継続の状態/版が不一致です')
        attempts=[dict(r) for r in db.execute('SELECT * FROM current_day_discord_delivery_attempts WHERE case_uuid=? ORDER BY platform,slot',(self.a['delivery_uuid'],))]
        if fingerprint([{k:r[k] for k in ('platform','slot','input','input_digest','nonce')} for r in attempts])!=row['inputs_digest']:raise ValueError('別継続で元入力/nonceを変更できません')
        now=self.clock()
        if now<timestamp(row['activated_at']) or now>=timestamp(row['deadline']) or sending and (timestamp(row['deadline'])-now).total_seconds()<60:raise ValueError('別継続の期限を超過しました')
    def reconcile_old(self,reader):
        with self.store.transaction() as db:
            self.check(db)
            pending=[dict(r) for r in db.execute("SELECT * FROM current_day_discord_delivery_attempts WHERE case_uuid=? AND state='unknown'",(self.a['delivery_uuid'],))]
        if len(pending)!=1 or not pending[0]['message_id']:raise ValueError('同ID照会可能な一件が必要です')
        row=pending[0];inputs=json.loads(row['input']);current=reader.message(row['message_id']);receipt=verify_receipt(current,inputs,reader)
        with self.store.transaction() as db:
            self.check(db)
            actual=db.execute('SELECT * FROM current_day_discord_delivery_attempts WHERE case_uuid=? AND platform=? AND slot=?',(self.a['delivery_uuid'],row['platform'],row['slot'])).fetchone()
            if dict(actual)!=row:raise ValueError('同ID照会の受領反映が競合しました')
            db.execute('CREATE TABLE IF NOT EXISTS current_day_discord_delivery_reconciliations(case_uuid TEXT,platform TEXT,slot TEXT,unknown_record TEXT,verified_receipt TEXT,PRIMARY KEY(case_uuid,platform,slot))')
            db.execute('INSERT INTO current_day_discord_delivery_reconciliations VALUES(?,?,?,?,?)',(self.a['delivery_uuid'],row['platform'],row['slot'],json.dumps(row),json.dumps(receipt)))
            db.execute("UPDATE current_day_discord_delivery_attempts SET state='sent',receipt=? WHERE case_uuid=? AND platform=? AND slot=?",(json.dumps(receipt),self.a['delivery_uuid'],row['platform'],row['slot']))
            old=json.loads(row['original'])
            if db.execute("UPDATE current_day_cards SET state='sent',message_id=? WHERE recovery_uuid=? AND platform=? AND slot=? AND state=? AND message_id IS NULL",(row['message_id'],self.s['owner'],row['platform'],row['slot'],old['state'])).rowcount!=1:raise ValueError('旧カードの受領反映が競合しました')
        return {'reconciled':1,'post_count':0,'old_job_unchanged':True}
