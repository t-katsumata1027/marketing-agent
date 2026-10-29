"""新たな解析・書込をせず、画像配信方式から独立したM1正本を照合する。"""
import hashlib,math
from datetime import UTC,datetime
from pathlib import Path
from .preparation import fingerprint
from .m1_contract import CONTRACT,validate_state,completed_request,validate_result
from .domain import instant

def read_current(candidate,client,host,bundle=None,clock=lambda:datetime.now(UTC)):
    binding=candidate.approval_source;state=client.day_state(candidate.target_date);current=validate_state(state,candidate.target_date,clock())
    if current!=binding['state_version'] or state['meal_count']!=3:raise ValueError('承認した三食の日版が変わりました')
    slots={m['meal']['meal_type']:m for m in state['meals']}
    if set(slots)!={'breakfast','lunch','dinner'}:raise ValueError('三食正本の構成が不正です')
    operation=binding['operation'];response=client.request_state(binding['request_key'],operation)
    if (response.get('contract_version')!=CONTRACT or response.get('request_uuid')!=binding['request_key'] or response.get('operation')!=operation or response.get('status')!='succeeded' or response.get('persistence')!='saved' or response.get('result_status')!='unchanged' or response.get('current_state_version')!=current or response.get('input_hash')!=binding['input_hash']):raise ValueError('元UUIDと現在の保存成否を確認できません')
    result=response['current_result'];completed_request(response,binding['request_key'],operation,result)
    if fingerprint(result)!=binding['result_sha256'] or result['result_version']!=binding['result_version']:raise ValueError('承認したM1完成結果が変わりました')
    if result['normalized_comment']!=candidate.comment or result['x']['fixed_text']!=candidate.api_text or result['x']['publish_text']!=candidate.source_content:raise ValueError('承認した本文が変わりました')
    normalized_meals={}
    for slot,value in slots.items():
        normalized_meals[slot]=validate_result(value,slot,candidate.target_date,host,logged_at=value['meal']['logged_at'],input_comment=value['normalized_comment'],now=clock())
    total=sum(v['nutrition']['calories'] for v in normalized_meals.values())
    if not 1800<=total<=2000:raise ValueError('三食の実合計が栄養範囲を外れています')
    kwargs={'meals':normalized_meals} if candidate.slot=='daily' else {'logged_at':result['meal']['logged_at']}
    normalized=validate_result(result,candidate.slot,candidate.target_date,host,input_comment=candidate.comment,now=clock(),**kwargs)
    ogp=result['ogp'];source_key='source_state_version' if candidate.slot=='daily' else 'source_result_version'
    if binding['ogp_revision']!=ogp['revision'] or binding['ogp_source_version']!=ogp[source_key]:raise ValueError('承認した共有情報の版が変わりました')
    if normalized['nutrition']!=candidate.nutrition or normalized['share_url']!=candidate.share_url:raise ValueError('承認した栄養と共有先が変わりました')
    if candidate.slot!='daily' and (result['image_sha256']!=candidate.image_sha256 or result['stored_image_sha256']!=binding['stored_image_sha256']):raise ValueError('承認した写真が変わりました')
    if bundle is not None:
        if bundle['state_version']!=current or set(bundle['meals'])!=set(slots):raise ValueError('投稿画像の三食版が変わりました')
        for slot,value in slots.items():
            info=bundle['meals'][slot]
            if value['image_sha256']!=info['image_sha256'] or normalized_meals[slot]['nutrition']!=info['nutrition'] or value['normalized_comment']!=info['comment']:raise ValueError('投稿画像の食品正本が変わりました')
            path=Path(info['image_path'])
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=info['image_sha256']:raise ValueError('投稿画像の原本が変わりました')
        for key in bundle['total']:
            if abs(sum(v['nutrition'][key] for v in normalized_meals.values())-bundle['total'][key])>1e-6:raise ValueError('画像の実合計が変わりました')
    return {'state':state,'result':result,'normalized':normalized,'meals':normalized_meals}
