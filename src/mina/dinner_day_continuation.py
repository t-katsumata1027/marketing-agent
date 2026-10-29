"""固定標準値による夕食2食品だけの一回訂正。朝昼/他3料理/旧判定を維持する。"""
import copy,hashlib,json,uuid
from decimal import Decimal
from pathlib import Path
from .lunch_day_continuation import LunchGate
from .normalization_checkpoint import verify as verify_normalization
from .partial_day_continuation import request_rows
from .current_day_checkpoint import rows,file_record
from .current_day_origin import TARGET
from .preparation import fingerprint,PreparationJournal

VERSION='mina-current-day-dinner-reference-v1'
REFERENCES={
 'rice':{'food_id':'01088','url':'https://fooddb.mext.go.jp/details/details.pl?ITEM_NO=1_01088_7&MODE=0&TYPE=print&WEIGHT=100','grams':220,'values':{'calories':'156','protein':'2.5','fat':'0.3','carbs':'37.1'}},
 'cabbage':{'food_id':'06061','url':'https://fooddb.mext.go.jp/details/details.pl?ITEM_NO=6_06061_7','grams':80,'values':{'calories':'23','protein':'1.2','fat':'0.1','carbs':'5.2'}}}

def proposal(meal,original):
    """画像の計量ではなく、本人が承認した凍結計画量と一次資料の換算である。"""
    foods=original['result']['analysis']['foods'];result=copy.deepcopy(foods)
    if [f['name'] for f in foods]!=['豚の生姜焼き（玉ねぎ入り）','千切りキャベツ','白米','きゅうりの浅漬け','味噌汁（豆腐とわかめ）']:raise ValueError('固定夕食5料理が不一致です')
    if (meal['foods'][7]['name'],meal['foods'][7]['amount'],meal['foods'][8]['name'],meal['foods'][8]['amount'])!=('キャベツ','80g（千切り）','ごはん','炊飯後220g'):raise ValueError('夕食の凍結計画量が不一致です')
    for index,plan_index,key in ((1,7,'cabbage'),(2,8,'rice')):
        ref=REFERENCES[key];result[index]['amount']=meal['foods'][plan_index]['amount']
        for nutrient,value in ref['values'].items():result[index][nutrient]=float(Decimal(value)*Decimal(ref['grams'])/100)
    if any(result[i]!=foods[i] for i in (0,3,4)):raise ValueError('他3料理は変更しません')
    return {'foods':result}

