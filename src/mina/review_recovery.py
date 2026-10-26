"""10/27の中断画像点検を、元unknownを保持する一度限りの本人承認で復旧する。"""
import hashlib
import json
import math
from contextlib import nullcontext
from datetime import UTC,datetime,timedelta
from pathlib import Path

from .clients import MealClient
from .daily_pipeline import DeadlineClient,write_artifact
from .domain import Candidate,JST,instant
from .generation import image_review_policy_sha256
from .m1_contract import validate_state,verify_source
from .ogp import capture_ogp
from .preparation import PreparationJournal,fingerprint,prepare_images,save_images_to_drive
from .readiness import build_candidates,check_evidence,read_json,release_commit
from .registration import register_day
from .runtime import import_manifest
from .preparation_status import QualityHold,PreparationDeadline

TARGET='2026-10-27'
END=datetime(2026,10,26,23,tzinfo=JST)
PASS={'status':'image_acceptable','reason_codes':[]}
JOB_ROOT=Path('/var/lib/mina-agent/codex-jobs')


def resume_review(store,directory,approval_path,history_path,root,commit,host,ai,drive,meal,
                  clock=lambda:datetime.now(UTC),capture=capture_ogp):
    """AI前に日/旧工程主キーのclaimを取得。再起動も新UUIDも自動反復しない。"""
    if not END-timedelta(hours=4)<=instant(clock())<END:raise PreparationDeadline('review_recovery_deadline')
    release_commit(root,commit)
    approval=read_json(approval_path);round_key=approval.get('recovery_id');MealClient.request_key(round_key)
    keys={'target_date','deadline','commit','recovery_id','original_digest','image_sha256','draft_sha256',
          'policy_sha256','cli_job','approval_reference','scope'}
    if (set(approval)!=keys or approval['target_date']!=TARGET or approval['deadline']!=END.isoformat()
            or approval['commit']!=commit or approval['policy_sha256']!=image_review_policy_sha256()
            or approval['scope']!='one_interrupted_lunch_review_then_four_candidates'
            or not isinstance(approval['approval_reference'],str) or not approval['approval_reference'].strip()):
        raise ValueError('review_recovery_approval_invalid')
    approval_sha=fingerprint(approval)
    base=Path(directory)/TARGET;draft_path=base/'draft.json';draft=read_json(draft_path)
    originals={}
    if draft.get('target_date')!=TARGET:raise ValueError('review_recovery_draft_changed')
    def files():
        if draft_path.is_symlink() or hashlib.sha256(draft_path.read_bytes()).hexdigest()!=approval['draft_sha256']:
            raise ValueError('review_recovery_draft_changed')
        for item in originals.values():
            p=Path(item['path'])
            if p.is_symlink() or not p.is_file() or p.stat().st_size!=item['size'] or hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:
                raise ValueError('review_recovery_image_changed')
    files()
    history=read_json(history_path);check_evidence(history,TARGET,clock())
    if not 0<=(instant(clock())-datetime.fromisoformat(history['checked_at'])).total_seconds()<=120:
        raise ValueError('review_recovery_history_stale')
    if instant(clock())>=END:raise PreparationDeadline('review_recovery_deadline')
    state=meal.day_state(TARGET);validate_state(state,TARGET,clock())
    if instant(clock())>=END:raise PreparationDeadline('review_recovery_deadline')
    if state['meals'] or state.get('daily_share'):raise ValueError('review_recovery_server_not_empty')
    with store.transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS interrupted_review_recoveries (
            target_date TEXT PRIMARY KEY,original_digest TEXT NOT NULL,recovery_id TEXT NOT NULL UNIQUE,
            approval_sha TEXT NOT NULL,approval_json TEXT NOT NULL,state TEXT NOT NULL,
            old_usage_status TEXT NOT NULL,started TEXT NOT NULL,UNIQUE(target_date,original_digest))''')
        if db.execute('SELECT 1 FROM interrupted_review_recoveries WHERE target_date=?',(TARGET,)).fetchone():
            raise ValueError('review_recovery_already_claimed')
        prep=db.execute('SELECT state FROM preparation WHERE target_date=?',(TARGET,)).fetchone()
        rows={r['step']:dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=?',(TARGET,))}
        unknown=rows.get('image_review:lunch',{})
        allowed={'daily_draft','image:breakfast','image_review:breakfast','image:lunch','image_review:lunch'}
        if (not prep or prep['state']!='held:images' or unknown.get('state')!='outcome_unknown'
                or unknown.get('digest')!=approval['original_digest']
                or any(r['state']!='complete' for k,r in rows.items() if k!='image_review:lunch')
                or any(k not in allowed and not k.startswith('menu_nutrition:') for k in rows)
                or not allowed<=set(rows)
                or db.execute('SELECT 1 FROM candidates WHERE target_date=?',(TARGET,)).fetchone()
                or db.execute('SELECT 1 FROM mina_requests WHERE target_date=?',(TARGET,)).fetchone()
                or (base/'drive.json').exists()):raise ValueError('review_recovery_source_unresolved')
        originals={s:json.loads(rows['image:'+s]['result']) for s in ('breakfast','lunch')}
        expected={'sha256':originals['lunch']['sha256'],'meal':draft['meals']['lunch'],'review_policy_sha256':approval['policy_sha256']}
        if fingerprint(expected)!=approval['original_digest'] or originals['lunch']['sha256']!=approval['image_sha256']:
            raise ValueError('review_recovery_review_binding_changed')
        if json.loads(rows['image_review:breakfast']['result'])!=PASS:raise ValueError('review_recovery_breakfast_not_passed')
        if rows['image_review:breakfast']['digest']!=fingerprint({'sha256':originals['breakfast']['sha256'],
                'meal':draft['meals']['breakfast'],'review_policy_sha256':approval['policy_sha256']}):
            raise ValueError('review_recovery_breakfast_binding_changed')
        inputs={'foods':{s:draft['meals'][s]['foods'] for s in ('breakfast','lunch','dinner')}}
        nutrition=rows.get('menu_nutrition:'+fingerprint(inputs),{})
        values=json.loads(nutrition.get('result') or '{}')
        if (nutrition.get('digest')!=fingerprint(inputs) or set(values)!=set(inputs['foods'])
                or any(not isinstance(values[s],list) or len(values[s])!=len(inputs['foods'][s])
                       or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=10000 for v in values[s]) for s in values)
                or not 1800<=sum(sum(v) for v in values.values())<=2000):raise ValueError('review_recovery_menu_budget_invalid')
        files()
        job=Path(approval['cli_job'])
        if (job.is_symlink() or not job.is_dir() or job.parent!=JOB_ROOT
                or read_json(job/'state.json')!={'state':'failed','reason':'execution'}
                or (job/'result.json').exists()
                or not any(p.is_file() and not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==approval['image_sha256'] for p in job.glob('input-*'))):
            raise ValueError('review_recovery_cli_evidence_changed')
        receipt_files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in job.iterdir() if p.is_file() and not p.is_symlink()}
        if instant(clock())>=END:raise PreparationDeadline('review_recovery_deadline')
        db.execute('INSERT INTO interrupted_review_recoveries VALUES(?,?,?,?,?,?,?,?)',
                   (TARGET,approval['original_digest'],round_key,approval_sha,json.dumps(approval,sort_keys=True),'claimed','unknown',clock().isoformat()))
        db.execute("UPDATE preparation SET state='resuming:interrupted_review' WHERE target_date=?",(TARGET,))
    def checkpoint(connection=None):
        if instant(clock())>=END:raise PreparationDeadline('review_recovery_deadline')
        files()
        if fingerprint(read_json(approval_path))!=approval_sha:raise ValueError('review_recovery_approval_changed')
        if (job.is_symlink() or read_json(job/'state.json')!={'state':'failed','reason':'execution'} or (job/'result.json').exists()
                or {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in job.iterdir() if p.is_file() and not p.is_symlink()}!=receipt_files):
            raise ValueError('review_recovery_cli_evidence_changed')
        with (store.transaction() if connection is None else nullcontext(connection)) as db:
            r=db.execute('SELECT * FROM interrupted_review_recoveries WHERE target_date=?',(TARGET,)).fetchone()
            if not r or r['approval_sha']!=approval_sha or r['recovery_id']!=round_key or r['state']!='claimed':
                raise ValueError('review_recovery_claim_changed')
            current={r['step']:dict(r) for r in db.execute('SELECT * FROM preparation_steps WHERE target_date=?',(TARGET,))}
            if any(current.get(k)!=v for k,v in rows.items()):raise ValueError('review_recovery_old_step_changed')
            permitted={'image:dinner','image_review:dinner','daily_request_key','daily_share','ogp_capture:daily','ogp_review:daily',
                       'image_review:lunch:interrupted:'+round_key,'review_adoption:lunch:'+round_key}
            for s in ('breakfast','lunch','dinner'):
                for stage in ('drive_upload','meal_request_key','meal_register','meal_review','correction_plan','correction_key','meal_correction','ogp_capture','ogp_review'):
                    permitted.add(stage+':'+s)
                permitted.update({'meal_review:'+s+':corrected','ogp_capture:'+s+'-corrected'})
            if set(current)-set(rows)-permitted:raise ValueError('review_recovery_unexpected_step')
            if instant(clock())>=END:raise PreparationDeadline('review_recovery_deadline')
    ai,drive,meal=(DeadlineClient(c,checkpoint) for c in (ai,drive,meal))
    def checked_capture(url,allowed):checkpoint();return capture(url,allowed)
    journal=PreparationJournal(store)
    stage='images'
    try:
        checkpoint()
        review_step='image_review:lunch:interrupted:'+round_key
        review=journal.run(TARGET,review_step,{'original_digest':approval['original_digest'],'approval_sha256':approval_sha,**expected},
                           lambda:ai.review_image(Path(originals['lunch']['path']).read_bytes(),draft['meals']['lunch']))
        checkpoint()
        if review!=PASS:raise QualityHold('review_recovery_quality_hold',review_step,review.get('reason_codes',()))
        adopted=journal.run(TARGET,'review_adoption:lunch:'+round_key,
                            {'original_digest':approval['original_digest'],'review_step':review_step,'approval_sha256':approval_sha,'image':originals['lunch']},
                            lambda:{**originals['lunch'],'image_review':review})
        dinner=prepare_images(store,draft_path,base/'images',ai,only_slots=('dinner',))
        images={'target_date':TARGET,'state':'images_reviewed','images':{
            'breakfast':{**originals['breakfast'],'image_review':PASS},'lunch':adopted,'dinner':dinner['images']['dinner']}}
        checkpoint();write_artifact(base/'images-reviewed.json',images)
        stage='drive'
        saved=save_images_to_drive(store,images,drive)
        checkpoint();write_artifact(base/'drive.json',saved)
        stage='registration'
        reviewed=register_day(store,draft,saved,base/'ogp',meal,ai,host,capture=checked_capture,allow_correction=True)
        checkpoint();write_artifact(base/'posts-reviewed.json',reviewed)
        check_evidence(history,TARGET,clock())
        stage='candidates'
        candidates=build_candidates(reviewed,history,commit,host,clock())
        for payload in candidates:checkpoint();verify_source(Candidate(**payload),meal,host,checked_capture,clock=clock)
        checkpoint();manifest=base/'candidates.json';write_artifact(manifest,candidates);checkpoint()
        import_manifest(store,manifest,host,clock(),guard=checkpoint);store.preparation_done(TARGET)
        with store.transaction() as db:db.execute("UPDATE interrupted_review_recoveries SET state='complete' WHERE target_date=?",(TARGET,))
        return {'target_date':TARGET,'state':'approval_ready','post_count':4}
    except BaseException as error:
        with store.transaction() as db:
            db.execute("UPDATE interrupted_review_recoveries SET state='held' WHERE target_date=?",(TARGET,))
            db.execute("UPDATE preparation SET state='held:interrupted_review' WHERE target_date=?",(TARGET,))
        store.record_preparation_failure(TARGET,stage,error,clock())
        raise
