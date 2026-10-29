"""本人の朝まで送付指示に束縛し、既存正本から八件の承認依頼だけを完成する。"""
import argparse,hashlib,json,os,time,uuid
from dataclasses import asdict
from datetime import UTC,datetime
from pathlib import Path
from . import morning_registry as m
from .preparation import fingerprint

END=datetime.fromisoformat('2026-10-30T06:30:00+09:00')

def authority(path):
    from .current_day_cli import protected_json
    from .readiness import release_commit
    d,sha=protected_json(path,root_owned=True,max_bytes=524288)
    a=d['approval'];uuid.UUID(a['case_uuid'])
    if (a['contract_version']!=m.VERSION or a['target_date']!=m.TARGET or datetime.fromisoformat(a['not_after'])!=END
        or a['allowed']!=['derived_media_drive','discord_eight_approvals'] or a['limits']!={'food_writes':0,'daily_writes':0,'ai_calls':0,'sns_mutations':0}
        or not a['goal_reference']):raise ValueError('朝まで準備の本人指示が不一致です')
    release_commit(Path(__file__).resolve().parents[2],a['commit'])
    binding=fingerprint({k:v for k,v in d.items() if k!='references'})
    if set(d['references'])!={'implementation_review','deployment_approval','execution_approval'}:raise ValueError('朝まで準備の三承認原本が不足しています')
    for role,ref in d['references'].items():
        value,h=protected_json(ref['path'],root_owned=True)
        if h!=ref['sha256'] or value!={'role':role,'contract_version':m.VERSION,'config_digest':binding,'verdict':'Go' if role=='implementation_review' else 'approved'}:raise ValueError('朝まで準備の承認原本が不一致です')
    values={}
    for name in ('preview','image_review','history'):
        value,h=protected_json(d[name]['path'],root_owned=True,max_bytes=524288 if name=='preview' else 131072)
        if h!=d[name]['sha256']:raise ValueError('朝まで準備の資料SHAが不一致です')
        values[name]=value
    preview=values['preview'];review=values['image_review'];context=preview['context']
    if (preview['context_digest']!=fingerprint(context) or review['context_digest']!=preview['context_digest']
        or review['document_sha256']!=d['preview']['sha256'] or review['status']!='publishable' or review['target_date']!=m.TARGET):raise ValueError('派生画像の点検文脈が不一致です')
    expected={('x',s,0):asset for s,asset in preview['x_assets'].items()}
    expected.update({('instagram',s,i):asset for s,assets in preview['instagram_manifest']['assets'].items() for i,asset in enumerate(assets)})
    required={('x',s,0) for s in (*m.MEALS,'daily')}|{('instagram',s,0) for s in m.MEALS}|{('instagram','daily',i) for i in range(5)}
    actual={(r['platform'],r['slot'],r['ordinal']):r for r in review['reviews']}
    if set(expected)!=required or set(actual)!=required or len(review['reviews'])!=12:raise ValueError('派生画像十二件の点検が不足しています')
    checks={'source_photo_correspondence','nutrition_values','readability','composition_and_clipping','source_manifest_sha','three_meals_present','goal_percentages'}
    for key,asset in expected.items():
        r=actual[key];p=Path(asset['path'])
        if (r['status']!='publishable' or r['blocking'] or set(r['checks'])!=checks or any(v not in ('pass','not_applicable') for v in r['checks'].values())
            or any(r['checks'][c]!='pass' for c in checks-{'three_meals_present','goal_percentages'})
            or r['image_sha256']!=asset['sha256'] or p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=asset['sha256']):raise ValueError('派生画像の実バイトと独立点検が不一致です')
        if key in (('x','daily',0),('instagram','daily',0)) and r['checks']['three_meals_present']!='pass':raise ValueError('三食の合成点検が不足しています')
        if key==('instagram','daily',4) and r['checks']['goal_percentages']!='pass':raise ValueError('達成率の点検が不足しています')
    def check():
        if protected_json(path,root_owned=True,max_bytes=524288)[1]!=sha:raise ValueError('朝まで準備の構成原本が変わりました')
        for ref in (*d['references'].values(),d['preview'],d['image_review'],d['history']):
            limit=524288 if ref is d['preview'] else 131072
            if protected_json(ref['path'],root_owned=True,max_bytes=limit)[1]!=ref['sha256']:raise ValueError('朝まで準備の承認・画像点検原本が変わりました')
    return d,values,check

