"""通常準備の通し試験用。外部AI・Drive・業務APIだけを代替する。"""
import copy
import json
import hashlib
from datetime import timedelta
from src.mina.clients import OutcomeUnknown
from src.mina.domain import ACCOUNT,FIXED_MARKERS
from src.mina.x_history import collect_timeline

POSITIVE={'status':'publishable','reason_codes':[]}


class NightlyXHistory:
    def __init__(self,clock):self.clock=clock
    def read_history(self,expected,target):
        assert expected==ACCOUNT
        return collect_timeline(lambda *args,**kwargs:{'data':[],'meta':{'result_count':0}},'1',target,as_of=self.clock[0])


class NightlyAI:
    def __init__(self,mode,clock):
        self.mode,self.clock=mode,clock
        self.images=self.drafts=self.proposals=0
        self.meal_reviews=0
        self.plan={'meals':{slot:{'menu':label+'のご飯と魚','comment':label+'の献立候補です。',
            'estimated_calories':600,'foods':[{'name':'ご飯','amount':'150g'},{'name':'魚','amount':'100g'}],
            'image_description':'ご飯と魚を白い器で盛り付けた一人分'}
            for slot,label in (('breakfast','朝'),('lunch','昼'),('dinner','夕'))},'daily_comment':'一日の献立まとめです。'}

    def structured(self,instructions,content,schema,name):
        if name=='mina_menu_nutrition_check':
            context=json.loads(content)
            return {slot:[600/len(foods)]*len(foods) for slot,foods in context['foods'].items()}
        if name=='mina_meal_replan':
            context=json.loads(content)
            slot=context['slot']
            value=copy.deepcopy(context['previous_meal'])
            value['menu']+='・別案'
            value['foods'][1]['name']='鶏肉' if value['foods'][1]['name']!='鶏肉' else '豆腐'
            self.plan['meals'][slot]=copy.deepcopy(value)
            return value
        if name=='mina_daily_draft':
            self.drafts+=1
            return copy.deepcopy(self.plan)
        if name=='mina_meal_correction':
            self.proposals+=1
            return {'foods':[{**food,'calories':300,'protein':15,'fat':10,'carbs':37.5}
                             for food in self.plan['meals']['breakfast']['foods']]}
        raise AssertionError('未定義のAI呼出し')

    def image(self,description):
        self.images+=1
        if self.mode=='deadline':self.clock[0]+=timedelta(hours=1)
        return b'\xff\xd8\xff'+str(self.images).encode()

    def review_image(self,raw,meal):
        return {'status':'image_acceptable','reason_codes':[]}

    def review_meal(self,raw,meal,response,meta):
        self.meal_reviews+=1
        result=response['result']
        if self.mode!='plain' and result['meal']['meal_type']=='breakfast':
            if result['analysis']['foods'][0]['amount']=='300g':
                return {'status':'hold','reason_codes':['nutrition_semantic_mismatch']}
            if self.mode=='quality_hold':return {'status':'hold','reason_codes':['context_mismatch']}
        return POSITIVE.copy()

    def review_ogp(self,images,expected,meta):
        return POSITIVE.copy()


class NightlyDrive:
    folder_id='isolated-test-folder'
    def __init__(self):self.uploads={}
    def upload(self,raw,filename):
        key=str(len(self.uploads)+1);self.uploads[key]=(raw,filename)
        return {'id':key}
    def verify(self,key,raw,filename):
        assert self.uploads[key]==(raw,filename)
        return {'verified':True,'sha256':hashlib.sha256(raw).hexdigest()}


