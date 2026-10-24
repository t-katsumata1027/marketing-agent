// 実救済ハンドラーをlocalhost限定HTTPで受け入れる。DB・AI・保存は合成、外部通信は禁止。
import {createServer} from 'node:http';
import * as net from 'node:net';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomUUID,createHash} from 'node:crypto';
const root=resolve(process.argv[2]);
const load=(p:string)=>import(pathToFileURL(resolve(root,p)).href);
const {NextRequest}=await load('node_modules/next/server.js');
const {sha256Hex}=await load('src/lib/minaM1/canonical.ts');
const {setRescueDepsForTests}=await load('src/lib/minaRescue/runtime.ts');
const {analysisDigest,canonicalAnalysis,digest,expectedFor,preconditionDigest,candidateVersion,conforms}=await load('src/lib/minaRescue/contract.ts');
const h=await load('src/lib/minaRescue/handlers.ts');
const {handleRescuePreparePost,handleRescueCandidateGet,handleRescueImageGet,handleRescueDiscardPost,handleRescueCommitPost,handleRescueCommitGet,handleRescueCaseGet,handleRescuePreconditionsGet,handleRescueControlGet,handleRescueResumePost,handleRescueResumeGet}=h;
const {FakeMinaStore}=await load('scripts/lib/mina-m1-fake-store.ts');
const {FakeReplacementStore}=await load('scripts/lib/mina-replacement-fake-store.ts');
const {ReplacementRejectedError}=await load('src/lib/minaReplacement/types.ts');
const fixtures=JSON.parse(readFileSync(resolve(root,'docs/marketing/plans/MINA_MEAL_REPLACEMENT_V2/FIXTURES.json'),'utf8'));
const date='2026-10-24',initial=Date.parse(date+'T00:01:00.000Z'),token='synthetic-rescue-'+ 'x'.repeat(40),email='rescue@example.invalid',base='http://localhost';
const original=Buffer.from(fixtures.binary_fixtures.original.base64,'base64'),stored=Buffer.from(fixtures.binary_fixtures.stored.base64,'base64');
const sample=(id:string)=>structuredClone(fixtures.samples.find((s:any)=>s.case_id===id).value);
const obj=(v:unknown)=>v as any;
const req=(path:string,body?:any,extra:any={})=>new NextRequest(base+path,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json','Idempotency-Key':String(body.request_uuid)}:{}),...extra},body:body?JSON.stringify(body):undefined});
async function result(response:Response){return {status:response.status,headers:response.headers,body:await response.json() as any};}
type RescueCase=any;type RescueRef=any;type JsonObject=any;type ContextRecord=any;type ReplacementReader=any;type ReplacementTx=any;type RescueReader=any;type RescueTx=any;type RescueStore=any;type RescueDeps=any;type RescueCandidate=any;
function harness(){
 let now=initial;
 const at=()=>new Date(now).toISOString();
 const m1=new FakeMinaStore({users:[{id:7,email,settings:{target_calories:1800,target_protein:90,target_fat:50,target_carbs:230,daily_calorie_target:null,tolerance_pct:10,share_og_image_enabled:true}}]});
 [484,633,610].forEach((kcal,i)=>m1.foreignInsertMeal({user_id:7,meal_type:["breakfast","lunch","dinner"][i],logged_at:date+`T0${i+1}:00:00.000Z`,total_calories:kcal,total_protein:kcal/10,total_fat:0,total_carbs:kcal*.15,share_enabled_at:date+"T00:00:00.000Z",image_url:"https://synthetic.invalid/old.webp",analyzed_data:{foods:[{name:"合成旧食",amount:null,calories:kcal,protein:kcal/10,fat:0,carbs:kcal*.15}],mina_comment:"合成旧版",mina_input_image_sha256:sha256Hex("old-original"),mina_stored_image_sha256:sha256Hex("old-stored")}}));
 const normal=new FakeReplacementStore(m1),assets=new Map<string,ArrayBuffer>(),calls={analysis:0,image:0,render:0,put:0};
 normal.guardEnabled=true;normal.guardNow=()=>now;
 const policy={version:"mina_gemini_waived_20261023_v1",budget_policy_digest:"244918faac42ea4b91b96116c75b251c9361b9a1389a88503b7ba6aedb07e79b",approval_reference_digest:"8747bbcffcedfe18baa09e59ea0d25985d3c8472c47bad31e93554cb305d6083",analysis_provider:"gemini" as const,model:"gemini-3.1-flash-lite",max_output_tokens:2048,agent_integration_verified:true as const,cost_control_mode:"waived" as const,currency:null,budget_total_minor:null,prepare_max_cost_minor:null,commit_max_cost_minor:null,cost_bounds_verified:false as const};
 let rescue:RescueCase;
 const ledger=new Map<string,ContextRecord>();
 const denied=()=>{throw new ReplacementRejectedError("rescue_authorization_conflict");};
 const enrich=(r:ReplacementReader):RescueReader=>({...r,originalContext:r.context.bind(r),case:async id=>id===rescue.case_uuid?structuredClone(rescue):null,caseForPrepare:async id=>id===rescue.planned_prepare_uuid?structuredClone(rescue):null,binding:async()=>null,dbNow:async()=>at(),context:async(d,s)=>structuredClone(ledger.get(d+":"+s)??null)});
 const store:RescueStore={m1Assets:m1,resolveAccount:normal.resolveAccount.bind(normal),read:async(uid,fn)=>normal.read(uid,r=>fn(enrich(r))),transaction:async(uid,fn)=>{
  const savedCase=structuredClone(rescue),savedLedger=structuredClone(ledger);
  try{return await normal.transaction(uid,async tx=>{
   const x:RescueTx={...tx,...enrich(tx),guard:tx.guard,
    issueCase:async()=>denied(),revokeCase:async()=>denied(),expireCase:async()=>denied(),reconcileCase:async()=>denied(),
    admitRescueRequest:async(_uuid,revision)=>{if(revision!==rescue.authorization_revision&&rescue.state==="active")denied();},
    saveContext:async c=>{ledger.set(c.date+":"+c.slot,structuredClone(c));},
    completeCase:async(id,revision,c)=>{if(id!==rescue.case_uuid||revision!==rescue.authorization_revision||rescue.state!=="active")denied();rescue={...rescue,state:"completed",authorization_revision:revision+1,saved_commit_uuid:c,saved_at:at(),ogp_finish_deadline_at:new Date(Math.min(now+900000,Date.parse(rescue.hard_deadline_at))).toISOString()};return structuredClone(rescue);},
    rejectCase:async(id,revision)=>{if(id!==rescue.case_uuid||revision!==rescue.authorization_revision||rescue.state!=="active")denied();rescue={...rescue,state:"rejected",authorization_revision:revision+1};return structuredClone(rescue);},
   };return fn(x);
  });}catch(e){if(!(e instanceof Error&&e.message==="replacement_outcome_unknown")){rescue=savedCase;ledger.clear();savedLedger.forEach((v,k)=>ledger.set(k,v));}throw e;}
 }};
 const hooks:{analysis?:()=>Promise<void>;quota?:()=>Promise<void>;process?:()=>Promise<void>;failRender?:boolean;ambiguous?:boolean}={};
 const deps:RescueDeps={store,token,accountEmail:email,enabled:true,rescueEnabled:true,qaRejected:false,policy,strictGuards:true,analysisReady:true,now:()=>new Date(now),claimQuota:async()=>{await hooks.quota?.();return true;},
 analyzeImage:async(_b,_m,before,after)=>{calls.analysis++;await before?.("recognition");await after?.("recognition");await hooks.analysis?.();return {foods:[{name:"合成新朝食",amount:"1食",calories:750,protein:75,fat:0,carbs:112.5}],isAmbiguous:hooks.ambiguous??false};},
 processImage:async()=>{await hooks.process?.();return stored;},storeImage:async()=>{calls.image++;return {url:"https://synthetic.invalid/new.webp",storedSha256:sha256Hex(stored)};},
 ogpIo:()=>({renderPng:async(_p,hash)=>{calls.render++;if(hooks.failRender)throw Error("synthetic");return new TextEncoder().encode("PNG:"+hash).buffer;},putAsset:async(k,b)=>{calls.put++;assets.set(k,b);},getAssetBytes:async k=>assets.get(k)??null,deleteAsset:async k=>{assets.delete(k);}})};
 setRescueDepsForTests(deps);
 const ready=async()=>{const expected=await normal.read(7,async r=>{const s=await r.m1.readDaySnapshot(7,date,50),m=s.meals.find(m=>m.meal_type==="breakfast")!;return expectedFor(s,m,await r.generations(date,m.id));});
 const caseUuid=randomUUID(),planned=randomUUID(),hard=new Date(now+900000).toISOString();
 rescue={case_uuid:caseUuid,authorization_digest:"c".repeat(64),authorization_revision:1,state:"active",first_activated_at:at(),hard_deadline_at:hard,created_at:at(),planned_prepare_uuid:planned,attempt_ordinal:1,used_ordinals:[1],saved_at:null,saved_commit_uuid:null,ogp_finish_deadline_at:null,
 basis:{target_date:date,target_slot:"breakfast",initial_expected:expected,original_image_sha256:sha256Hex("old-original"),source_ledger_digest:"d".repeat(64),cost_history_digest:"e".repeat(64),original_first_replan_started_at:null,original_preparation_deadline_at:"2026-10-23T11:00:00.000Z",original_hard_deadline_at:"2026-10-23T11:00:00.000Z",baseline_consumed_ordinals:[],source_requests_terminal:true,hold_epoch:1,pause_receipt_digest:"a".repeat(64),policy_version:"mina_gemini_waived_20261023_v1",budget_policy_digest:policy.budget_policy_digest,cost_approval_digest:policy.approval_reference_digest,operator_approval_digest:"b".repeat(64),not_after:hard}};
 ledger.set(date+":breakfast",{userId:7,date,slot:"breakfast",firstStartedAt:rescue.first_activated_at,preparationDeadline:hard,hardDeadline:hard,policyVersion:policy.version,budgetDigest:policy.budget_policy_digest,costControlMode:"waived",approvalReferenceDigest:policy.approval_reference_digest,usedOrdinals:[1],receiptDigests:[],reservedCostMinor:null,costReservations:{}});
 };
 const ref=():RescueRef=>({case_uuid:rescue.case_uuid,authorization_digest:rescue.authorization_digest,expected_authorization_revision:rescue.authorization_revision});
 const hold=()=>({...obj(sample("prepare_input").publication_hold),hold_epoch:1,pause_receipt_digest:"a".repeat(64),provider_checked_at:at()});
 const prepareBody=()=>{const b=sample("prepare_input");delete b.migration;Object.assign(b,{contract_version:"mina-rescue-v1",request_uuid:rescue.planned_prepare_uuid,expected:rescue.basis.initial_expected,precondition_digest:preconditionDigest(rescue.basis.initial_expected),input_image_sha256:sha256Hex(original),rescue:ref(),attempt_ordinal:rescue.attempt_ordinal,publication_hold:hold()});return b;};
 const prepare=async(b=prepareBody())=>{const form=new FormData();form.set("metadata",JSON.stringify(b));form.set("image",new File([original],"synthetic.png",{type:"image/png"}));return result(await handleRescuePreparePost(new NextRequest(base+"/prepare",{method:"POST",headers:{Authorization:"Bearer "+token,"Idempotency-Key":String(b.request_uuid)},body:form})));};
 const candidate=()=>structuredClone(normal.candidates.get(rescue.planned_prepare_uuid)!.document) as RescueCandidate;
 const open=async()=>{await normal.transaction(7,async tx=>{for(let n=0;n<21;n++)await tx.guard!.admit({uuid:randomUUID(),inputHash:"f".repeat(64),operation:"candidate_prepare"});});};
 const resume=(originalUuid:string)=>{const r=normal.requests.get(originalUuid)!,receipt=normal.guardData.receipts.get(originalUuid)!.document;return {contract_version:"mina-rescue-v1",request_uuid:randomUUID(),original_request_uuid:originalUuid,expected_stage_version:receipt.stageVersion,expected_input_sha256:r.input.input_image_sha256,policy_version:policy.version,budget_policy_digest:policy.budget_policy_digest,publication_hold:hold(),rescue:ref()};};
 return {normal,m1,calls,hooks,store,ready,deps,ref,hold,prepareBody,prepare,candidate,open,resume,case:()=>structuredClone(rescue),setCase:(changes:Partial<RescueCase>)=>{rescue={...rescue,...changes};},advance:(ms:number)=>{now+=ms;},at,setDeps:(v:Partial<RescueDeps>)=>setRescueDepsForTests({...deps,...v})};
}
let networkAttempts=0;globalThis.fetch=(()=>{networkAttempts++;throw Error('external_network_forbidden');}) as typeof fetch;
net.Socket.prototype.connect=function(){networkAttempts++;throw Error('external_network_forbidden');} as any;
let fixture=harness();await fixture.ready();let pauseSaved=false;
function wrap(){
 const prior=fixture.store.transaction.bind(fixture.store);
 fixture.store.transaction=async(uid:any,fn:any)=>{const before=fixture.normal.history.length;const value=await prior(uid,fn);if(pauseSaved&&fixture.normal.history.length>before){pauseSaved=false;await fixture.open();}return value;};
}
wrap();
let lose:string|null=null;const httpCounts:any={};
const server=createServer(async(request,response)=>{
 try{
  const chunks=[];for await(const chunk of request)chunks.push(chunk);const bytes=Buffer.concat(chunks);
  const url=new URL(request.url!,'http://localhost'),path=url.pathname,method=request.method!;
  if(path==='/__fixture'){
   const c=bytes.length?JSON.parse(bytes.toString()):{};
   if(c.reset){fixture=harness();await fixture.ready();wrap();for(const k of Object.keys(httpCounts))delete httpCounts[k];}
   if(c.pause_saved)pauseSaved=true;
   if(c.advance_ms)fixture.advance(c.advance_ms);
   if(c.case)fixture.setCase(c.case);
   if(c.lose)lose=c.lose;
   if('fail_render' in c)fixture.hooks.failRender=c.fail_render;
   if(c.open)await fixture.open();
   if(c.ambiguous)fixture.hooks.ambiguous=true;
   if(c.disabled)fixture.setDeps({rescueEnabled:false});
   response.setHeader('Content-Type','application/json');response.end(JSON.stringify({target:date,now:fixture.at(),token,case:fixture.case(),
    calls:fixture.calls,httpCounts,history:fixture.normal.history.length,requests:fixture.normal.requests.size,external_network_attempts:networkAttempts}));return;
  }
  httpCounts[method+' '+path]=(httpCounts[method+' '+path]??0)+1;
  const req=new NextRequest('http://localhost'+request.url,{method,headers:request.headers as any,...(method==='POST'?{body:bytes}:{})});
  const tail=path.split('/').pop()!,parts=path.split('/');let out;
  if(path.endsWith('/preconditions'))out=await h.handleRescuePreconditionsGet(req,parts.at(-2));
  else if(path.includes('/meal-rescue-cases/'))out=await h.handleRescueCaseGet(req,tail);
  else if(path.endsWith('/meal-rescue-candidates')&&method==='POST')out=await h.handleRescuePreparePost(req);
  else if(path.endsWith('/discard'))out=await h.handleRescueDiscardPost(req,parts.at(-2));
  else if(path.includes('/images/'))out=await h.handleRescueImageGet(req,parts.at(-3),tail);
  else if(path.includes('/meal-rescue-candidates/'))out=await h.handleRescueCandidateGet(req,tail);
  else if(path.includes('/meal-rescue-requests/'))out=await h.handleRescueControlGet(req,tail);
  else if(path.endsWith('/meal-rescues'))out=await h.handleRescueCommitPost(req);
  else if(path.includes('/meal-rescues/'))out=await h.handleRescueCommitGet(req,tail);
  else if(path.endsWith('/meal-rescue-resumes'))out=await h.handleRescueResumePost(req);
  else if(path.includes('/meal-rescue-resumes/'))out=await h.handleRescueResumeGet(req,tail);
  else throw Error('fixture_route_missing');
  const raw=Buffer.from(await out.arrayBuffer());
  if(lose&&method==='POST'&&path.endsWith(lose)){lose=null;response.destroy();return;}
  response.writeHead(out.status,Object.fromEntries(out.headers.entries()));response.end(raw);
 }catch(error){process.stderr.write(String(error)+'\n');response.writeHead(500);response.end('fixture_failed');}
});
server.listen(0,'127.0.0.1',()=>process.stdout.write(JSON.stringify({port:(server.address() as any).port,version:'agent-rescue-http-v1',harness_source_sha256:process.argv[3]})+'\n'));
