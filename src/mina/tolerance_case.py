"""旧画像を保持した一回の概算許容点検から、未開始の3食準備へ接続する。"""
import hashlib,json,uuid
from pathlib import Path
from .current_day_continuation import ContinuationGate
from .current_day_origin import TARGET
from .tolerance_checkpoint import VERSION,verify
from .tolerance_review import request,validate,policy_sha256
from .preparation import PreparationJournal,fingerprint,prepare_images
from .preparation_status import QualityHold

class ToleranceGate(ContinuationGate):
    TABLE='current_day_tolerance_cases';FLAG='MINA_CURRENT_DAY_TOLERANCE_ENABLED';CONTRACT=VERSION
    ID_FIELD='tolerance_case_uuid';EXTRA_FIELDS={'slot','selected_image_sha256'}
    portion_policy_sha256=policy_sha256()

    def verify_source(self,db):
        verify(db,self.snapshot)
        from .clients import MealClient
        MealClient.request_key(self.approval[self.ID_FIELD])
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='tolerance_case_calls'").fetchone():
            old={r['id'] for r in self.snapshot['calls']}
            linked={r[0] for r in db.execute('SELECT call_id FROM tolerance_case_calls WHERE case_uuid=?',(self.approval[self.ID_FIELD],))}
            actual={r[0] for r in db.execute('SELECT id FROM current_day_calls WHERE recovery_uuid=?',(self.approval['recovery_uuid'],))}-old
            if actual!=linked:raise ValueError('概算許容case以外の操作が混入しました')

    def validate_scope(self,a):
        if a['slot']!='lunch' or a['selected_image_sha256']!=self.snapshot['selected_image']['sha256']:raise ValueError('選択済み昼画像以外へ転用しません')

    def issue(self,state,receipt):
        self.enabled()
        with self.store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tolerance_case_calls(case_uuid TEXT NOT NULL,call_id INTEGER NOT NULL UNIQUE,operation TEXT NOT NULL,step TEXT,PRIMARY KEY(case_uuid,call_id))')
        return super().issue(state,receipt)

    def record_call(self,db,key,name):
        from .generation_receipts import CURRENT
        current=CURRENT.get();step=current['step'] if current else None;case=self.approval[self.ID_FIELD]
        dinner={'image:dinner'}
        row=db.execute("SELECT state,result FROM preparation_steps WHERE target_date=? AND step='automatic_image_repair:dinner'",(TARGET,)).fetchone()
        if row and row['state']=='complete':dinner.add('image:dinner:'+json.loads(row['result'])['round'])
        if name=='ai:image' and step not in dinner:raise ValueError('朝昼の画像は生成しません')
        if name=='ai:image' and db.execute("SELECT count(*) FROM tolerance_case_calls WHERE case_uuid=? AND operation='ai:image'",(case,)).fetchone()[0]>=2:raise ValueError('夕画像は初回と既存一回修復までです')
        allowed={s.replace('image:','image_review:',1) for s in dinner}|{'tolerance_image_review:lunch:'+case}
        allowed|={'meal_review:'+s+suffix for s in ('breakfast','lunch','dinner') for suffix in ('',':corrected')}
        allowed|={'correction_plan:'+s for s in ('breakfast','lunch','dinner')}
        if name=='ai:structured' and step not in allowed:raise ValueError('許可された概算点検/既存訂正だけを実行します')
        if name=='ai:review_image' or name=='ai:review_meal':raise ValueError('旧厳格policyへの点検切戻しを拒否します')
        if name.startswith('ai:'):
            if not current or current['target_date']!=TARGET:raise ValueError('AIの対象工程がありません')
            row=db.execute('SELECT state,digest FROM preparation_steps WHERE target_date=? AND step=?',(TARGET,step)).fetchone()
            if not row or row['state']!='started' or row['digest']!=current['input_digest']:raise ValueError('AI送信前の工程入力原本がありません')
            if db.execute('SELECT 1 FROM tolerance_case_calls WHERE case_uuid=? AND step=? AND operation=?',(case,step,name)).fetchone():raise ValueError('同じ工程のAIを再送しません')
            if step and step.startswith('meal_replan:'):raise ValueError('再計画の上限を維持します')
        db.execute('INSERT INTO tolerance_case_calls VALUES(?,?,?,?)',(case,key,name,step))

