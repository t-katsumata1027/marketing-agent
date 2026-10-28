// 固定本体v3実ハンドラーだけをlocalhostへ公開する。外部送信は拒否する。
import {createServer} from 'node:http';
import * as net from 'node:net';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
const root=resolve(process.argv[2]);
const load=(p:string)=>import(pathToFileURL(resolve(root,p)).href);
// 合成画像・メモリ正本・固定clockで救済実ハンドラーの境界を検証する。
import assert from "node:assert/strict";
const {observeOgpTransactions}=await load('scripts/lib/mina-replacement-ogp-transaction-probe.ts');
const {admitQaRescueWrite:admitFinalizationWrite}=await load('src/lib/minaRescue/qaStore.ts');
import {randomUUID} from "node:crypto";
const {NextRequest}=await load('node_modules/next/server.js');
const fixtures=JSON.parse(readFileSync(resolve(root,'docs/marketing/plans/MINA_MEAL_REPLACEMENT_V2/FIXTURES.json'),'utf8'));
const {sha256Hex}=await load('src/lib/minaM1/canonical.ts');
const {setRescueDepsForTests}=await load('src/lib/minaRescue/runtime.ts');
const {analysisDigest,canonicalAnalysis,digest:prefixedDigest,expectedFor,preconditionDigest,candidateVersion:versionedCandidateVersion,conforms:conformsV1}=await load('src/lib/minaRescue/contract.ts');
const digest=(v:unknown)=>prefixedDigest('',v);
const replacementHandlers=await load('src/lib/minaReplacement/handlers.ts');
const {setReplacementDepsForTests}=await load('src/lib/minaReplacement/runtime.ts');
const m1Handlers=await load('src/lib/minaM1/handlers.ts');
const {createRescueOperator}=await load('src/lib/minaRescue/operator.ts');
const {createRescueHandlers}=await load('src/lib/minaRescue/handlers.ts');
const {conforms:conformsV3}=await load('src/lib/minaRescue/v3Schema.ts');
const {verifyScopedSnapshot,independentReviewDigest}=await load('src/lib/minaRescue/scoped.ts');
const {mealImageHashes,mealOgRenderDigestFromRow,computeMealResultVersion}=await load('src/lib/minaM1/versions.ts');
const {FakeMinaStore}=await load('scripts/lib/mina-m1-fake-store.ts');
const {FakeReplacementStore}=await load('scripts/lib/mina-replacement-fake-store.ts');
const {ReplacementRejectedError}=await load('src/lib/minaReplacement/types.ts');
import type {JsonObject,ContextRecord,Slot,ReplacementReader,ReplacementTx} from "../src/lib/minaReplacement/types";
import type {RescueCase,RescueRef,RescueCandidate,RescueReader,RescueTx,RescueStore,RescueContractVersion,ScopedRescueBasis} from "../src/lib/minaRescue/types";
const version="mina-rescue-v3" as RescueContractVersion;
const {handleRescuePreparePost,handleRescueCandidateGet,handleRescueImageGet,handleRescueDiscardPost,handleRescueCommitPost,handleRescueCommitGet,handleRescueCaseGet,handleRescuePreconditionsGet,handleRescueControlGet,handleRescueResumePost,handleRescueResumeGet}=createRescueHandlers(version);
const conforms=conformsV3;
const candidateVersion=(candidate:RescueCandidate)=>versionedCandidateVersion(candidate,version);
const date="2026-10-28",initial=Date.parse("2026-10-27T13:00:00.000Z"),token="synthetic-rescue-"+"x".repeat(40),email="rescue@example.invalid",base="http://localhost";
const oldOriginal=readFileSync(resolve(root,'synthetic-old.jpg'));
const original=Buffer.from(readFileSync(resolve(root,'synthetic.jpg'))),stored=Buffer.from(fixtures.binary_fixtures.stored.base64,"base64");
const sample=(id:string)=>structuredClone(fixtures.samples.find(s=>s.case_id===id)!.value) as JsonObject;
const obj=(v:unknown)=>v as JsonObject;
const req=(path:string,body?:JsonObject,extra:Record<string,string>={})=>new NextRequest(base+path,{method:body?"POST":"GET",headers:{Authorization:"Bearer "+token,...(body?{"Content-Type":"application/json","Idempotency-Key":String(body.request_uuid)}:{}),...extra},body:body?JSON.stringify(body):undefined});
async function result(response:Response){return {status:response.status,headers:response.headers,body:await response.json() as JsonObject};}
function harness(targetSlot:Slot="lunch",pendingGrant=false,late=false){
 let now=late?Date.parse("2026-10-28T01:00:00.000Z"):initial;
 const at=()=>new Date(now).toISOString();
 const m1=new FakeMinaStore({users:[{id:7,email,settings:{target_calories:1800,target_protein:90,target_fat:50,target_carbs:230,daily_calorie_target:null,tolerance_pct:10,share_og_image_enabled:true}}]});
 const normal=new FakeReplacementStore(m1),assets=new Map<string,ArrayBuffer>(),calls={analysis:0,image:0,render:0,put:0,grant:0,native_analysis:0,native_render:0,native_put:0};
 normal.guardEnabled=true;normal.guardNow=()=>now;
 const policy={version:"mina_gemini_waived_20261023_v1",budget_policy_digest:"244918faac42ea4b91b96116c75b251c9361b9a1389a88503b7ba6aedb07e79b",approval_reference_digest:"8747bbcffcedfe18baa09e59ea0d25985d3c8472c47bad31e93554cb305d6083",analysis_provider:"gemini" as const,model:"gemini-3.1-flash-lite",max_output_tokens:2048,agent_integration_verified:true as const,cost_control_mode:"waived" as const,currency:null,budget_total_minor:null,prepare_max_cost_minor:null,commit_max_cost_minor:null,cost_bounds_verified:false as const};
 let rescue:RescueCase;let issued=false;const nativeRecords:any[]=[];
 const ledger=new Map<string,ContextRecord>();
 const denied=()=>{throw new ReplacementRejectedError("rescue_authorization_conflict");};
 const enrich=(r:ReplacementReader):RescueReader=>({...r,originalContext:r.context.bind(r),case:async id=>issued&&id===rescue.case_uuid?structuredClone(rescue):null,caseForPrepare:async id=>issued&&id===rescue.planned_prepare_uuid?structuredClone(rescue):null,binding:async()=>null,dbNow:async()=>at(),context:async(d,s)=>structuredClone(ledger.get(d+":"+s)??null)});
 const store:RescueStore={m1Assets:m1,resolveAccount:normal.resolveAccount.bind(normal),read:async(uid,fn)=>normal.read(uid,r=>fn(enrich(r))),transaction:async(uid,fn)=>{
  let savedCase:RescueCase|undefined,savedLedger:Map<string,ContextRecord>|undefined;
  try{return await normal.transaction(uid,async tx=>{
   savedCase=structuredClone(rescue);savedLedger=structuredClone(ledger);
   const x:RescueTx={...tx,...enrich(tx),guard:tx.guard,
    issueCase:async()=>denied(),revokeCase:async ref=>{if(rescue.state!=="active"||ref.expected_authorization_revision!==rescue.authorization_revision)denied();rescue={...rescue,state:"revoked",authorization_revision:rescue.authorization_revision+1};return structuredClone(rescue);},expireCase:async()=>denied(),reconcileCase:async()=>denied(),
    admitRescueRequest:async(_uuid,revision)=>{if(revision!==rescue.authorization_revision&&rescue.state==="active")denied();},
    saveContext:async c=>{ledger.set(c.date+":"+c.slot,structuredClone(c));},
    completeCase:async(id,revision,c)=>{if(id!==rescue.case_uuid||revision!==rescue.authorization_revision||rescue.state!=="active")denied();rescue={...rescue,state:"completed",authorization_revision:revision+1,saved_commit_uuid:c,saved_at:at(),ogp_finish_deadline_at:new Date(Math.min(now+900000,Date.parse(rescue.hard_deadline_at))).toISOString()};return structuredClone(rescue);},
    rejectCase:async(id,revision)=>{if(id!==rescue.case_uuid||revision!==rescue.authorization_revision||rescue.state!=="active")denied();rescue={...rescue,state:"rejected",authorization_revision:revision+1};return structuredClone(rescue);},
   };return fn(x);
  });}catch(e){if(!(e instanceof Error&&e.message==="replacement_outcome_unknown")){if(savedCase)rescue=savedCase;ledger.clear();savedLedger?.forEach((v,k)=>ledger.set(k,v));}throw e;}
 }};
 const candidateCalories=targetSlot==="breakfast"?600:targetSlot==="lunch"?750:800;
 const hooks:{calories?:number;analysis?:()=>Promise<void>;quota?:()=>Promise<void>;process?:()=>Promise<void>;failRender?:boolean;afterRender?:()=>Promise<void>;ambiguous?:boolean}={};
 const deps:RescueDeps={store,token,accountEmail:email,enabled:true,rescueEnabled:true,qaRejected:false,policy,strictGuards:true,analysisReady:true,now:()=>new Date(now),claimQuota:async()=>{await hooks.quota?.();return true;},
 analyzeImage:async(_b,_m,before,after)=>{calls.analysis++;await before?.("recognition");await after?.("recognition");await hooks.analysis?.();const calories=hooks.calories??candidateCalories;return {foods:[{name:"合成新献立",amount:"1食",calories,protein:calories/10,fat:0,carbs:calories*.15}],isAmbiguous:hooks.ambiguous??false};},
 processImage:async()=>{await hooks.process?.();return stored;},storeImage:async()=>{calls.image++;return {url:"https://synthetic.invalid/new.webp",storedSha256:sha256Hex(stored)};},
 ogpIo:()=>({renderPng:async(_p,hash)=>{calls.render++;if(hooks.failRender)throw Error("synthetic");await hooks.afterRender?.();const bytes=readFileSync(resolve(root,'synthetic-ogp.png'));return bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);},putAsset:async(k,b)=>{calls.put++;assets.set(k,b);},getAssetBytes:async k=>assets.get(k)??null,deleteAsset:async k=>{assets.delete(k);}})};
 setRescueDepsForTests(deps);setReplacementDepsForTests({...deps,store:normal});
 const m1Deps={store:m1,env:()=>({token,accountEmail:email,analysisAvailable:true,imageStorageAvailable:true}),claimQuota:async()=>true,
 analyzeImage:async()=>{calls.native_analysis++;const kcal=[455,628.4,716][nativeRecords.length]??600;return {foods:[{name:'合成旧食',amount:'1食',calories:kcal,protein:kcal/10,fat:0,carbs:kcal*.15}],isAmbiguous:false};},
 storeImage:async()=>({url:'https://synthetic.invalid/old.webp',storedSha256:sha256Hex(stored)}),deleteImage:async()=>{},ogpIo:()=>({renderPng:async()=>{calls.native_render++;const bytes=readFileSync(resolve(root,'synthetic-ogp.png'));return bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);},putAsset:async(k,b)=>{calls.native_put++;assets.set(k,b);},getAssetBytes:async k=>assets.get(k)??null,deleteAsset:async k=>{assets.delete(k);}}),now:()=>new Date(now)};
 const seedNative=async()=>{
 for(const [i,slot] of ['breakfast','lunch','dinner'].entries()){
  const uuid=randomUUID(),logged=[date+'T07:30:00+09:00',date+'T12:15:00+09:00',date+'T19:15:00+09:00'][i],comment='合成旧版'+slot;
  const state=await result(await m1Handlers.handleMinaStateGet(req('/api/internal/mina/state?date='+date),m1Deps));
  const expected=state.body.state_version,form=new FormData();
  Object.entries({contract_version:'mina-write-v1',meal_type:slot,logged_at:logged,comment,expected_state_version:expected}).forEach(([k,v])=>form.set(k,String(v)));
  form.set('image',new File([oldOriginal],date+'-'+slot+'.jpg',{type:'image/jpeg'}));
  const response=await result(await m1Handlers.handleMinaMealRegisterM1(req('/api/internal/mina/meals'),uuid,form,m1Deps));
  if(response.status!==200)throw Error('fixture_native_register_failed');
  nativeRecords.push({uuid,slot,logged,comment,expected,response:response.body});
 }
 };
 const issuePacket=async(input:any,approval:any,agentEvidence:any)=>{
  if(issued)throw Error('fixture_grant_reissue_forbidden');
  const previous=rescue;rescue={...rescue,basis:input.basis,case_uuid:input.case_uuid,planned_prepare_uuid:input.planned_prepare_uuid,attempt_ordinal:input.attempt_ordinal};
  const grantTx={m1,dbNow:async()=>at(),generations:async(d:string,id:number)=>normal.read(7,r=>r.generations(d,id)),case:async()=>null,originalContext:async()=>null,
   issueCase:async()=>{calls.grant++;issued=true;rescue.authorization_digest=digest({contract_version:version,case_uuid:input.case_uuid,user_id:7,basis:input.basis});return structuredClone(rescue);}};
  try{return await createRescueOperator(version).issueRescueGrant({query:async()=>({rows:[],rowCount:0}),release(){}},grantTx,7,input,
    {operatorApprovalArtifact:approval,agentEvidence,operatorRuntime:{body_commit:agentEvidence.integration.body_commit,schema_sha256:agentEvidence.integration.schema_sha256}},policy);
  }catch(error){rescue=previous;throw error;}
 };
 const ready=async()=>{await seedNative();const expected=await normal.read(7,async r=>{const s=await r.m1.readDaySnapshot(7,date,50),m=s.meals.find(m=>m.meal_type===targetSlot)!;return expectedFor(s,m,await r.generations(date,m.id));});
 const caseUuid=randomUUID(),planned=randomUUID(),hard=new Date(now+900000).toISOString();
 rescue={contract_version:version,case_uuid:caseUuid,authorization_digest:"c".repeat(64),authorization_revision:1,state:"active",first_activated_at:at(),hard_deadline_at:hard,created_at:at(),planned_prepare_uuid:planned,attempt_ordinal:1,used_ordinals:[1],saved_at:null,saved_commit_uuid:null,ogp_finish_deadline_at:null,
 basis:{target_date:date,target_slot:targetSlot,initial_expected:expected,original_image_sha256:sha256Hex(oldOriginal),source_ledger_digest:"d".repeat(64),cost_history_digest:"e".repeat(64),original_first_replan_started_at:null,original_preparation_deadline_at:"2026-10-27T11:00:00.000Z",original_hard_deadline_at:null,baseline_consumed_ordinals:[],source_requests_terminal:true,hold_epoch:1,pause_receipt_digest:digest({target_date:date,owner:"99999999-9999-4999-8999-999999999999",epoch:1}),policy_version:"mina_gemini_waived_20261023_v1",budget_policy_digest:policy.budget_policy_digest,cost_approval_digest:policy.approval_reference_digest,operator_approval_digest:"b".repeat(64),not_after:hard}};
 const snapshot=await m1.readDaySnapshot(7,date,50),target=snapshot.meals.find(m=>m.meal_type===targetSlot)!;
 const reviews=snapshot.meals.filter(m=>m.meal_type!==targetSlot).map(meal=>({target_slot:meal.meal_type,target_result_version:computeMealResultVersion(meal,snapshot.settings),original_sha256:mealImageHashes(meal).image_sha256,stored_sha256:mealImageHashes(meal).stored_image_sha256,analysis_digest:analysisDigest(canonicalAnalysis(meal)),ogp_input_digest:mealOgRenderDigestFromRow(meal,snapshot.settings),reviewer_role:"independent_reviewer",verdict:"pass",quality_status:"pass",review_policy_version:"synthetic-review-v3",reviewed_at:at()}));
 rescue.basis={...rescue.basis,exception_kind:"operator_scoped_meal_replacement",original_context_present:false,original_stored_image_sha256:mealImageHashes(target).stored_image_sha256!,target_analysis_digest:analysisDigest(canonicalAnalysis(target)),review_policy_version:"synthetic-review-v3",other_meals_reviews:reviews,other_meals_expected:reviews.map(r=>({target_slot:r.target_slot as Slot,target_result_version:r.target_result_version,original_sha256:r.original_sha256!,stored_sha256:r.stored_sha256!,analysis_digest:r.analysis_digest,ogp_input_digest:r.ogp_input_digest,independent_review_digest:independentReviewDigest(r)}))} as ScopedRescueBasis;
 // 管理資格・DBを使わず、固定本体の実operator検査を合成transactionへ接続する。
 const publication={checked_at:at(),all_pages_checked:true,all_providers_checked:true,hold_epoch:1,pause_receipt_digest:rescue.basis.pause_receipt_digest,dispatching_count:0,unknown_count:0,external_accepted_count:0,published_count:0};
 const sourceLedger={all_sources_enumerated:true,all_native_meal_requests_terminal:true,unfinished_native_meal_request_count:0,same_menu_repair_history_preserved:true,consumed_ordinals:[],original_context_present:false,original_first_replan_started_at:null,original_preparation_deadline_at:rescue.basis.original_preparation_deadline_at,original_hard_deadline_at:null,publication_evidence_sha256:digest(publication),other_meals_reviews:reviews};
 const costs={history_complete:true,unresolved_cost_count:0,waived_approval_digest:policy.approval_reference_digest};
 Object.assign(rescue.basis,{source_ledger_digest:digest(sourceLedger),cost_history_digest:digest(costs)});
 const approvalBasis={...rescue.basis};delete approvalBasis.operator_approval_digest;
 const integration={agent_commit:'a'.repeat(40),body_commit:'858e2bbb4f62a26ec8dc6e0d5310944541d0ec27',schema_sha256:'baf76695c55298cd97fe8b24fc488c3c164585c11ae735986da380c4e52397ab'};
 const approval={action:'issue',approved:true,case_uuid:caseUuid,planned_prepare_uuid:planned,not_after:hard,basis_sha256:digest(approvalBasis),expected_review_policy_version:'synthetic-review-v3',expected_agent_commit:integration.agent_commit,expected_body_commit:integration.body_commit,expected_schema_sha256:integration.schema_sha256};
 rescue.basis.operator_approval_digest=digest(approval);
 const grantInput={contract_version:version,case_uuid:caseUuid,basis:rescue.basis,planned_prepare_uuid:planned,attempt_ordinal:1};
 if(!pendingGrant)await issuePacket(grantInput,approval,{source_ledger:sourceLedger,cost_history:costs,publication,integration});
 ledger.set(date+":"+targetSlot,{userId:7,date,slot:targetSlot,firstStartedAt:rescue.first_activated_at,preparationDeadline:hard,hardDeadline:hard,policyVersion:policy.version,budgetDigest:policy.budget_policy_digest,costControlMode:"waived",approvalReferenceDigest:policy.approval_reference_digest,usedOrdinals:[1],receiptDigests:[],reservedCostMinor:null,costReservations:{}});
 };
 const ref=():RescueRef=>({case_uuid:rescue.case_uuid,authorization_digest:rescue.authorization_digest,expected_authorization_revision:rescue.authorization_revision});
 const hold=()=>({...obj(sample("prepare_input").publication_hold),hold_epoch:1,pause_receipt_digest:digest({target_date:date,owner:"99999999-9999-4999-8999-999999999999",epoch:1}),provider_checked_at:at()});
 const prepareBody=()=>{const b=sample("prepare_input");delete b.migration;Object.assign(b,{contract_version:version,request_uuid:rescue.planned_prepare_uuid,expected:rescue.basis.initial_expected,precondition_digest:preconditionDigest(rescue.basis.initial_expected),input_image_sha256:sha256Hex(original),rescue:ref(),attempt_ordinal:rescue.attempt_ordinal,publication_hold:hold()});return b;};
 const prepare=async(b=prepareBody())=>{const form=new FormData();form.set("metadata",JSON.stringify(b));form.set("image",new File([original],"synthetic.png",{type:"image/png"}));return result(await handleRescuePreparePost(new NextRequest(base+"/prepare",{method:"POST",headers:{Authorization:"Bearer "+token,"Idempotency-Key":String(b.request_uuid)},body:form})));};
 const candidate=()=>structuredClone(normal.candidates.get(rescue.planned_prepare_uuid)!.document) as RescueCandidate;
 const open=async()=>{await normal.transaction(7,async tx=>{for(let n=0;n<21;n++)await tx.guard!.admit({uuid:randomUUID(),inputHash:"f".repeat(64),operation:"candidate_prepare"});});};
 const resume=(originalUuid:string)=>{const r=normal.requests.get(originalUuid)!,receipt=normal.guardData.receipts.get(originalUuid)!.document;return {contract_version:version,request_uuid:randomUUID(),original_request_uuid:originalUuid,expected_stage_version:receipt.stageVersion,expected_input_sha256:r.input.input_image_sha256,policy_version:policy.version,budget_policy_digest:policy.budget_policy_digest,publication_hold:hold(),rescue:ref()};};
 return {assets,m1Deps,nativeRecords,issuePacket,targetSlot,normal,m1,calls,hooks,store,ready,ref,hold,prepareBody,prepare,candidate,open,resume,case:()=>structuredClone(rescue),setCase:(changes:Partial<RescueCase>)=>{rescue={...rescue,...changes};},advance:(ms:number)=>{now+=ms;},at,setDeps:(v:Partial<RescueDeps>)=>setRescueDepsForTests({...deps,...v})};
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
  if(path==='/__ogp'){const found=[...fixture.assets.values()].find(raw=>sha256Hex(raw)===url.searchParams.get('sha'));if(!found)throw Error('fixture_asset_missing');response.setHeader('Content-Type','image/png');response.end(Buffer.from(found));return;}
  if(path==='/__fixture'){
   const c=bytes.length?JSON.parse(bytes.toString()):{};
   if(c.reset){fixture=harness("lunch",!!c.pending_grant,!!c.late);await fixture.ready();wrap();for(const k of Object.keys(httpCounts))delete httpCounts[k];}
   if(c.grant_packet)await fixture.issuePacket(c.grant_packet.input,c.grant_packet.approval,c.grant_packet.evidence);
   if(c.pause_saved)pauseSaved=true;
   if(c.advance_ms)fixture.advance(c.advance_ms);
   if(c.case)fixture.setCase(c.case);
   if(c.lose)lose=c.lose;
   if('fail_render' in c)fixture.hooks.failRender=c.fail_render;
   if(c.open)await fixture.open();
   if(c.ambiguous)fixture.hooks.ambiguous=true;
   if(c.disabled)fixture.setDeps({rescueEnabled:false});
   response.setHeader('Content-Type','application/json');response.end(JSON.stringify({target:date,now:fixture.at(),token,case:fixture.case(),
    old_original_base64:oldOriginal.toString('base64'),original_base64:original.toString('base64'),stored_base64:stored.toString('base64'),native_records:fixture.nativeRecords,calls:fixture.calls,httpCounts,history:fixture.normal.history.length,requests:fixture.normal.requests.size,external_network_attempts:networkAttempts}));return;
  }
  httpCounts[method+' '+path]=(httpCounts[method+' '+path]??0)+1;
  const req=new NextRequest('http://localhost'+request.url,{method,headers:request.headers as any,...(method==='POST'?{body:bytes}:{})});
  const tail=path.split('/').pop()!,parts=path.split('/');let out;
  if(path==='/api/internal/mina/meal-replacement-preconditions')out=await replacementHandlers.handleReplacementPreconditionsGet(req);
  else if(path==='/api/internal/mina/state')out=await m1Handlers.handleMinaStateGet(req,fixture.m1Deps);
  else if(path.startsWith('/api/internal/mina/requests/'))out=await m1Handlers.handleMinaRequestGet(req,tail,fixture.m1Deps);
  else if(path==='/api/internal/mina/daily-share'&&method==='POST')out=await m1Handlers.handleMinaDailyShareM1(req,request.headers['idempotency-key'],JSON.parse(bytes.toString()),fixture.m1Deps);
  else if(path.endsWith('/preconditions'))out=await handleRescuePreconditionsGet(req,parts.at(-2));
  else if(path.includes('/rescue-v3/cases/'))out=await handleRescueCaseGet(req,tail);
  else if(path.endsWith('/rescue-v3/candidates')&&method==='POST')out=await handleRescuePreparePost(req);
  else if(path.endsWith('/discard'))out=await handleRescueDiscardPost(req,parts.at(-2));
  else if(path.includes('/images/'))out=await handleRescueImageGet(req,parts.at(-3),tail);
  else if(path.includes('/rescue-v3/candidates/'))out=await handleRescueCandidateGet(req,tail);
  else if(path.includes('/rescue-v3/requests/'))out=await handleRescueControlGet(req,tail);
  else if(path.endsWith('/rescue-v3/commits'))out=await handleRescueCommitPost(req);
  else if(path.includes('/rescue-v3/commits/'))out=await handleRescueCommitGet(req,tail);
  else if(path.endsWith('/rescue-v3/resumes'))out=await handleRescueResumePost(req);
  else if(path.includes('/rescue-v3/resumes/'))out=await handleRescueResumeGet(req,tail);
  else throw Error('fixture_route_missing');
  const raw=Buffer.from(await out.arrayBuffer());
  if(lose&&method==='POST'&&path.endsWith(lose)){lose=null;response.destroy();return;}
  response.writeHead(out.status,Object.fromEntries(out.headers.entries()));response.end(raw);
 }catch(error){process.stderr.write(String(error)+'\n');response.writeHead(500);response.end('fixture_failed');}
});
server.listen(0,'127.0.0.1',()=>process.stdout.write(JSON.stringify({port:(server.address() as any).port,version:'agent-scoped-rescue-http-v3',harness_source_sha256:process.argv[3]})+'\n'));
