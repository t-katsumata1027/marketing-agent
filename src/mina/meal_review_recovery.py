"""保存済み朝昼を保持し、本人許可の昼食訂正後点検だけ一度追加する。"""
import hashlib,json
from contextlib import nullcontext
from contextvars import ContextVar
from datetime import UTC,datetime
from pathlib import Path
from .clients import MealClient
from .daily_pipeline import DeadlineClient,write_artifact
from .domain import Candidate,JST,instant
from .m1_contract import validate_state,completed_request,verify_source
from .ogp import capture_ogp
from .preparation import fingerprint
from .preparation_status import PreparationDeadline,QualityHold
from .readiness import read_json,release_commit,check_evidence,build_candidates
from .request_ledger import RequestLedger

TARGET='2026-10-27'
END=datetime(2026,10,27,1,tzinfo=JST)
STEP='meal_review:lunch:corrected'
JOB_ROOT=Path('/var/lib/mina-agent/codex-jobs')
PARENTS=('interrupted_review_recoveries','interrupted_image_repairs','correction_recovery_claims')
_scope=ContextVar('one_lunch_corrected_review',default=None)


def review_once(journal,target,step,inputs,operation):
    """通常点検は無変更。許可された昼訂正後だけ元unknownを残し別採用する。"""
    from .next_saved import CURRENT
    gate=CURRENT.get()
    if gate is not None:
        result=gate.review(journal,target,step,inputs,operation)
        if result is not None:return result
    scope=_scope.get()
    if scope is None:
        from .ogp_finish_recovery import cached_lunch_review
        cached=cached_lunch_review(target,step,inputs)
        if cached is not None:return cached
    if scope is None or target!=TARGET or step!=STEP:return journal.run(target,step,inputs,operation)
    scope['check']()
    if fingerprint(inputs)!=scope['digest']:raise ValueError('review_recovery_input_changed')
    new=STEP+':recovery:'+scope['id']
    result=journal.run(target,new,{'original_digest':scope['digest'],'approval_sha256':scope['sha'],'inputs':inputs},operation)
    scope['check']()
    if result!={'status':'publishable','reason_codes':[]}:raise QualityHold('追加点検の品質保留を保持します',new,result.get('reason_codes',()))
    return journal.run(target,STEP+':adoption:'+scope['id'],
        {'original_digest':scope['digest'],'review_sha256':fingerprint(result),'approval_sha256':scope['sha']},lambda:result)


def state_result(value):
    """日GETの補助項目は、正本内の同値を確認してから除外する。"""
    result=dict(value)
    for key,expected in {'meal_type':result['meal']['meal_type'],'logged_at':result['meal']['logged_at'],'share_url':result['share']['url']}.items():
        if key in result and result.pop(key)!=expected:raise ValueError('review_recovery_alias_changed')
    return result


