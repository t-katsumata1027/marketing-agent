"""旧品質救済の失敗原本まで凍結し、選択済み旧修復画像へ束縛する。"""
import copy,hashlib,io,json
from pathlib import Path
from .quality_checkpoint import verify as verify_quality,VERSION as QUALITY_VERSION
from .current_day_checkpoint import rows,file_record
from .current_day_origin import TARGET
from .preparation import fingerprint

VERSION='mina-current-day-tolerance-v1'

def quality_rows(db,table,case):
    if table=='current_day_quality_cases':return rows(db,table)
    return [dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE case_uuid=? ORDER BY rowid',(case,))]

def verify(db,snapshot):
    if snapshot.get('spec')!=VERSION:raise ValueError('概算許容caseの原本形式が不一致です')
    prior=copy.deepcopy(snapshot);prior['spec']=QUALITY_VERSION
    verify_quality(db,prior)
    case=snapshot['quality_tables']['current_day_quality_cases'][0]['quality_case_uuid']
    for table,records in snapshot['quality_tables'].items():
        if quality_rows(db,table,case)!=records:raise ValueError('旧品質caseの原本が変更されています')
    selected=snapshot['selected_image']
    if file_record(selected['path'])['sha256']!=selected['sha256']:raise ValueError('選択済み旧画像の実体が不一致です')

def capture(db,previous,previous_path):
    verify_quality(db,previous)
    quality=rows(db,'current_day_quality_cases')
    if len(quality)!=1 or quality[0]['state']!='held':raise ValueError('停止済み旧品質caseが必要です')
    case=quality[0]['quality_case_uuid'];steps=rows(db,'preparation_steps');by={r['step']:r for r in steps}
    new_steps={'image:lunch:'+case,'image_review:lunch:'+case}
    if set(by)-{r['step'] for r in previous['tables']['preparation_steps']}!=new_steps:raise ValueError('旧品質救済後に別工程があります')
    if any(by[k]['state']!='complete' for k in new_steps):raise ValueError('旧追加生成/点検の成否確認が必要です')
    image=json.loads(by['image:lunch:'+case]['result']);review=json.loads(by['image_review:lunch:'+case]['result'])
    if review.get('status')!='hold':raise ValueError('旧品質保留を解除しません')
    calls=[dict(r) for r in db.execute('SELECT * FROM current_day_calls WHERE recovery_uuid=? ORDER BY id',(previous['parent']['recovery_uuid'],))]
    added=[r for r in calls if r['id'] not in {x['id'] for x in previous['calls']}]
    if [r['operation'] for r in added]!=['m1:day_state','ai:image','ai:structured'] or any(r['state']!='complete' for r in added):raise ValueError('旧追加操作の成否/消費が不一致です')
    linked=quality_rows(db,'quality_case_calls',case)
    if {r['call_id'] for r in linked}!={r['id'] for r in added}:raise ValueError('旧AI呼出の所有情報が不一致です')
    verdicts=quality_rows(db,'quality_case_reviews',case)
    if len(verdicts)!=1 or verdicts[0]['image_sha256']!=image['sha256'] or verdicts[0]['raw_digest']!=fingerprint(review):raise ValueError('旧独立点検の原本が不一致です')
    from .portion_review import validate,policy_sha256
    draft=json.loads((Path(previous['output'])/'draft.json').read_bytes())
    if json.loads(verdicts[0]['verdict'])!=validate(review,draft['meals']['lunch']) or verdicts[0]['policy_sha256']!=policy_sha256():raise ValueError('旧点検policy/判定が不一致です')
    files=[file_record(previous_path),file_record(image['path']),file_record(Path(image['path']).parent/'generation-policy.json')]
    found=[]
    for path in Path(previous['receipts']).glob('*/invocation.json'):
        invocation=json.loads(path.read_bytes());prep=invocation.get('preparation') or {}
        if prep.get('target_date')!=TARGET or prep.get('step') not in new_steps:continue
        d=path.parent;step=prep['step'];raw=json.loads((d/'result.json').read_bytes());diag=json.loads((d/'diagnostic.json').read_bytes())
        if invocation.get('job')!=d.name or prep['input_digest']!=by[step]['digest'] or invocation['schema_digest']!=fingerprint(json.loads((d/'schema.json').read_bytes())) or diag.get('exit_code')!=0 or json.loads((d/'state.json').read_bytes())!={'state':'complete','reason':None}:raise ValueError('旧品質CLIの入力/完了原本が不一致です')
        for name in ('invocation.json','schema.json','result.json','state.json','diagnostic.json'):files.append(file_record(d/name))
        if step.startswith('image_review:'):
            from .portion_review import schema
            if json.loads((d/'schema.json').read_bytes())!=schema():raise ValueError('旧構造化点検Schemaが不一致です')
            if raw!=review or diag.get('input_image_sha256')!=[image['sha256']]:raise ValueError('旧点検実画像が不一致です')
            record=file_record(d/'input-0.jpg');files.append(record)
            if record['sha256']!=image['sha256']:raise ValueError('旧入力実バイトが不一致です')
        else:
            from .generation import object_schema
            if json.loads((d/'schema.json').read_bytes())!=object_schema({'generated':{'type':'boolean'}}):raise ValueError('旧画像生成Schemaが不一致です')
            if raw!={'generated':True}:raise ValueError('旧生成完了を確認できません')
            from PIL import Image
            files.append(file_record(d/'image.png'))
            with Image.open(d/'image.png') as im:
                out=io.BytesIO();im.convert('RGB').save(out,format='JPEG',quality=90,optimize=True)
            if hashlib.sha256(out.getvalue()).hexdigest()!=image['sha256']:raise ValueError('旧生成/保存画像が不一致です')
        found.append(step)
    expected_results={'ai:image':fingerprint({'bytes_sha256':image['sha256'],'length':image['size']}),'ai:structured':fingerprint(review)}
    for call in added:
        if call['operation'] in expected_results and call['result_digest']!=expected_results[call['operation']]:raise ValueError('旧操作と実生成結果が一致しません')
    if len(found)!=2 or set(found)!=new_steps:raise ValueError('旧追加CLIの集合が不一致です')
    selected=json.loads(by['image:lunch:'+previous['rounds']['lunch']]['result'])
    value=copy.deepcopy(previous);value.update(spec=VERSION,calls=calls,selected_image=selected,
       files=sorted(previous['files']+files,key=lambda r:r['path']),ai_receipt_count=12,
       tables={t:rows(db,t) for t in previous['tables']},audit_rows=rows(db,'operation_events'),
       quality_tables={'current_day_quality_cases':quality,'quality_case_calls':linked,'quality_case_reviews':verdicts})
    verify(db,value)
    return value
