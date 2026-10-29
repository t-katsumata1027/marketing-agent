"""保存済み朝食一食の同一行訂正と、未保存二食だけの限定継続。"""
import copy,hashlib,json
from pathlib import Path
from .normalization_continuation import NormalizationGate
from .normalization_checkpoint import verify as verify_normalization
from .current_day_checkpoint import rows,file_record
from .current_day_origin import TARGET
from .preparation import fingerprint,PreparationJournal

VERSION='mina-current-day-partial-continuation-v1'
MAPPING=((0,(0,1,2,3,4)),(1,(7,)),(2,(5,6)))

def request_rows(db):
    return [{k:({'bytes_sha256':hashlib.sha256(v).hexdigest(),'length':len(v)} if isinstance(v,bytes) else v) for k,v in r.items()} for r in rows(db,'mina_requests')]

def validate_mapping(draft,response,review):
    """この朝食の料理と材料の重複・欠落がない対応だけを許可する。"""
    planned=draft['meals']['breakfast']['foods'];actual=response['result']['analysis']['foods']
    if [p['name'] for p in planned]!=['食パン','卵','牛乳','砂糖','バター','プレーンヨーグルト','アーモンド','オレンジ']:raise ValueError('固定朝食材料が不一致です')
    if [a['name'] for a in actual]!=['フレンチトースト','オレンジ','ヨーグルト（アーモンドトッピング）']:raise ValueError('固定朝食料理が不一致です')
    if sorted(i for _,items in MAPPING for i in items)!=list(range(len(planned))):raise ValueError('材料対応の重複/欠落です')
    if review['status']!='hold' or review['checks']['nutrition']!='hold' or any(review['checks'][k]!='pass' for k in ('composition','style','food_identity','visible_portions')) or not review['blocking'] or {b['code'] for b in review['blocking']}!={'nutrition_inconsistent'}:raise ValueError('栄養だけの旧保留以外へ転用しません')
    return [{'analysis_index':i,'planned_indices':list(items)} for i,items in MAPPING]

def capture(db,previous):
    verify_normalization(db,previous)
    parent=rows(db,'current_day_normalization_continuations')
    if len(parent)!=1 or parent[0]['state']!='held':raise ValueError('停止済み正規化継続が必要です')
    steps=rows(db,'preparation_steps');by={r['step']:r for r in steps}
    old={r['step'] for r in previous['tables']['preparation_steps']}
    if any(r['state']!='complete' for r in steps if r['step'] not in old):raise ValueError('追加工程の不明/未完了が残っています')
    if any(k.startswith(('correction_','meal_correction:','meal_register:lunch','meal_register:dinner')) for k in by):raise ValueError('訂正・昼夕は未開始だけを許可します')
    original=json.loads(by['meal_register:breakfast']['result'])
    review=dict(db.execute('SELECT * FROM tolerance_case_reviews WHERE case_uuid=? AND step=?',(parent[0]['normalization_continuation_uuid'],'meal_review:breakfast')).fetchone() or {})
    raw=json.loads(review['raw'])
    from .tolerance_review import policy_sha256
    if review['image_sha256']!=original['result']['image_sha256'] or review['image_sha256']!=previous['approved_images']['breakfast']['sha256'] or review['policy_sha256']!=policy_sha256():raise ValueError('朝食点検の実画像/policyが不一致です')
    if fingerprint(raw)!=review['raw_digest'] or json.loads(review['verdict'])!={'status':'hold','reason_codes':['nutrition_semantic_mismatch']}:raise ValueError('旧保留原本が不一致です')
    draft=json.loads((Path(previous['output'])/'draft.json').read_bytes());mapping=validate_mapping(draft,original,raw)
    calls=[dict(r) for r in db.execute('SELECT * FROM current_day_calls WHERE recovery_uuid=? ORDER BY id',(previous['parent']['recovery_uuid'],))]
    if any(r['state']!='complete' for r in calls):raise ValueError('不明要求を再開しません')
    request=request_rows(db)
    if len(request)!=1 or request[0]['operation']!='meal_register' or request[0]['slot']!='breakfast' or request[0]['phase']!='complete':raise ValueError('朝食保存一件の完了証跡だけが必要です')
    out=copy.deepcopy(previous);out.update(calls=calls,tables={t:rows(db,t) for t in previous['tables']})
    path=Path(previous['output'])/('normalization-'+parent[0]['normalization_continuation_uuid'])/'drive.json'
    drive=json.loads(path.read_bytes())
    if drive['state']!='images_drive_verified' or drive['target_date']!=TARGET:raise ValueError('Drive完了原本が必要です')
    out['files'].append(file_record(path))
    out['partial']={'parent':parent,'review':review,'original':original,'requests':request,'mapping':mapping,'drive':drive}
    matched=[]
    for path in Path(previous['receipts']).glob('*/invocation.json'):
        invocation=json.loads(path.read_bytes());prep=invocation.get('preparation') or {}
        if prep.get('target_date')!=TARGET or prep.get('step')!='meal_review:breakfast':continue
        directory=path.parent
        if json.loads((directory/'result.json').read_bytes())!=raw or json.loads((directory/'state.json').read_bytes())!={'state':'complete','reason':None}:raise ValueError('旧栄養CLI結果/完了原本が不一致です')
        image=file_record(directory/'input-0.jpg')
        if image['sha256']!=review['image_sha256']:raise ValueError('旧栄養CLI実画像が不一致です')
        for name in ('invocation.json','schema.json','result.json','state.json','diagnostic.json','input-0.jpg'):out['files'].append(file_record(directory/name))
        matched.append(path)
    if len(matched)!=1:raise ValueError('旧栄養CLI原本は一件だけ必要です')
    return out