def resume_review(store,directory,approval_path,history_path,root,commit,host,ai,meal,
                  clock=lambda:datetime.now(UTC),capture=capture_ogp):
    """旧claimを解除せず、版と原本へ束縛した新しい一意許可で残工程を進める。"""
    def deadline():
        if not datetime(2026,10,26,21,30,tzinfo=JST)<=instant(clock())<END:raise PreparationDeadline('lunch_review_recovery_deadline')
    deadline();release_commit(root,commit)
    approval=read_json(approval_path)
    required={'target_date','deadline','commit','recovery_id','scope','approval_reference','original_digest','failed_job',
              'draft_sha256','saved_sha256','steps_sha256','parents_sha256','state_version','current_results_sha256'}
    if (set(approval)!=required or approval['target_date']!=TARGET or approval['deadline']!=END.isoformat()
            or approval['commit']!=commit or approval['scope']!='one_lunch_corrected_review_then_remaining_candidates'
            or not isinstance(approval['approval_reference'],str) or not approval['approval_reference'].strip()):raise ValueError('review_recovery_approval_invalid')
    key=approval['recovery_id'];MealClient.request_key(key);approval_sha=fingerprint(approval)
    base=Path(directory)/TARGET;draft=read_json(base/'draft.json');saved=read_json(base/'drive.json')
    if draft.get('target_date')!=TARGET or saved.get('target_date')!=TARGET or saved.get('state')!='images_drive_verified' or set(saved.get('images',{}))!={'breakfast','lunch','dinner'}:
        raise ValueError('review_recovery_images_invalid')
    artifacts={}
    def bind(path,sha=None):
        p=Path(path)
        if p.is_symlink() or not p.is_file():raise ValueError('review_recovery_artifact_invalid')
        digest=hashlib.sha256(p.read_bytes()).hexdigest()
        if sha is not None and digest!=sha:raise ValueError('review_recovery_artifact_changed')
        artifacts[p]=digest
    bind(base/'draft.json',approval['draft_sha256']);bind(base/'drive.json',approval['saved_sha256'])
    for item in saved['images'].values():
        bind(item['path'],item['sha256'])
        if (Path(item['path']).stat().st_size!=item['size'] or item.get('image_review')!={'status':'image_acceptable','reason_codes':[]}
                or item['drive'].get('verified') is not True or item['drive'].get('sha256')!=item['sha256']):raise ValueError('review_recovery_image_binding_changed')
    job=Path(approval['failed_job'])
    if job.is_symlink() or job.parent!=JOB_ROOT or read_json(job/'state.json')!={'state':'failed','reason':'authentication'} or (job/'result.json').exists():raise ValueError('review_recovery_failed_job_changed')
    diagnostic=read_json(job/'diagnostic.json')
    if diagnostic.get('exit_code')!=1 or diagnostic.get('input_image_sha256')!=[saved['images']['lunch']['sha256']]:raise ValueError('review_recovery_failed_input_changed')
    receipts={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in job.iterdir() if p.is_file() and not p.is_symlink()}
    history=read_json(history_path);check_evidence(history,TARGET,clock())
    if not 0<=(instant(clock())-datetime.fromisoformat(history['checked_at'])).total_seconds()<=120:raise ValueError('review_recovery_history_stale')
    deadline();state=meal.day_state(TARGET);validate_state(state,TARGET,clock());deadline()
    current={m['meal']['meal_type']:state_result(m) for m in state['meals']}
    if state['state_version']!=approval['state_version'] or state['meal_count']!=2 or set(current)!={'breakfast','lunch'} or state.get('daily_share') or fingerprint(current)!=approval['current_results_sha256']:
        raise ValueError('review_recovery_current_state_changed')
    with store.transaction() as db:
        rows={r['step']:dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=?',(TARGET,))}
        parents={t:dict(db.execute('SELECT * FROM '+t+' WHERE target_date=?',(TARGET,)).fetchone()) for t in PARENTS}
        requests={r['request_key']:dict(r) for r in db.execute('SELECT * FROM mina_requests WHERE target_date=?',(TARGET,))}
        if (fingerprint(rows)!=approval['steps_sha256'] or fingerprint(parents)!=approval['parents_sha256']
                or any(p['state']!='held' or p['old_usage_status']!='unknown' or fingerprint(json.loads(p['approval_json']))!=p['approval_sha'] for p in parents.values())
                or rows.get(STEP,{}).get('digest')!=approval['original_digest']
                or {k for k,v in rows.items() if v['state']!='complete'}!={'image_review:lunch','correction_plan:breakfast',STEP}
                or any(rows[k]['state']!='outcome_unknown' for k in ('image_review:lunch','correction_plan:breakfast',STEP))
                or db.execute('SELECT state FROM preparation WHERE target_date=?',(TARGET,)).fetchone()['state']!='held:correction_recovery'
                or db.execute('SELECT 1 FROM candidates WHERE target_date=?',(TARGET,)).fetchone()
                or {(r['slot'],r['operation'],r['phase']) for r in requests.values()}!={(s,o,'complete') for s in ('breakfast','lunch') for o in ('meal_register','meal_correction')}
                or len(requests)!=4):raise ValueError('review_recovery_baseline_changed')
    ledger=RequestLedger(store)
    old_receipts={job:receipts}
    for parent in parents.values():
        document=json.loads(parent['approval_json'])
        for field in ('cli_job','parent_review_job','failed_job'):
            if not document.get(field):continue
            p=Path(document[field])
            if p.is_symlink() or p.parent!=JOB_ROOT or not p.is_dir():raise ValueError('review_recovery_parent_job_changed')
            old_receipts[p]={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir() if f.is_file() and not f.is_symlink()}
    for request in requests.values():ledger.restore(request['request_key'])
    for slot in ('breakfast','lunch'):
        response=json.loads(rows['meal_correction:'+slot]['result']);binding=json.loads(rows['correction_key:'+slot]['result'])
        original=json.loads(rows['meal_register:'+slot]['result'])['result']
        if (response['result']!=current[slot] or binding['key'] not in requests
                or current[slot]['image_sha256']!=saved['images'][slot]['sha256']
                or current[slot]['stored_image_sha256']!=original['stored_image_sha256']):raise ValueError('review_recovery_saved_correction_changed')
        completed_request(meal.request_state(binding['key'],'meal_correction'),binding['key'],'meal_correction',current[slot]);deadline()
    for name,row in rows.items():
        if row['state']!='complete':continue
        result=json.loads(row['result'])
        if name.startswith(('image:','ogp_capture:')) and isinstance(result,dict) and result.get('path'):
            bind(result['path'],result.get('preview_sha256') or result.get('sha256'))
    def files():
        deadline()
        if fingerprint(read_json(approval_path))!=approval_sha:raise ValueError('review_recovery_permission_changed')
        for p,sha in artifacts.items():
            if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise ValueError('review_recovery_artifact_changed')
        for p,snapshot in old_receipts.items():
            if p.is_symlink() or {f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir() if f.is_file() and not f.is_symlink()}!=snapshot:raise ValueError('review_recovery_job_changed')
    files()
    with store.transaction() as db:
        deadline()
        db.execute('''CREATE TABLE IF NOT EXISTS meal_review_recovery_claims (
            target_date TEXT PRIMARY KEY,step TEXT NOT NULL,original_digest TEXT NOT NULL,recovery_id TEXT NOT NULL UNIQUE,
            approval_sha TEXT NOT NULL,state TEXT NOT NULL,UNIQUE(target_date,step,original_digest))''')
        if db.execute('SELECT 1 FROM meal_review_recovery_claims WHERE target_date=?',(TARGET,)).fetchone():raise ValueError('review_recovery_already_claimed')
        db.execute('INSERT INTO meal_review_recovery_claims VALUES(?,?,?,?,?,?)',(TARGET,STEP,approval['original_digest'],key,approval_sha,'claimed'))
        db.execute("UPDATE preparation SET state='resuming:lunch_review' WHERE target_date=?",(TARGET,))
    completed_added={}
    def checkpoint(connection=None):
        files()
        with (store.transaction() if connection is None else nullcontext(connection)) as db:
            claim=db.execute('SELECT * FROM meal_review_recovery_claims WHERE target_date=?',(TARGET,)).fetchone()
            if not claim or claim['state']!='claimed' or claim['recovery_id']!=key or claim['approval_sha']!=approval_sha:raise ValueError('review_recovery_claim_changed')
            if any(dict(db.execute('SELECT * FROM '+t+' WHERE target_date=?',(TARGET,)).fetchone())!=value for t,value in parents.items()):raise ValueError('review_recovery_parent_changed')
            live={r['step']:dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=?',(TARGET,))}
            if any(live.get(k)!=v for k,v in rows.items()):raise ValueError('review_recovery_old_step_changed')
            permitted={STEP+':recovery:'+key,STEP+':adoption:'+key,'ogp_review:lunch','daily_request_key','daily_share','ogp_capture:daily','ogp_review:daily'}
            permitted|={stage+':dinner' for stage in ('meal_request_key','meal_register','meal_review','correction_plan','correction_key','meal_correction','ogp_capture','ogp_review')}
            permitted|={'meal_review:dinner:corrected','ogp_capture:dinner-corrected'}
            if set(live)-set(rows)-permitted:raise ValueError('review_recovery_unexpected_step')
            for k,v in live.items():
                if k in rows:continue
                if k in completed_added and completed_added[k]!=v:raise ValueError('review_recovery_completed_step_changed')
                if v['state']=='complete':completed_added[k]=v
            seen=set()
            for r in db.execute('SELECT * FROM mina_requests WHERE target_date=?',(TARGET,)):
                r=dict(r);pair=(r['slot'],r['operation'])
                if pair in seen:raise ValueError('review_recovery_duplicate_request')
                seen.add(pair)
                if r['request_key'] in requests:
                    old=requests[r['request_key']]
                    if r['phase']!='complete' or any(r[k]!=v for k,v in old.items() if k not in ('updated','observation')):raise ValueError('review_recovery_old_request_changed')
                    continue
                if pair not in {('dinner','meal_register'),('dinner','meal_correction'),('daily','daily_share')} or r['phase'] not in ('prepared','dispatching','response_received','complete'):raise ValueError('review_recovery_foreign_request')
                slot,operation=pair;prefix='daily_request_key' if slot=='daily' else ('correction_key:' if operation=='meal_correction' else 'meal_request_key:')+slot
                record=live.get(prefix,{});binding=json.loads(record.get('result') or '{}');payload=json.loads(r['input_json'])
                sha=hashlib.sha256(r['image']).hexdigest() if r['image'] is not None else None
                if (record.get('state')!='complete' or binding.get('key')!=r['request_key'] or payload.get('expected_state_version')!=binding.get('expected_state_version')
                        or sha!=r['image_sha256'] or fingerprint({'operation':operation,'target_date':TARGET,'slot':slot,'payload':payload,'image_sha256':sha})!=r['input_digest']):raise ValueError('review_recovery_request_changed')
                if operation=='meal_register':
                    from .domain import slot_time,date
                    expected={'contract_version':'mina-write-v1','meal_type':slot,'logged_at':slot_time(date.fromisoformat(TARGET),slot).isoformat(),
                        'comment':draft['meals'][slot]['comment'],'expected_state_version':binding['expected_state_version'],'filename':f'{TARGET}-{slot}.jpg'}
                    if payload!=expected or sha!=saved['images'][slot]['sha256']:raise ValueError('review_recovery_registration_changed')
                elif operation=='meal_correction':
                    proposal=json.loads(live['correction_plan:dinner']['result']);source=json.loads(live['meal_register:dinner']['result'])['result']
                    expected={'contract_version':'mina-write-v1','reason':'codex_secondary_review','share_url':source['share']['url'],'foods':proposal['foods'],
                        'comment':source['normalized_comment'],'expected_state_version':binding['expected_state_version']}
                    if payload!=expected or r['image'] is not None:raise ValueError('review_recovery_patch_changed')
                elif payload!={'contract_version':'mina-write-v1','date':TARGET,'comment':draft['daily_comment'],'expected_meal_count':3,'expected_state_version':binding['expected_state_version']} or r['image'] is not None:raise ValueError('review_recovery_daily_changed')
            if not set(requests)<=set(r['request_key'] for r in db.execute('SELECT request_key FROM mina_requests WHERE target_date=?',(TARGET,))):raise ValueError('review_recovery_old_request_missing')
            deadline()
    ai,meal=(DeadlineClient(c,checkpoint) for c in (ai,meal))
    def captured(url,allowed):checkpoint();return capture(url,allowed)
    token=_scope.set({'id':key,'digest':approval['original_digest'],'sha':approval_sha,'check':checkpoint})
    stage='registration'
    try:
        from .registration import register_day
        reviewed=register_day(store,draft,saved,base/'ogp',meal,ai,host,capture=captured,allow_correction=True)
        checkpoint();write_artifact(base/'posts-reviewed.json',reviewed)
        stage='candidates';check_evidence(history,TARGET,clock())
        candidates=build_candidates(reviewed,history,commit,host,clock())
        for payload in candidates:checkpoint();verify_source(Candidate(**payload),meal,host,captured,clock=clock)
        checkpoint();manifest=base/'candidates.json';write_artifact(manifest,candidates);checkpoint()
        from .runtime import import_manifest
        import_manifest(store,manifest,host,clock(),guard=checkpoint);store.preparation_done(TARGET)
        with store.transaction() as db:db.execute("UPDATE meal_review_recovery_claims SET state='complete' WHERE target_date=?",(TARGET,))
        return {'target_date':TARGET,'state':'approval_ready','post_count':4}
    except BaseException as error:
        with store.transaction() as db:
            db.execute("UPDATE meal_review_recovery_claims SET state='held' WHERE target_date=?",(TARGET,))
            db.execute("UPDATE preparation SET state='held:lunch_review_recovery' WHERE target_date=?",(TARGET,))
        store.record_preparation_failure(TARGET,stage,error,clock());raise
    finally:_scope.reset(token)
