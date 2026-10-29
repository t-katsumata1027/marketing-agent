"""保存済み朝昼を維持し、昼食一回訂正から夕食・日次案だけを送付する。"""
import copy,hashlib,json
from pathlib import Path
from .partial_day_continuation import PartialGate,request_rows
from .normalization_checkpoint import verify as verify_normalization
from .current_day_checkpoint import rows,file_record
from .current_day_origin import TARGET
from .preparation import fingerprint,PreparationJournal

VERSION='mina-current-day-lunch-continuation-v1'
NAMES=['ごはん','鶏ひき肉','卵','しょうが','植物油','砂糖','みりん','しょうゆ','ほうれん草','すりごま','にんじん','乾燥わかめ','みそ','だし']
AMOUNTS=['炊飯後180g','80g','1/2個（可食部25g）','3g','2g','4g','5g','計10g（そぼろ用7g、ごまびたし用3g）','70g','3g','40g','1g','10g','計180ml（ごまびたし用30ml、みそ汁用150ml）']

def mapping(meal,response,review):
    if [(f['name'],f['amount']) for f in meal['foods']]!=list(zip(NAMES,AMOUNTS)):raise ValueError('昼食の凍結材料/分量が不一致です')
    actual=response['result']['analysis']['foods']
    if [f['name'] for f in actual]!=['そぼろ丼（鶏そぼろと炒り卵）','ほうれん草の胡麻和え','味噌汁（わかめと人参）']:raise ValueError('昼食の旧料理が不一致です')
    if review['status']!='hold' or review['checks']['nutrition']!='hold' or any(review['checks'][k]!='pass' for k in ('composition','style','food_identity','visible_portions')) or {b['code'] for b in review['blocking']}!={'nutrition_inconsistent'}:raise ValueError('栄養だけの昼食保留以外は継続しません')
    # 共有調味料は配分を示す。訂正食品明細では合計量を一度だけ保持する。
    return {'dishes':[[0,1,2,3,4,5,6],[8,9],[10,11,12]],'shared':[{'index':7,'total':10,'unit':'g','allocations':[7,3,0]},{'index':13,'total':180,'unit':'ml','allocations':[0,30,150]}]}

def capture(db,previous):
    verify_normalization(db,previous)
    parent=rows(db,'current_day_partial_continuations')
    if len(parent)!=1 or parent[0]['state']!='held':raise ValueError('停止済み部分日caseが必要です')
    steps=rows(db,'preparation_steps');by={r['step']:r for r in steps};old={r['step'] for r in previous['tables']['preparation_steps']}
    if any(r['state']!='complete' for r in steps if r['step'] not in old):raise ValueError('追加の不明/未完了工程が残っています')
    if any(k.startswith(('correction_plan:lunch','correction_key:lunch','meal_correction:lunch','meal_register:dinner')) for k in by):raise ValueError('昼訂正/夕食登録は未開始だけを許可します')
    case=parent[0]['partial_continuation_uuid'];reviews=[dict(r) for r in db.execute('SELECT * FROM tolerance_case_reviews WHERE case_uuid=? ORDER BY step',(case,))]
    review=next(r for r in reviews if r['step']=='meal_review:lunch');raw=json.loads(review['raw'])
    if fingerprint(raw)!=review['raw_digest'] or json.loads(review['verdict'])!={'status':'hold','reason_codes':['nutrition_semantic_mismatch']}:raise ValueError('旧昼保留の原本が不一致です')
    breakfast=json.loads(by['meal_correction:breakfast']['result']);lunch=json.loads(by['meal_register:lunch']['result'])
    for step in ('meal_review:breakfast:corrected','ogp_review:breakfast'):
        if json.loads(by[step]['result'])!={'status':'publishable','reason_codes':[]}:raise ValueError('朝食の合格原本が必要です')
    from .tolerance_review import policy_sha256
    if review['image_sha256']!=lunch['result']['image_sha256'] or review['image_sha256']!=previous['approved_images']['lunch']['sha256'] or review['policy_sha256']!=policy_sha256():raise ValueError('昼点検の画像/policyが不一致です')
    draft=json.loads((Path(previous['output'])/'draft.json').read_bytes());bound=mapping(draft['meals']['lunch'],lunch,raw)
    calls=[dict(r) for r in db.execute('SELECT * FROM current_day_calls WHERE recovery_uuid=? ORDER BY id',(previous['parent']['recovery_uuid'],))]
    if any(r['state']!='complete' for r in calls):raise ValueError('追加要求の不明を再送しません')
    out=copy.deepcopy(previous);out.update(calls=calls,tables={t:rows(db,t) for t in previous['tables']})
    out['lunch']={'parent':parent,'reviews':reviews,'breakfast':breakfast,'original':lunch,'mapping':bound,'requests':request_rows(db)}
    if [(r['slot'],r['operation'],r['phase']) for r in out['lunch']['requests']]!=[('breakfast','meal_register','complete'),('breakfast','meal_correction','complete'),('lunch','meal_register','complete')]:raise ValueError('保存済み朝昼の要求集合が不一致です')
    matches=[]
    for path in Path(previous['receipts']).glob('*/invocation.json'):
        invocation=json.loads(path.read_bytes());prep=invocation.get('preparation') or {}
        if prep.get('target_date')!=TARGET or prep.get('step')!='meal_review:lunch':continue
        directory=path.parent
        if json.loads((directory/'result.json').read_bytes())!=raw or json.loads((directory/'state.json').read_bytes())!={'state':'complete','reason':None} or file_record(directory/'input-0.jpg')['sha256']!=review['image_sha256']:raise ValueError('昼CLI実原本が不一致です')
        for name in ('invocation.json','schema.json','result.json','state.json','diagnostic.json','input-0.jpg'):out['files'].append(file_record(directory/name))
        matches.append(path)
    if len(matches)!=1:raise ValueError('昼CLIは一件だけ必要です')
    return out