class Run:
    def __init__(self,store,d,values,check,clock=lambda:datetime.now(UTC)):
        self.store,self.d,self.values,self.check_files,self.clock=store,d,values,check,clock;self.key=d['approval']['case_uuid'];self.stage='register';self.cohort=None
    def register(self,path):
        now=self.clock()
        if now>=END:raise ValueError('朝まで準備の期限を超過しました')
        with self.store.transaction() as db:
            m.initialize(db)
            if m.job(db,m.TARGET):raise ValueError('朝まで準備は開始済みです。元caseの状態を照会してください')
            parents={};parent_records={}
            for table in ('next_saved_jobs','next_meal_jobs'):
                r=db.execute('SELECT * FROM '+table+' WHERE target_date=?',(m.TARGET,)).fetchone()
                if not r or r['state']!='held':raise ValueError('旧停止caseを確認できません')
                parents[table]={k:r[k] for k in ('case_uuid','first_started','deadline')}
                parent_records[table]=dict(r)
            hold=db.execute('SELECT * FROM publication_holds WHERE target_date=?',(m.TARGET,)).fetchone()
            if not hold or hold['state']!='held' or dict(hold)!=self.d['old_hold'] or parents!=self.d['old_parents']:raise ValueError('旧停止・時刻・所有原本が変わりました')
            if any(m.read_rows(db,t) for t in ('candidates','instagram_candidates','buffer_publication','instagram_attempts')):raise ValueError('既存の候補や送信を上書きしません')
            # 食品点検の原本と、元保留を残した昼食の新独立点検を明示的に要求する。
            steps={v['step']:v for v in m.read_rows(db,'preparation_steps')}
            from .generation import meal_review_policy_sha256
            from .tolerance_review import validate as validate_review
            from .next_saved import POLICY
            context=self.values['preview']['context'];draft_path=Path('/var/lib/mina-agent/daily')/m.TARGET/'draft.json';draft=json.loads(draft_path.read_text())
            for slot in m.MEALS:
                key='meal_review:'+slot+':corrected';capture=json.loads(steps['ogp_capture:'+slot+'-corrected']['result'])
                inputs={'image_sha256':context['bundle']['meals'][slot]['image_sha256'],'meal':draft['meals'][slot],'response':context['responses'][slot],'ogp_digest':capture['digest'],'policy_sha256':meal_review_policy_sha256()}
                if steps[key]['state']!='complete' or steps[key]['digest']!=fingerprint(inputs):raise ValueError('食品点検の原本入力が変わりました')
                if slot!='lunch' and json.loads(steps[key]['result'])!={'status':'publishable','reason_codes':[]}:raise ValueError('食品点検の合格証跡が不足しています')
                if slot=='lunch':
                    old=parent_records['next_saved_jobs'];manual=json.loads(old['manual']);approval=json.loads(old['approval'])
                    expected=fingerprint({'target_date':m.TARGET,'meal':draft['meals'][slot],'response':context['responses'][slot]})
                    if manual['context_digest']!=expected or approval['review_context_digest']!=expected or manual['policy_sha256']!=POLICY or manual['image_sha256']!=inputs['image_sha256'] or validate_review(manual['raw'],inputs['meal'],'publishable')!={'status':'publishable','reason_codes':[]}:raise ValueError('昼食の新独立点検原本が不一致です')
                    new=steps[key+':continuation:'+old['case_uuid']]
                    if new['state']!='complete' or json.loads(new['result'])!={'status':'publishable','reason_codes':[]}:raise ValueError('昼食の新独立点検採用が不足しています')
            sealed_files={**self.d['source_files'],str(path):hashlib.sha256(Path(path).read_bytes()).hexdigest()}
            origin={'hold':dict(hold),'parents':{k:{**v,'record_digest':fingerprint(parent_records[k])} for k,v in parents.items()},'files':sealed_files,'discord_channel':self.d['discord_channel'],
                'mina_requests_digest':fingerprint(m.read_rows(db,'mina_requests')),'preparation_steps_digest':fingerprint(m.read_rows(db,'preparation_steps')),
                'scoped_preparation_locks':m.read_rows(db,'scoped_preparation_locks') if m.exists(db,'scoped_preparation_locks') else None}
            db.execute("INSERT INTO morning_preparations VALUES(?,?,?,?,?,?,'started',?,?)",(m.TARGET,self.key,self.d['approval']['commit'],str(path),fingerprint(self.d),json.dumps(origin),now.timestamp(),END.timestamp()))
            for cohort in m.COHORTS:db.execute("INSERT INTO morning_cohorts VALUES(?,?,'preparing',NULL)",(self.key,cohort))
        self.guard();m.event(self.store,self.key,None,'registration','completed',{'new_preparation_deadline':END.isoformat(),'old_parents_preserved':parents})
    def guard(self):
        self.check_files()
        if self.clock()>=END:raise ValueError('朝まで準備の期限を超過しました')
        with self.store.transaction() as db:
            r=m.job(db,m.TARGET)
            if not r or r['case_uuid']!=self.key or r['phase'] not in ('started','partial','complete') or not m.intact(db,r):raise ValueError('朝まで準備の正本・所有・停止状態が変わりました')
    def phase(self,name,operation):
        self.stage=name;self.guard();m.event(self.store,self.key,self.cohort,name,'started',{})
        try:
            result=operation();self.guard();m.event(self.store,self.key,self.cohort,name,'completed',{});return result
        except BaseException as error:
            from .audit import safe_failure
            reason,details=safe_failure(error);details.update(error_type=type(error).__name__,automatic_write_retry=False)
            status=getattr(error,'code',getattr(error,'http_status',None))
            if type(status) is int:details['http_status']=status
            m.event(self.store,self.key,self.cohort,name,'failed',{'reason':reason,**details});raise

