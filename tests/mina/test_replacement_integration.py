"""凍結契約と、実SQLite・合成HTTPによるP/D/C通し検証。"""
import base64,copy,hashlib,json,tempfile,unittest
from datetime import UTC,datetime,timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock,patch
from src.mina.replacement_contract import Contract,DIRECTORY,canonical,digest,stamp
from src.mina.replacement_client import ReplacementClient
from src.mina.replacement_flow import ReplacementFlow
from src.mina.replacement_policy import ReplacementPolicy
from src.mina.replacement_inventory import ExternalInventory,REQUIRED_STATES
from src.mina.store import Store
from src.mina.instagram_store import InstagramStore
from src.mina.domain import ACCOUNT

FIXTURE=json.loads((DIRECTORY/'FIXTURES.json').read_text(encoding='utf-8'))
SAMPLES={s['case_id']:s['value'] for s in FIXTURE['samples']}
TARGET='2030-01-01';NOW=datetime(2029,12,31,10,tzinfo=UTC)
FLAGS={'MINA_MEAL_REPLACEMENT_ENABLED':'true'}
def fresh(now):return {'read_started_at':stamp(now),'checked_at':stamp(now),'valid_until':stamp(now+timedelta(seconds=120)),'complete':True}

def candidate_digest(c):
 e=c['prepared_preconditions']
 return digest({'spec':'mina-meal-replacement-candidate-v1','target_date':e['target_date'],'target_slot':e['target_slot'],
  **{k:c[k] for k in ('prepare_uuid','prepared_preconditions','prepared_publication_hold','analysis_digest','normalized_comment','policy_version','hard_deadline_at','candidate_expires_at','attempt_ordinal')},
  'original_sha256':c['original']['sha256'],'stored_sha256':c['stored']['sha256'],'original_mime':c['original']['mime'],'stored_mime':c['stored']['mime'],
  'original_byte_length':c['original']['byte_length'],'stored_byte_length':c['stored']['byte_length']},'cv1_')

class Response(BytesIO):
 status=200
 def __init__(self,value,status=200,headers=None):
  super().__init__(value if isinstance(value,bytes) else json.dumps(value).encode());self.status=status;self.headers=headers or {}

