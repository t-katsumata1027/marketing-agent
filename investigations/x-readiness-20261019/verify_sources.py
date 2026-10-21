"""承認済みの全X候補に本番の公開前読取照合だけを行い、送信しない。"""
import hashlib,json,os,pathlib,sqlite3,sys,time,traceback
from datetime import UTC,datetime
sys.path.insert(0,'/opt/marketing-agent')
from src.mina.clients import MealClient
from src.mina.domain import Candidate
from src.mina.m1_contract import verify_source
from src.mina.ogp import capture_ogp
from src.mina.audit import safe_failure

target='2026-10-19';host=os.environ['MINA_SHARE_HOST']
db=sqlite3.connect('file:/var/lib/mina-agent/mina.sqlite3?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
out={'target_date':target,'started_at':datetime.now(UTC).isoformat(),'read_only':True,'publication_calls':0,'slots':[]}
class ObservedClient:
 def __init__(self,entry):
  self.entry=entry;self.stage='initial_state';self.calls=0
  self.client=MealClient(os.environ['MINA_MEAL_API_BASE_URL'],os.environ['MINA_AGENT_API_TOKEN'])
  self.client.clock_observer=lambda ms:entry['clock_ms'].append(round(ms,3))
 def day_state(self,target):
  self.stage='initial_state' if self.calls==0 else 'final_state';self.calls+=1
  r=self.client.day_state(target);self.state=r;return r
 def request_state(self,key,operation):
  self.stage='request';r=self.client.request_state(key,operation);self.result=r.get('current_result');return r
for row in db.execute('SELECT * FROM candidates WHERE target_date=? ORDER BY scheduled',(target,)):
 e={'slot':row['slot'],'approved':row['state']=='approved','revision':row['revision'],'clock_ms':[]};out['slots'].append(e)
 client=ObservedClient(e);start=time.monotonic()
 try:
  c=Candidate(**json.loads(row['payload']));e['digest_matches']=c.digest==row['digest']
  if not e['digest_matches'] or not e['approved'] or not row['actor'] or not row['approved']:raise ValueError('approval_invalid')
  c.validate(host,datetime.now(UTC));e['candidate_valid']=True
  def capture(url,allowed_host):
   client.stage='ogp_real_bytes';s=capture_ogp(url,allowed_host);e['ogp_actual_bytes_read']=True;return s
  proof=verify_source(c,client,host,capture)
  e.update(preflight_passed=True,elapsed_seconds=round(time.monotonic()-start,3),ogp_expires_after_schedule=datetime.fromisoformat(proof['expires_at']).timestamp()>row['scheduled'])
  e['calories']=client.result['summary']['totalCalories'] if c.slot=='daily' else client.result['analysis']['summary']['totalCalories']
  if c.slot!='daily':
   e['original_local_sha_matches']=False
   saved=json.loads((pathlib.Path('/var/lib/mina-agent/daily')/target/'drive.json').read_text(encoding='utf-8-sig'))
   image=saved['images'][c.slot];actual=hashlib.sha256(pathlib.Path(image['path']).read_bytes()).hexdigest()
   e['original_local_sha_matches']=actual==c.image_sha256==client.result['image_sha256']==image['sha256']==image['drive']['sha256']
 except Exception as error:
  reason,details=safe_failure(error);e.update(preflight_passed=False,failure_stage=client.stage,reason=reason,frames=[{'file':p.filename.rsplit('/',1)[-1],'line':p.lineno} for p in traceback.extract_tb(error.__traceback__)],elapsed_seconds=round(time.monotonic()-start,3))
out['all_four_passed']=len(out['slots'])==4 and all(s.get('preflight_passed') for s in out['slots'])
out['finished_at']=datetime.now(UTC).isoformat();db.close();print(json.dumps(out,ensure_ascii=False,indent=2))
