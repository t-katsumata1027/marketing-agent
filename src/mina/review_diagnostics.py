"""AIの内部思考を求めず、画素・数値の観測事実だけを検証して記録する。"""
import math

CODES=('dish_cropped','food_missing','portion_mismatch','cooking_structure','separate_serving_missing',
       'nutrition_mismatch','text_mismatch','ogp_mismatch','uncertain')
REGIONS=('left','right','top','bottom','center','whole','unknown')


def finding_schema():
    properties={'code':{'type':'string','enum':list(CODES)},'region':{'type':'string','enum':list(REGIONS)},
        'food_index':{'type':'integer','minimum':-1,'maximum':19},
        'expected':{'type':['number','null']},'observed':{'type':['number','null']},
        'unit':{'type':'string','enum':['g','ml','kcal','count','none']}}
    return {'type':'array','maxItems':6,'items':{'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}}


def validate_findings(value):
    if not isinstance(value,list) or len(value)>6:
        raise ValueError('点検の観測事実の形式が不正です')
    for finding in value:
        if (not isinstance(finding,dict) or set(finding)!={'code','region','food_index','expected','observed','unit'} or
                finding['code'] not in CODES or finding['region'] not in REGIONS or
                type(finding['food_index']) is not int or not -1<=finding['food_index']<=19 or
                finding['unit'] not in ('g','ml','kcal','count','none')):
            raise ValueError('点検の観測項目が不正です')
        if any(v is not None and (type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1000000)
               for v in (finding['expected'],finding['observed'])):
            raise ValueError('点検の観測値が不正です')
    return value


def record_findings(store,client,target,slot,stage,image_sha256):
    value=getattr(client,'last_review_evidence',None)
    if type(value) is dict and value.get('image_sha256')==image_sha256 and value.get('stage')==stage:
        findings=validate_findings(value['findings'])
        if findings:
            store.audit.emit('creation',target,slot,None,'review_diagnosis','observed',
                             details={'image_sha256':image_sha256,'findings':findings})


def record_verdict(store,target,slot,stage,review):
    from .audit import REASONS
    held=review.get('status')=='hold'
    codes=review.get('reason_codes',[])
    reason=next((code for code in codes if code in REASONS),'quality_hold') if held else 'none'
    store.audit.emit('creation',target,slot,None,stage,'held' if held else 'observed',reason,{'matched':not held})