def capture(db,previous):
    verify_normalization(db,previous)
    parent=rows(db,'current_day_lunch_continuations')
    if len(parent)!=1 or parent[0]['state']!='held':raise ValueError('停止済み昼継続が必要です')
    steps=rows(db,'preparation_steps');by={r['step']:r for r in steps};old={r['step'] for r in previous['tables']['preparation_steps']}
    if any(r['state']!='complete' for r in steps if r['step'] not in old):raise ValueError('追加不明/未完了を再送しません')
    if any(k.startswith(('correction_plan:dinner','correction_key:dinner','meal_correction:dinner','dinner_reference_proposal','daily_share')) for k in by):raise ValueError('夕訂正/日次は未開始だけを許可します')
    for step in ('meal_review:breakfast:corrected','ogp_review:breakfast','meal_review:lunch:corrected','ogp_review:lunch'):
        if json.loads(by[step]['result'])!={'status':'publishable','reason_codes':[]}:raise ValueError('朝昼の合格原本が必要です')
    breakfast=json.loads(by['meal_correction:breakfast']['result']);lunch=json.loads(by['meal_correction:lunch']['result']);original=json.loads(by['meal_register:dinner']['result'])
    case=parent[0]['lunch_continuation_uuid'];reviews=[dict(r) for r in db.execute('SELECT * FROM tolerance_case_reviews WHERE case_uuid=? ORDER BY step',(case,))];review=next(r for r in reviews if r['step']=='meal_review:dinner');raw=json.loads(review['raw'])
    from .tolerance_review import policy_sha256
    if review['image_sha256']!=original['result']['image_sha256'] or review['image_sha256']!=previous['approved_images']['dinner']['sha256'] or review['policy_sha256']!=policy_sha256() or fingerprint(raw)!=review['raw_digest'] or json.loads(review['verdict'])!={'status':'hold','reason_codes':['nutrition_semantic_mismatch']}:raise ValueError('夕食保留の原本が不一致です')
    if raw['status']!='hold' or raw['checks']['nutrition']!='hold' or any(raw['checks'][k]!='pass' for k in ('composition','style','food_identity','visible_portions')) or {(b['code'],b['food_index']) for b in raw['blocking']}!={('nutrition_inconsistent',7),('nutrition_inconsistent',8)}:raise ValueError('承認済み夕食2食品の栄養保留だけを扱います')
    draft=json.loads((Path(previous['output'])/'draft.json').read_bytes());value=proposal(draft['meals']['dinner'],original)
    calls=[dict(r) for r in db.execute('SELECT * FROM current_day_calls WHERE recovery_uuid=? ORDER BY id',(previous['parent']['recovery_uuid'],))]
    if any(r['state']!='complete' for r in calls):raise ValueError('追加要求の不明が残っています')
    out=copy.deepcopy(previous);out.update(calls=calls,tables={t:rows(db,t) for t in previous['tables']})
    out['dinner']={'parent':parent,'reviews':reviews,'breakfast':breakfast,'lunch':lunch,'original':original,'proposal':value,'references':REFERENCES,'requests':request_rows(db)}
    expected=[('breakfast','meal_register'),('breakfast','meal_correction'),('lunch','meal_register'),('lunch','meal_correction'),('dinner','meal_register')]
    if [(r['slot'],r['operation']) for r in out['dinner']['requests']]!=expected or any(r['phase']!='complete' for r in out['dinner']['requests']):raise ValueError('保存済み三食の要求集合が不一致です')
    matched=[]
    for path in Path(previous['receipts']).glob('*/invocation.json'):
        invocation=json.loads(path.read_bytes());prep=invocation.get('preparation') or {};step=prep.get('step')
        if prep.get('target_date')!=TARGET or step not in ('correction_plan:lunch','meal_review:lunch:corrected','ogp_review:lunch','meal_review:dinner'):continue
        directory=path.parent
        if prep.get('input_digest')!=by[step]['digest'] or json.loads((directory/'state.json').read_bytes())!={'state':'complete','reason':None}:raise ValueError('元CLI入力/完了証跡が不一致です')
        if step=='meal_review:dinner' and (json.loads((directory/'result.json').read_bytes())!=raw or file_record(directory/'input-0.jpg')['sha256']!=review['image_sha256']):raise ValueError('夕食点検CLIの実原本が不一致です')
        for item in directory.iterdir():
            if item.name in ('invocation.json','schema.json','result.json','state.json','diagnostic.json') or item.name.startswith('input-') and item.suffix=='.jpg':out['files'].append(file_record(item))
        matched.append(step)
    if sorted(matched)!=sorted(('correction_plan:lunch','meal_review:lunch:corrected','ogp_review:lunch','meal_review:dinner')):raise ValueError('元4CLIの証跡集合が不一致です')
    return out

class DinnerGate(LunchGate):
    TABLE='current_day_dinner_continuations';FLAG='MINA_CURRENT_DAY_DINNER_ENABLED';CONTRACT=VERSION;ID_FIELD='dinner_continuation_uuid'
    def verify_source(self,db):
        super().verify_source(db)
        if rows(db,'current_day_lunch_continuations')!=self.snapshot['dinner']['parent']:raise ValueError('旧昼継続のheld/期限が変わりました')
        for record in self.snapshot['dinner']['reviews']:
            actual=db.execute('SELECT * FROM tolerance_case_reviews WHERE case_uuid=? AND step=?',(record['case_uuid'],record['step'])).fetchone()
            if not actual or dict(actual)!=record:raise ValueError('旧昼合格/夕保留が変わりました')
    def validate_initial_state(self,state):
        from .registration import assert_day_state
        if state['meal_count']!=3 or state.get('daily_share') is not None:raise ValueError('日次未作成の三食だけが必要です')
        assert_day_state(state,TARGET,{s:self.snapshot['dinner'][s if s!='dinner' else 'original'] for s in ('breakfast','lunch','dinner')},self.clock())
    def validate_requests(self,db):
        from .publication_hold import exists
        for table in ('meal_replacement_requests','replacement_attempts','candidates','instagram_candidates','publication_mutation_leases','buffer_publication','instagram_attempts'):
            if exists(db,table) and rows(db,table):raise ValueError('公開/予約/置換が存在します')
        if request_rows(db)!=self.snapshot['dinner']['requests']:raise ValueError('保存済み三食の要求集合が変わりました')

