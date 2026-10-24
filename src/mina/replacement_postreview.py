"""C後の全投稿を、現在正本・実OGP・独立点検から組み立てる。"""
import hashlib
from .domain import SLOTS
from .m1_contract import validate_state,validate_result,completed_request
from .replacement_contract import stamp
from .preparation import fingerprint
from .ogp import verify_image_reference

def review_posts(store,client,ai,capture,target,commit_key,commit_response,draft,images,host,clock):
    started=clock();state=client.day_state(target);version=validate_state(state,target,clock())
    response=commit_response;request=response['request']
    if (request['request_uuid']!=commit_key or request['persistence']!='saved' or request['status']!='succeeded'
            or response['result_status']!='unchanged' or response['current_state_version']!=version
            or response['result']!=response['current_result']):raise ValueError('replacement_source_changed')
    results={v['meal']['meal_type']:v for v in state['meals']};results['daily']=state.get('daily_share')
    affected=response['current_result']['meal']['meal_type'];posts={}
    if results.get(affected) is None or any(results[affected].get(k)!=response['current_result'].get(k) for k in response['current_result']):raise ValueError('replacement_source_changed')
    for slot in SLOTS:
        result=results.get(slot)
        if not isinstance(result,dict):raise ValueError('replacement_daily_share_required')
        if slot==affected or slot=='daily' and response['affected_daily_result'] is not None:
            r=response['current_daily_result'] if slot=='daily' else response['current_result']
            if not r or any(result.get(k)!=r.get(k) for k in r):raise ValueError('replacement_source_changed')
            result=r
            source={'contract_version':response.get('contract_version','mina-replace-v1'),'request_key':commit_key,'operation':'meal_replace','input_hash':request['input_hash']}
        else:
            with store.transaction() as db:
                row=db.execute("SELECT request_key,operation FROM mina_requests WHERE target_date=? AND slot=? AND phase='complete' ORDER BY CASE operation WHEN 'meal_correction' THEN 0 ELSE 1 END LIMIT 1",(target,slot)).fetchone()
            if not row:raise ValueError('replacement_original_request_missing')
            lookup=client.request_state(row['request_key'],row['operation']);completed_request(lookup,row['request_key'],row['operation'],lookup['current_result'])
            if any(result.get(k)!=lookup['current_result'].get(k) for k in lookup['current_result']):raise ValueError('replacement_source_changed')
            source={'request_key':row['request_key'],'operation':row['operation'],'input_hash':lookup['input_hash']}
            result=lookup['current_result']
        kwargs={'meals':posts} if slot=='daily' else {'logged_at':result['meal']['logged_at']}
        normalized=validate_result(result,slot,target,host,input_comment=result['normalized_comment'],now=clock(),**kwargs)
        snapshot=capture(result['share']['url'],host);verify_image_reference(snapshot,result['ogp'],result['share']['url'],host)
        if snapshot['meta']['image_sha256']!=result['ogp']['image_sha256']:raise ValueError('replacement_saved_ogp_changed')
        raws=[images[s] for s in ('breakfast','lunch','dinner')] if slot=='daily' else [images[slot]]
        if slot!='daily':
            if hashlib.sha256(raws[0]).hexdigest()!=result['image_sha256']:raise ValueError('replacement_source_image_changed')
            verdict=ai.review_meal(raws[0],draft['meals'][slot],{'result':result},snapshot['meta'])
            if verdict!={'status':'publishable','reason_codes':[]}:raise ValueError('replacement_saved_food_hold')
        verdict=ai.review_ogp([snapshot['review_image']]+raws,normalized,snapshot['meta'])
        if verdict!={'status':'publishable','reason_codes':[]}:raise ValueError('replacement_saved_ogp_hold')
        source.update(state_version=version,result_sha256=fingerprint(result),result_version=result['result_version'],ogp_revision=result['ogp']['revision'],
            ogp_source_version=normalized['source_version'])
        if slot!='daily':source['stored_image_sha256']=result['stored_image_sha256']
        menu=' / '.join(draft['meals'][s]['menu'] for s in ('breakfast','lunch','dinner')) if slot=='daily' else draft['meals'][slot]['menu']
        posts[slot]={**normalized,'menu':menu,'image_sha256':fingerprint({k:hashlib.sha256(v).hexdigest() for k,v in images.items()}) if slot=='daily' else result['image_sha256'],
            'ogp_sha256':snapshot['digest'],'api_quality':'pass','secondary_review':'publishable','ogp_verified':True,'approval_source':source,'ogp':result['ogp']}
    if not 1800<=sum(posts[s]['nutrition']['calories'] for s in ('breakfast','lunch','dinner'))<=2000:raise ValueError('nutrition_budget_failed')
    if validate_state(client.day_state(target),target,clock())!=version or not 0<=(clock()-started).total_seconds()<=120:raise ValueError('replacement_source_stale')
    return {'target_date':target,'state':'posts_reviewed','posts':posts}, {'read_started_at':stamp(started),'verdict':'pass','state_version':version,
        'verification':'current-source-uuid-ogp-v1','checks':['current_state','uuid_results','original_bytes','ogp_bytes','independent_review']}
