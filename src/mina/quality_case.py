"""本人の一回例外で昼画像1枚を追加する。旧2画像/2hold/失効窓は不変。"""
import hashlib,json,uuid
from pathlib import Path
from .current_day_continuation import ContinuationGate
from .current_day_origin import TARGET
from .quality_checkpoint import VERSION,verify
from .preparation import PreparationJournal,fingerprint,prepare_images
from .preparation_status import QualityHold
from .portion_review import request,validate,policy_sha256

REPAIR_INSTRUCTION=('献立と全食品・分量は維持する。卵は計画の半個分25gを意識した控えめな少量として薄く盛り、'
    '鶏そぼろを主な具にし、白米も適度に見える自然な二色丼にする。'
    '正確な重量が写真から測れると主張しない。主菜・副菜・汁物の全器の外周と余白を保持し、食品を省略しない。')


def image_policy(gate,meal):
    from .generation import IMAGE_STYLE,meal_image_description
    description=meal_image_description(meal)+'\n'+REPAIR_INSTRUCTION
    value={'policy':'human-additional-lunch-image-v1','case_uuid':gate.approval['quality_case_uuid'],
        'meal_digest':fingerprint(meal),'repair_instruction':REPAIR_INSTRUCTION,
        'prompt_sha256':hashlib.sha256((IMAGE_STYLE+'\n料理と量：'+description).encode()).hexdigest()}
    return value,description


