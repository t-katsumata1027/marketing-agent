"""今回の卵保留だけを別承認へ束縛する。旧完了・保留は変更しない。"""
import copy,json
from datetime import timedelta
from pathlib import Path
from collections import Counter
from .current_day_origin import TARGET
from .current_day_checkpoint import verify as verify_base,rows,file_record
from .preparation import fingerprint
from .portion_review import visible_indices

VERSION='mina-current-day-quality-image-v1'


def capture(db,previous,previous_path,stop_trace):
    verify_base(db,previous)
    trace=stop_trace.decode('utf-8')
    if not trace.rstrip().endswith('src.mina.preparation_status.QualityHold: 画像の事前点検で保留になりました') or 'current_day_continuation.py' not in trace:raise ValueError('今回の昼画像保留以外は対象にしません')
    continuation=dict(db.execute('SELECT * FROM current_day_continuations WHERE target_date=?',(TARGET,)).fetchone() or {})
    if continuation.get('state')!='held' or continuation.get('recovery_uuid')!=previous['parent']['recovery_uuid']:raise ValueError('旧継続の停止が不一致です')
    from .current_day_checkpoint import VERSION as PREVIOUS_VERSION
    from .replacement_client import timestamp
    approval=json.loads(continuation['approval']);a_digest=fingerprint(approval)
    first=[r['occurred'] for r in db.execute("SELECT occurred,details FROM operation_events WHERE target_date=? AND stage='recovery' AND event='started'",(TARGET,)) if json.loads(r['details']).get('input_sha256')==a_digest]
    if (continuation['approval_digest']!=a_digest or approval['spec']!=PREVIOUS_VERSION or approval['target_date']!=TARGET
            or approval['checkpoint_digest']!=fingerprint(previous) or approval['recovery_uuid']!=continuation['recovery_uuid']
            or approval['continuation_uuid']!=continuation['continuation_uuid'] or approval['mode']!='prepare_only' or approval['cost_mode']!='waived'
            or not first or min(first)!=timestamp(continuation['activated_at']).timestamp()
            or timestamp(continuation['deadline'])!=min(timestamp(approval['not_after']),timestamp(continuation['activated_at'])+timedelta(minutes=15))):raise ValueError('旧継続の承認・初回・期限原本が不一致です')
    adopted=json.loads((Path(previous['output'])/'draft.json').read_bytes());visible_indices(adopted['meals']['lunch'])
    current=rows(db,'preparation_steps');old=previous['tables']['preparation_steps'];key='image_review:lunch:'+previous['rounds']['lunch']
    if {r['step'] for r in current}!={r['step'] for r in old}|{key}:raise ValueError('旧継続の停止工程集合が不一致です')
    row=next(r for r in current if r['step']==key)
    held={'status':'hold','reason_codes':['image_analysis_mismatch']}
    if row['state']!='complete' or json.loads(row['result'])!=held:raise ValueError('旧昼点検保留を確認できません')
    calls=[dict(r) for r in db.execute('SELECT * FROM current_day_calls WHERE recovery_uuid=? ORDER BY id',(previous['parent']['recovery_uuid'],))]
    if len(calls)!=12 or any(r['state']!='complete' or not r['result_digest'] for r in calls) or Counter(r['operation'] for r in calls)!=Counter({'ai:structured':2,'ai:image':4,'ai:review_image':4,'m1:day_state':2}):raise ValueError('旧12操作と費用の照合が必要です')
    if calls[:len(previous['calls'])]!=previous['calls'] or calls[-1]['operation']!='ai:review_image' or calls[-1]['result_digest']!=fingerprint(held):raise ValueError('追加点検の操作結果が不一致です')
    from .current_day_continuation import ContinuationGate
    tables=('mina_requests','meal_replacement_requests','replacement_attempts','candidates','instagram_candidates','publication_mutation_leases','buffer_publication','instagram_attempts')
    for table in tables:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone() and rows(db,table):raise ValueError('既存要求・公開状態がある日は対象外です')
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='current_day_cards'").fetchone() and db.execute('SELECT 1 FROM current_day_cards WHERE recovery_uuid=?',(previous['parent']['recovery_uuid'],)).fetchone():raise ValueError('既存の専用カードがあります')
    found=[]
    image=json.loads(next(r['result'] for r in current if r['step']=='image:lunch:'+previous['rounds']['lunch']))
    for p in Path(previous['receipts']).glob('*/invocation.json'):
        invocation=json.loads(p.read_bytes());prep=invocation.get('preparation')
        if not prep or prep.get('target_date')!=TARGET or prep.get('step')!=key:continue
        d=p.parent
        raw=json.loads((d/'result.json').read_bytes());diag=json.loads((d/'diagnostic.json').read_bytes())
        expected={'status':'hold','reason_codes':['image_analysis_mismatch'],'findings':[{'code':'portion_mismatch','region':'left','food_index':2,'expected':25,'observed':None,'unit':'g'}]}
        from .generation import review_schema
        if (raw!=expected or prep['input_digest']!=row['digest'] or invocation['job']!=d.name
                or json.loads((d/'state.json').read_bytes())!={'state':'complete','reason':None}
                or diag.get('exit_code')!=0 or diag.get('job')!=d.name or diag.get('input_image_sha256')!=[image['sha256']]
                or review_schema('image_acceptable',True)!=json.loads((d/'schema.json').read_bytes())):raise ValueError('旧卵保留の実CLI原本が不一致です')
        from .generation_receipts import digest
        if invocation['schema_digest']!=digest(json.loads((d/'schema.json').read_bytes())):raise ValueError('旧点検Schema版が不一致です')
        fs=[file_record(d/name) for name in ('invocation.json','schema.json','result.json','state.json','diagnostic.json','input-0.jpg')]
        if fs[-1]['sha256']!=image['sha256']:raise ValueError('旧卵画像の実入力が不一致です')
        found.append(fs)
    if len(found)!=1:raise ValueError('旧卵点検の実CLIが一意ではありません')
    value=copy.deepcopy(previous);value.update(spec=VERSION,continuation=continuation,calls=calls,
        tables={t:rows(db,t) for t in previous['tables']},ai_receipt_count=10,
        files=sorted(previous['files']+found[0]+[file_record(previous_path)],key=lambda r:r['path']))
    value['quality_stop_trace_sha256']=__import__('hashlib').sha256(stop_trace).hexdigest()
    value['old_lunch_generation_count']=2;value['permitted_additional_lunch_generation_count']=1
    value['audit_rows']=rows(db,'operation_events')
    return value


def verify(db,snapshot):
    verify_base(db,snapshot,expected_version=VERSION)
    current=db.execute('SELECT * FROM current_day_continuations WHERE target_date=?',(TARGET,)).fetchone()
    if not current or dict(current)!=snapshot['continuation']:raise ValueError('旧継続job・初回・期限が変更されています')
    for row in snapshot['audit_rows']:
        current=db.execute('SELECT * FROM operation_events WHERE id=?',(row['id'],)).fetchone()
        if not current or dict(current)!=row:raise ValueError('旧監査記録が変更されています')
