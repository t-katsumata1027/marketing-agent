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
        for start,lost in (('2026-10-30T19:00:00+09:00',False),('2026-12-31T19:00:00+09:00',False),('2026-10-30T19:00:00+09:00',True)):
            with self.subTest(start=start,lost=lost),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);now=datetime.fromisoformat(start);target=(now.date()+timedelta(days=1)).isoformat();store=Store(root/'db');helper=fixture.RegistrationTests()
                draft,_=helper.setup_inputs(root)
                evidence={'account':'EatsLog88161','target_date':target,'checked_at':now.isoformat(),'reviewer':'隔離担当','history_source':'合成履歴','schedule_source':'合成予約','history_complete':True,'legacy_schedule_clear':True,'history_from':(now.date()-timedelta(days=13)).isoformat(),'history_to':now.date().isoformat(),'entries':[]}
                history=root/'history.json';history.write_text(json.dumps(evidence),encoding='utf-8')
                ai=Mock()
                def structured(instructions,content,schema,name):
                    if name=='mina_menu_nutrition_check':return {slot:[300,300] for slot in draft['meals']}
                    if name=='mina_routine_quality_review':
                        context=json.loads(content[0]['text']);value=positive(context['planned_meal'],context['positive'])
                        value['foods'][0].update(portion='unmeasurable',evidence='cannot_measure_weight');value['warnings']=[{'code':'weight_unmeasurable','region':'center','food_index':0}]
                        return value
                    return {'meals':draft['meals'],'daily_comment':draft['daily_comment']}
                ai.structured.side_effect=structured;ai.review_ogp.return_value={'status':'publishable','reason_codes':[]}
                image=io.BytesIO();Image.new('RGB',(64,64),'ivory').save(image,'JPEG');raw=image.getvalue();ai.image.return_value=raw
                drive=Mock(folder_id='合成フォルダ');drive.upload.side_effect=lambda raw,name:{'id':name};drive.verify.side_effect=lambda key,raw,name:{'verified':True,'sha256':hashlib.sha256(raw).hexdigest()}
                meal=helper.client();oldstate=meal.day_state.side_effect;oldrequest=meal.request_state.side_effect
                def state(day):
                    s=oldstate(day);s['checked_at']=now.isoformat();return s
                def request(key,op):
                    r=oldrequest(key,op);r['current_state_version']=state(target)['state_version']
                    if lost:
                        from src.mina.request_ledger import RequestLedger
                        from src.mina.routine_request_recovery import expected_hash
                        r['input_hash']=expected_hash(RequestLedger(store).restore(key))
                    return r
                meal.day_state.side_effect=state;meal.request_state.side_effect=request
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
                        self.assertEqual(db.execute('SELECT count(*) FROM routine_quality_reviews').fetchone()[0],6)
                        self.assertEqual(db.execute("SELECT count(*) FROM routine_notice_receipts WHERE state='sent'").fetchone()[0],4)
                        self.assertEqual({json.loads(r[0])['nonce_echo_present'] for r in db.execute('SELECT receipt FROM routine_notice_receipts')},{False})
                    self.assertEqual(meal.register.call_count,3);self.assertEqual(meal.json_write.call_count,1);self.assertEqual(ai.image.call_count,3)
