"""本人の最新直接指示で朝二件だけを新時刻へ束縛し、旧expiredを履歴保存する。"""
import argparse,hashlib,json,os,uuid
from dataclasses import asdict,replace
from datetime import UTC,datetime,timedelta
from pathlib import Path
from . import morning_registry as m
from .preparation import fingerprint
KEY='morning_breakfast_late';VERSION='mina-breakfast-direct-v1'
END=datetime.fromisoformat('2026-10-30T08:30:00+09:00')

def proposed(source,target,slot):
    if not isinstance(source,dict) or KEY not in source:return None
    v=source[KEY]
    if (set(v)!={'contract_version','request_uuid','target_date','slot','publish_at','not_after','approval_reference_sha256'}
        or v['contract_version']!=VERSION or target!=m.TARGET or slot!='breakfast' or v['target_date']!=target or v['slot']!=slot):raise ValueError('朝食直接公開の限定契約が不正です')
    uuid.UUID(v['request_uuid']);at=datetime.fromisoformat(v['publish_at']);end=datetime.fromisoformat(v['not_after'])
    if at.tzinfo is None or end.tzinfo is None or not at<end<=END or len(v['approval_reference_sha256'])!=64:raise ValueError('朝食直接公開の時間窓が不正です')
    return at

def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS morning_late_authorities(request_uuid TEXT PRIMARY KEY,target_date TEXT UNIQUE,case_uuid TEXT,config_path TEXT,config_sha256 TEXT,old_rows TEXT,old_members TEXT,old_receipts TEXT,old_material TEXT,new_members TEXT,publish_at REAL,deadline REAL,created REAL,inventory TEXT)')

def authorized(db,job,platform,row):
    if not m.exists(db,'morning_late_authorities'):return False
    a=db.execute('SELECT * FROM morning_late_authorities WHERE target_date=?',(m.TARGET,)).fetchone()
    if not a or a['case_uuid']!=job['case_uuid'] or row['slot']!='breakfast' or datetime.now(UTC).timestamp()>=a['deadline']:return False
    inventory=json.loads(a['inventory'])
    if not 0<=datetime.now(UTC).timestamp()-inventory['checked_at']<=120:return False
    from .current_day_cli import protected_json
    config,sha=protected_json(a['config_path'],root_owned=True)
    if sha!=a['config_sha256'] or config['request_uuid']!=a['request_uuid'] or config['approval_method']!='explicit_user_instruction':return False
    expected=json.loads(a['new_members']).get(platform)
    if not expected or any(row[k]!=expected[k] for k in ('revision','digest','payload','actor','approved','scheduled')):return False
    payload=json.loads(row['payload']);source=payload['approval_source'] if platform=='x' else payload['source'];v=source[KEY]
    return (v['request_uuid']==a['request_uuid'] and v['approval_reference_sha256']==fingerprint(config['approval_reference'])
            and proposed(source,m.TARGET,'breakfast').timestamp()==a['publish_at'] and datetime.fromisoformat(v['not_after']).timestamp()==a['deadline'])

