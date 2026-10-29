"""今回の新画像だけに適用する、量差と精密計測不能を区別した点検。"""
import base64,copy,hashlib,json,math
from .generation import IMAGE_STYLE,REVIEW_INSTRUCTIONS,IMAGE_REVIEW_SCOPE,object_schema,REVIEW_REASONS
from .review_diagnostics import finding_schema,validate_findings,REGIONS

POLICY='mina-visible-portion-review-v1'
CHECKS=('composition','style','food_identity','visible_portions')
RELATIONS=('no_obvious_difference','obvious_excess','obvious_shortage','unverifiable','not_visually_measurable')
BASES=('visible_relative_amount','countable_units','measurement_unavailable','identity_uncertain','not_applicable')
INSTRUCTIONS=(REVIEW_INSTRUCTIONS+'\n'+IMAGE_REVIEW_SCOPE+
    '\nこの画像は既知の食品・計画量から生成した広報用の料理写真です。写真は計量器ではありません。'
    '正確なg/mlを測れないことと、見て明らかな大きな量差を区別してください。観測g/mlを創作しないでください。'
    '全foodsを一度ずつ0始まりのfood_indexで記録します。visible_food_indicesの食品は見える料理の構成要素です。'
    'それ以外の混ざった調味料はmixed_ingredientでよく、精密計測不能だけを不合格理由にしません。'
    '食品の欠落・種類の識別不能・見て明らかな大幅な過大/過少はhold。'
    '方向と根拠をportion_relation/basis/comparison/regionへ固定項目で記録します。'
    '明らかな大差の証拠がなければno_obvious_differenceと可視比較の根拠を記録し、'
    '写真で正確な重量を確認できたと主張しません。観測数値は数えられる個数以外はnullにします。'
    '炒り卵のかけらの数や見た目の量を、卵何個という数に読み替えないでください。'
    '今回の献立は調理済み重量/容量の計画なので、米粒・葉・そぼろの個数も重量の根拠にせず、observedはnull、unitはnoneとします。'
    '大差の有無そのものを判断できない場合はunverifiableとしてholdにします。'
    'checksの全項目がpass、食品識別・料理構成・自然な量/構図に問題がない場合だけimage_acceptable。'
    '器の切れ等はfindingsへ記録し、量差の方向や根拠が不明なままportion_mismatchを作らないでください。'
    '合格時のreason_codes/findingsは空。内部思考や自由文は出力しないでください。')


def schema():
    enum=lambda values:{'type':'string','enum':list(values)}
    food=object_schema({'food_index':{'type':'integer','minimum':0,'maximum':19},
        'visibility':enum(('visible','mixed_ingredient','absent','unidentifiable')),
        'portion_relation':enum(RELATIONS),'basis':enum(BASES),
        'comparison':enum(('relative_to_planned_dish','countable_unit','none')),'region':enum(REGIONS),
        'observed':{'type':['number','null']},'unit':enum(('count','none'))})
    return object_schema({'status':enum(('image_acceptable','hold')),
        'reason_codes':{'type':'array','items':enum(REVIEW_REASONS)},
        'checks':object_schema({k:enum(('pass','hold','unverifiable')) for k in CHECKS}),
        'foods':{'type':'array','minItems':2,'maxItems':20,'items':food},'findings':finding_schema()})