class Server:
 def __init__(self,clock,policy):
  self.clock=clock;self.calls=[];self.requests={};self.c=None;self.policy=policy;self.lost=None
  self.pre=copy.deepcopy(SAMPLES['precondition_success']);self.pre['freshness']=fresh(clock())
  self.pre['policy'].update(policy_version=policy.policy['policy_version'],budget_policy_digest=policy.policy_digest,budget_configured=True,existing_preparation_deadline_verified=True,blocking_reasons=[])
 def control(self,metadata,operation,state='applied'):
  return {'request_uuid':metadata['request_uuid'],'operation':operation,'input_hash':digest(metadata,'ih1_'),'outcome':state,'stage':'discarded' if operation=='candidate_discard' else 'ready','reason':None,'discard_input':metadata if operation=='candidate_discard' else None}
 def __call__(self,request,timeout):
  from urllib.parse import urlsplit,parse_qs
  path=urlsplit(request.full_url).path;query=parse_qs(urlsplit(request.full_url).query);self.calls.append((request.method,path))
  now=self.clock()
  if request.method=='POST':
   if 'multipart' in request.get_header('Content-type'):
    metadata=json.loads(request.data.split(b'\r\n\r\n',1)[1].split(b'\r\n--',1)[0]);operation='candidate_prepare'
    self.c=copy.deepcopy(SAMPLES['prepare_ready']['candidate']);self.c.update(prepare_uuid=metadata['request_uuid'],prepared_preconditions=metadata['expected'],precondition_digest=metadata['precondition_digest'],
     prepared_publication_hold=metadata['publication_hold'],policy_version=metadata['migration']['policy_version'],attempt_ordinal=metadata['migration']['attempt_ordinal'],
     hard_deadline_at=stamp(now+timedelta(minutes=15)),candidate_expires_at=stamp(now+timedelta(minutes=15)))
    for role in ('original','stored'):self.c[role]['authenticated_path']=f"/api/internal/mina/meal-replacement-candidates/{self.c['prepare_uuid']}/images/{role}"
    from PIL import Image
    original=request.data.split(b'name="image"',1)[1].split(b'\r\n\r\n',1)[1].rsplit(b'\r\n--',1)[0]
    buffer=BytesIO()
    with Image.open(BytesIO(original)) as im:im.save(buffer,format='WEBP')
    self.raws={'original':original,'stored':buffer.getvalue()}
    for role,raw in self.raws.items():self.c[role].update(sha256=hashlib.sha256(raw).hexdigest(),byte_length=len(raw))
    self.c['candidate_version']=candidate_digest(self.c)
    value={'contract_version':'mina-replace-v1','request':self.control(metadata,operation),'candidate':copy.deepcopy(self.c),'freshness':fresh(now),'reason':None}
   elif path.endswith('/discard'):
    metadata=json.loads(request.data);operation='candidate_discard';self.c['candidate_state']='discarded';self.c['effective_usable']=False;self.c['control_revision']+=1
    value={'contract_version':'mina-replace-v1','request':self.control(metadata,operation),'candidate':copy.deepcopy(self.c),'freshness':fresh(now),'reason':None}
    self.requests[self.c['prepare_uuid']]['candidate']=copy.deepcopy(self.c)
   else:
    metadata=json.loads(request.data);operation='meal_replace';self.c['candidate_state']='consumed';self.c['effective_usable']=False
    value=copy.deepcopy(SAMPLES['commit_success']);value['request'].update(request_uuid=metadata['request_uuid'],input_hash=digest(metadata,'ih1_'));value['freshness']=fresh(now)
   self.requests[metadata['request_uuid']]=value
   if self.lost==operation:self.lost=None;raise TimeoutError()
   return Response(value,201 if operation=='candidate_prepare' else 200)
  if 'preconditions' in path:
   p=copy.deepcopy(self.pre);p['freshness']=fresh(now);return Response(p)
  if '/images/' in path:
   role=path.rsplit('/',1)[1];image=self.c[role];raw=self.raws[role];headers={'Content-Type':image['mime'],'Content-Length':str(len(raw)),
    'Content-Digest':'sha-256=:'+base64.b64encode(hashlib.sha256(raw).digest()).decode()+':','X-Mina-Candidate-Version':self.c['candidate_version'],'X-Mina-Image-SHA256':image['sha256'],
    'X-Mina-Complete':'true','X-Mina-Checked-At':stamp(now),'X-Mina-Valid-Until':stamp(now+timedelta(seconds=120))};return Response(raw,headers=headers)
  key=path.rsplit('/',1)[1]
  if 'operation' in query:return Response(self.requests[key])
  value=copy.deepcopy(self.requests[self.c['prepare_uuid']]);value['candidate']=copy.deepcopy(self.c);value['freshness']=fresh(now);return Response(value)

class IntegrationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(Path(self.tmp.name)/'db');InstagramStore(self.store.path)
  self.now=NOW;self.clock=lambda:self.now;self.contract=Contract(self.clock)
  policy={'approval_digest':'a'*64,'currency':'synthetic-units','limit_units':10000,'opening_exposure_units':100,'server_allocation_units':500,
   'call_bounds':{'menu':100,'image':100,'review':100,'drive':100},'cost_bounds_verified':True,'existing_usage_verified':True,'server_allocation_verified':True,'policy_version':'synthetic-policy-v1'}
  self.policy=ReplacementPolicy(self.store,policy,self.clock);self.server=Server(self.clock,self.policy)
  self.client=ReplacementClient('https://www.log-eats.com','synthetic-token-for-offline-tests-only',decoder=self.contract.decode,opener=self.server,clock=self.clock,write_authorized=True)
  def page(channel,binding,target,cursor):return {'account':ACCOUNT,'channel':binding,'date':target,'complete':True,'covered_states':sorted(REQUIRED_STATES),'items':[],'has_next':False}
  self.inventory=ExternalInventory(self.store,page,self.clock,{'x':'synthetic-x','instagram':'synthetic-ig'})
  self.ports=Mock();self.ports.previous_image_hashes.return_value={m['expected']['target_slot']:m['image_sha256'] for m in self.server.pre['meals']};self.ports.plan.side_effect=lambda slot,p,n:{'comment':'合成fixture','menu':'別メニュー'+str(n),'foods':[{'name':'合成主菜'+str(n),'amount':'100g'},{'name':'合成主食','amount':'100g'}],'estimated_calories':750,'image_description':'合成試験'};self.ports.image.side_effect=lambda plan:self.image(plan)
  self.ports.review.return_value={'verdict':'pass','observations':[]}
  def drive(raw,c):
   previous=next(m for m in self.server.pre['meals'] if m['expected']['target_slot']=='dinner')['image_sha256']
   return {'candidate_version':c['candidate_version'],'original_sha256':c['original']['sha256'],'original_download_sha256':c['original']['sha256'],
    'previous_original_sha256':previous,'previous_download_sha256':previous,'checked_at':stamp(self.clock()),'verdict':'pass'}
  self.ports.drive.side_effect=drive
  self.flow=ReplacementFlow(self.store,self.client,self.policy,self.inventory,self.ports,self.clock)
  self.flags=patch.dict('os.environ',FLAGS);self.flags.start();self.addCleanup(self.flags.stop)
 def image(self,plan):
  from PIL import Image
  out=BytesIO();Image.new('RGB',(3,3),'white' if plan['menu'].endswith('1') else 'black').save(out,format='PNG');return out.getvalue()
 def hold(self):
  from src.mina.preparation_status import QualityHold
  return QualityHold('合成保留','meal_review:dinner',('image_analysis_mismatch',))
 def test_frozen_51_schema_cases_and_three_hash_vectors(self):
  for sample in FIXTURE['samples']:
   if sample['expected_valid']:self.contract.validate(sample['schema'],sample['value'])
   else:
    with self.assertRaises(Exception):self.contract.validate(sample['schema'],sample['value'])
  for name,v in FIXTURE['hash_vectors'].items():
   self.assertEqual(digest(v['payload'],{'precondition':'pc1_','analysis':'ad1_','candidate':'cv1_'}[name]),v['expected'])
 def test_two_failed_review_to_discard_next_menu_and_commit(self):
  self.ports.review.side_effect=[{'verdict':'fail','observations':['image_analysis_mismatch']},{'verdict':'pass','observations':[]}]
  self.flow.begin(TARGET,'dinner',quality_hold=self.hold());self.assertEqual(self.flow.episode(TARGET)['stage'],'ready')
  self.flow.decide_candidate(TARGET);self.assertEqual(self.flow.episode(TARGET)['stage'],'discarded')
  self.flow.reconcile(TARGET);self.flow.next_candidate(TARGET);self.flow.decide_candidate(TARGET)
  self.assertEqual(self.flow.episode(TARGET)['stage'],'saved')
  posts=[p for method,p in self.server.calls if method=='POST'];self.assertEqual(len(posts),4)
  with self.assertRaises(ValueError):self.flow.next_candidate(TARGET)
  with self.store.transaction() as db:
   self.assertEqual(db.execute('SELECT count(*) FROM replacement_attempts').fetchone()[0],2)
   self.assertEqual(db.execute('SELECT state FROM publication_holds').fetchone()[0],'saved_pending_review')
 def test_lost_prepare_never_posts_twice_on_restart(self):
  self.server.lost='candidate_prepare'
  with self.assertRaises(ValueError):self.flow.begin(TARGET,'dinner',quality_hold=self.hold())
  restarted=ReplacementFlow(self.store,self.client,self.policy,self.inventory,self.ports,self.clock);restarted.reconcile(TARGET)
  self.assertEqual(restarted.episode(TARGET)['stage'],'ready');self.assertEqual(sum(method=='POST' for method,path in self.server.calls),1)
  with self.assertRaises(ValueError):restarted.begin(TARGET,'dinner',quality_hold=self.hold())
 def test_lost_commit_lookups_preserve_saved_without_repost(self):
  self.flow.begin(TARGET,'dinner',quality_hold=self.hold());self.server.lost='meal_replace'
  with self.assertRaises(ValueError):self.flow.decide_candidate(TARGET)
  self.flow.reconcile(TARGET);self.assertEqual(self.flow.episode(TARGET)['stage'],'saved')
  self.flow.reconcile(TARGET);self.assertEqual(sum(method=='POST' for method,path in self.server.calls),2)
 def test_changed_precondition_never_rebases(self):
  self.flow.begin(TARGET,'dinner',quality_hold=self.hold());self.server.pre['meals'][2]['expected']['meal_generation']='gn1_'+'a'*64
  self.server.pre['meals'][2]['precondition_digest']=digest(self.server.pre['meals'][2]['expected'],'pc1_')
  with self.assertRaises(ValueError):self.flow.decide_candidate(TARGET)
  self.assertEqual(sum(method=='POST' for method,path in self.server.calls),1)
 def test_unknown_external_page_and_repeated_cursor_stop(self):
  self.inventory.read_page=lambda *args:{'complete':False}
  with self.assertRaises(ValueError):self.flow.begin(TARGET,'dinner',quality_hold=self.hold())
  self.assertEqual(sum(method=='POST' for method,path in self.server.calls),0)
 def test_expired_deadline_and_unverified_money_never_start(self):
  self.now=NOW+timedelta(hours=2)
  with self.assertRaisesRegex(ValueError,'deadline'):self.flow.begin(TARGET,'dinner',quality_hold=self.hold())
  with self.assertRaisesRegex(ValueError,'budget_unconfigured'):ReplacementPolicy(self.store,{},self.clock)
 def test_budget_reservations_survive_unknown_and_apply_to_all_slots(self):
  self.policy.reserve(TARGET,'call-1','image');self.policy.called('call-1',False)
  with self.assertRaises(ValueError):self.policy.reserve(TARGET,'call-1','image')
  p=dict(self.policy.policy);p['limit_units']=1;other=ReplacementPolicy(self.store,p,self.clock)
  with self.assertRaises(ValueError):other.reserve(TARGET,'call-2','image')
 def test_no_quality_hold_never_acquires_or_posts(self):
  with self.assertRaisesRegex(ValueError,'quality_hold_required'):self.flow.begin(TARGET,'dinner',quality_hold=None)
  self.assertEqual(self.server.calls,[])
 def test_unknown_drive_does_not_repeat_review_or_upload(self):
  self.flow.begin(TARGET,'dinner',quality_hold=self.hold());self.ports.drive.side_effect=TimeoutError()
  with self.assertRaises(TimeoutError):self.flow.decide_candidate(TARGET)
  self.flow.reconcile(TARGET)
  with self.assertRaisesRegex(ValueError,'external_lookup_required'):self.flow.decide_candidate(TARGET)
  self.assertEqual(self.ports.review.call_count,1);self.assertEqual(self.ports.drive.call_count,1)
  self.assertEqual(sum(method=='POST' for method,path in self.server.calls),1)
 def test_failed_candidate_image_cannot_be_reused_by_second_plan(self):
  self.ports.review.return_value={'verdict':'fail','observations':['image_analysis_mismatch']}
  self.flow.begin(TARGET,'dinner',quality_hold=self.hold());raw=self.server.raws['original'];self.flow.decide_candidate(TARGET)
  self.ports.image.side_effect=None;self.ports.image.return_value=raw
  with self.assertRaisesRegex(ValueError,'failed_image_reused'):self.flow.next_candidate(TARGET)
  self.assertEqual(sum(method=='POST' for method,path in self.server.calls),2)
 def test_discarded_candidate_can_restore_only_unchanged_original_queue(self):
  self.ports.review.return_value={'verdict':'fail','observations':['image_analysis_mismatch']}
  self.flow.begin(TARGET,'dinner',quality_hold=self.hold());self.flow.decide_candidate(TARGET)
  from src.mina.replacement_contract import source_digest
  def verify(target,p):return {'verdict':'empty_queue','read_started_at':stamp(self.clock()),'source_digest':source_digest(p)}
  self.assertEqual(self.flow.restore_old(TARGET,verify)['state'],'released_old')
  with self.store.transaction() as db:self.assertEqual(db.execute('SELECT state FROM publication_holds').fetchone()[0],'released')
 def test_diagnostics_exposes_stages_without_ids_images_or_request_body(self):
  from src.mina.replacement_diagnostics import summarize
  self.flow.begin(TARGET,'dinner',quality_hold=self.hold())
  with self.store.transaction() as db:report=summarize(db,self.clock())
  self.assertEqual(report[0]['stage'],'ready');self.assertEqual(report[0]['attempt_ordinal'],1)
  self.assertEqual(report[0]['requests'][0]['last_confirmed'],'applied')
  text=json.dumps(report);self.assertNotIn(self.flow.episode(TARGET)['prepare_uuid'],text);self.assertNotIn('合成主菜',text)
 def test_canonical_edge_cases_do_not_use_python_sort_or_number_print(self):
  self.assertEqual(canonical({'z':-0.0,'a':1e-7,'b':1e20}),b'{"a":1e-7,"b":100000000000000000000,"z":0}')
  self.assertEqual(canonical({'\uffff':1,'\U00010000':2}),'{"𐀀":2,"￿":1}'.encode())

if __name__=='__main__':unittest.main()
