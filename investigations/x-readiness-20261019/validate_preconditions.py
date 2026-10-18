"""正式実行Schemaで応答を検証し、秘密・本文・識別子を除いた結果だけを出す。"""
import hashlib,json,math,pathlib
from datetime import UTC,datetime,timedelta
from jsonschema import Draft202012Validator,FormatChecker
base=pathlib.Path('tmp/x-readiness-20261019')
raw=(base/'mina-replace-v1.schema.json').read_bytes()
assert hashlib.sha256(raw).hexdigest()=='2c2a44cc3830ef07bd0666182915b0e5e4bbab11239bb6aec6745f0d9d37a4f6'
schema=json.loads(raw);Draft202012Validator.check_schema(schema)
validator=Draft202012Validator({**schema,'$ref':'#/$defs/PreconditionResponse'},format_checker=FormatChecker())
responses=json.loads((base/'preconditions.private.json').read_text(encoding='utf-8-sig'))
state=responses['m1_state'];out={'schema_sha256':hashlib.sha256(raw).hexdigest(),'http_methods':['GET'],'dates':[]}
for target in ('2026-10-18','2026-10-19'):
 envelope=responses[target];p=envelope['response'];validator.validate(p)
 f=p['freshness'];begin,checked,until=[datetime.fromisoformat(f[k].replace('Z','+00:00')) for k in ('read_started_at','checked_at','valid_until')]
 assert f['complete'] is True and begin<=checked<until<=checked+timedelta(seconds=120)
 assert set(m['expected']['target_slot'] for m in p['meals'])=={'breakfast','lunch','dinner'}
 assert all(m['expected']['target_date']==target and m['expected']['state_version']==p['state_version'] for m in p['meals'])
 for key in ('totalCalories','totalProtein','totalFat','totalCarbs'):
  assert math.isclose(math.fsum(m['analysis']['summary'][key] for m in p['meals']),p['summary'][key],abs_tol=1e-8)
 e={'target_date':target,'http_status':envelope['status'],'cache_control':envelope['cache_control'],'schema_valid':True,'complete':True,'calories':p['summary']['totalCalories'],'meal_calories':{m['expected']['target_slot']:m['analysis']['summary']['totalCalories'] for m in p['meals']},'share_statuses':[m['share_status'] for m in p['meals']],'daily_share_present':p['daily_share'] is not None,'policy':p['policy'],'freshness_checked_at':f['checked_at']}
 if target=='2026-10-19':
  assert p['state_version']==state['state_version'] and state['meal_count']==3
  for meal in p['meals']:
   actual=next(m for m in state['meals'] if m['meal']['meal_type']==meal['expected']['target_slot'])
   assert all(actual['analysis'][k]==meal['analysis'][k] for k in ('foods','summary'))
   assert all(actual.get(k)==meal.get(k) for k in ('image_sha256','stored_image_sha256'))
  e['m1_foods_nutrients_and_image_shas_match']=True
  e['ambiguity_projection']=[{'slot':m['expected']['target_slot'],'m1':next(x for x in state['meals'] if x['meal']['meal_type']==m['expected']['target_slot'])['analysis']['is_ambiguous'],'replacement':m['analysis']['is_ambiguous']} for m in p['meals']]
  e['ambiguity_projection_match']=all(x['m1']==x['replacement'] for x in e['ambiguity_projection'])
 out['dates'].append(e)
out['observed_at']=datetime.now(UTC).isoformat()
(base/'preconditions.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
