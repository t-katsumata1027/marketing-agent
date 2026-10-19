"""保存完了済みの当日結果をGETと実画像だけで再点検する。再登録はしない。"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .domain import JST, Candidate, slot_time
from .generation import MEAL_SLOTS, OGP_REVIEW_INSTRUCTIONS, meal_review_policy_sha256
from .m1_contract import completed_request, validate_write
from .ogp import capture_ogp, verify_image_reference
from .preparation import PreparationJournal, fingerprint
from .readiness import release_commit, check_evidence, build_candidates, read_json
from .registration import assert_day_state
from .request_ledger import RequestLedger
from .daily_pipeline import write_artifact
from .runtime import import_manifest


def finalize_saved_day(store, directory, root, commit, host, ai, client, round_key,
                       clock=lambda: datetime.now(UTC), capture=capture_ogp,
                       history_directory=None, publish_lunch_after_approval=False):
    """完了台帳・旧合格証跡・現在正本が一致した日だけ、日次画像を別証跡で再点検する。"""
    from .clients import MealClient
    MealClient.request_key(round_key)
    now = clock()
    day = now.astimezone(JST).date()
    target = day.isoformat()
    if now >= slot_time(day, 'dinner'):
        raise ValueError('当日夕食の準備期限を超えています')
    commit = release_commit(root, commit)
    base = Path(directory) / target
    draft = json.loads((base/'draft.json').read_text())
    saved = json.loads((base/'drive.json').read_text())
    if draft.get('target_date') != target or saved.get('target_date') != target:
        raise ValueError('当日の保存資料が必要です')
    ledger, journal = RequestLedger(store), PreparationJournal(store)
    with store.transaction() as db:
        rows = list(db.execute('SELECT request_key,operation,slot,phase FROM mina_requests WHERE target_date=?', (target,)))
        if (not rows or any(r['phase'] != 'complete' for r in rows) or
                len({(r['slot'], r['operation']) for r in rows}) != len(rows) or
                {(r['slot'],r['operation']) for r in rows} -
                {(s,op) for s in MEAL_SLOTS for op in ('meal_register','meal_correction')} - {('daily','daily_share')} or
                not ({(s,'meal_register') for s in MEAL_SLOTS} | {('daily','daily_share')}) <=
                    {(r['slot'],r['operation']) for r in rows}):
            raise ValueError('全要求の保存完了が必要です。成否不明は再開しません')
        if db.execute('SELECT 1 FROM candidates WHERE target_date=?', (target,)).fetchone():
            raise ValueError('既存候補の再承認は通常の再レビューを使用してください')
        if not db.execute("SELECT 1 FROM publication_exclusions WHERE target_date=? AND slot='breakfast'", (target,)).fetchone():
            raise ValueError('本人指定の朝食除外が必要です')
        old = db.execute("SELECT result FROM preparation_steps WHERE target_date=? AND step='ogp_review:daily' AND state='complete'", (target,)).fetchone()
        if not old or json.loads(old['result']) != {'status':'hold','reason_codes':['share_ogp_mismatch']}:
            raise ValueError('既存日次OGPの仕様点検保留からだけ再点検できます')
        marker = 'saved_day_review'
        if db.execute('SELECT 1 FROM preparation_steps WHERE target_date=? AND step=?', (target,marker)).fetchone():
            raise ValueError('日次の再点検は既に開始済みです。自動反復しません')
        db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'started',NULL,?)",
                   (target,marker,fingerprint({'commit':commit,'round':round_key}),now.timestamp()))
    by_slot = {s: [r for r in rows if r['slot']==s] for s in (*MEAL_SLOTS,'daily')}
    outputs, completed, originals = {}, {}, {}
    for slot in (*MEAL_SLOTS,'daily'):
        selected = next((r for r in by_slot[slot] if r['operation']=='meal_correction'), by_slot[slot][0])
        key, operation = selected['request_key'], selected['operation']
        ledger.restore(key)
        step = 'daily_share' if slot=='daily' else operation+':'+slot
        with store.transaction() as db:
            row = db.execute("SELECT result FROM preparation_steps WHERE target_date=? AND step=? AND state='complete'", (target,step)).fetchone()
        if not row:
            raise ValueError('保存応答の証跡がありません')
        response = json.loads(row['result'])
        lookup = client.request_state(key,operation)
        input_hash = completed_request(lookup,key,operation,response['result'])
        comment = draft['daily_comment'] if slot=='daily' else draft['meals'][slot]['comment']
        kwargs = {'meals':outputs} if slot=='daily' else {'logged_at':slot_time(day,slot).isoformat()}
        normalized = validate_write(response,slot,target,host,operation=operation,input_comment=comment,**kwargs)
        snapshot = capture(normalized['share_url'],host)
        verify_image_reference(snapshot,normalized['ogp'],normalized['share_url'],host)
        if snapshot['meta']['image_sha256'] != normalized['ogp']['image_sha256']:
            raise ValueError('現在の配信OGPが不一致です')
        if slot!='daily':
            info = saved['images'][slot]
            path = Path(info['path'])
            raw = path.read_bytes()
            if (path.is_symlink() or hashlib.sha256(raw).hexdigest()!=info['sha256'] or
                    response['result']['image_sha256']!=info['sha256'] or
                    info['drive'].get('verified') is not True or info['drive'].get('sha256')!=info['sha256']):
                raise ValueError('原本画像と保存結果が不一致です')
            originals[slot] = raw
            inputs = {'image_sha256':info['sha256'],'meal':draft['meals'][slot],'response':response,
                      'ogp_digest':snapshot['digest'],'policy_sha256':meal_review_policy_sha256()}
            ogp_inputs = {'image_sha256':info['sha256'],'normalized':normalized,'ogp_digest':snapshot['digest']}
            with store.transaction() as db:
                reviews = list(db.execute("SELECT result FROM preparation_steps WHERE target_date=? AND step LIKE ? AND digest=? AND state='complete'",
                    (target,'meal_review:'+slot+'%',fingerprint(inputs))))
                ogp_review = db.execute("SELECT result FROM preparation_steps WHERE target_date=? AND step=? AND digest=? AND state='complete'",
                    (target,'ogp_review:'+slot,fingerprint(ogp_inputs))).fetchone()
            positive = {'status':'publishable','reason_codes':[]}
            if not any(json.loads(r['result'])==positive for r in reviews) or not ogp_review or json.loads(ogp_review['result'])!=positive:
                raise ValueError('同じ食品正本・実OGPの合格証跡がありません')
            completed[slot] = response
        else:
            assert_day_state(client.day_state(target),target,completed,clock())
            verdict = journal.run(target,'ogp_review:daily:specification:'+round_key,
                {'result':response['result'],'ogp_digest':snapshot['digest'],
                 'policy_sha256':hashlib.sha256(OGP_REVIEW_INSTRUCTIONS.encode()).hexdigest()},
                lambda: ai.review_ogp([snapshot['review_image'],*(originals[s] for s in MEAL_SLOTS)],
                    {**normalized,'target_date':target,'slot':'daily','meal_count':3},snapshot['meta']))
            if verdict != {'status':'publishable','reason_codes':[]}:
                raise ValueError('日次実OGPの再点検が保留です')
        source = {'request_key':key,'operation':operation,'input_hash':input_hash,
            'result_sha256':normalized['result_sha256'],'result_version':response['result']['result_version'],
            'ogp_revision':normalized['ogp']['revision'],'ogp_source_version':normalized['source_version']}
        if slot!='daily':
            source['stored_image_sha256'] = response['result']['stored_image_sha256']
        outputs[slot] = {**normalized,'menu':draft['meals'][slot]['menu'] if slot!='daily' else ' / '.join(draft['meals'][s]['menu'] for s in MEAL_SLOTS),
            'api_quality':'pass','secondary_review':'publishable','ogp_verified':True,'ogp_sha256':snapshot['digest'],
            'image_sha256':saved['images'][slot]['sha256'] if slot!='daily' else fingerprint({s:saved['images'][s]['sha256'] for s in MEAL_SLOTS}),
            'meal_count':3 if slot=='daily' else 1,'approval_source':source}
    version = assert_day_state(client.day_state(target),target,completed,clock())
    if version != outputs['daily']['source_version']:
        raise ValueError('日次生成元と現在の日版が不一致です')
    for post in outputs.values():
        post['approval_source']['state_version'] = version
    reviewed = {'target_date':target,'state':'posts_reviewed','posts':outputs}
    out = base/('saved-finalization-'+round_key)/'posts-reviewed.json'
    write_artifact(out,reviewed)
    with store.transaction() as db:
        db.execute("UPDATE preparation_steps SET state='complete',result=? WHERE target_date=? AND step=?",
                   (json.dumps({'reviewed_path':str(out),'source_commit':commit}),target,marker))
    if not publish_lunch_after_approval:
        return {'target_date':target,'state':'posts_reviewed','post_count':4}
    evidence = None
    for path in sorted(Path(history_directory or '/var/lib/mina-agent/history').glob('*.json'),reverse=True):
        value = read_json(path)
        if value.get('target_date')==target:
            evidence=value
            break
    check_evidence(evidence or {},target,clock())
    until = (slot_time(day,'dinner')-timedelta(minutes=15)).isoformat()
    candidates = build_candidates(reviewed,evidence,commit,host,clock(),excluded_slots=('breakfast',),
                                  lunch_after_approval_until=until)
    from .m1_contract import verify_source
    for payload in candidates:
        verify_source(Candidate(**payload),client,host,capture,clock=clock)
    manifest = out.parent/'candidates.json'
    write_artifact(manifest,candidates)
    with store.transaction() as db:
        db.execute("INSERT INTO publication_exceptions VALUES(?,'lunch',?,'本人承認：当日昼食はDiscord承認後に臨時公開')",(target,until))
    import_manifest(store,manifest,host,clock(),excluded_slots=('breakfast',))
    store.preparation_done(target)
    return {'target_date':target,'state':'approval_ready','post_count':3,'lunch':'publish_after_discord_approval'}