class ReadOnlyMeal:
    """M1のGETだけを提供し、再登録・再訂正・日次再送の能力を渡さない。"""
    def __init__(self,client,run):self.client,self.run=client,run
    def day_state(self,target):self.run.guard();return self.client.day_state(target)
    def request_state(self,key,operation):self.run.guard();return self.client.request_state(key,operation)

class ReadOnlyBuffer:
    def __init__(self,client,run):self.client,self.run=client,run
    def __getattr__(self,name):return getattr(self.client,name)
    def _request(self,query,variables=None):
        self.run.guard()
        if not query.lstrip().startswith('query'):raise ValueError('準備中のBuffer mutationは禁止です')
        return self.client._request(query,variables)

def accept_source(run,meal):
    from .registration import assert_day_state
    from .m1_contract import completed_request,validate_write
    context=run.values['preview']['context'];state=meal.day_state(m.TARGET);bundle=context['bundle'];responses=context['responses'];selected=context['requests']
    if assert_day_state(state,m.TARGET,{s:responses[s] for s in m.MEALS},run.clock())!=context['state_version']:raise ValueError('三食正本の期待版が変わりました')
    if state['nutrition_targets']!=bundle['targets']:raise ValueError('達成率の表示原本が変わりました')
    normalized={}
    for slot in (*m.MEALS,'daily'):
        r=selected[slot];lookup=meal.request_state(r['request_key'],r['operation']);ih=completed_request(lookup,r['request_key'],r['operation'],responses[slot]['result'])
        if ih!=r['input_hash'] or lookup.get('current_state_version')!=context['state_version'] or lookup.get('result_status')!='unchanged':raise ValueError('元UUIDと現在正本を照合できません')
        if fingerprint(responses[slot]['result'])!=r['result_sha256']:raise ValueError('保存済み結果原本が変わりました')
        kwargs={'meals':normalized} if slot=='daily' else {'logged_at':responses[slot]['result']['meal']['logged_at']}
        normalized[slot]=validate_write(responses[slot],slot,m.TARGET,'www.log-eats.com',operation=r['operation'],input_comment=responses[slot]['result']['normalized_comment'],**kwargs)
        expected=bundle['total'] if slot=='daily' else bundle['meals'][slot]['nutrition']
        if normalized[slot]['nutrition']!=expected:raise ValueError('派生画像の栄養表示が実正本と違います')
    if not 1800<=bundle['total']['calories']<=2000:raise ValueError('三食の実合計が範囲外です')
    return normalized