def register(store,config,path,sha,inventory,now):
    from .domain import Candidate
    from .instagram_domain import InstagramCandidate
    if now>=END or inventory.get('verified') is not True or now.timestamp()-inventory['checked_at']>120:raise ValueError('朝食の外部未送信照合・期限を確認できません')
    at=now+timedelta(seconds=30);deadline=min(END,now+timedelta(minutes=15))
    if at>=deadline:raise ValueError('朝食公開の時間が不足しています')
    marker={'contract_version':VERSION,'request_uuid':config['request_uuid'],'target_date':m.TARGET,'slot':'breakfast','publish_at':at.isoformat(),'not_after':deadline.isoformat(),'approval_reference_sha256':fingerprint(config['approval_reference'])}
    with store.transaction() as db:
        initialize(db)
        if db.execute('SELECT 1 FROM morning_late_authorities WHERE target_date=?',(m.TARGET,)).fetchone():raise ValueError('朝食の直接公開権限は作成済みです。元要求を照会してください')
        job=m.job(db,m.TARGET)
        if not job or job['case_uuid']!=config['case_uuid'] or not m.intact(db,job) or m.members(db,job,'meals') is None:raise ValueError('朝食の正本・公開排他が変わりました')
        old={};members={};receipts={}
        for platform,table,attempts in (('x','candidates','buffer_publication'),('instagram','instagram_candidates','instagram_attempts')):
            row=db.execute('SELECT * FROM '+table+" WHERE target_date=? AND slot='breakfast'",(m.TARGET,)).fetchone()
            if (not row or dict(row)!=config['old_rows'][platform] or row['state']!='expired' or row['actor']!=config['actor'] or row['approved'] is None
                or db.execute('SELECT 1 FROM '+attempts+" WHERE target_date=? AND slot='breakfast'",(m.TARGET,)).fetchone()):raise ValueError('朝食の元expired・本人判断・未送信が不一致です')
            if not m.receipts(db,job,'meals',only=(platform,'breakfast')):raise ValueError('元の朝食の同ID受領がありません')
            old[platform]=dict(row);members[platform]=dict(db.execute("SELECT * FROM morning_members WHERE case_uuid=? AND platform=? AND slot='breakfast'",(job['case_uuid'],platform)).fetchone())
            notice_table='notices' if platform=='x' else 'instagram_notices'
            n=db.execute('SELECT id FROM '+notice_table+" WHERE target_date=? AND slot='breakfast' AND revision=? AND kind='approval'",(m.TARGET,row['revision'])).fetchone()
            receipts[platform]=dict(db.execute('SELECT * FROM routine_notice_receipts WHERE platform=? AND notice_id=?',(platform,n['id'])).fetchone())
        x=Candidate(**json.loads(old['x']['payload']));ig=InstagramCandidate(**json.loads(old['instagram']['payload']))
        x=replace(x,approval_source={**x.approval_source,KEY:marker},source_commit=config['commit'])
        ig=replace(ig,source={**ig.source,KEY:marker,'x_digests':{**ig.source['x_digests'],'breakfast':x.digest}})
        x.validate('www.log-eats.com',now)
        from .instagram_media import HOSTS
        ig.validate(now,HOSTS)
        new={}
        for platform,c,table in (('x',x,'candidates'),('instagram',ig,'instagram_candidates')):
            payload=asdict(c);revision=old[platform]['revision']+1
            db.execute('UPDATE '+table+" SET revision=?,payload=?,digest=?,scheduled=?,state='approved',actor=?,approved=?,nonce_hash=NULL,reason=NULL,dispatch_started=NULL WHERE target_date=? AND slot='breakfast' AND state='expired' AND revision=?",(revision,json.dumps(payload,ensure_ascii=False),c.digest,at.timestamp(),config['actor'],now.timestamp(),m.TARGET,old[platform]['revision']))
            if db.execute('SELECT changes()').fetchone()[0]!=1:raise ValueError('朝食の新承認版が競合しました')
            db.execute("UPDATE morning_members SET revision=?,digest=?,payload_digest=? WHERE case_uuid=? AND platform=? AND slot='breakfast'",(revision,c.digest,fingerprint(payload),job['case_uuid'],platform))
            new[platform]=dict(db.execute('SELECT * FROM '+table+" WHERE target_date=? AND slot='breakfast'",(m.TARGET,)).fetchone())
        material=m.material(db,job,'meals');old_material=json.dumps(material,ensure_ascii=False);material['xs']['breakfast']=asdict(x);material['igs']['breakfast']=asdict(ig)
        db.execute("UPDATE morning_cohorts SET state='notified',material=? WHERE case_uuid=? AND cohort='meals'",(json.dumps(material,ensure_ascii=False),job['case_uuid']))
        db.execute('INSERT INTO morning_late_authorities VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(config['request_uuid'],m.TARGET,job['case_uuid'],str(path),sha,json.dumps(old),json.dumps(members),json.dumps(receipts),old_material,json.dumps(new),at.timestamp(),deadline.timestamp(),now.timestamp(),json.dumps(inventory)))
    return {'publish_at':at.isoformat(),'not_after':deadline.isoformat(),'approval_method':'explicit_user_instruction','food_ai_image_calls':0,'direct_sns_calls':0}

def run(path):
    from .current_day_cli import protected_json,scoped_meal
    from .readiness import release_commit
    from .store import Store
    from .domain import Candidate
    from .m1_contract import verify_source
    from .buffer_publisher import production_client
    from .next_saved_cli import reserve
    from .buffer_budget import using,release
    from .replacement_inventory import ExternalInventory,BufferInventoryAdapter
    config,sha=protected_json(path,root_owned=True);uuid.UUID(config['request_uuid'])
    if (config['contract_version']!=VERSION or config['target_date']!=m.TARGET or config['slot']!='breakfast' or config['approval_method']!='explicit_user_instruction'
        or config['not_after']!=END.isoformat() or not config['approval_reference']):raise ValueError('最新直接指示の原本が不正です')
    release_commit(Path(__file__).resolve().parents[2],config['commit'])
    os.environ['MINA_PUBLISH_ENABLED']='false';os.environ['MINA_INSTAGRAM_PUBLISH_ENABLED']='false'
    store=Store('/var/lib/mina-agent/mina.sqlite3');meal=scoped_meal()
    # 登録済みの同じ朝食を読み直すだけで、食事APIの書込み能力を使わない。
    verify_source(Candidate(**json.loads(config['old_rows']['x']['payload'])),meal,'www.log-eats.com',capture=None)
    api=production_client(store);budget=reserve(store,api,config['request_uuid'],'breakfast_direct_inventory',min(END.timestamp(),datetime.now(UTC).timestamp()+120))
    try:
        with using(budget):proof=ExternalInventory(store,BufferInventoryAdapter(api,config['organization_id']),lambda:datetime.now(UTC),config['channels']).check(m.TARGET)
    finally:release(store,budget)
    # inventory実装の成功戻り値だけを採用し、外部呼出が失敗した場合は発行しない。
    inventory={'verified':True,'checked_at':datetime.now(UTC).timestamp(),'proof':proof}
    if protected_json(path,root_owned=True)[1]!=sha:raise ValueError('直接指示の原本が変わりました')
    print(json.dumps(register(store,config,path,sha,inventory,datetime.now(UTC))),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);run(parser.parse_args().config)