def policy_sha256():
    return hashlib.sha256(json.dumps({'policy':POLICY,'instructions':INSTRUCTIONS,'style':IMAGE_STYLE,'schema':schema()},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def visible_indices(meal):
    # この承認caseの炒り卵を、混合調味料へ分類して点検から外さない。
    if len(meal['foods'])<3 or meal['foods'][2]!= {'name':'卵','amount':'1/2個（可食部25g）'}:raise ValueError('承認対象の卵計画と一致しません')
    return {0,1,2}|{i for i,f in enumerate(meal['foods']) if f['name'] in ('ほうれん草','にんじん','乾燥わかめ')}


def request(ai,raw,meal):
    context={'planned_meal':meal,'visible_food_indices':sorted(visible_indices(meal)),
             'analysis_response':None,'ogp_meta':None,'style':IMAGE_STYLE,'policy':POLICY}
    return ai.structured(INSTRUCTIONS,[{'type':'input_text','text':json.dumps(context,ensure_ascii=False)},
        {'type':'input_image','image_url':'data:image/jpeg;base64,'+base64.b64encode(raw).decode(),'detail':'high'}],schema(),'mina_visible_portion_review')


def validate(value,meal):
    """矛盾・根拠不足をholdにし、rawのhold/nullからpassを作らない。"""
    incomplete={'status':'hold','reason_codes':['diagnostic_incomplete']}
    try:
        if not isinstance(value,dict) or set(value)!={'status','reason_codes','checks','foods','findings'}:return incomplete
        if value['status'] not in ('image_acceptable','hold') or not isinstance(value['reason_codes'],list) or any(c not in REVIEW_REASONS for c in value['reason_codes']):return incomplete
        checks=value['checks']
        if not isinstance(checks,dict) or set(checks)!=set(CHECKS) or any(v not in ('pass','hold','unverifiable') for v in checks.values()):return incomplete
        findings=validate_findings(copy.deepcopy(value['findings']))
        fs=value['foods'];required=set(range(len(meal['foods'])));visible=visible_indices(meal)
        if not isinstance(fs,list) or len(fs)!=len(required) or {f.get('food_index') for f in fs}!=required:return incomplete
        negative=False
        keys={'food_index','visibility','portion_relation','basis','comparison','region','observed','unit'}
        for f in fs:
            if set(f)!=keys or type(f['food_index']) is not int or f['visibility'] not in ('visible','mixed_ingredient','absent','unidentifiable') or f['portion_relation'] not in RELATIONS or f['basis'] not in BASES or f['region'] not in REGIONS or f['comparison'] not in ('relative_to_planned_dish','countable_unit','none') or f['unit'] not in ('count','none'):return incomplete
            if f['observed'] is not None and (type(f['observed']) not in (int,float) or not math.isfinite(f['observed']) or f['observed']<0 or f['basis']!='countable_units' or f['unit']!='count'):return incomplete
            if f['food_index'] not in visible and f['visibility']=='mixed_ingredient':
                if f['portion_relation']!='not_visually_measurable' or f['basis']!='measurement_unavailable' or f['comparison']!='none' or f['observed'] is not None:return incomplete
                continue
            if f['visibility'] in ('absent','unidentifiable'):
                if f['basis']!='identity_uncertain':return incomplete
                negative=True;continue
            if f['visibility']!='visible':return incomplete
            if f['portion_relation']=='unverifiable':negative=True;continue
            if f['portion_relation']=='not_visually_measurable':return incomplete
            if f['basis'] not in ('visible_relative_amount','countable_units') or f['comparison']=='none' or f['region']=='unknown':return incomplete
            if f['basis']=='countable_units' and (f['observed'] is None or f['comparison']!='countable_unit'):return incomplete
            if f['basis']=='countable_units':return incomplete
            if f['basis']=='visible_relative_amount' and (f['comparison']!='relative_to_planned_dish' or f['observed'] is not None or f['unit']!='none'):return incomplete
            if f['portion_relation'] in ('obvious_excess','obvious_shortage'):negative=True
        # 数値がnullでも、可視の量差や識別不能は保留。測定不能だけでpassにも変換しない。
        if value['status']=='image_acceptable':
            if negative or findings or value['reason_codes'] or any(v!='pass' for v in checks.values()):return incomplete
            return {'status':'image_acceptable','reason_codes':[]}
        if not value['reason_codes'] or (not negative and not findings) or all(v=='pass' for v in checks.values()):return incomplete
        for finding in findings:
            if finding['code']=='portion_mismatch':
                corresponding=next((f for f in fs if f['food_index']==finding['food_index']),None)
                if corresponding is None or corresponding['portion_relation'] not in ('obvious_excess','obvious_shortage'):return incomplete
        return {'status':'hold','reason_codes':list(value['reason_codes'])}
    except (KeyError,TypeError,ValueError,AttributeError):return incomplete