def inventory(run,api,phase):
    from .next_saved_cli import reserve
    from .buffer_budget import using,release
    from .replacement_inventory import ExternalInventory,BufferInventoryAdapter
    deadline=min(END.timestamp(),run.clock().timestamp()+120)
    budget=reserve(run.store,api,run.key,'morning:'+phase,deadline)
    try:
        with using(budget):return ExternalInventory(run.store,BufferInventoryAdapter(api,run.d['organization_id']),run.clock,run.d['channels']).check(m.TARGET)
    finally:release(run.store,budget)

def x_candidate(run,slot,hosted,normalized):
    from .domain import Candidate
    from .native_media import KEY,VERSION
    from .tracking import configured_policy
    preview=run.values['preview'];context=preview['context'];bundle=context['bundle'];r=context['requests'][slot];response=context['responses'][slot]['result'];n=normalized[slot];ogp=response['ogp']
    source={**r,'state_version':context['state_version'],'result_version':response['result_version'],'ogp_revision':ogp['revision'],
        'ogp_source_version':ogp['source_state_version' if slot=='daily' else 'source_result_version'],'ogp_image_url':ogp['image_url'],'ogp_image_sha256':ogp['image_sha256'],
        'readiness_sha256':fingerprint(run.values['history']),m.KEY:m.marker(run.key,slot)}
    if slot!='daily':source['stored_image_sha256']=response['stored_image_sha256']
    tracking=configured_policy()
    if tracking is not None:source['url_tracking']=tracking
    images={s:bundle['meals'][s]['image_sha256'] for s in m.MEALS}
    source[KEY]={'contract_version':VERSION,'asset':hosted,'review':{'status':'publishable','context_digest':preview['context_digest'],'image_sha256':hosted['sha256'],'source_images':images,'receipt_sha256':run.d['image_review']['sha256']},
        'source_images':images,**{k:source[k] for k in ('state_version','result_version','request_key','input_hash','result_sha256')},
        'body_sha256':hashlib.sha256(response['x']['publish_text'].encode()).hexdigest(),
        'legacy_ogp':{'state':'hold' if slot=='daily' else 'unverified','reason_codes':['share_ogp_mismatch' if slot=='daily' else 'ogp_delivery_unstable'],'revision':ogp['revision'],'image_sha256':ogp['image_sha256']}}
    menu=' / '.join(bundle['meals'][s]['menu'] for s in m.MEALS) if slot=='daily' else bundle['meals'][slot]['menu']
    return Candidate(m.TARGET,slot,n['comment'],n['api_text'],n['share_url'],menu,n['nutrition'],fingerprint(images) if slot=='daily' else images[slot],ogp['image_sha256'],
        'pass','publishable',False,True,True,run.d['approval']['commit'],meal_count=3 if slot=='daily' else 1,publish_text=response['x']['publish_text'],approval_source=source)

