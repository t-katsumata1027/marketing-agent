"""保存済み日次の文字数だけを、既存APIと独立点検で修復する明示実行部。"""
import copy
import hashlib
import json
import uuid
from datetime import date
from pathlib import Path
from .clients import require_live,OutcomeUnknown,MealClient
from .domain import Candidate,approval_time
from .m1_contract import CONTRACT,completed_request,validate_state,validate_write,verify_source
from .ogp import verify_image_reference
from .preparation import PreparationJournal,fingerprint
from .request_ledger import RequestLedger
from .x_text import weighted_length,shorten_comment

FIELDS=('comment','api_text','share_url','menu','nutrition','image_sha256','ogp_sha256','api_quality','secondary_review','ogp_verified','publish_text')


def repair_daily_text(store,reviewed,saved,client,ai,host,clock,capture,round_key,commit):
    """候補0・全保存済みの日だけ、同一3食を維持し日次コメントを一度更新する。"""
    require_live();MealClient.request_key(round_key)
    target=reviewed['target_date']
    def deadline():
        if clock()>=approval_time(date.fromisoformat(target)):raise ValueError('通常の準備期限を維持します')
    deadline()
    if reviewed.get('state')!='posts_reviewed' or set(reviewed.get('posts',{}))!={'breakfast','lunch','dinner','daily'}:
        raise ValueError('点検済み4投稿が必要です')
    original=reviewed['posts']['daily']
    if weighted_length(original['publish_text'])<=280:raise ValueError('文字数超過の日次だけを修復します')
    journal=PreparationJournal(store);ledger=RequestLedger(store)
    marker='daily_text_request:'+round_key
    with store.transaction() as db:
        if db.execute('SELECT 1 FROM candidates WHERE target_date=?',(target,)).fetchone():raise ValueError('候補取込後の修復は行いません')
        # AI待機や正本取得の前に日単位で占有し、別実行が新UUIDを作る競合を防ぐ。
        db.execute('CREATE TABLE IF NOT EXISTS daily_text_recovery_claims(target_date TEXT PRIMARY KEY,round_key TEXT NOT NULL,input_digest TEXT NOT NULL)')
        digest=fingerprint(reviewed)
        db.execute('INSERT OR IGNORE INTO daily_text_recovery_claims VALUES(?,?,?)',(target,round_key,digest))
        claim=db.execute('SELECT round_key,input_digest FROM daily_text_recovery_claims WHERE target_date=?',(target,)).fetchone()
        if claim['round_key']!=round_key or claim['input_digest']!=digest:raise ValueError('日次修復の実行IDまたは元入力が異なります')
        if db.execute("SELECT 1 FROM preparation_steps WHERE target_date=? AND step LIKE 'daily_text_request:%' AND step<>?",(target,marker)).fetchone():raise ValueError('別UUIDで日次更新を重ねません')
        existing=db.execute('SELECT result FROM preparation_steps WHERE target_date=? AND step=?',(target,marker)).fetchone()
        if not existing and db.execute("SELECT 1 FROM mina_requests WHERE target_date=? AND phase<>'complete'",(target,)).fetchone():raise ValueError('旧要求の成否照合が必要です')
    def candidate(slot,post):
        return Candidate(target_date=target,slot=slot,**{k:post[k] for k in FIELDS},approval_source=post['approval_source'],source_commit=commit,history_verified=False,legacy_schedule_clear=False,meal_count=3 if slot=='daily' else 1)
    for slot,post in reviewed['posts'].items():
        deadline()
        if existing and slot=='daily':continue
        verify_source(candidate(slot,post),client,host,capture,clock=clock)
    version=validate_state(client.day_state(target),target,clock())
    if any(p['approval_source']['state_version']!=version for p in reviewed['posts'].values()):raise ValueError('元の3食が変更されています')
    raw_images=[]
    for slot in ('breakfast','lunch','dinner'):
        info=saved['images'][slot];path=Path(info['path'])
        if path.is_symlink():raise ValueError('原本へのリンクを拒否します')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=reviewed['posts'][slot]['image_sha256']:raise ValueError('原本画像が変わっています')
        raw_images.append(raw)
    if existing:
        request=json.loads(existing['result'])
    else:
        value=journal.run(target,'daily_text_comment:'+round_key,{'original':original['comment'],'source':original['approval_source']},lambda:{'comment':shorten_comment(ai,original['comment'],excluded=[p['comment'] for p in reviewed['posts'].values() if p is not original],context={'date':target,'purpose':'保存済み日次の文字数修復'})})
        deadline()
        request=journal.run(target,marker,{'comment':value['comment'],'expected':version},lambda:{'key':str(uuid.uuid4()),'comment':value['comment'],'expected_state_version':version})
    if request['expected_state_version']!=version:raise ValueError('更新期待版が変わっています')
    payload={'contract_version':CONTRACT,'date':target,'comment':request['comment'],'expected_meal_count':3,'expected_state_version':version}
    ledger.prepare(request['key'],'daily_share',target,'daily',payload)
    record=ledger.restore(request['key']);deadline()
    if record['phase']=='prepared':
        verify_source(candidate('daily',original),client,host,capture,clock=clock)
        deadline()
        response=ledger.execute_once(request['key'],client,lambda:client.json_write('/api/internal/mina/daily-share',payload,request_key=request['key']))
    else:
        lookup=client.request_state(request['key'],'daily_share')
        result=lookup.get('current_result');completed_request(lookup,request['key'],'daily_share',result)
        response={**result,'result':result}
    lookup=client.request_state(request['key'],'daily_share')
    result=response['result'];input_hash=completed_request(lookup,request['key'],'daily_share',result)
    normalized=validate_write(response,'daily',target,host,input_comment=request['comment'],meals={s:p for s,p in reviewed['posts'].items() if s!='daily'})
    ledger.record(request['key'],'complete',{'persistence':'saved','stage':'completed'})
    if result['source_state_version']!=version or weighted_length(normalized['publish_text'])>280:raise ValueError('修復後の版・文字数が不正です')
    deadline();snapshot=capture(normalized['share_url'],host);verify_image_reference(snapshot,normalized['ogp'],normalized['share_url'],host)
    if snapshot['meta']['image_sha256']!=normalized['ogp']['image_sha256']:raise ValueError('新しいOGP実画像が不一致です')
    verdict=journal.run(target,'daily_text_ogp_review:'+round_key,{'result':fingerprint(result),'ogp':snapshot['digest']},lambda:ai.review_ogp([snapshot['review_image'],*raw_images],{**normalized,'target_date':target,'slot':'daily','meal_count':3},snapshot['meta']))
    if verdict!={'status':'publishable','reason_codes':[]}:raise ValueError('新しい日次OGPの点検が保留です')
    output=copy.deepcopy(reviewed)
    output['posts']['daily']={**original,**normalized,'ogp_sha256':snapshot['digest'],'approval_source':{'request_key':request['key'],'operation':'daily_share','input_hash':input_hash,'result_sha256':normalized['result_sha256'],'result_version':result['result_version'],'ogp_revision':normalized['ogp']['revision'],'ogp_source_version':normalized['source_version'],'state_version':version}}
    for slot,post in output['posts'].items():
        deadline();verify_source(candidate(slot,post),client,host,capture,clock=clock)
    return output