class PartialGate(NormalizationGate):
    TABLE='current_day_partial_continuations';FLAG='MINA_CURRENT_DAY_PARTIAL_ENABLED';CONTRACT=VERSION;ID_FIELD='partial_continuation_uuid';EXTRA_FIELDS=set()
    def validate_scope(self,a):pass
    def verify_source(self,db):
        verify_normalization(db,self.snapshot)
        partial=self.snapshot['partial']
        if rows(db,'current_day_normalization_continuations')!=partial['parent']:raise ValueError('旧held/期限が変更されました')
        old=db.execute('SELECT * FROM tolerance_case_reviews WHERE case_uuid=? AND step=?',(partial['review']['case_uuid'],'meal_review:breakfast')).fetchone()
        if not old or dict(old)!=partial['review']:raise ValueError('旧保留が変更されました')
        oldids={r['id'] for r in self.snapshot['calls']}
        actual={r[0] for r in db.execute('SELECT id FROM current_day_calls WHERE recovery_uuid=?',(self.approval['recovery_uuid'],))}-oldids
        linked={r[0] for r in db.execute('SELECT call_id FROM tolerance_case_calls WHERE case_uuid=?',(self.approval[self.ID_FIELD],))}
        if actual!=linked:raise ValueError('部分日継続以外の操作が混入しました')
    def validate_initial_state(self,state):
        from .registration import assert_day_state
        if state['meal_count']!=1 or state.get('daily_share') is not None:raise ValueError('朝食一食だけが必要です')
        assert_day_state(state,TARGET,{'breakfast':self.snapshot['partial']['original']},self.clock())
    def validate_requests(self,db):
        from .publication_hold import exists
        for t in ('meal_replacement_requests','replacement_attempts','candidates','instagram_candidates','publication_mutation_leases','buffer_publication','instagram_attempts'):
            if exists(db,t) and rows(db,t):raise ValueError('公開・予約・置換が存在します')
        if request_rows(db)!=self.snapshot['partial']['requests']:raise ValueError('旧要求が変わりました')

def execute(gate,ai,drive,meal,host,evidence,capture,discord,commit):
    from .current_day_gate import Operations
    from .tolerance_case import ReviewedAI
    from .correction import correct_meal_once
    from .request_ledger import RequestLedger
    from .registration import assert_day_state
    from .current_day_recovery import finish
    from .readiness import check_evidence
    from .tolerance_review import policy_sha256
    source=Path(gate.snapshot['output']);draft=json.loads((source/'draft.json').read_bytes())
    if commit!=gate.approval['code_commit'] or fingerprint(draft)!=gate.snapshot['draft_digest']:raise ValueError('実装/凍結計画が不一致です')
    check_evidence(evidence,TARGET,gate.clock())
    partial=gate.snapshot['partial'];validate_mapping(draft,partial['original'],json.loads(partial['review']['raw']))
    with gate.running():
        ops=Operations(gate);ai=ReviewedAI(gate,ops.client(ai,'ai',{'structured','review_ogp'}))
        meal=ops.client(meal,'m1',{'day_state','request_state','register','json_write'})
        gate.validate_initial_state(meal.day_state(TARGET))
        journal=PreparationJournal(gate.store)
        journal.run(TARGET,'partial_breakfast_mapping',{'source':partial['original'],'plan':draft['meals']['breakfast'],'mapping':partial['mapping']},lambda:partial['mapping'])
        original=partial['original'];image=gate.snapshot['approved_images']['breakfast'];raw=Path(image['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=original['result']['image_sha256']:raise ValueError('同一画像だけを訂正します')
        def state():return assert_day_state(meal.day_state(TARGET),TARGET,{'breakfast':original},gate.clock())
        correct_meal_once(journal,RequestLedger(gate.store),meal,ai,state,TARGET,'breakfast',draft['meals']['breakfast'],raw,original,host)
        # Driveの旧完了工程は同じ入力で再利用し、uploadを追加しない。
        class ReuseDrive:
            def upload(self,*a,**kw):raise ValueError('Driveを再送しません')
            def verify(self,*a,**kw):raise ValueError('Drive点検を追加しません')
        reader=lambda url,host:ops.invoke('ogp:read',{'url':url},lambda:capture(url,host),lookup=True)
        packet={'target_date':TARGET,'state':'images_reviewed','images':gate.snapshot['approved_images']}
        # 旧OGP/保留Journalの保存場所を維持し、訂正版だけを別variantで追記する。
        out=source/('normalization-'+partial['parent'][0]['normalization_continuation_uuid'])
        return finish(gate,draft,packet,out,ReuseDrive(),meal,ai,host,evidence,reader,discord,commit,meal_policy_sha256=policy_sha256(),saved_images=partial['drive'])
