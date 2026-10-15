"""本人指示による翌日準備の再開。保存済み朝食を再登録せず、一度だけ訂正・再点検する。"""

import json
from datetime import UTC, datetime, timedelta, time
from pathlib import Path

from .clients import MealClient
from .daily_pipeline import DeadlineClient, write_artifact
from .domain import Candidate, JST, instant
from .m1_contract import verify_source
from .ogp import capture_ogp
from .preparation import PreparationJournal, fingerprint
from .readiness import build_candidates, check_evidence, read_json, release_commit
from .registration import register_day
from .runtime import import_manifest


def resume_preparation(store, directory, target, round_key, root, commit, host, ai, meal,
                       history_directory, clock=lambda: datetime.now(UTC), capture=capture_ogp):
    """朝食の栄養点検で終端停止した翌日分だけを、明示的な再開要求で処理する。"""
    MealClient.request_key(round_key)
    now = instant(clock())
    local = now.astimezone(JST)
    if target != (local.date()+timedelta(days=1)).isoformat() or not time(20) <= local.time() < time(23):
        raise ValueError('明示的な翌日再開は当日20時から23時前までです')
    commit = release_commit(root,commit)
    base = Path(directory)/target
    draft,saved = read_json(base/'draft.json'),read_json(base/'drive.json')
    evidence = None
    for path in sorted(Path(history_directory).glob('*.json'),reverse=True):
        value=read_json(path)
        if value.get('target_date')==target:
            evidence=value
            break
    check_evidence(evidence or {},target,now)
    if draft.get('target_date')!=target or saved.get('target_date')!=target:
        raise ValueError('同じ対象日の原本と保存証跡が必要です')
    with store.transaction() as db:
        prep=db.execute('SELECT state FROM preparation WHERE target_date=?',(target,)).fetchone()
        requests=list(db.execute('SELECT slot,operation,phase FROM mina_requests WHERE target_date=?',(target,)))
        hold=db.execute("SELECT result FROM preparation_steps WHERE target_date=? AND step='meal_review:breakfast' AND state='complete'",(target,)).fetchone()
        verdict=json.loads(hold['result']) if hold else {}
        request_rows={tuple(r) for r in requests}
        pending=[tuple(r) for r in db.execute("SELECT step,state FROM preparation_steps WHERE target_date=? AND state<>'complete'",(target,))]
        original_case=(prep and prep['state']=='held:registration' and
                request_rows=={('breakfast','meal_register','complete')} and len(requests)==1 and
                verdict.get('status')=='hold' and bool(verdict.get('reason_codes')) and
                set(verdict['reason_codes']) <= {'nutrition_semantic_mismatch','image_analysis_mismatch'} and
                not pending and not db.execute("SELECT 1 FROM preparation_steps WHERE target_date=? AND step LIKE 'ogp_review:%'",(target,)).fetchone())
        context_hold=db.execute("SELECT result FROM preparation_steps WHERE target_date=? AND step='meal_review:lunch:corrected' AND state='complete'",(target,)).fetchone()
        context_case=(prep and prep['state']=='held:explicit_resume' and len(requests)==4 and
                request_rows=={(s,op,'complete') for s in ('breakfast','lunch') for op in ('meal_register','meal_correction')} and
                pending==[('explicit_preparation_resume','outcome_unknown')] and context_hold and
                json.loads(context_hold['result'])=={'status':'hold','reason_codes':['context_mismatch']} and
                not db.execute("SELECT 1 FROM preparation_steps WHERE target_date=? AND step='ogp_review:lunch'",(target,)).fetchone())
        if (not (original_case or context_case) or db.execute('SELECT 1 FROM candidates WHERE target_date=?',(target,)).fetchone()):
            raise ValueError('朝食保存完了・栄養点検保留以外は再開しません。未知状態は照合が必要です')
        marker='publication_context_resume' if context_case else 'explicit_preparation_resume'
        if db.execute('SELECT 1 FROM preparation_steps WHERE target_date=? AND step=?',(target,marker)).fetchone():
            raise ValueError('再開は既に開始済みです。自動反復しません')
        db.execute("UPDATE preparation SET state='resuming:registration' WHERE target_date=?",(target,))
    journal=PreparationJournal(store)
    deadline=datetime.combine(local.date(),time(23),JST)
    def within_deadline():
        if instant(clock())>=instant(deadline):
            raise ValueError('本人指定の再開期限を過ぎました')
    def checked_capture(url,allowed_host):
        within_deadline()
        return capture(url,allowed_host)
    try:
        # 既存の朝食POST・画像・Drive保存・保留結果を再利用し、新しい訂正だけを別UUIDで実行する。
        reviewed=journal.run(target,marker,
            {'round':round_key,'commit':commit,'draft':fingerprint(draft),'saved':fingerprint(saved)},
            lambda:register_day(store,draft,saved,(base/('context-resume-'+round_key)/'ogp') if context_case else base/'ogp',
                                DeadlineClient(meal,within_deadline),DeadlineClient(ai,within_deadline),host,
                                capture=checked_capture,allow_correction=True,capture_round=round_key if context_case else None))
        write_artifact(base/'posts-reviewed.json',reviewed)
        check_evidence(evidence,target,clock())
        candidates=build_candidates(reviewed,evidence,commit,host,clock())
        for payload in candidates:
            within_deadline()
            verify_source(Candidate(**payload),meal,host,checked_capture,clock=clock)
        manifest=base/'candidates.json'
        write_artifact(manifest,candidates)
        within_deadline()
        import_manifest(store,manifest,host,clock())
        store.preparation_done(target)
        return {'target_date':target,'state':'approval_ready','post_count':4}
    except Exception:
        with store.transaction() as db:
            db.execute("UPDATE preparation SET state='held:explicit_resume' WHERE target_date=?",(target,))
        raise RuntimeError('翌日準備の再開を保留しました。自動再実行しません') from None