class QualityGate(ContinuationGate):
    TABLE='current_day_quality_cases'
    FLAG='MINA_CURRENT_DAY_QUALITY_ENABLED'
    CONTRACT=VERSION
    ID_FIELD='quality_case_uuid'
    EXTRA_FIELDS={'slot'}

    def verify_source(self,db):
        verify(db,self.snapshot)
        from .clients import MealClient
        MealClient.request_key(self.approval[self.ID_FIELD])
        directory=Path(self.snapshot['output'])/'images'/self.approval[self.ID_FIELD];policy=directory/'generation-policy.json'
        existing=db.execute('SELECT 1 FROM preparation_steps WHERE target_date=? AND step=?',(TARGET,'image:lunch:'+self.approval[self.ID_FIELD])).fetchone()
        if existing or policy.exists() or policy.is_symlink():
            from .current_day_checkpoint import file_record
            file_record(policy)
            draft=json.loads((Path(self.snapshot['output'])/'draft.json').read_bytes());expected,_=image_policy(self,draft['meals']['lunch'])
            if policy.read_bytes()!=json.dumps(expected,ensure_ascii=False,sort_keys=True).encode():raise ValueError('追加画像の指示原本が変わっています')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='quality_case_calls'").fetchone():
            old={r['id'] for r in self.snapshot['calls']}
            linked={r[0] for r in db.execute('SELECT call_id FROM quality_case_calls WHERE case_uuid=?',(self.approval[self.ID_FIELD],))}
            added={r[0] for r in db.execute('SELECT id FROM current_day_calls WHERE recovery_uuid=?',(self.approval['recovery_uuid'],))}-old
            if added!=linked:raise ValueError('別caseの操作が混入しました')

    def validate_scope(self,a):
        if a['slot']!='lunch' or self.snapshot.get('old_lunch_generation_count')!=2 or self.snapshot.get('permitted_additional_lunch_generation_count')!=1:raise ValueError('今回の昼追加1画像だけが対象です')

    def issue(self,state,receipt):
        # 外部呼出はないDDLだけ。発行判定は親と旧集合を同じTXで再検査する。
        self.enabled()
        with self.store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS quality_case_calls (
                case_uuid TEXT NOT NULL,call_id INTEGER NOT NULL UNIQUE,operation TEXT NOT NULL,step TEXT,
                PRIMARY KEY(case_uuid,call_id))''')
        return super().issue(state,receipt)

    def record_call(self,db,key,name):
        from .generation_receipts import CURRENT
        current=CURRENT.get();step=current['step'] if current else None
        case=self.approval[self.ID_FIELD];lunch_image='image:lunch:'+case;lunch_review='image_review:lunch:'+case
        dinner_steps={'image:dinner'}
        repair=db.execute("SELECT state,result FROM preparation_steps WHERE target_date=? AND step='automatic_image_repair:dinner'",(TARGET,)).fetchone()
        if repair and repair['state']=='complete':dinner_steps.add('image:dinner:'+json.loads(repair['result'])['round'])
        if name=='ai:image':
            if step not in dinner_steps|{lunch_image}:raise ValueError('朝画像や別の昼/夕画像は生成しません')
            if db.execute("SELECT 1 FROM quality_case_calls WHERE case_uuid=? AND step=? AND operation='ai:image'",(case,step)).fetchone():raise ValueError('同じ画像生成を重ねません')
            if db.execute("SELECT count(*) FROM quality_case_calls WHERE case_uuid=? AND operation='ai:image'",(case,)).fetchone()[0]>=3:raise ValueError('昼1/夕最大2画像の上限です')
        if name=='ai:review_image' and step not in {s.replace('image:','image_review:',1) for s in dinner_steps}:raise ValueError('朝昼の旧画像を再点検しません')
        if name=='ai:structured' and step not in {lunch_review,'correction_plan:breakfast','correction_plan:lunch','correction_plan:dinner'}:raise ValueError('献立・概算の追加生成は行いません')
        if name.startswith('ai:') and (not current or current['target_date']!=TARGET):raise ValueError('AIは対象日の工程台帳へ束縛します')
        if name.startswith('ai:'):
            journal=db.execute('SELECT state,digest FROM preparation_steps WHERE target_date=? AND step=?',(TARGET,step)).fetchone()
            if not journal or journal['state']!='started' or journal['digest']!=current['input_digest']:raise ValueError('送信前の工程入力原本がありません')
        if step and step.startswith('image_review:lunch') and (name!='ai:structured' or step!=lunch_review):raise ValueError('昼点検の別経路を拒否します')
        if step==lunch_review and db.execute('SELECT 1 FROM quality_case_calls WHERE case_uuid=? AND step=?',(case,step)).fetchone():raise ValueError('昼追加画像の点検は一度だけです')
        if step and step.startswith('meal_replan:'):raise ValueError('再計画を追加しません')
        db.execute('INSERT INTO quality_case_calls VALUES(?,?,?,?)',(case,key,name,step))


def lunch_image(gate,draft,ai,out):
    directory=out/'images'/gate.approval['quality_case_uuid'];directory.mkdir(mode=0o700)
    path=directory/(TARGET+'-lunch.jpg');meal=draft['meals']['lunch'];policy,description=image_policy(gate,meal)
    policy_path=directory/'generation-policy.json'
    with policy_path.open('x',encoding='utf-8') as output:json.dump(policy,output,ensure_ascii=False,sort_keys=True)
    journal=PreparationJournal(gate.store);suffix=':lunch:'+gate.approval['quality_case_uuid']
    def generate():
        if path.exists() or path.is_symlink():raise ValueError('既存画像を上書きしません')
        raw=ai.image(description)
        import io
        from PIL import Image
        if not isinstance(raw,bytes) or not 0<len(raw)<=4*1024*1024:raise ValueError('新画像の容量が不正です')
        with Image.open(io.BytesIO(raw)) as image:
            if image.format!='JPEG':raise ValueError('新画像はJPEGが必要です')
        with path.open('xb') as output:output.write(raw)
        return {'path':str(path.resolve()),'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}
    image=journal.run(TARGET,'image'+suffix,{'meal':meal,'directory':str(directory.resolve()),'prompt_sha256':policy['prompt_sha256'],'policy_artifact_digest':fingerprint(policy),'exception':'human_additional_lunch_image_1'},generate)
    raw=path.read_bytes()
    if image['sha256']!=hashlib.sha256(raw).hexdigest() or image['size']!=len(raw):raise ValueError('新画像の実体が変わりました')
    result=journal.run(TARGET,'image_review'+suffix,{'sha256':image['sha256'],'meal':meal,'review_policy_sha256':policy_sha256()},lambda:request(ai,raw,meal))
    verdict=validate(result,meal)
    with gate.store.transaction() as db:
        gate.check(db,sending=True)
        db.execute('''CREATE TABLE IF NOT EXISTS quality_case_reviews (
            case_uuid TEXT PRIMARY KEY,image_sha256 TEXT NOT NULL,policy_sha256 TEXT NOT NULL,
            raw_digest TEXT NOT NULL,verdict TEXT NOT NULL)''')
        db.execute('INSERT INTO quality_case_reviews VALUES(?,?,?,?,?)',(gate.approval['quality_case_uuid'],image['sha256'],policy_sha256(),fingerprint(result),json.dumps(verdict)))
        gate.store.audit.emit('creation',TARGET,'lunch',None,'image_review','observed' if verdict['status']=='image_acceptable' else 'held',
            'none' if verdict['status']=='image_acceptable' else ('diagnostic_incomplete' if 'diagnostic_incomplete' in verdict['reason_codes'] else 'image_analysis_mismatch'),{'image_sha256':image['sha256'],'matched':verdict['status']=='image_acceptable'},db=db)
    if verdict!={'status':'image_acceptable','reason_codes':[]}:raise QualityHold('追加昼画像の点検で保留しました','image_review:lunch',verdict['reason_codes'])
    return {**image,'image_review':verdict}


def execute(gate,ai,drive,meal,host,evidence,capture,discord,commit):
    from .current_day_gate import Operations
    from .current_day_recovery import bounded_drive,finish
    from .daily_pipeline import write_artifact
    from .readiness import check_evidence
    from .m1_contract import validate_state
    out=Path(gate.snapshot['output']);draft=json.loads((out/'draft.json').read_bytes())
    if commit!=gate.approval['code_commit'] or fingerprint(draft)!=gate.snapshot['draft_digest']:raise ValueError('品質救済の実装/計画版が不一致です')
    check_evidence(evidence,TARGET,gate.clock())
    with gate.running(),bounded_drive(drive,gate):
        ops=Operations(gate);ai=ops.client(ai,'ai',{'structured','image','review_image','review_meal','review_ogp'})
        drive=ops.client(drive,'drive',{'upload','verify'});meal=ops.client(meal,'m1',{'day_state','request_state','register','json_write'})
        reader=lambda url,host:ops.invoke('ogp:read',{'url':url},lambda:capture(url,host),lookup=True)
        state=meal.day_state(TARGET)
        if validate_state(state,TARGET,gate.clock())!=gate.approval['initial_state_version'] or state['meal_count']!=0:raise ValueError('品質救済の発行後に正本が変わりました')
        # 旧policyで合格した朝画像を原本参照で使い、別policyの完了を作らない。
        by={r['step']:r for r in gate.snapshot['tables']['preparation_steps']};r=gate.snapshot['rounds']['breakfast']
        breakfast=json.loads(by['image:breakfast:'+r]['result']);review=json.loads(by['image_review:breakfast:'+r]['result'])
        if review!={'status':'image_acceptable','reason_codes':[]}:raise ValueError('旧朝画像の合格原本不足')
        images={'breakfast':{**breakfast,'image_review':review},'lunch':lunch_image(gate,draft,ai,out)}
        try:result=prepare_images(gate.store,out/'draft.json',out/'images',ai,only_slots=('dinner',))
        except QualityHold as error:
            if error.step!='image_review:dinner' or set(error.reason_codes)!={'image_analysis_mismatch'}:raise
            key=PreparationJournal(gate.store).run(TARGET,'automatic_image_repair:dinner',{'meal':draft['meals']['dinner'],'reason_codes':list(error.reason_codes)},lambda:{'round':str(uuid.uuid4())})['round']
            result=prepare_images(gate.store,out/'draft.json',out/'images',ai,replacement_round=key,replace_slots=('dinner',),only_slots=('dinner',))
        images.update(result['images']);packet={'target_date':TARGET,'state':'images_reviewed','images':images}
        write_artifact(out/'images-reviewed.json',packet)
        return finish(gate,draft,packet,out,drive,meal,ai,host,evidence,reader,discord,commit)
