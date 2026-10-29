"""任意の翌日について実準備→四候補→実形式のボタン受領を外部書込なしで通す。"""
import copy,hashlib,io,json,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
from src.mina.daily_pipeline import prepare_day
from src.mina.domain import JST,SLOTS
from src.mina.store import Store
from src.mina.runtime import notify_day,send_notifications
from test_tolerance_case import positive
import test_registration as fixture

class Discord:
    channel='1234'
    def __init__(self):self.messages={};self.posts=0
    def identity(self):return '999'
    def send(self,wire):
        self.posts+=1;key=str(self.posts)
        value={**copy.deepcopy(wire),'id':key,'author':{'id':'999'},'channel_id':self.channel,'mentions':[],'mention_roles':[],'mention_everyone':False}
        # 実Discord GETはnonceを省くことがある。
        self.messages[key]={k:v for k,v in value.items() if k!='nonce'}
        return value
    def message(self,key):return self.messages[key]

class RoutinePipelineTests(unittest.TestCase):
    def test_future_month_and_year_boundaries_end_to_end(self):
        for start,lost,mixed in (('2026-10-30T19:00:00+09:00',False,False),('2026-12-31T19:00:00+09:00',False,False),('2026-10-30T19:00:00+09:00',True,False),('2026-10-30T19:00:00+09:00',False,True)):
            with self.subTest(start=start,lost=lost,mixed=mixed),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);now=datetime.fromisoformat(start);target=(now.date()+timedelta(days=1)).isoformat();store=Store(root/'db');helper=fixture.RegistrationTests()
                draft,_=helper.setup_inputs(root)
                if mixed:draft['meals']['breakfast']['foods']=[{'name':name,'amount':'50g'} for name in ('ご飯','鶏肉','キャベツ','にんじん','玉ねぎ','ねぎ','大根','豆腐','わかめ','きのこ','小松菜','ごぼう','れんこん','じゃがいも')]
                evidence={'account':'EatsLog88161','target_date':target,'checked_at':now.isoformat(),'reviewer':'隔離担当','history_source':'合成履歴','schedule_source':'合成予約','history_complete':True,'legacy_schedule_clear':True,'history_from':(now.date()-timedelta(days=13)).isoformat(),'history_to':now.date().isoformat(),'entries':[]}
                history=root/'history.json';history.write_text(json.dumps(evidence),encoding='utf-8')
                ai=Mock()
                def structured(instructions,content,schema,name):
                    if name=='mina_menu_nutrition_check':return {slot:([40]*13+[80] if len(m['foods'])==14 else [300,300]) for slot,m in draft['meals'].items()}
                    if name=='mina_meal_correction' and mixed:
                        inputs=json.loads(content[0]['text']);fs=inputs['meal']['foods']
                        return {'foods':[{**f,'calories':600/len(fs),'protein':30/len(fs),'fat':20/len(fs),'carbs':75/len(fs)} for f in fs], 'dish_mapping':[{'food_index':i,'shares':[{'analysis_index':min(i//5,2),'fraction':1}]} for i in range(len(fs))]}
                    if name=='mina_routine_quality_review':
                        context=json.loads(content[0]['text']);value=positive(context['planned_meal'],context['positive'])
                        value['foods'][0].update(portion='unmeasurable',evidence='cannot_measure_weight');value['warnings']=[{'code':'weight_unmeasurable','region':'center','food_index':0}]
                        if mixed and context['positive']=='publishable' and len(context['planned_meal']['foods'])==14 and len(context['analysis_response']['result']['analysis']['foods'])==3:
                            value.update(status='hold',blocking=[{'code':'nutrition_inconsistent','region':'whole','food_index':0}]);value['checks']['nutrition']='hold'
                        return value
                    return {'meals':draft['meals'],'daily_comment':draft['daily_comment']}
                ai.structured.side_effect=structured;ai.review_ogp.return_value={'status':'publishable','reason_codes':[]}
                image=io.BytesIO();Image.new('RGB',(64,64),'ivory').save(image,'JPEG');raw=image.getvalue();ai.image.return_value=raw
                drive=Mock(folder_id='合成フォルダ');drive.upload.side_effect=lambda raw,name:{'id':name};drive.verify.side_effect=lambda key,raw,name:{'verified':True,'sha256':hashlib.sha256(raw).hexdigest()}
                meal=helper.client();oldstate=meal.day_state.side_effect;oldrequest=meal.request_state.side_effect
                overrides={};extra_requests={};revisions=[0];daily_results=[]
                def state(day):
                    s=oldstate(day);s['checked_at']=now.isoformat()
                    if mixed:
                        s['state_version']='sv1_'+format(revisions[0],'064x');s['meals']=[copy.deepcopy(overrides.get(v['meal']['meal_type'],v)) for v in s['meals']]
                        s['summary']={key:sum(v['analysis']['summary'][key] for v in s['meals']) for key in ('totalCalories','totalProtein','totalFat','totalCarbs')};s['nutrition_targets']={k:None for k in ('calories','protein','fat','carbs')}
                        if daily_results:s['daily_share']=copy.deepcopy(daily_results[-1])
                    return s
                def request(key,op):
                    r=copy.deepcopy(extra_requests[key]) if key in extra_requests else oldrequest(key,op);r['current_state_version']=state(target)['state_version']
                    if lost:
                        from src.mina.request_ledger import RequestLedger
                        from src.mina.routine_request_recovery import expected_hash
                        r['input_hash']=expected_hash(RequestLedger(store).restore(key))
                    return r
                meal.day_state.side_effect=state;meal.request_state.side_effect=request
                if mixed:
                    register_original=meal.register.side_effect;json_original=meal.json_write.side_effect
                    def register_mixed(*args,**kwargs):
                        self.assertEqual(kwargs['expected_state_version'],state(target)['state_version']);kwargs['expected_state_version']=oldstate(target)['state_version'];result=register_original(*args,**kwargs);revisions[0]+=1
                        if args[2]=='breakfast':
                            r=result['result'];r['analysis']['foods']=[{'name':n,'calories':200,'protein':10,'fat':20/3,'carbs':25} for n in ('主菜','副菜','汁物')];overrides['breakfast']=copy.deepcopy(r)
                            result={**copy.deepcopy(r),'result':r,'request':result['request']}
                        return result
                    def write_mixed(path,body,request_key=None,method='POST'):
                        if method!='PATCH':
                            result=json_original(path,body,request_key=request_key)
                            result['result']['source_state_version']=body['expected_state_version'];result['result']['ogp']['source_state_version']=body['expected_state_version'];result['source_state_version']=body['expected_state_version'];daily_results.append(copy.deepcopy(result['result']))
                            extra_requests[request_key]={'contract_version':'mina-write-v1','request_uuid':request_key,'operation':'daily_share','status':'succeeded','persistence':'saved','stage':'completed','result_status':'unchanged','result_hold_reason':None,'input_hash':'ih1_'+'f'*64,'result':copy.deepcopy(result['result']),'current_result':copy.deepcopy(result['result'])};return result
                        self.assertEqual(body['expected_state_version'],state(target)['state_version']);r=copy.deepcopy(overrides['breakfast']);r['analysis']['foods']=copy.deepcopy(body['foods']);r['result_version']='rv1_'+'c'*64;r['ogp']['source_result_version']=r['result_version'];overrides['breakfast']=copy.deepcopy(r);revisions[0]+=1
                        extra_requests[request_key]={'contract_version':'mina-write-v1','request_uuid':request_key,'operation':'meal_correction','status':'succeeded','persistence':'saved','stage':'completed','result_status':'unchanged','result_hold_reason':None,'input_hash':'ih1_'+'e'*64,'result':copy.deepcopy(r),'current_result':copy.deepcopy(r)}
                        return {**copy.deepcopy(r),'result':r}
                    meal.register.side_effect=register_mixed;meal.json_write.side_effect=write_mixed
                if lost:
                    original_register=meal.register.side_effect;lost_once=[]
                    def register(*args,**kwargs):
                        result=original_register(*args,**kwargs)
                        if not lost_once:lost_once.append(True);raise TimeoutError('保存後の応答喪失')
                        return result
                    meal.register.side_effect=register
                def capture(url,host):return {'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':raw}
                class Clock(datetime):
                    @classmethod
                    def now(cls,tz=None):return now.astimezone(tz) if tz else now.replace(tzinfo=None)
                with patch.dict('os.environ',{'MINA_DAILY_RECOVERY_ENABLED':'true'}),patch('socket.socket.connect',side_effect=AssertionError('実ネットワーク禁止')),patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('実HTTP禁止')),patch('src.mina.daily_pipeline.release_commit',return_value='a'*40),patch('src.mina.registration.datetime',Clock),patch('src.mina.ogp.capture_ogp',side_effect=capture):
                    args=(store,root/'daily',history,history,'a'*40,root,'www.log-eats.com',ai,drive,meal)
                    self.assertEqual(prepare_day(*args,clock=lambda:now)['state'],'approval_ready')
                    self.assertEqual(prepare_day(*args,clock=lambda:now)['state'],'already_complete')
                    rows=store.list_day(target);self.assertEqual(len(rows),4)
                    for row in rows:self.assertTrue(json.loads(row['payload'])['approval_source']['ogp_image_url'].endswith('/og-image'))
                    notify_day(store,target,now+timedelta(hours=1));discord=Discord();self.assertEqual(send_notifications(store,discord),4);self.assertEqual(discord.posts,4)
                    self.assertEqual(send_notifications(store,discord),0);self.assertEqual(discord.posts,4)
                    with store.transaction() as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM routine_quality_reviews').fetchone()[0],7 if mixed else 6)
                        self.assertEqual(db.execute("SELECT count(*) FROM routine_notice_receipts WHERE state='sent'").fetchone()[0],4)
                        self.assertEqual({json.loads(r[0])['nonce_echo_present'] for r in db.execute('SELECT receipt FROM routine_notice_receipts')},{False})
                    self.assertEqual(meal.register.call_count,3);self.assertEqual(meal.json_write.call_count,2 if mixed else 1);self.assertEqual(ai.image.call_count,3)
                    if mixed:
                        self.assertEqual(len(overrides['breakfast']['analysis']['foods']),14)
                        with store.transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM routine_correction_claims').fetchone()[0],1)
                        self.instagram(store,root,target,state(target),now,Clock)

    def instagram(self,store,root,target,state,now,Clock):
        from src.mina.instagram_source import load_bundle
        from src.mina.instagram_runtime import InstagramRuntime,save_json
        from src.mina.instagram_render import render_bundle
        from src.mina.instagram_store import InstagramStore
        from src.mina.instagram_media import HOSTS
        from src.mina.instagram_domain import LAYOUT_VERSION
        from src.mina.instagram_preparation import prepare
        bundle=load_bundle(store.path,root/'daily',target,state,now)
        destination=root/'instagram'/target/LAYOUT_VERSION/bundle['bundle_sha256'];destination.mkdir(parents=True);save_json(destination/'bundle.json',bundle);manifest=render_bundle(bundle,destination)
        ig=InstagramStore(store.path);runtime=InstagramRuntime(store.path,root/'daily',root/'instagram',store=ig,api=Mock())
        runtime.state=lambda day:copy.deepcopy(state)
        runtime.render=lambda day:(bundle,manifest,destination)
        def host(asset,*args,**kwargs):
            runtime.preparation_begin_write()
            return {**asset,'url':'https://drive.google.com/uc?id='+asset['sha256']}
        with patch('src.mina.instagram_runtime.datetime',Clock),patch('src.mina.instagram_runtime.instagram_channel',return_value={'id':'channel','serviceId':'account'}),patch('src.mina.instagram_runtime.DriveClient.from_env',return_value=Mock()),patch('src.mina.instagram_runtime.host_asset',side_effect=host):
            self.assertEqual(len(prepare(runtime,target,True,clock=lambda:now)),4)
            for row in ig.rows(target):ig.queue_approval(row)
            discord=Discord();self.assertEqual(ig.send_notices(discord),4);self.assertEqual(discord.posts,4)
            self.assertEqual(ig.send_notices(discord),0)