def stage(run,cohort,xs,igs,bundle,manifest):
    from .readiness import check_evidence
    from .instagram_media import HOSTS
    from .instagram_domain import InstagramCandidate
    entries=check_evidence(run.values['history'],m.TARGET,run.clock());comments=[e['comment'] for e in entries];weekly=list(comments);menus={e['menu'] for e in entries}
    for c in xs:
        if c.slot!='daily' and c.menu in menus:raise ValueError('直前十四日間の献立と重複しています')
        c.validate('www.log-eats.com',run.clock(),comments,weekly);comments.append(c.comment);weekly.append(c.comment)
    for c in igs:c.validate(run.clock(),HOSTS)
    expected=set(m.COHORTS[cohort])
    if {c.slot for c in xs}!=expected or {c.slot for c in igs}!=expected:raise ValueError('準備する投稿枠が不足しています')
    with run.store.transaction() as db:
        r=m.job(db,m.TARGET)
        if not r or r['case_uuid']!=run.key or not m.intact(db,r) or run.clock()>=END:raise ValueError('候補登録前の原本・期限が変わりました')
        state=db.execute('SELECT state FROM morning_cohorts WHERE case_uuid=? AND cohort=?',(run.key,cohort)).fetchone()
        if not state or state['state']!='preparing':raise ValueError('同じ候補を再作成しません')
        for platform,cs in (('x',xs),('instagram',igs)):
            table='candidates' if platform=='x' else 'instagram_candidates'
            for c in cs:
                if db.execute('SELECT 1 FROM '+table+' WHERE target_date=? AND slot=?',(m.TARGET,c.slot)).fetchone():raise ValueError('既存承認を上書きしません')
                payload=asdict(c)
                db.execute('INSERT INTO '+table+"(target_date,slot,revision,payload,digest,scheduled,state) VALUES(?,?,1,?,?,?,'approval_pending')",(m.TARGET,c.slot,json.dumps(payload,ensure_ascii=False),c.digest,c.scheduled_at.timestamp()))
                db.execute('INSERT INTO morning_members VALUES(?,?,?,?,1,?,?)',(run.key,platform,c.slot,cohort,c.digest,fingerprint(payload)))
        material={'bundle':bundle,'manifest':manifest,'xs':{c.slot:asdict(c) for c in xs},'igs':{c.slot:asdict(c) for c in igs}}
        db.execute("UPDATE morning_cohorts SET state='staged',material=? WHERE case_uuid=? AND cohort=? AND state='preparing'",(json.dumps(material,ensure_ascii=False),run.key,cohort))
        db.execute("UPDATE morning_preparations SET phase='partial' WHERE target_date=?",(m.TARGET,))

def send_cohort(run,ig,discord,cohort):
    from .routine_handoff import transmit
    slots=m.COHORTS[cohort];run.store.issue_cards(m.TARGET,run.clock(),slots=slots)
    for row in ig.rows(m.TARGET):
        if row['slot'] in slots and row['state']=='approval_pending':ig.queue_approval(row)
    for platform,table in (('x','notices'),('instagram','instagram_notices')):
        while True:
            run.guard()
            with run.store.transaction() as db:
                uncertain=db.execute('SELECT slot FROM '+table+" WHERE target_date=? AND kind='approval' AND state IN ('sending','outcome_unknown')",(m.TARGET,)).fetchall()
                if any(r['slot'] in slots for r in uncertain):raise ValueError('同じ組の通知に成否不明があります。元受領の照会が必要です')
                rows=db.execute('SELECT * FROM '+table+" WHERE target_date=? AND kind='approval' AND state='pending' ORDER BY id",(m.TARGET,)).fetchall()
                row=next((dict(r) for r in rows if r['slot'] in slots),None)
                if not row:break
                if not m.notice(db,row,platform):raise ValueError('承認通知の対象版が変わりました')
                db.execute('UPDATE '+table+" SET state='sending' WHERE id=? AND state='pending'",(row['id'],))
            try:transmit(run.store,row,platform,discord)
            except BaseException:
                with run.store.transaction() as db:db.execute('UPDATE '+table+" SET state='outcome_unknown' WHERE id=?",(row['id'],))
                raise
            with run.store.transaction() as db:db.execute('UPDATE '+table+" SET state='sent' WHERE id=? AND state='sending'",(row['id'],))
    with run.store.transaction() as db:
        r=m.job(db,m.TARGET)
        if not m.receipts(db,r,cohort):raise ValueError('全承認カードの同ID受領が未確認です')
        db.execute("UPDATE morning_cohorts SET state='notified' WHERE case_uuid=? AND cohort=? AND state='staged'",(run.key,cohort));m.maybe_ready(db,m.TARGET)

