"""保存済み朝食と未確認訂正案を保持し、追加本人許可だけで一度訂正を再開する。"""
import hashlib,json
import math
from contextlib import nullcontext
from contextvars import ContextVar
from datetime import UTC,datetime
from pathlib import Path
from .clients import MealClient
from .daily_pipeline import DeadlineClient,write_artifact
from .domain import Candidate,JST,instant
from .m1_contract import validate_state,completed_request,verify_source
from .ogp import capture_ogp
from .preparation import PreparationJournal,fingerprint
from .preparation_status import PreparationDeadline
from .readiness import read_json,release_commit,check_evidence,build_candidates
from .registration import register_day
from .runtime import import_manifest
from .request_ledger import RequestLedger

TARGET='2026-10-27'
END=datetime(2026,10,26,23,30,tzinfo=JST)
JOB_ROOT=Path('/var/lib/mina-agent/codex-jobs')
_scope=ContextVar('one_correction_recovery',default=None)


def correction_proposal(journal,target,slot,inputs,propose):
    """通常呼出しは無変更。追加許可中の朝食だけ別提案・採用証跡へ結ぶ。"""
    scope=_scope.get()
    if scope is None or target!=TARGET or slot!='breakfast':
        return journal.run(target,'correction_plan:'+slot,inputs,propose)
    scope['check']()
    if fingerprint(inputs)!=scope['old_digest']:raise ValueError('correction_recovery_proposal_changed')
    step='correction_plan:breakfast:recovery:'+scope['id']
    proposal=journal.run(target,step,{'original_digest':scope['old_digest'],'approval_sha256':scope['approval_sha'],'inputs':inputs},propose)
    scope['check']()
    return journal.run(target,'correction_adoption:breakfast:'+scope['id'],
        {'proposal_step':step,'proposal_sha256':fingerprint(proposal),'original_digest':scope['old_digest'],'approval_sha256':scope['approval_sha']},lambda:proposal)


