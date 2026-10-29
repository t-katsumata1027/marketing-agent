"""鮮度停止の実原本を固定する。完了工程や旧期限を作り直さない。"""
import hashlib,json,re
from pathlib import Path
from .current_day_origin import TARGET,stored,verify_stored_rows
from .preparation import fingerprint

VERSION='mina-current-day-continuation-v1'


def file_record(path):
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file():raise ValueError('継続原本のリンク・欠損を拒否します')
    return {'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def rows(db,table):
    return [dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE target_date=? ORDER BY rowid',(TARGET,))]


def capture(db,base,receipts,stop_trace):
    """root管理入口が実停止traceと台帳・実ファイルを読み、別承認の原本を作る。"""
    trace=stop_trace.decode('utf-8')
    if (not trace.rstrip().endswith('ValueError: 外部予約の鮮度・原本を再照合してください')
            or 'current_day_gate.py' not in trace or 'preparation.py' not in trace):raise ValueError('今回の鮮度停止以外は継続しません')
    job=dict(db.execute('SELECT * FROM current_day_jobs WHERE target_date=?',(TARGET,)).fetchone() or {})
    if job.get('state')!='held':raise ValueError('停止済みの親jobが必要です')
    origin=stored(db);verify_stored_rows(db,origin)
    supplied=Path(base)
    if any(p.is_symlink() for p in (supplied,*supplied.parents)):raise ValueError('停止原本領域のリンクを拒否します')
    base=supplied.resolve();original=json.loads((base/'draft.json').read_bytes())
    if fingerprint(original)!=origin['draft_digest']:raise ValueError('旧原本計画が変わっています')
    out=base/('current-day-'+job['recovery_uuid']);draft=json.loads((out/'draft.json').read_bytes())
    steps=rows(db,'preparation_steps');by={r['step']:r for r in steps}
    prior={r['step'] for r in origin['steps']}
    rounds={}
    for slot in ('breakfast','lunch'):
        key='automatic_image_repair:'+slot
        r=by.get(key)
        if not r or r['state']!='complete':raise ValueError('朝昼の一回修復の原本不足')
        rounds[slot]=json.loads(r['result'])['round']
    expected=prior|{'meal_replan:dinner:2','automatic_image_repair:breakfast','automatic_image_repair:lunch',
        'image:breakfast','image_review:breakfast','image:lunch','image_review:lunch',
        'image:breakfast:'+rounds['breakfast'],'image_review:breakfast:'+rounds['breakfast'],'image:lunch:'+rounds['lunch']}
    extra=set(by)-expected
    if len(extra)!=1 or not next(iter(extra)).startswith('menu_nutrition:') or set(by)!=expected|extra:raise ValueError('停止箇所が今回の固定工程と一致しません')
    if any(r['state']!='complete' for r in steps if r['step'] not in prior):raise ValueError('未完了・未知の工程が存在します')
    positive={'status':'image_acceptable','reason_codes':[]};negative={'status':'hold','reason_codes':['image_analysis_mismatch']}
    if (json.loads(by['image_review:breakfast']['result'])!=negative or json.loads(by['image_review:lunch']['result'])!=negative
            or json.loads(by['image_review:breakfast:'+rounds['breakfast']]['result'])!=positive):raise ValueError('朝昼の点検原本が不一致です')
    calls=[dict(r) for r in db.execute('SELECT * FROM current_day_calls WHERE recovery_uuid=? ORDER BY id',(job['recovery_uuid'],))]
    from collections import Counter
    if (Counter(r['operation'] for r in calls)!=Counter({'ai:structured':2,'ai:image':4,'ai:review_image':3,'m1:day_state':1})
            or any(r['state']!='complete' or not r['result_digest'] for r in calls)):raise ValueError('既存操作の完了と費用を照合してください')
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='current_day_cards'").fetchone() and db.execute('SELECT 1 FROM current_day_cards WHERE recovery_uuid=?',(job['recovery_uuid'],)).fetchone():raise ValueError('既存の準備カード・送付があります')
    for table in ('mina_requests','meal_replacement_requests','replacement_attempts','candidates','instagram_candidates','publication_mutation_leases','buffer_publication','instagram_attempts'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone() and rows(db,table):raise ValueError('要求・公開・送付がある日は継続しません')
    adoptions=rows(db,'meal_plan_adoptions')
    proposed=json.loads(by['meal_replan:dinner:2']['result'])
    if (len(adoptions)!=1 or adoptions[0]['state']!='adopted' or adoptions[0]['round']!=proposed['round']
            or proposed['draft']!=draft or adoptions[0]['after_digest']!=fingerprint(draft)
            or adoptions[0]['meal_digest']!=fingerprint(draft['meals']['dinner'])):raise ValueError('夕食の採用版が不一致です')
    if any(draft['meals'][s]!=original['meals'][s] for s in ('breakfast','lunch')) or draft['daily_comment']!=original['daily_comment']:raise ValueError('朝昼・日次計画が変更されています')
    from .menu_budget import validate_draft_plan
    validate_draft_plan(draft,db=db)
    if adoptions[0]['before_digest']!=origin['draft_digest'] or adoptions[0]['input_digest']!=by['meal_replan:dinner:2']['digest']:raise ValueError('夕食採用前の入力版が不一致です')
    files=[file_record(base/'draft.json')]
    for p in out.rglob('*'):
        if p.is_symlink():raise ValueError('既存成果物のリンクを拒否します')
        if p.is_file():files.append(file_record(p))
    for slot in ('breakfast','lunch'):
        image=json.loads(by['image:'+slot+':'+rounds[slot]]['result']);path=Path(image['path'])
        expected_path=out/'images'/rounds[slot]/(TARGET+'-'+slot+'.jpg')
        if path!=expected_path or file_record(path)['sha256']!=image['sha256'] or path.stat().st_size!=image['size']:raise ValueError('修復画像の実path・SHA・長さ不一致')
    # 実CLIの送信・結果・state・診断を旧Journalへ結び、9呼出を一対一で確認する。
    ai_steps=[r['step'] for r in steps if r['step'] not in prior and (r['step'].startswith(('image:','image_review:','menu_nutrition:')) or r['step']=='meal_replan:dinner:2')]
    ai_calls=[r for r in calls if r['operation'].startswith('ai:')]
    if len(ai_steps)!=len(ai_calls):raise ValueError('CLI工程と操作数が一致しません')
    step_calls=dict(zip(ai_steps,ai_calls));matched=[]
    for p in Path(receipts).glob('*/invocation.json'):
        invocation=json.loads(p.read_bytes());prep=invocation.get('preparation')
        if not prep or prep.get('target_date')!=TARGET or prep.get('step') not in by or prep['step'] in prior:continue
        row=by[prep['step']]
        if prep.get('input_digest')!=row['digest']:raise ValueError('旧CLIとJournal入力の版が不一致です')
        directory=p.parent
        if invocation.get('job')!=directory.name or not re.fullmatch('[a-f0-9]{32}',directory.name):raise ValueError('CLIの所有jobが不一致です')
        state=json.loads((directory/'state.json').read_bytes());diagnostic=json.loads((directory/'diagnostic.json').read_bytes())
        if state!={'state':'complete','reason':None} or diagnostic.get('exit_code')!=0 or diagnostic.get('job')!=directory.name:raise ValueError('CLI完了原本がありません')
        from .generation_receipts import digest
        if invocation['schema_digest']!=digest(json.loads((directory/'schema.json').read_bytes())):raise ValueError('CLI schema版が不一致です')
        raw_result=json.loads((directory/'result.json').read_bytes());journal_result=json.loads(row['result'])
        operation=step_calls[prep['step']]
        if prep['step'].startswith('image:'):
            expected_operation='ai:image'
            if raw_result!={'generated':True}:raise ValueError('画像生成の成功結果が不一致です')
            returned={'bytes_sha256':journal_result['sha256'],'length':journal_result['size']}
        elif prep['step'].startswith('image_review:'):
            expected_operation='ai:review_image'
            from .generation import review_observations
            from types import SimpleNamespace
            returned=review_observations(SimpleNamespace(),dict(raw_result),(directory/'input-0.jpg').read_bytes(),'image_review')
            if returned!=journal_result:raise ValueError('CLI点検結果と旧Journalの判定が不一致です')
        else:
            expected_operation='ai:structured';returned=raw_result
            expected=journal_result['draft']['meals']['dinner'] if prep['step']=='meal_replan:dinner:2' else journal_result
            if returned!=expected:raise ValueError('CLI生成結果と旧採用・食品概算が不一致です')
        if operation['operation']!=expected_operation or operation['result_digest']!=fingerprint(returned):raise ValueError('CLI結果と旧操作digestが一致しません')
        for name in ('invocation.json','schema.json','result.json','state.json','diagnostic.json'):
            files.append(file_record(directory/name))
        if prep['step'].startswith('image:'):
            files.append(file_record(directory/'image.png'))
            import io
            from PIL import Image
            with Image.open(directory/'image.png') as image:
                converted=io.BytesIO();image.convert('RGB').save(converted,format='JPEG',quality=90,optimize=True)
            if hashlib.sha256(converted.getvalue()).hexdigest()!=json.loads(row['result'])['sha256']:raise ValueError('CLI生成原本と保存JPEGが一致しません')
        if prep['step'].startswith('image_review:'):
            image=json.loads(by[prep['step'].replace('image_review:','image:',1)]['result'])
            if diagnostic.get('input_image_sha256')!=[image['sha256']]:raise ValueError('点検した実画像が不一致です')
            files.append(file_record(directory/'input-0.jpg'))
            if files[-1]['sha256']!=image['sha256']:raise ValueError('CLI入力画像の実体が不一致です')
        matched.append(prep['step'])
    if Counter(matched)!=Counter(ai_steps) or len(matched)!=9:raise ValueError('9回の実CLI receipt集合が不完全です')
    tables={t:rows(db,t) for t in ('preparation_steps','menu_nutrition_bases','menu_nutrition_admissions','meal_plan_adoptions')}
    # 既存値は更新不可。以降に追加した工程だけを別の継続台帳で扱う。
    return {'spec':VERSION,'target_date':TARGET,'parent':job,'tables':tables,'calls':calls,'files':sorted(files,key=lambda r:r['path']),
        'rounds':rounds,'output':str(out),'base':str(base),'receipts':str(Path(receipts).resolve()),'draft_digest':fingerprint(draft),
        'stop_trace_sha256':hashlib.sha256(stop_trace).hexdigest(),'ai_receipt_count':9}


def verify(db,snapshot):
    if snapshot.get('spec')!=VERSION or snapshot.get('target_date')!=TARGET:raise ValueError('停止原本の契約・対象日が不正です')
    job=db.execute('SELECT * FROM current_day_jobs WHERE target_date=?',(TARGET,)).fetchone()
    if not job or dict(job)!=snapshot['parent']:raise ValueError('親job・旧初回・旧期限が変更されています')
    verify_stored_rows(db,stored(db))
    for table,records in snapshot['tables'].items():
        current=rows(db,table)
        # 元食品概算・採用台帳の新規追記も認めず、再計画を増やさない。
        if table!='preparation_steps' and current!=records:raise ValueError('旧食品概算・採用根拠が変わっています')
        if table=='preparation_steps':
            by={r['step']:r for r in current}
            if any(by.get(r['step'])!=r for r in records):raise ValueError('旧工程が変更されています')
    for row in snapshot['calls']:
        current=db.execute('SELECT * FROM current_day_calls WHERE id=?',(row['id'],)).fetchone()
        if not current or dict(current)!=row:raise ValueError('旧操作・費用原本が変わっています')
    for record in snapshot['files']:
        if file_record(record['path'])!=record:raise ValueError('旧画像・receipt・計画が変わっています')
