// 固定本体の実ハンドラーをlocalhostだけで待ち受ける。正本・guard・外部I/Oは隔離する。
import {createServer} from 'node:http';
import {readFileSync,readdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
async function main(){
 const root=resolve(process.argv[2]);
 const manifest=JSON.parse(readFileSync(process.argv[4],'utf8'));
 const hashes=new Map<string,string>();
 function scan(folder:string,relative=''){
  for(const entry of readdirSync(folder,{withFileTypes:true})){
   if(entry.name==='node_modules'||entry.name==='.git')continue;
   const name=relative+entry.name,path=resolve(folder,entry.name);
   if(entry.isDirectory())scan(path,name+'/');
   else if(entry.isFile())hashes.set(name,createHash('sha256').update(readFileSync(path)).digest('hex'));
   else throw Error('fixture_source_non_regular');
  }
 }
 scan(root);
 const tree=createHash('sha256');for(const name of [...hashes.keys()].sort())tree.update(name+'\0'+hashes.get(name)+'\n');
 const sourceHash=tree.digest('hex');
 if(sourceHash!==manifest.source_tree_sha256||hashes.size!==manifest.file_count)throw Error('fixture_fixed_commit_mismatch');
 const load=(path:string)=>import(pathToFileURL(resolve(root,path)).href);
 const {NextRequest}=await load('node_modules/next/server.js');
 const h=await load('src/lib/minaReplacement/handlers.ts');
 const {setReplacementDepsForTests}=await load('src/lib/minaReplacement/runtime.ts');
 const {FakeMinaStore}=await load('scripts/lib/mina-m1-fake-store.ts');
 const {FakeReplacementStore}=await load('scripts/lib/mina-replacement-fake-store.ts');
 const {sha256Hex}=await load('src/lib/minaM1/canonical.ts');
 const {buildCurrentMealResult}=await load('src/lib/minaM1/results.ts');
 const fixtures=JSON.parse(readFileSync(resolve(root,'docs/marketing/plans/MINA_MEAL_REPLACEMENT_V2/FIXTURES.json'),'utf8'));
 const original=Buffer.from(fixtures.binary_fixtures.original.base64,'base64');
 const stored=Buffer.from(fixtures.binary_fixtures.stored.base64,'base64');
 const policy=JSON.parse(readFileSync(process.argv[3],'utf8'));
 const token='synthetic-isolated-http-'+ 'x'.repeat(40), email='isolated@example.invalid',target='2030-01-02';
 let now=Date.parse('2030-01-01T00:01:00Z'),m1:any,store:any,deps:any;
 let counts:any,lose:string|null=null,hooks:any;
 const open=async()=>{await store.transaction(7,async(tx:any)=>{for(let n=0;n<21;n++)await tx.guard.admit({uuid:crypto.randomUUID(),inputHash:'f'.repeat(64),operation:'candidate_prepare'});});};
 function reset(){
  now=Date.parse('2030-01-01T00:01:00Z');counts={http:{},analysis:0,image:0,render:0,put:0};lose=null;hooks={};
  m1=new FakeMinaStore({users:[{id:7,email,settings:{target_calories:1800,target_protein:90,target_fat:50,target_carbs:230,daily_calorie_target:null,tolerance_pct:10,share_og_image_enabled:true}}]});
  [484,633,610].forEach((kcal,index)=>m1.foreignInsertMeal({user_id:7,meal_type:['breakfast','lunch','dinner'][index],logged_at:target+`T0${index+1}:00:00Z`,total_calories:kcal,total_protein:kcal/10,total_fat:0,total_carbs:kcal*.15,share_enabled_at:'2030-01-01T00:00:00Z',image_url:'https://synthetic.invalid/old.webp',analyzed_data:{foods:[{name:'合成旧食',amount:null,calories:kcal,protein:kcal/10,fat:0,carbs:kcal*.15}],mina_comment:'合成',mina_input_image_sha256:sha256Hex(original),mina_stored_image_sha256:sha256Hex(stored)}}));
  store=new FakeReplacementStore(m1);store.guardEnabled=true;store.guardNow=()=>now;
  const transaction=store.transaction.bind(store);
  store.transaction=async(userId:any,fn:any)=>{const before=store.history.length,value=await transaction(userId,fn);if(hooks.pause_saved&&store.history.length>before){hooks.pause_saved=false;await open();}return value;};
  const assets=new Map();
  deps={store,token,accountEmail:email,enabled:true,policy,strictGuards:true,analysisReady:true,qaRejected:false,now:()=>new Date(now),claimQuota:async()=>true,
   analyzeImage:async()=>{counts.analysis++;if(hooks.fail_analysis)throw Error('replacement_analysis_unknown');if(hooks.pause_after_analysis){hooks.pause_after_analysis=false;await open();}return {foods:[{name:'合成新食',amount:'1食',calories:750,protein:75,fat:0,carbs:112.5}],isAmbiguous:false};},
   processImage:async()=>stored,storeImage:async()=>{counts.image++;return {url:'https://synthetic.invalid/new.webp',storedSha256:sha256Hex(stored)};},
   ogpIo:()=>({renderPng:async(_path:any,hash:any)=>{counts.render++;return new TextEncoder().encode('PNG:'+hash).buffer;},putAsset:async(key:any,bytes:any)=>{counts.put++;assets.set(key,bytes);},getAssetBytes:async(key:any)=>assets.get(key)??null,deleteAsset:async(key:any)=>{assets.delete(key);}})};
  setReplacementDepsForTests(deps);
 }
 reset();
 const server=createServer(async(req,res)=>{
  try{
   const chunks=[];for await(const chunk of req)chunks.push(chunk);const bytes=Buffer.concat(chunks);
   const url=new URL(req.url!,'http://127.0.0.1'),path=url.pathname;
   if(path==='/__fixture'){
    const command=bytes.length?JSON.parse(bytes.toString()):{};
    if(command.reset)reset();
    if(command.advance_ms)now+=command.advance_ms;
    if(command.lose)lose=command.lose;
    Object.assign(hooks,command.hooks??{});
    if(command.partial)m1.state.meals.pop();
    if(command.version_change)store.generation++;
    if(command.enabled===false){deps.enabled=false;setReplacementDepsForTests(deps);}
    if(command.provider_missing){deps.analysisReady=false;setReplacementDepsForTests(deps);}
    if(command.policy_missing){deps.policy=null;setReplacementDepsForTests(deps);}
    if(command.guard_missing)store.guardEnabled=false;
    if(command.open)await open();
    const snapshot=await m1.readDaySnapshot(7,target,50);
    const results=await Promise.all(snapshot.meals.map(async(meal:any)=>buildCurrentMealResult(meal,snapshot.settings,await m1.listOgAssets('meal',meal.share_id),new Date(now))));
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify({target,now,token,original:original.toString('base64'),stored:stored.toString('base64'),results,counts,contexts:[...store.contexts.keys()],history:store.history.length,requests:store.requests.size}));return;
   }
   const method=req.method!,route=method+' '+path;counts.http[route]=(counts.http[route]??0)+1;
   const request=new NextRequest('http://localhost'+req.url,{method,headers:req.headers as any,...(method==='POST'?{body:bytes}:{} )});
   const tail=path.split('/').pop()!,parts=path.split('/');let response;
   if(path.endsWith('/meal-replacement-preconditions'))response=await h.handleReplacementPreconditionsGet(request);
   else if(path.endsWith('/meal-replacement-candidates')&&method==='POST')response=await h.handleReplacementPreparePost(request);
   else if(path.endsWith('/discard'))response=await h.handleReplacementDiscardPost(request,parts.at(-2));
   else if(path.includes('/images/'))response=await h.handleReplacementImageGet(request,parts.at(-3),tail);
   else if(path.includes('/meal-replacement-candidates/'))response=await h.handleReplacementCandidateGet(request,tail);
   else if(path.includes('/meal-replacement-requests/'))response=await h.handleReplacementControlGet(request,tail);
   else if(path.endsWith('/meal-replacements'))response=await h.handleReplacementCommitPost(request);
   else if(path.includes('/requests/'))response=await h.handleReplacementCommitGet(request,tail);
   else if(path.endsWith('/meal-replacement-resumes'))response=await h.handleReplacementResumePost(request);
   else if(path.includes('/meal-replacement-resumes/'))response=await h.handleReplacementResumeGet(request,tail);
   else throw Error('fixture_route_missing');
   const body=Buffer.from(await response.arrayBuffer());
   if(lose&&method==='POST'&&path.endsWith(lose)){lose=null;res.destroy();return;}
   res.writeHead(response.status,Object.fromEntries(response.headers.entries()));res.end(body);
  }catch(error){process.stderr.write(String(error)+'\n');res.writeHead(500);res.end('fixture_failed');}
 });
 server.listen(0,'127.0.0.1',()=>process.stdout.write(JSON.stringify({port:(server.address() as any).port,version:'mina-initial-prepare-http-v1',provider_commit:manifest.provider_commit,source_tree_sha256:sourceHash,verified_files:hashes.size,schema_sha256:manifest.schema_sha256})+'\n'));
}
main().catch(error=>{process.stderr.write(String(error));process.exit(1);});