def resume_correction(store,directory,approval_path,history_path,root,commit,host,ai,meal,
                      clock=lambda:datetime.now(UTC),capture=capture_ogp):
    """旧23時の許可は変更せず、この追加許可の23:30だけを各工程に適用する。"""
    def deadline():
        if not datetime(2026,10,26,21,30,tzinfo=JST)<=instant(clock())<END:raise PreparationDeadline('correction_recovery_deadline')
    deadline();release_commit(root,commit)
    approval=read_json(approval_path);key=approval.get('recovery_id');MealClient.request_key(key)
    required={'target_date','deadline','commit','recovery_id','scope','approval_reference','parent_id','repair_id',
              'original_digest','failed_job','draft_sha256','saved_sha256','state_version','result_version',
              'saved_request_uuid','image_sha256','stored_image_sha256'}
    if (set(approval)!=required or approval['target_date']!=TARGET or approval['deadline']!=END.isoformat()
            or approval['commit']!=commit or approval['scope']!='one_breakfast_correction_then_remaining_candidates'
            or not isinstance(approval['approval_reference'],str) or not approval['approval_reference'].strip()):
        raise ValueError('correction_recovery_approval_invalid')
    for k in ('parent_id','repair_id','saved_request_uuid'):MealClient.request_key(approval[k])
    approval_sha=fingerprint(approval);base=Path(directory)/TARGET
    draft=read_json(base/'draft.json');saved=read_json(base/'drive.json')
    if (draft.get('target_date')!=TARGET or saved.get('target_date')!=TARGET
            or saved.get('state')!='images_drive_verified' or set(saved.get('images',{}))!={'breakfast','lunch','dinner'}):
        raise ValueError('correction_recovery_saved_images_invalid')
    def files():
        for name,k in (('draft.json','draft_sha256'),('drive.json','saved_sha256')):
            p=base/name
            if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=approval[k]:raise ValueError('correction_recovery_artifact_changed')
        for image in saved['images'].values():
            p=Path(image['path'])
            if (p.is_symlink() or not p.is_file() or p.stat().st_size!=image['size'] or hashlib.sha256(p.read_bytes()).hexdigest()!=image['sha256']
                    or image.get('image_review')!={'status':'image_acceptable','reason_codes':[]}
                    or image.get('drive',{}).get('verified') is not True or image['drive'].get('sha256')!=image['sha256']):
                raise ValueError('correction_recovery_image_changed')
    files();history=read_json(history_path);check_evidence(history,TARGET,clock())
    if not 0<=(instant(clock())-datetime.fromisoformat(history['checked_at'])).total_seconds()<=120:raise ValueError('correction_recovery_history_stale')
    deadline();state=meal.day_state(TARGET);validate_state(state,TARGET,clock());deadline()
    if (state['state_version']!=approval['state_version'] or state['meal_count']!=1
            or state['meals'][0]['meal']['meal_type']!='breakfast' or state.get('daily_share')):
        raise ValueError('correction_recovery_state_changed')
    current=state['meals'][0]
    if any(current.get(k)!=approval[k] for k in ('result_version','image_sha256','stored_image_sha256')):
        raise ValueError('correction_recovery_result_changed')
    lookup=meal.request_state(approval['saved_request_uuid'],'meal_register')
    completed_request(lookup,approval['saved_request_uuid'],'meal_register',current)
    if lookup.get('result_status')!='unchanged':raise ValueError('correction_recovery_saved_request_changed')
    restored=RequestLedger(store).restore(approval['saved_request_uuid'])
    if (restored['operation'],restored['target_date'],restored['slot'],restored['phase'],restored['image_sha256'])!=('meal_register',TARGET,'breakfast','complete',approval['image_sha256']):
        raise ValueError('correction_recovery_saved_request_changed')
    job=Path(approval['failed_job'])
    if (job.is_symlink() or job.parent!=JOB_ROOT or read_json(job/'state.json')!={'state':'failed','reason':'execution'}
            or (job/'result.json').exists()):raise ValueError('correction_recovery_job_invalid')
    diag=read_json(job/'diagnostic.json')
    if diag.get('exit_code')!=1 or diag.get('input_image_sha256')!=[approval['image_sha256']]:raise ValueError('correction_recovery_job_binding_changed')
    receipt={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in job.iterdir() if p.is_file() and not p.is_symlink()}
    old_receipts={job:receipt}
    with store.transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS correction_recovery_claims (
            target_date TEXT PRIMARY KEY,slot TEXT NOT NULL,original_digest TEXT NOT NULL,recovery_id TEXT NOT NULL UNIQUE,
            approval_sha TEXT NOT NULL,approval_json TEXT NOT NULL,state TEXT NOT NULL,old_usage_status TEXT NOT NULL,
            UNIQUE(target_date,slot,original_digest))''')
        if db.execute('SELECT 1 FROM correction_recovery_claims WHERE target_date=?',(TARGET,)).fetchone():raise ValueError('correction_recovery_already_claimed')
        parents={}
        for table,idkey in (('interrupted_review_recoveries','parent_id'),('interrupted_image_repairs','repair_id')):
            r=db.execute('SELECT * FROM '+table+' WHERE target_date=?',(TARGET,)).fetchone()
            if not r or r['state']!='held' or r['recovery_id']!=approval[idkey]:raise ValueError('correction_recovery_parent_changed')
            parents[table]=dict(r)
            document=json.loads(r['approval_json'])
            expected_scope='one_interrupted_lunch_review_then_four_candidates' if table=='interrupted_review_recoveries' else 'one_lunch_image_repair_then_four_candidates'
            if (fingerprint(document)!=r['approval_sha'] or document.get('recovery_id')!=r['recovery_id']
                    or document.get('target_date')!=TARGET or document.get('scope')!=expected_scope
                    or document.get('deadline')!=datetime(2026,10,26,23,tzinfo=JST).isoformat()
                    or document.get('draft_sha256')!=approval['draft_sha256'] or r['old_usage_status']!='unknown'):
                raise ValueError('correction_recovery_parent_approval_changed')
            for field in ('cli_job','parent_review_job'):
                if not document.get(field):continue
                p=Path(document[field])
                if p.is_symlink() or p.parent!=JOB_ROOT or not p.is_dir():raise ValueError('correction_recovery_parent_job_changed')
                s=read_json(p/'state.json')
                expected_image=document['image_sha256']
                if field=='cli_job':
                    if (s!={'state':'failed','reason':'execution'} or (p/'result.json').exists()
                            or not any(f.is_file() and not f.is_symlink() and hashlib.sha256(f.read_bytes()).hexdigest()==expected_image for f in p.glob('input-*'))):
                        raise ValueError('correction_recovery_parent_job_changed')
                else:
                    result=read_json(p/'result.json');diag_parent=read_json(p/'diagnostic.json')
                    from .review_diagnostics import validate_findings
                    validate_findings(result.get('findings'))
                    if (s!={'state':'complete','reason':None} or result.get('status')!='hold' or result.get('reason_codes')!=['image_analysis_mismatch']
                            or diag_parent.get('exit_code')!=0 or diag_parent.get('input_image_sha256')!=[expected_image]):
                        raise ValueError('correction_recovery_parent_job_changed')
                old_receipts[p]={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir() if f.is_file() and not f.is_symlink()}
        prep=db.execute('SELECT state FROM preparation WHERE target_date=?',(TARGET,)).fetchone()
        rows={r['step']:dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=?',(TARGET,))}
        old=rows.get('correction_plan:breakfast',{})
        foods={'foods':{s:draft['meals'][s]['foods'] for s in ('breakfast','lunch','dinner')}}
        expected_steps={'daily_draft','menu_nutrition:'+fingerprint(foods),'image:breakfast','image_review:breakfast','image:lunch','image_review:lunch',
            'image_review:lunch:interrupted:'+approval['parent_id'],'image:lunch:'+approval['repair_id'],'image_review:lunch:'+approval['repair_id'],
            'review_adoption:lunch:'+approval['repair_id'],'image:dinner','image_review:dinner','drive_upload:breakfast','drive_upload:lunch','drive_upload:dinner',
            'meal_request_key:breakfast','meal_register:breakfast','ogp_capture:breakfast','meal_review:breakfast','correction_plan:breakfast'}
        if (not prep or prep['state']!='held:interrupted_review' or old.get('state')!='outcome_unknown' or old.get('digest')!=approval['original_digest']
                or set(rows)!=expected_steps or any(v['state']!=('outcome_unknown' if k in ('image_review:lunch','correction_plan:breakfast') else 'complete') for k,v in rows.items())
                or db.execute('SELECT 1 FROM candidates WHERE target_date=?',(TARGET,)).fetchone()
                or any(k.startswith(('correction_key:','meal_correction:','daily_')) and k not in ('daily_draft',) for k in rows)
                or any(k.startswith(('meal_register:lunch','meal_register:dinner')) for k in rows)):
            raise ValueError('correction_recovery_original_unresolved')
        budget=json.loads(rows['menu_nutrition:'+fingerprint(foods)]['result'])
        if (set(budget)!=set(foods['foods']) or any(not isinstance(budget[s],list) or len(budget[s])!=len(foods['foods'][s])
                or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=10000 for v in budget[s]) for s in budget)
                or not 1800<=sum(map(sum,budget.values()))<=2000):raise ValueError('correction_recovery_budget_changed')
        old_lunch=json.loads(rows['image:lunch']['result'])
        if old_lunch['sha256']!=json.loads(parents['interrupted_review_recoveries']['approval_json'])['image_sha256']:
            raise ValueError('correction_recovery_old_image_changed')
        original_parent=json.loads(parents['interrupted_review_recoveries']['approval_json'])
        repair_parent=json.loads(parents['interrupted_image_repairs']['approval_json'])
        if (parents['interrupted_review_recoveries']['original_digest']!=rows['image_review:lunch']['digest']
                or parents['interrupted_image_repairs']['original_digest']!=rows['image_review:lunch']['digest']
                or original_parent.get('original_digest')!=rows['image_review:lunch']['digest']
                or repair_parent.get('parent_recovery_id')!=approval['parent_id']
                or repair_parent.get('parent_hold_digest')!=rows['image_review:lunch:interrupted:'+approval['parent_id']]['digest']):
            raise ValueError('correction_recovery_parent_binding_changed')
        old_lunch_path=Path(old_lunch['path'])
        if old_lunch_path.is_symlink() or hashlib.sha256(old_lunch_path.read_bytes()).hexdigest()!=old_lunch['sha256']:raise ValueError('correction_recovery_old_image_changed')
        requests=list(db.execute('SELECT * FROM mina_requests WHERE target_date=?',(TARGET,)))
        if len(requests)!=1 or requests[0]['request_key']!=approval['saved_request_uuid'] or requests[0]['operation']!='meal_register' or requests[0]['slot']!='breakfast' or requests[0]['phase']!='complete':
            raise ValueError('correction_recovery_existing_request')
        original=json.loads(rows['meal_register:breakfast']['result'])
        inputs={'meal':draft['meals']['breakfast'],'image_sha256':saved['images']['breakfast']['sha256'],'source':original['result']}
        if fingerprint(inputs)!=approval['original_digest'] or original['result']!=current:raise ValueError('correction_recovery_source_changed')
        old_request=dict(requests[0]);completed_added={}
        deadline();db.execute('INSERT INTO correction_recovery_claims VALUES(?,?,?,?,?,?,?,?)',
            (TARGET,'breakfast',approval['original_digest'],key,approval_sha,json.dumps(approval,sort_keys=True),'claimed','unknown'))
        db.execute("UPDATE preparation SET state='resuming:correction_recovery' WHERE target_date=?",(TARGET,))
    def checkpoint(connection=None):
        deadline();files()
        if fingerprint(read_json(approval_path))!=approval_sha or job.is_symlink() or {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in job.iterdir() if p.is_file() and not p.is_symlink()}!=receipt:
            raise ValueError('correction_recovery_evidence_changed')
        if old_lunch_path.is_symlink() or hashlib.sha256(old_lunch_path.read_bytes()).hexdigest()!=old_lunch['sha256']:raise ValueError('correction_recovery_old_image_changed')
        for p,snapshot in old_receipts.items():
            if p.is_symlink() or {f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir() if f.is_file() and not f.is_symlink()}!=snapshot:
                raise ValueError('correction_recovery_parent_job_changed')
        with (store.transaction() if connection is None else nullcontext(connection)) as db:
            claim=db.execute('SELECT * FROM correction_recovery_claims WHERE target_date=?',(TARGET,)).fetchone()
            if not claim or claim['state']!='claimed' or claim['recovery_id']!=key or claim['approval_sha']!=approval_sha:raise ValueError('correction_recovery_claim_changed')
            for table,value in parents.items():
                p=db.execute('SELECT * FROM '+table+' WHERE target_date=?',(TARGET,)).fetchone()
                if not p or dict(p)!=value:raise ValueError('correction_recovery_parent_changed')
            current_rows={r['step']:dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=?',(TARGET,))}
            if any(current_rows.get(k)!=v for k,v in rows.items()):raise ValueError('correction_recovery_old_step_changed')
            permitted={'correction_plan:breakfast:recovery:'+key,'correction_adoption:breakfast:'+key,'daily_request_key','daily_share','ogp_capture:daily','ogp_review:daily'}
            for slot in ('breakfast','lunch','dinner'):
                for stage in ('meal_request_key','meal_register','meal_review','correction_plan','correction_key','meal_correction','ogp_capture','ogp_review'):
                    permitted.add(stage+':'+slot)
                permitted|={'meal_review:'+slot+':corrected','ogp_capture:'+slot+'-corrected'}
            if set(current_rows)-set(rows)-permitted:raise ValueError('correction_recovery_unexpected_step')
            for k,r in current_rows.items():
                if k in rows:continue
                if k in completed_added and r!=completed_added[k]:raise ValueError('correction_recovery_completed_step_changed')
                if r['state']=='complete':completed_added[k]=r
            pairs=set()
            for req in db.execute('SELECT * FROM mina_requests WHERE target_date=?',(TARGET,)):
                req=dict(req);pair=(req['slot'],req['operation'])
                if pair in pairs:raise ValueError('correction_recovery_duplicate_request')
                pairs.add(pair)
                if req['request_key']==approval['saved_request_uuid']:
                    if req['phase']!='complete' or any(req[k]!=v for k,v in old_request.items() if k not in ('updated','observation','phase')):
                        raise ValueError('correction_recovery_saved_request_changed')
                    continue
                slot,operation=pair
                allowed_pairs={('breakfast','meal_correction'),('lunch','meal_register'),('dinner','meal_register'),('lunch','meal_correction'),('dinner','meal_correction'),('daily','daily_share')}
                if pair not in allowed_pairs or req['phase'] not in ('prepared','dispatching','response_received','complete'):
                    raise ValueError('correction_recovery_foreign_request')
                prefix='daily_request_key' if slot=='daily' else ('correction_key:' if operation=='meal_correction' else 'meal_request_key:')+slot
                record=current_rows.get(prefix,{})
                binding=json.loads(record.get('result') or '{}');payload=json.loads(req['input_json'])
                sha=hashlib.sha256(req['image']).hexdigest() if req['image'] is not None else None
                if (record.get('state')!='complete' or binding.get('key')!=req['request_key']
                        or payload.get('expected_state_version')!=binding.get('expected_state_version')
                        or sha!=req['image_sha256'] or fingerprint({'operation':operation,'target_date':TARGET,'slot':slot,'payload':payload,'image_sha256':sha})!=req['input_digest']):
                    raise ValueError('correction_recovery_request_binding_changed')
                if operation=='meal_register':
                    from .domain import slot_time,date
                    expected_payload={'contract_version':'mina-write-v1','meal_type':slot,'logged_at':slot_time(date.fromisoformat(TARGET),slot).isoformat(),
                        'comment':draft['meals'][slot]['comment'],'expected_state_version':binding['expected_state_version'],'filename':f'{TARGET}-{slot}.jpg'}
                    if payload!=expected_payload or sha!=saved['images'][slot]['sha256']:raise ValueError('correction_recovery_registration_changed')
                elif operation=='meal_correction':
                    proposal_step='correction_adoption:breakfast:'+key if slot=='breakfast' else 'correction_plan:'+slot
                    proposal=json.loads(current_rows.get(proposal_step,{}).get('result') or '{}')
                    source=json.loads(current_rows['meal_register:'+slot]['result'])['result']
                    expected_payload={'contract_version':'mina-write-v1','reason':'codex_secondary_review','share_url':source['share']['url'],
                        'foods':proposal.get('foods'),'comment':source['normalized_comment'],'expected_state_version':binding['expected_state_version']}
                    if payload!=expected_payload or req['image'] is not None:raise ValueError('correction_recovery_patch_changed')
                else:
                    if payload!={'contract_version':'mina-write-v1','date':TARGET,'comment':draft['daily_comment'],
                            'expected_meal_count':3,'expected_state_version':binding['expected_state_version']} or req['image'] is not None:
                        raise ValueError('correction_recovery_daily_changed')
            deadline()
    ai,meal=(DeadlineClient(c,checkpoint) for c in (ai,meal))
    def captured(url,allowed):checkpoint();return capture(url,allowed)
    token=_scope.set({'id':key,'old_digest':approval['original_digest'],'approval_sha':approval_sha,'check':checkpoint})
    stage='registration'
    try:
        reviewed=register_day(store,draft,saved,base/'ogp',meal,ai,host,capture=captured,allow_correction=True)
        checkpoint();write_artifact(base/'posts-reviewed.json',reviewed)
        stage='candidates';check_evidence(history,TARGET,clock())
        candidates=build_candidates(reviewed,history,commit,host,clock())
        for payload in candidates:checkpoint();verify_source(Candidate(**payload),meal,host,captured,clock=clock)
        checkpoint();manifest=base/'candidates.json';write_artifact(manifest,candidates);checkpoint()
        import_manifest(store,manifest,host,clock(),guard=checkpoint);store.preparation_done(TARGET)
        with store.transaction() as db:db.execute("UPDATE correction_recovery_claims SET state='complete' WHERE target_date=?",(TARGET,))
        return {'target_date':TARGET,'state':'approval_ready','post_count':4}
    except BaseException as error:
        with store.transaction() as db:
            db.execute("UPDATE correction_recovery_claims SET state='held' WHERE target_date=?",(TARGET,))
            db.execute("UPDATE preparation SET state='held:correction_recovery' WHERE target_date=?",(TARGET,))
        store.record_preparation_failure(TARGET,stage,error,clock());raise
    finally:_scope.reset(token)