class NightlyMeal:
    def __init__(self,mode,clock,host):
        self.mode,self.clock,self.host=mode,clock,host
        self.meals={};self.requests={};self.version=0;self.calls=[]

    @property
    def sv(self):return 'sv1_'+format(self.version,'064x')

    def day_state(self,target):
        return {'contract_version':'mina-write-v1','account':ACCOUNT,'date':target,
                'checked_at':self.clock[0].isoformat(),'complete':True,'state_version':self.sv,
                'meal_count':len(self.meals),'meals':copy.deepcopy(list(self.meals.values()))}

    def wrap(self,result,key,source=None):
        daily='meal_count' in result
        share=result['share']['url'];summary=result['summary'] if daily else result['analysis']['summary']
        calories=int(summary['totalCalories']+.5);p=int(summary['totalProtein']+.5)
        f=int(summary['totalFat']+.5);c=int(summary['totalCarbs']+.5)
        fixed=('今日1日の食事まとめ 🔥' if daily else '今日の食事解析結果 🔥')+'\n'
        fixed+=('3食 ' if daily else '')+f'{calories}kcal (P:{p}g F:{f}g C:{c}g)\n'+' '.join(FIXED_MARKERS)
        text=result['normalized_comment']+'\n\n'+fixed
        result['x']={'text':text,'fixed_text':fixed,'publish_text':text+'\n'+share,
                     'intent_url':'https://twitter.com/intent/tweet?text=test'}
        result['result_version']='rv1_'+format(self.version+10 if daily else self.version,'064x')
        if daily:result['source_state_version']=source
        result['ogp']={'status':'ready','revision':'test','image_sha256':'e'*64,
            'source_state_version' if daily else 'source_result_version':source if daily else result['result_version'],
            'image_url':share+'/og-image','expires_at':'2099-01-01T00:00:00Z'}
        self.requests[key]=copy.deepcopy(result)
        return {**copy.deepcopy(result),'result':copy.deepcopy(result),'request':{'input_hash':'ih1_'+'f'*64}}

    def register(self,raw,filename,slot,logged,comment,request_key,expected_state_version):
        assert expected_state_version==self.sv
        assert request_key not in self.requests
        self.calls.append(('POST',slot,request_key));self.version+=1
        foods=[{'name':'ご飯','amount':'300g' if self.mode!='plain' and slot=='breakfast' else '150g',
                'calories':300,'protein':15,'fat':10,'carbs':37.5},
               {'name':'魚','amount':'100g','calories':300,'protein':15,'fat':10,'carbs':37.5}]
        result={'meal':{'meal_type':slot,'logged_at':logged},'analysis':{'foods':foods,
            'summary':{'totalCalories':600,'totalProtein':30,'totalFat':20,'totalCarbs':75},'is_ambiguous':False},
            'quality':{'status':'pass','warnings':[]},'share':{'url':f'https://{self.host}/s/meal_{slot}'},
            'normalized_comment':comment,'image_sha256':hashlib.sha256(raw).hexdigest(),
            'stored_image_sha256':hashlib.sha256(b'converted'+raw).hexdigest()}
        response=self.wrap(result,request_key)
        self.meals[slot]=copy.deepcopy(response['result'])
        if self.mode in ('post_unknown','post_unknown_404'):raise OutcomeUnknown('応答破棄')
        return response

    def json_write(self,path,payload,method='POST',request_key=None):
        assert request_key not in self.requests
        assert payload['expected_state_version']==self.sv
        if method=='PATCH':
            assert path=='/api/internal/mina/meals'
            self.calls.append(('PATCH','breakfast',request_key));self.version+=1
            result=copy.deepcopy(self.meals['breakfast']);foods=copy.deepcopy(payload['foods'])
            result['analysis']['foods']=foods
            response=self.wrap(result,request_key)
            self.meals['breakfast']=copy.deepcopy(response['result'])
            if self.mode=='patch_unknown':raise OutcomeUnknown('訂正応答破棄')
            return response
        assert path=='/api/internal/mina/daily-share'
        self.calls.append(('POST','daily',request_key))
        return self.wrap({'date':payload['date'],'meal_count':3,'normalized_comment':payload['comment'],
            'summary':{'totalCalories':1800,'totalProtein':90,'totalFat':60,'totalCarbs':225},
            'share':{'url':f'https://{self.host}/s/day_daily'}},request_key,self.sv)

    def request_state(self,key,operation):
        if self.mode in ('lookup_404','post_unknown_404'):raise RuntimeError('404')
        result=self.requests[key]
        current=self.meals.get(result.get('meal',{}).get('meal_type'),result)
        return {'contract_version':'mina-write-v1','request_uuid':key,'operation':operation,
            'status':'succeeded','persistence':'saved','stage':'completed',
            'result_status':'modified' if self.mode=='modified' else 'unchanged','result_hold_reason':None,
            'current_state_version':self.sv,
            'input_hash':'ih1_'+'f'*64,'image_sha256':result.get('image_sha256'),
            'result':copy.deepcopy(result),'current_result':copy.deepcopy(current)}