def execute(gate,ai,drive,meal,host,evidence,capture,discord,commit):
    from .current_day_gate import Operations
    from .tolerance_case import ReviewedAI
    from .request_ledger import RequestLedger
    from .registration import assert_day_state
    from .m1_contract import validate_write,completed_request
    from .current_day_recovery import finish
    from .readiness import check_evidence
    from .tolerance_review import policy_sha256
    source=Path(gate.snapshot['output']);draft=json.loads((source/'draft.json').read_bytes());bound=gate.snapshot['dinner'];original=bound['original']
    if commit!=gate.approval['code_commit'] or fingerprint(draft)!=gate.snapshot['draft_digest'] or proposal(draft['meals']['dinner'],original)!=bound['proposal'] or bound['references']!=REFERENCES:raise ValueError('凍結計画/標準値/実装が不一致です')
    check_evidence(evidence,TARGET,gate.clock())
    with gate.running():
        ops=Operations(gate);ai=ReviewedAI(gate,ops.client(ai,'ai',{'structured','review_ogp'}));meal=ops.client(meal,'m1',{'day_state','request_state','register','json_write'});gate.validate_initial_state(meal.day_state(TARGET))
        journal=PreparationJournal(gate.store);requests=RequestLedger(gate.store)
        value=journal.run(TARGET,'dinner_reference_proposal',{'source':original,'plan':draft['meals']['dinner'],'references':REFERENCES},lambda:bound['proposal'])
        def state():return assert_day_state(meal.day_state(TARGET),TARGET,{'breakfast':bound['breakfast'],'lunch':bound['lunch'],'dinner':original},gate.clock())
        key=journal.run(TARGET,'correction_key:dinner',{'proposal':value,'source_version':original['result']['result_version']},lambda:{'key':str(uuid.uuid4()),'expected_state_version':state()})
        payload={'contract_version':'mina-write-v1','reason':'codex_secondary_review','share_url':original['result']['share']['url'],'foods':value['foods'],'comment':original['result']['normalized_comment'],'expected_state_version':key['expected_state_version']}
        requests.prepare(key['key'],'meal_correction',TARGET,'dinner',payload)
        def send():
            if state()!=key['expected_state_version']:raise ValueError('夕食訂正前に正本版が変わりました')
            return meal.json_write('/api/internal/mina/meals',payload,method='PATCH',request_key=key['key'])
        result=journal.run(TARGET,'meal_correction:dinner',payload,lambda:requests.execute_once(key['key'],meal,send))
        validate_write(result,'dinner',TARGET,host,operation='meal_correction',logged_at=original['result']['meal']['logged_at'],input_comment=payload['comment']);completed_request(meal.request_state(key['key'],'meal_correction'),key['key'],'meal_correction',result['result'])
        if any(result['result'][k]!=original['result'][k] for k in ('meal','image_sha256','stored_image_sha256')) or result['result']['share']['url']!=original['result']['share']['url']:raise ValueError('同一夕食行/原画像/共有先が変わりました')
        requests.record(key['key'],'complete',{'persistence':'saved','stage':'completed'})
        reader=lambda url,host:ops.invoke('ogp:read',{'url':url},lambda:capture(url,host),lookup=True)
        packet={'target_date':TARGET,'state':'images_reviewed','images':gate.snapshot['approved_images']};out=source/('normalization-'+gate.snapshot['partial']['parent'][0]['normalization_continuation_uuid'])
        return finish(gate,draft,packet,out,None,meal,ai,host,evidence,reader,discord,commit,meal_policy_sha256=policy_sha256(),saved_images=gate.snapshot['partial']['drive'])