class ReviewedAI:
    """実送信policyと原本を記録し、通常の合格/保留型へ橋渡しする。"""
    def __init__(self,gate,ai):self.gate=gate;self.ai=ai
    def structured(self,*args,**kwargs):return self.ai.structured(*args,**kwargs)
    def image(self,*args,**kwargs):return self.ai.image(*args,**kwargs)
    def review_ogp(self,*args,**kwargs):return self.ai.review_ogp(*args,**kwargs)
    def allow_correction(self,meal,response,verdict):
        """画像差の訂正は禁止。同名食品の解析量に数値根拠がある場合だけ一回訂正へ進む。"""
        value=getattr(self,'last_raw_review',None)
        if not value or verdict!={'status':'hold','reason_codes':['nutrition_semantic_mismatch']}:return False
        if value['status']!='hold' or {f['code'] for f in value['blocking']}!={'nutrition_inconsistent'}:return False
        if value['checks']['nutrition']!='hold' or any(value['checks'][k]!='pass' for k in ('composition','style','food_identity','visible_portions')):return False
        import re
        def quantity(text):
            m=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\s*',text.lower())
            if not m:return None
            number=float(m[1]);unit=m[2]
            return ('g' if unit in ('kg','g') else 'ml',number*(1000 if unit in ('kg','l') else 1))
        actual=response['result']['analysis']['foods'];planned=meal['foods']
        if len(actual)!=len(planned) or any(a['name'].strip()!=p['name'].strip() for a,p in zip(actual,planned)):return False
        differences=[]
        for a,p in zip(actual,planned):
            left,right=quantity(a['amount']),quantity(p['amount'])
            if left and right and left[0]==right[0] and abs(left[1]-right[1])>1e-6:differences.append(p['name'])
        return bool(differences)
    def review_image(self,raw,meal):return self.review(raw,meal,'image_acceptable')
    def review_meal(self,raw,meal,response,ogp):return self.review(raw,meal,'publishable',response)
    def review(self,raw,meal,positive,response=None):
        from .generation_receipts import CURRENT
        current=CURRENT.get()
        if current is None:raise ValueError('概算点検の工程原本がありません')
        value=request(self.ai,raw,meal,positive,response);verdict=validate(value,meal,positive)
        # 点検が長引いても、既存の全外部一覧を更新してから同TXで結果を記録する。
        self.gate.refresh_external()
        with self.gate.store.transaction() as db:
            self.gate.check(db,sending=True)
            db.execute('''CREATE TABLE IF NOT EXISTS tolerance_case_reviews(
              case_uuid TEXT NOT NULL,step TEXT NOT NULL,image_sha256 TEXT NOT NULL,policy_sha256 TEXT NOT NULL,
              raw_digest TEXT NOT NULL,raw TEXT NOT NULL,verdict TEXT NOT NULL,PRIMARY KEY(case_uuid,step))''')
            db.execute('INSERT INTO tolerance_case_reviews VALUES(?,?,?,?,?,?,?)',(self.gate.approval[self.gate.ID_FIELD],current['step'],hashlib.sha256(raw).hexdigest(),policy_sha256(),fingerprint(value),json.dumps(value,ensure_ascii=False),json.dumps(verdict)))
        self.last_raw_review=value
        return verdict

def execute(gate,ai,drive,meal,host,evidence,capture,discord,commit):
    from .current_day_gate import Operations
    from .current_day_recovery import bounded_drive,finish
    from .daily_pipeline import write_artifact
    from .readiness import check_evidence
    from .m1_contract import validate_state
    out=Path(gate.snapshot['output']);draft=json.loads((out/'draft.json').read_bytes())
    if commit!=gate.approval['code_commit'] or fingerprint(draft)!=gate.snapshot['draft_digest']:raise ValueError('概算許容caseの実装/計画が不一致です')
    check_evidence(evidence,TARGET,gate.clock())
    with gate.running(),bounded_drive(drive,gate):
        ops=Operations(gate);ai=ReviewedAI(gate,ops.client(ai,'ai',{'structured','image','review_ogp'}))
        drive=ops.client(drive,'drive',{'upload','verify'});meal=ops.client(meal,'m1',{'day_state','request_state','register','json_write'})
        reader=lambda url,host:ops.invoke('ogp:read',{'url':url},lambda:capture(url,host),lookup=True)
        state=meal.day_state(TARGET)
        if validate_state(state,TARGET,gate.clock())!=gate.approval['initial_state_version'] or state['meal_count']!=0:raise ValueError('発行後に正本が変わりました')
        by={r['step']:r for r in gate.snapshot['tables']['preparation_steps']};r=gate.snapshot['rounds']['breakfast']
        breakfast=json.loads(by['image:breakfast:'+r]['result']);old=json.loads(by['image_review:breakfast:'+r]['result'])
        if old!={'status':'image_acceptable','reason_codes':[]}:raise ValueError('朝の旧合格原本がありません')
        selected=gate.snapshot['selected_image'];raw=Path(selected['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=gate.approval['selected_image_sha256']:raise ValueError('昼の選択画像が不一致です')
        review=PreparationJournal(gate.store).run(TARGET,'tolerance_image_review:lunch:'+gate.approval[gate.ID_FIELD],
           {'sha256':selected['sha256'],'meal':draft['meals']['lunch'],'policy_sha256':policy_sha256()},lambda:ai.review_image(raw,draft['meals']['lunch']))
        if review!={'status':'image_acceptable','reason_codes':[]}:raise QualityHold('概算許容基準で昼画像を保留しました','image_review:lunch',review.get('reason_codes',()))
        images={'breakfast':{**breakfast,'image_review':old},'lunch':{**selected,'image_review':review}}
        try:value=prepare_images(gate.store,out/'draft.json',out/'images',ai,only_slots=('dinner',),review_policy_sha256=policy_sha256())
        except QualityHold as error:
            if error.step!='image_review:dinner' or set(error.reason_codes)!={'image_analysis_mismatch'}:raise
            key=PreparationJournal(gate.store).run(TARGET,'automatic_image_repair:dinner',{'meal':draft['meals']['dinner'],'reason_codes':list(error.reason_codes)},lambda:{'round':str(uuid.uuid4())})['round']
            value=prepare_images(gate.store,out/'draft.json',out/'images',ai,replacement_round=key,replace_slots=('dinner',),only_slots=('dinner',),review_policy_sha256=policy_sha256())
        images.update(value['images']);packet={'target_date':TARGET,'state':'images_reviewed','images':images}
        write_artifact(out/'images-reviewed.json',packet)
        return finish(gate,draft,packet,out,drive,meal,ai,host,evidence,reader,discord,commit,meal_policy_sha256=policy_sha256())
