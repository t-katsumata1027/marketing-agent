"""C由来の承認元を旧M1の完成応答へ偽装せずに照合する。"""
import re
import time
from datetime import UTC,datetime
from .preparation import fingerprint
from .replacement_contract import Contract

def validate_binding(candidate):
    from .clients import MealClient
    from .m1_contract import version
    b=candidate.approval_source
    if b.get('operation')!='meal_replace' or candidate.publish_text is None:raise ValueError('replacement_approval_source_invalid')
    MealClient.request_key(b['request_key'])
    for name,prefix in (('state_version','sv1'),('input_hash','ih1'),('result_version','rv1')):version(b[name],prefix)
    for name in (('result_sha256','ogp_image_sha256') if candidate.slot=='daily' else ('result_sha256','stored_image_sha256','ogp_image_sha256')):
        if not re.fullmatch('[a-f0-9]{64}',b[name]):raise ValueError('replacement_approval_source_invalid')

def verify_source(candidate,client,host,capture,now=None,clock=None,timer=time.monotonic):
    from .replacement_client import ReplacementReadClient
    from .m1_contract import validate_state,validate_ogp
    from .ogp import verify_image_reference
    clock=clock or (lambda:now or datetime.now(UTC));started=clock();monotonic=timer()
    validate_binding(candidate);b=candidate.approval_source;contract=Contract(clock)
    reader=ReplacementReadClient(client.base,client.token,decoder=contract.decode,clock=clock)
    response=reader.request(b['request_key'],'meal_replace');r=response['request']
    if (r['request_uuid']!=b['request_key'] or r['persistence']!='saved' or r['status']!='succeeded'
            or r['input_hash']!=b['input_hash'] or response['result_status']!='unchanged'
            or response['current_state_version']!=b['state_version']):raise ValueError('replacement_source_changed')
    result=response['current_daily_result'] if candidate.slot=='daily' else response['current_result']
    if not result or result['result_version']!=b['result_version'] or fingerprint(result)!=b['result_sha256'] or result['normalized_comment']!=candidate.comment or result['share']['url']!=candidate.share_url or result['x']['publish_text']!=candidate.source_content:
        raise ValueError('replacement_source_changed')
    if candidate.slot!='daily' and (result['meal']['meal_type']!=candidate.slot or result['image_sha256']!=candidate.image_sha256 or result['stored_image_sha256']!=b['stored_image_sha256']):raise ValueError('replacement_source_image_changed')
    ogp=validate_ogp(result,host,candidate.slot=='daily',clock());snapshot=capture(candidate.share_url,host)
    verify_image_reference(snapshot,ogp,candidate.share_url,host)
    if snapshot['digest']!=candidate.ogp_sha256 or snapshot['meta']['image_sha256']!=b['ogp_image_sha256']:raise ValueError('replacement_source_ogp_changed')
    from .tracking import verify_tracked_ogp
    verify_tracked_ogp(candidate, host, capture, snapshot)
    state=client.day_state(candidate.target_date)
    if validate_state(state,candidate.target_date,clock())!=b['state_version'] or state['meal_count']!=3:raise ValueError('replacement_source_changed')
    elapsed=(clock()-started).total_seconds();mono=timer()-monotonic
    if not 0<=elapsed<=120 or not 0<=mono<=120:raise ValueError('replacement_source_stale')
    return {'started_at':started.isoformat(),'observed_at':clock().isoformat(),'expires_at':ogp['expires_at'],
            'started_monotonic':monotonic,'observed_monotonic':timer()}

def verify_old_queue(store,target,preconditions,client,host,capture,clock):
    """復帰対象の旧候補を通常の公開前検証で全件読み直す。空キューも推測生成しない。"""
    import json
    from .domain import Candidate
    from .m1_contract import verify_source as verify_any_source,validate_state
    from .replacement_contract import source_digest,stamp,Contract
    started=clock()
    Contract(clock).preconditions(preconditions,target)
    rows=store.list_day(target)
    with store.transaction() as db:
        # 旧Instagramの全実体照合はこの復帰関数の範囲外。未確認のまま解除しない。
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='instagram_candidates'").fetchone() and db.execute('SELECT 1 FROM instagram_candidates WHERE target_date=?',(target,)).fetchone():raise ValueError('replacement_original_instagram_review_required')
    if rows and {r['slot'] for r in rows}!={'breakfast','lunch','dinner','daily'}:raise ValueError('replacement_original_queue_changed')
    if not rows:
        with store.transaction() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='instagram_candidates'").fetchone() and db.execute('SELECT 1 FROM instagram_candidates WHERE target_date=?',(target,)).fetchone():raise ValueError('replacement_original_queue_changed')
    for row in rows:
        if row['state'] not in ('approval_pending','approved','cancelled','changes_requested'):raise ValueError('replacement_original_queue_changed')
        c=Candidate(**json.loads(row['payload']));c.validate(host,clock())
        verify_any_source(c,client,host,capture,clock=clock)
    state=client.day_state(target)
    if validate_state(state,target,clock())!=preconditions['state_version']:raise ValueError('replacement_source_changed')
    if not 0<=(clock()-started).total_seconds()<=120:raise ValueError('replacement_source_stale')
    return {'source_digest':source_digest(preconditions),'verdict':'pass' if rows else 'empty_queue','read_started_at':stamp(started)}