class LunchGate(PartialGate):
    TABLE='current_day_lunch_continuations';FLAG='MINA_CURRENT_DAY_LUNCH_ENABLED';CONTRACT=VERSION;ID_FIELD='lunch_continuation_uuid'
    delivery_slots=('dinner','daily')
    def parent(self,db):
        from .current_day_continuation import ContinuationGate
        ContinuationGate.parent(self,db)
        import os
        if os.getenv('MINA_LIVE_WRITES','false').lower()=='true':
            from .buffer_budget import RESERVATION,reservation,subject
            from .replacement_client import timestamp
            _,row=reservation(db,RESERVATION.get())
            prior=db.execute('SELECT deadline FROM '+self.TABLE+' WHERE target_date=?',(TARGET,)).fetchone() if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(self.TABLE,)).fetchone() else None
            if not row or row['subject']!=subject(os.getenv('BUFFER_API_KEY','')) or row['target_date']!=TARGET or row['state']!='active' or row['remaining']<(1 if prior else 60) or row['deadline']<timestamp(self.approval['not_after']).timestamp():raise ValueError('別承認Buffer予約の残量/期限を確認してください')
    def verify_source(self,db):
        super().verify_source(db)
        if rows(db,'current_day_partial_continuations')!=self.snapshot['lunch']['parent']:raise ValueError('旧部分日caseのheld/期限が変わりました')
        for r in self.snapshot['lunch']['reviews']:
            actual=db.execute('SELECT * FROM tolerance_case_reviews WHERE case_uuid=? AND step=?',(r['case_uuid'],r['step'])).fetchone()
            if not actual or dict(actual)!=r:raise ValueError('旧朝合格/昼保留が変わりました')
    def validate_initial_state(self,state):
        from .registration import assert_day_state
        if state['meal_count']!=2 or state.get('daily_share') is not None:raise ValueError('保存済み朝昼の二食だけが必要です')
        assert_day_state(state,TARGET,{'breakfast':self.snapshot['lunch']['breakfast'],'lunch':self.snapshot['lunch']['original']},self.clock())
    def validate_requests(self,db):
        from .publication_hold import exists
        for table in ('meal_replacement_requests','replacement_attempts','candidates','instagram_candidates','publication_mutation_leases','buffer_publication','instagram_attempts'):
            if exists(db,table) and rows(db,table):raise ValueError('公開/予約/置換が存在します')
        if request_rows(db)!=self.snapshot['lunch']['requests']:raise ValueError('保存済み朝昼の要求集合が変わりました')

def execute(gate,ai,drive,meal,host,evidence,capture,discord,commit):
    from .current_day_gate import Operations
    from .tolerance_case import ReviewedAI
    from .correction import correct_meal_once
    from .request_ledger import RequestLedger
    from .registration import assert_day_state
    from .current_day_recovery import finish
    from .readiness import check_evidence
    from .tolerance_review import policy_sha256
    source=Path(gate.snapshot['output']);draft=json.loads((source/'draft.json').read_bytes());bound=gate.snapshot['lunch']
    if commit!=gate.approval['code_commit'] or fingerprint(draft)!=gate.snapshot['draft_digest'] or mapping(draft['meals']['lunch'],bound['original'],json.loads(next(r for r in bound['reviews'] if r['step']=='meal_review:lunch')['raw']))!=bound['mapping']:raise ValueError('凍結計画/対応/実装が不一致です')
    check_evidence(evidence,TARGET,gate.clock())
    with gate.running():
        ops=Operations(gate);ai=ReviewedAI(gate,ops.client(ai,'ai',{'structured','review_ogp'}));meal=ops.client(meal,'m1',{'day_state','request_state','register','json_write'})
        gate.validate_initial_state(meal.day_state(TARGET));journal=PreparationJournal(gate.store)
        journal.run(TARGET,'lunch_material_mapping',{'source':bound['original'],'meal':draft['meals']['lunch'],'mapping':bound['mapping']},lambda:bound['mapping'])
        raw=Path(gate.snapshot['approved_images']['lunch']['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=bound['original']['result']['image_sha256']:raise ValueError('同じ昼画像だけを訂正します')
        def state():return assert_day_state(meal.day_state(TARGET),TARGET,{'breakfast':bound['breakfast'],'lunch':bound['original']},gate.clock())
        correct_meal_once(journal,RequestLedger(gate.store),meal,ai,state,TARGET,'lunch',draft['meals']['lunch'],raw,bound['original'],host)
        reader=lambda url,host:ops.invoke('ogp:read',{'url':url},lambda:capture(url,host),lookup=True)
        packet={'target_date':TARGET,'state':'images_reviewed','images':gate.snapshot['approved_images']}
        out=source/('normalization-'+gate.snapshot['partial']['parent'][0]['normalization_continuation_uuid'])
        return finish(gate,draft,packet,out,None,meal,ai,host,evidence,reader,discord,commit,meal_policy_sha256=policy_sha256(),saved_images=gate.snapshot['partial']['drive'])
