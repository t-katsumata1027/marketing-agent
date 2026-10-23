"""既存認証によるGETだけを実行し、ローカルの非公開検証入力へ渡す。"""
import json,os,sys
from urllib.request import Request
sys.path.insert(0,'/opt/marketing-agent')
from src.mina.clients import MealClient,urlopen
client=MealClient(os.environ['MINA_MEAL_API_BASE_URL'],os.environ['MINA_AGENT_API_TOKEN'],max_clock_ahead=5)
assert client.base=='https://www.log-eats.com'
out={}
for target in ('2026-10-18','2026-10-19'):
 request=Request(client.base+'/api/internal/mina/meal-replacement-preconditions?date='+target,method='GET',headers={'Authorization':'Bearer '+client.token,'Accept':'application/json','Cache-Control':'no-store'})
 with urlopen(request,timeout=15) as response:
  raw=response.read(131073)
  if len(raw)>131072:raise ValueError('response_too_large')
  out[target]={'status':response.status,'cache_control':response.headers.get('Cache-Control'),'response':json.loads(raw)}
out['m1_state']=client.day_state('2026-10-19')
print(json.dumps(out,ensure_ascii=False))
