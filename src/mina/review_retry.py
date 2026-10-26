"""書込み能力のない独立点検だけを同じ入力・同じ時間枠で最大3回扱う。"""
import hashlib,json,time,uuid
from .external_guard import guard,normal_retry,managed

NAMES=frozenset(('mina_independent_review','mina_ogp_review','mina_meal_correction','mina_daily_draft','mina_meal_replan','mina_menu_nutrition_check'))
FATAL=frozenset(('invalid_json_schema','invalid_request_error','unsupported_parameter','insufficient_quota','model_not_found','context_length_exceeded'))


def execute_review(client,prompt,schema,images,name,clock=time.monotonic,sleep=None):
    """失敗原本と不明使用量を残し、タイムアウト/認証/品質結果は再試行しない。"""
    from .codex_client import CodexFailure
    sleep=sleep or time.sleep
    if name not in NAMES or not normal_retry.get() or managed.get() or guard.get() is None:return client.execute(prompt,schema,images)
    schema=json.loads(json.dumps(schema,allow_nan=False));images=tuple(bytes(raw) for raw in images)
    client.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    chain=client.directory/('review-chain-'+uuid.uuid4().hex);chain.mkdir(mode=0o700)
    state={'name':name,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
           'schema_sha256':hashlib.sha256(json.dumps(schema,sort_keys=True).encode()).hexdigest(),
           'image_sha256':[hashlib.sha256(raw).hexdigest() for raw in images],'attempts':[],'state':'started'}
    def save():
        pending=chain/'state.pending';pending.write_text(json.dumps(state),encoding='utf-8');pending.replace(chain/'state.json')
    start=clock();save()
    try:
        for ordinal in range(1,4):
            check=guard.get()
            if check:check()
            remaining=360-(clock()-start)
            if remaining<=0:raise ValueError('review_retry_deadline')
            entry={'ordinal':ordinal,'state':'started','usage_status':'unmeasured'};state['attempts'].append(entry);save()
            try:
                result=client.execute(prompt,schema,images,timeout=remaining)
                entry['state']='complete';state['state']='complete';save();return result
            except CodexFailure as error:
                job=getattr(error,'cli_job_id',None)
                entry.update(state='failed',job=job,reason=error.audit_reason,exit_code=getattr(error,'cli_exit_code',None),result_exists=getattr(error,'cli_result_exists',None));save()
                diagnostic={}
                if isinstance(job,str) and len(job)==32 and all(c in '0123456789abcdef' for c in job):
                    p=client.directory/job/'diagnostic.json'
                    if p.is_file() and not p.is_symlink():diagnostic=json.loads(p.read_text())
                finished=False
                if diagnostic:
                    job_path=client.directory/job
                    finished=(not (job_path/'result.json').exists() and json.loads((job_path/'state.json').read_text())=={'state':'failed','reason':'execution'}
                              and diagnostic.get('exit_code')==entry['exit_code'] and diagnostic.get('input_image_sha256')==state['image_sha256']
                              and diagnostic.get('schema_sha256')==state['schema_sha256'])
                retry=(error.audit_reason=='execution' and type(entry['exit_code']) is int and entry['exit_code']>0
                       and entry['result_exists'] is False and bool(diagnostic)
                       and finished
                       and not FATAL.intersection(diagnostic.get('provider_error_codes',[])))
                if not retry or ordinal==3:raise
                if check:check()
                sleep(2 if ordinal==1 else 5)
        raise AssertionError('独立点検の上限を超えています')
    except BaseException:
        state['state']='held';save();raise