def run(path):
    from .store import Store
    from .instagram_store import InstagramStore
    from .current_day_cli import scoped_meal
    from .buffer_publisher import production_client
    from .instagram_publisher import instagram_channel
    from .next_saved_cli import reserve
    from .buffer_budget import using,release
    from .drive import DriveClient
    from .instagram_media import host_asset
    from .instagram_runtime import caption
    from .instagram_domain import InstagramCandidate
    from .m1_contract import verify_source
    from .clients import DiscordClient
    d,values,check=authority(path);store=Store('/var/lib/mina-agent/mina.sqlite3');ig=InstagramStore(store.path);run=Run(store,d,values,check)
    os.environ['MINA_PUBLISH_ENABLED']='false';os.environ['MINA_INSTAGRAM_PUBLISH_ENABLED']='false'
    run.register(path);meal=ReadOnlyMeal(scoped_meal(),run);api=ReadOnlyBuffer(production_client(store),run);preview=values['preview'];bundle=preview['context']['bundle'];manifest=preview['instagram_manifest'];discord=DiscordClient(os.environ['DISCORD_BOT_TOKEN'],d['discord_channel'])
    try:
        normalized=run.phase('initial_m1_acceptance',lambda:accept_source(run,meal));run.phase('initial_external_inventory',lambda:inventory(run,api,'initial'))
        budget=reserve(store,api,run.key,'morning:channel',min(END.timestamp(),run.clock().timestamp()+120))
        try:
            with using(budget):channel=run.phase('instagram_channel',lambda:instagram_channel(api))
        finally:release(store,budget)
        drive=DriveClient.from_env();drive.before_media_write=run.guard
        def drive_guard(url,data,method,operation):
            run.guard();return operation()
        drive.request_guard=drive_guard;all_xs={}
        for cohort,slots in m.COHORTS.items():
            run.cohort=cohort;xs=[];igs=[]
            for slot in slots:
                xasset=run.phase('drive_x_'+slot,lambda slot=slot:host_asset(preview['x_assets'][slot],drive,store,allow_public=True));c=x_candidate(run,slot,xasset,normalized);xs.append(c);all_xs[slot]=c
                igassets=[]
                for i,asset in enumerate(manifest['assets'][slot]):igassets.append(run.phase('drive_ig_'+slot+'_'+str(i),lambda asset=asset:host_asset(asset,drive,store,allow_public=True)))
                source={'bundle_sha256':bundle['bundle_sha256'],'state_version':bundle['state_version'],'x_digests':{},m.KEY:m.marker(run.key,slot)}
                igs.append(InstagramCandidate(m.TARGET,slot,channel['id'],channel['serviceId'],'post' if slot=='daily' else 'story',caption(bundle,slot),igassets,source))
            for c in igs:c.source['x_digests']={s:all_xs[s].digest for s in (m.MEALS if c.slot!='daily' else (*m.MEALS,'daily'))}
            normalized=run.phase('m1_before_'+cohort,lambda:accept_source(run,meal))
            for c in xs:run.phase('native_source_'+c.slot,lambda c=c:verify_source(c,meal,'www.log-eats.com',capture=None))
            run.phase('external_before_'+cohort,lambda cohort=cohort:inventory(run,api,'stage_'+cohort))
            run.phase('stage_'+cohort,lambda:stage(run,cohort,xs,igs,bundle,manifest));run.phase('notify_'+cohort,lambda:send_cohort(run,ig,discord,cohort))
            print(json.dumps({'cohort':cohort,'cards_verified':len(slots)*2,'sns_writes':0,'food_writes':0,'ai_calls':0}),flush=True)
        with store.transaction() as db:
            r=m.job(db,m.TARGET)
            if not all(m.receipts(db,r,c) for c in m.COHORTS):raise ValueError('八件の同ID受領を確認できません')
            db.execute("UPDATE morning_preparations SET phase='complete' WHERE target_date=? AND case_uuid=?",(m.TARGET,run.key))
        print(json.dumps({'target_date':m.TARGET,'state':'eight_approval_cards_verified','count':8,'sns_writes':0,'food_writes':0,'daily_writes':0,'ai_calls':0}),flush=True)
    except BaseException:
        with store.transaction() as db:
            if run.cohort:db.execute("UPDATE morning_cohorts SET state='held' WHERE case_uuid=? AND cohort=? AND state NOT IN ('notified','ready')",(run.key,run.cohort))
            db.execute("UPDATE morning_preparations SET phase='partial' WHERE target_date=?",(m.TARGET,))
        raise

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);run(parser.parse_args().config)
