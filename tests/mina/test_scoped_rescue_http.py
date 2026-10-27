"""本体の実救済ハンドラーへlocalhost HTTPだけで接続する隔離受入。"""
import base64,copy,hashlib,io,json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from datetime import UTC,datetime,timedelta
from urllib.request import Request,urlopen
from urllib.parse import urlsplit
from unittest.mock import Mock,patch
from PIL import Image
from src.mina.scoped_rescue_flow import ScopedRescueFlow
from src.mina.scoped_rescue_contract import ScopedRescueClient,ScopedRescueContract
TARGET="2026-10-28"
from src.mina.replacement_contract import canonical,digest,stamp
from src.mina.replacement_client import ReplacementReadFailure
from src.mina.replacement_policy import ReplacementPolicy
from src.mina.publication_hold import acquire
from src.mina.store import Store
from scripts.build_replacement_waived_policy import build
from tests.mina.test_rescue import FLAGS,OWNER

ROOT=Path(__file__).resolve().parents[2]
REPORT=[]

@unittest.skipUnless(os.getenv('MINA_SCOPED_RESCUE_PROVIDER_ROOT'),'固定本体を使う明示的な隔離HTTP受入')
class ScopedRescueHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        source=Path(os.environ['MINA_SCOPED_RESCUE_PROVIDER_ROOT']);snapshot=Path(cls.tmp.name)/'provider';snapshot.mkdir()
        # 固定Git treeからだけ展開し、作業中の未commit変更を混ぜない。
        provider_commit='858e2bbb4f62a26ec8dc6e0d5310944541d0ec27'
        archive=subprocess.check_output(['git','-C',str(source),'archive',provider_commit,'--','src','scripts','docs/marketing','tsconfig.json','package.json','package-lock.json'])
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(snapshot,filter='data')
        from PIL import Image
        Image.new('RGB',(1200,630),'white').save(snapshot/'synthetic-ogp.png',format='PNG')
        Image.new('RGB',(64,64),'red').save(snapshot/'synthetic.jpg',format='JPEG')
        Image.new('RGB',(64,64),'blue').save(snapshot/'synthetic-old.jpg',format='JPEG')
        cls.files={p.relative_to(snapshot).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in snapshot.rglob('*') if p.is_file()}
        assert cls.files['docs/marketing/contracts/mina-rescue-v3.schema.json']=='baf76695c55298cd97fe8b24fc488c3c164585c11ae735986da380c4e52397ab'
        if os.name=='nt':
            junction_env={**os.environ,'RESCUE_TEST_LINK':str(snapshot/'node_modules'),'RESCUE_TEST_MODULES':str(source/'node_modules')}
            subprocess.run(['powershell','-NoProfile','-Command',"New-Item -ItemType Junction -Path $env:RESCUE_TEST_LINK -Target $env:RESCUE_TEST_MODULES | Out-Null"],env=junction_env,check=True,capture_output=True)
        else: (snapshot/'node_modules').symlink_to(source/'node_modules',target_is_directory=True)
        cls.provider_commit=provider_commit
        cls.tree_digest=digest(cls.files)
        env={k:os.environ[k] for k in ('PATH','SYSTEMROOT','SystemRoot','WINDIR','COMSPEC','PATHEXT','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP') if k in os.environ}
        env.update(NODE_ENV='test',NEXT_PUBLIC_SITE_URL='https://www.log-eats.com',MINA_AGENT_API_TOKEN='synthetic-rescue-'+40*'x',MINA_AGENT_ACCOUNT_EMAIL='rescue@example.invalid')
        cls.stderr=(Path(cls.tmp.name)/'stderr').open('w+',encoding='utf-8');cls.addClassCleanup(cls.stderr.close)
        cls.process=subprocess.Popen(['node',str(snapshot/'node_modules/tsx/dist/cli.mjs'),'--tsconfig',str(snapshot/'tsconfig.json'),str(ROOT/'scripts/mina_scoped_rescue_http.mts'),str(snapshot),cls.tree_digest],cwd=snapshot,env=env,stdout=subprocess.PIPE,stderr=cls.stderr,text=True,encoding='utf-8')
        cls.addClassCleanup(cls.stop)
        line=cls.process.stdout.readline()
        if not line:
            cls.stderr.seek(0);raise RuntimeError(cls.stderr.read())
        cls.version=json.loads(line);cls.base='http://127.0.0.1:'+str(cls.version['port'])

    @classmethod
    def stop(cls):
        cls.process.terminate()
        try:cls.process.communicate(timeout=5)
        except subprocess.TimeoutExpired:cls.process.kill();cls.process.communicate()
        snapshot=Path(cls.tmp.name)/'provider'
        assert all((snapshot/name).is_file() and hashlib.sha256((snapshot/name).read_bytes()).hexdigest()==sha for name,sha in cls.files.items())
        path=os.getenv('MINA_SCOPED_RESCUE_HTTP_REPORT')
        if path:Path(path).write_text(json.dumps({'fixture':getattr(cls,'version',{'started':False}),'provider_commit':cls.provider_commit,
            'provider_files_sha256':cls.files,'cases':REPORT},ensure_ascii=False,indent=2),encoding='utf-8')

    def control(self,**value):
        with urlopen(Request(self.base+'/__fixture',data=json.dumps(value).encode(),headers={'Content-Type':'application/json'}),timeout=10) as response:return json.load(response)

    def setUp(self):
        data=self.control(reset=True);self.now=datetime.fromisoformat(data['now'].replace('Z','+00:00'));self.clock=lambda:self.now
        self.flags=patch.dict('os.environ',{**FLAGS,'MINA_SCOPED_RESCUE_ENABLED':'true'});self.flags.start();self.addCleanup(self.flags.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'db');acquire(self.store,TARGET,OWNER,self.now)
        oldpath=Path(self.temp.name)/'sealed-old.jpg';oldraw=base64.b64decode(data['old_original_base64']);oldpath.write_bytes(oldraw)
        self.oldpath=oldpath
        from src.mina.preparation import PreparationJournal
        PreparationJournal(self.store)
        with self.store.transaction() as db:
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete',?,?)",(TARGET,'image:lunch','synthetic-original-step',
                json.dumps({'path':str(oldpath),'size':len(oldraw),'sha256':hashlib.sha256(oldraw).hexdigest()}),self.now.timestamp()))
        self.contract=ScopedRescueContract(self.clock,TARGET,"lunch")
        def transport(request,timeout):
            parsed=urlsplit(request.full_url)
            if parsed.hostname!='www.log-eats.com':raise AssertionError('隔離origin以外を拒否')
            return urlopen(Request(self.base+parsed.path+('?' + parsed.query if parsed.query else ''),data=request.data,headers=dict(request.header_items()),method=request.method),timeout=timeout)
        self.client=ScopedRescueClient('https://www.log-eats.com',data['token'],decoder=self.contract.decode,opener=transport,clock=self.clock,write_authorized=True)
        self.policy=ReplacementPolicy(self.store,build()['agent_policy'],self.clock)
        self.inventory=Mock();self.inventory.check.side_effect=lambda target:{'checked_at':stamp(self.now),'read_started_at':stamp(self.now),'target_date':TARGET,'pages':['合成全ページ']}
        self.ports=Mock()
        self.ports.plan.return_value={'menu':'合成新朝食','foods':[{'name':'合成','amount':'1食'}],'comment':'合成新朝食','estimated_calories':750,'image_description':'隔離'}
        from PIL import Image
        raw=io.BytesIO();Image.new('RGB',(16,16),'white').save(raw,format='JPEG');self.image=raw.getvalue();self.ports.image.return_value=self.image
        self.ports.review.return_value={'verdict':'pass','observations':[]}
        self.flow=ScopedRescueFlow(self.store,self.client,Mock(),self.policy,self.inventory,self.ports,self.clock,TARGET,'lunch',data['case']['case_uuid'])
        case=data['case'];case['basis']['pause_receipt_digest']=digest({'target_date':TARGET,'owner':OWNER,'epoch':1})
        self.control(case={'basis':case['basis']})
        issue={'contract_version':'mina-rescue-v3','case_uuid':case['case_uuid'],'planned_prepare_uuid':case['planned_prepare_uuid'],'attempt_ordinal':case['attempt_ordinal'],'basis':case['basis']}
        # grantは本体側の合成運用者fixtureが発行済み。Agentによる発行ではない。
        self.flow.journal.issue(issue,OWNER,1,{'scope':'合成HTTP fixtureの管理側発行済み原本。Agent原本検証は別試験'})
        self.ports.drive.side_effect=lambda raw,c:{'candidate_version':c['candidate_version'],'original_sha256':c['original']['sha256'],
            'original_download_sha256':c['original']['sha256'],'previous_original_sha256':case['basis']['original_image_sha256'],
            'previous_download_sha256':case['basis']['original_image_sha256'],'checked_at':stamp(self.clock()),'verdict':'pass'}

    def tearDown(self):
        data=self.control();REPORT.append({'case':self.id().rsplit('.',1)[-1],**{k:data[k] for k in ('calls','httpCounts','history','requests','external_network_attempts')}})
        self.assertEqual(data['external_network_attempts'],0)

    def test_actual_prepare_images_review_commit_and_saved_requery(self):
        self.flow.prepare_candidate();self.assertEqual(self.flow.journal.job()['stage'],'ready')
        result=self.flow.decide();self.assertEqual(result['request']['persistence'],'saved')
        self.assertEqual(self.control()['history'],1)
        self.flow.reconcile(result['request']['request_uuid']);self.assertEqual(self.control()['history'],1)
        with self.assertRaises(ValueError):self.flow.prepare_candidate()
        self.assertEqual(self.control()['calls']['analysis'],1)

    def test_independent_fail_discards_and_does_not_offer_second_candidate(self):
        self.ports.review.return_value={'verdict':'fail','observations':['image_analysis_mismatch']}
        self.flow.prepare_candidate();result=self.flow.decide()
        self.assertEqual(result['request']['outcome'],'applied');self.assertEqual(result['rescue_case']['state'],'rejected')
        with self.assertRaises(ValueError):self.flow.prepare_candidate()
        self.assertEqual(self.control()['calls']['analysis'],1);self.assertEqual(self.control()['history'],0)

    def test_prepare_response_loss_uses_only_original_uuid_get(self):
        self.control(lose='candidates')
        with self.assertRaises(ReplacementReadFailure):self.flow.prepare_candidate()
        key=self.flow.journal.job()['prepare_uuid'];self.flow.reconcile(key)
        self.assertEqual(self.flow.journal.job()['stage'],'ready');self.assertEqual(self.control()['calls']['analysis'],1)
        with self.assertRaises(ValueError):self.flow.prepare_candidate()
        self.assertEqual(self.control()['calls']['analysis'],1)

    def test_commit_response_loss_reconciles_sticky_saved(self):
        self.flow.prepare_candidate();self.control(lose='commits')
        with self.assertRaises(ReplacementReadFailure):self.flow.decide()
        with self.store.transaction() as db:key=db.execute("SELECT request_key FROM scoped_rescue_requests WHERE operation='meal_replace'").fetchone()[0]
        self.flow.reconcile(key);self.assertEqual(self.flow.journal.job()['stage'],'saved')
        self.assertEqual(self.control()['history'],1)

    def test_case_revocation_stops_before_new_generation(self):
        self.control(case={'state':'revoked','authorization_revision':2})
        with self.assertRaises(ValueError):self.flow.prepare_candidate()
        self.ports.image.assert_not_called();self.assertEqual(self.control()['calls']['analysis'],0)

    def test_global_cooldown_stops_without_new_analysis(self):
        self.control(open=True)
        with self.assertRaises(ValueError):self.flow.prepare_candidate()
        self.ports.image.assert_not_called();self.assertEqual(self.control()['calls']['analysis'],0)

    def test_safe_discard_after_expiry_and_flag_off_uses_prepared_revision(self):
        self.flow.prepare_candidate();job=self.flow.journal.job()
        self.flow.call('review','review',{},lambda:{'verdict':'fail','observations':['image_analysis_mismatch']})
        self.control(case={'state':'expired','authorization_revision':2},disabled=True)
        self.now+=timedelta(minutes=16);self.control(advance_ms=16*60000)
        with patch.dict('os.environ',{'MINA_SCOPED_RESCUE_ENABLED':'false'}):
            result=self.flow.discard_reviewed()
        self.assertEqual(result['request']['outcome'],'applied')
        self.assertEqual(result['candidate']['candidate_state'],'discarded')
        self.assertEqual(result['candidate']['rescue']['expected_authorization_revision'],1)
        self.assertEqual(self.control()['history'],0)

    def test_old_p_lookup_keeps_completed_case_and_completed_preparation(self):
        self.flow.prepare_candidate();result=self.flow.decide()
        self.flow.journal.stage('all_meals_reviewed')
        self.flow.reconcile(self.flow.journal.job()['prepare_uuid'])
        self.assertEqual(self.flow.journal.job()['stage'],'all_meals_reviewed')
        self.assertEqual(json.loads(self.flow.journal.job()['case_observation'])['state'],'completed')
        self.flow.reconcile(result['request']['request_uuid'])
        self.assertEqual(self.flow.journal.job()['stage'],'all_meals_reviewed')

    def test_saved_ogp_resume_survives_flag_off_and_new_flow(self):
        self.flow.prepare_candidate();self.control(pause_saved=True)
        try:self.flow.decide()
        except ReplacementReadFailure:pass
        with self.store.transaction() as db:key=db.execute("SELECT request_key FROM scoped_rescue_requests WHERE operation='meal_replace'").fetchone()[0]
        self.now+=timedelta(minutes=10,seconds=1);self.control(advance_ms=601000,disabled=True)
        restarted=ScopedRescueFlow(self.store,self.client,Mock(),self.policy,self.inventory,Mock(),self.clock,TARGET,'lunch',self.flow.journal.scope[3])
        with patch.dict('os.environ',{'MINA_SCOPED_RESCUE_ENABLED':'false'}):
            result=restarted.resume(key)
        self.assertEqual(result['request']['status'],'succeeded',json.dumps({'request':result['request'],'reason':result.get('reason'),'cooldown':result.get('cooldown')}))
        self.assertEqual(result['request']['stage'],'completed')
        self.assertEqual(self.control()['calls']['render'],1)
        self.assertTrue(restarted.journal.saved());self.assertEqual(self.control()['history'],1)

    def test_saved_ogp_failure_is_not_replaced_again(self):
        self.flow.prepare_candidate();self.control(fail_render=True)
        try:self.flow.decide()
        except ReplacementReadFailure:pass
        self.assertTrue(self.flow.journal.saved());self.assertEqual(self.control()['history'],1)
        with self.assertRaises(ValueError):self.flow.prepare_candidate()

    def test_real_ports_drive_and_final_v3_source_reader(self):
        from tests.mina.scoped_fakes import SyntheticDrive
        from src.mina.replacement_ports import PreparationPorts
        from src.mina.domain import Candidate,ACCOUNT
        from src.mina.preparation import fingerprint
        from src.mina.m1_contract import verify_source
        data=self.control();old=base64.b64decode(data['old_original_base64']);oldpath=Path(self.temp.name)/'old.jpg';oldpath.write_bytes(old)
        drive=SyntheticDrive();drive.seed('synthetic_old_id_123',old,TARGET+'-lunch.jpg')
        ai=Mock();ai.structured.return_value={'menu':'合成別献立','foods':[{'name':'合成別食品','amount':'1食'}],
            'comment':'合成別献立','estimated_calories':750,'image_description':'隔離画像'}
        ai.image.return_value=self.image;ai.execute.return_value={'verdict':'pass','observations':[]}
        draft={'daily_comment':'旧パスタのコメント','meals':{s:{'menu':'合成旧食','foods':[{'name':'旧食品','amount':'1食'}],'comment':'旧'+s} for s in ('breakfast','lunch','dinner')}}
        saved={'images':{'lunch':{'path':str(oldpath),'sha256':hashlib.sha256(old).hexdigest(),'drive':{'id':'synthetic_old_id_123'}}}}
        ports=PreparationPorts(ai,drive,draft,saved,Path(self.temp.name)/'ports',self.clock)
        self.flow=ScopedRescueFlow(self.store,self.client,Mock(),self.policy,self.inventory,ports,self.clock,TARGET,'lunch',data['case']['case_uuid'])
        self.flow.prepare_candidate();result=self.flow.decide();r=result['current_result']
        self.assertEqual(result['request']['persistence'],'saved');self.assertEqual(self.control()['calls']['grant'],1)
        self.assertEqual(sum(c['method']=='POST' for c in drive.calls),1);self.assertEqual(drive.files['synthetic_old_id_123'][0],old)
        self.assertEqual(len(ai.execute.call_args.kwargs['images']),2)
        pre=self.client.preconditions(TARGET,data['case']['case_uuid'])
        meal=Mock();meal.base=self.client.base;meal.token=self.client.token
        meal.day_state.return_value={'contract_version':'mina-write-v1','complete':True,'checked_at':stamp(self.now),'date':TARGET,
            'account':ACCOUNT,'state_version':pre['state_version'],'meal_count':3,'meals':pre['meals']}
        def capture(url,host):
            with urlopen(self.base+'/__ogp?sha='+r['ogp']['image_sha256']) as response:raw=response.read()
            pixels=Image.open(io.BytesIO(raw));pixels.verify()
            meta={'og:image':r['ogp']['image_url'],'image_sha256':hashlib.sha256(raw).hexdigest()}
            return {'meta':meta,'digest':fingerprint(meta),'review_image':raw}
        snapshot=capture(r['share']['url'],'www.log-eats.com')
        source={'contract_version':'mina-rescue-v3','operation':'meal_replace','request_key':result['request']['request_uuid'],
            'input_hash':result['request']['input_hash'],'state_version':result['current_state_version'],'result_version':r['result_version'],
            'result_sha256':fingerprint(r),'stored_image_sha256':r['stored_image_sha256'],'ogp_image_sha256':r['ogp']['image_sha256']}
        candidate=Candidate(TARGET,'lunch',r['normalized_comment'],r['x']['fixed_text'],r['share']['url'],'合成別献立',
            {'calories':750,'protein':75,'fat':0,'carbs':112.5},r['image_sha256'],snapshot['digest'],'pass','publishable',True,True,True,'a'*40,
            publish_text=r['x']['publish_text'],approval_source=source)
        candidate.validate('www.log-eats.com',self.now)
        from src.mina.scoped_rescue_contract import ScopedRescueClient as ActualClient
        def reader(*args,**kwargs):return ActualClient(*args,**kwargs,opener=self.client.opener)
        with patch('src.mina.scoped_rescue_contract.ScopedRescueClient',side_effect=reader):
            proof=verify_source(candidate,meal,'www.log-eats.com',capture,clock=self.clock)
        self.assertIn('observed_at',proof)
        with patch.dict('os.environ',{'MINA_SCOPED_RESCUE_ENABLED':'false'}):
            self.flow.reconcile(result['request']['request_uuid'])

    def test_no_post_if_two_gets_use_remaining_sixty_seconds(self):
        prior=self.client.preconditions;calls=[0]
        def delayed(*args):
            calls[0]+=1
            if calls[0]==2:self.now+=timedelta(seconds=860);self.control(advance_ms=860000)
            return prior(*args)
        self.client.preconditions=delayed
        with self.assertRaises(ValueError):self.flow.prepare_candidate()
        self.assertFalse(any(k.startswith('POST ') for k in self.control()['httpCounts']))
        self.ports.image.assert_called_once()

    def test_old_original_ledger_change_stops_before_generation(self):
        with self.store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS preparation_steps(target_date TEXT,step TEXT,digest TEXT,state TEXT,result TEXT,started REAL)')
            db.execute("INSERT INTO preparation_steps VALUES(?,?,?,'complete','{}',1)",(TARGET,'old','changed'))
        with self.assertRaises(ValueError):self.flow.prepare_candidate()
        self.ports.image.assert_not_called();self.assertEqual(self.control()['calls']['analysis'],0)

    def test_old_image_bytes_change_stops_before_generation(self):
        self.oldpath.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.flow.prepare_candidate()
        self.ports.image.assert_not_called();self.assertEqual(self.control()['calls']['analysis'],0)

    def test_revocation_between_case_and_pre_get_stops_before_ai(self):
        prior=self.client.preconditions
        def revoked(*args):self.control(case={'state':'revoked','authorization_revision':2});return prior(*args)
        self.client.preconditions=revoked
        with self.assertRaises(ValueError):self.flow.prepare_candidate()
        self.ports.plan.assert_not_called();self.assertEqual(self.control()['calls']['analysis'],0)

    def test_each_rpc_rechecks_deadline_and_preserves_first_call_history(self):
        from src.mina.scoped_rescue_rpc import rpc
        self.flow.bind_case();counts=[0,0]
        def first():
            counts[0]+=1;self.now+=timedelta(minutes=16);return {'done':True}
        def operation():
            rpc(self.flow,{'operation':'first'},first)
            return rpc(self.flow,{'operation':'second'},lambda:counts.__setitem__(1,counts[1]+1))
        with self.assertRaises(ValueError):self.flow.call('rebuild','rebuild',{},operation)
        self.assertEqual(counts,[1,0])

    def test_oauth_response_is_memory_only_and_never_in_database(self):
        from src.mina.drive import DriveClient
        from src.mina.scoped_rescue_rpc import guarded_drive
        self.flow.bind_case();marker='unique-synthetic-access-token-not-for-storage'
        drive=DriveClient('synthetic','synthetic','synthetic','synthetic_folder_123')
        drive._transport_request=lambda *a,**kw:json.dumps({'access_token':marker,'expires_in':3600,'token_type':'Bearer'}).encode()
        client=guarded_drive(self.flow,drive)
        def operation():
            self.assertEqual(client._authorization(),{'Authorization':'Bearer '+marker});return {'ok':True}
        self.flow.call('rebuild','rebuild',{},operation)
        with self.store.transaction() as db:
            dump=''.join(db.iterdump())
            rows=list(db.execute('SELECT encoding,value FROM scoped_rescue_calls'))
        self.assertNotIn(marker,dump);self.assertNotIn(marker.encode().hex().upper(),dump)
        self.assertNotIn(marker.encode(),Path(self.store.path).read_bytes())
        self.assertEqual(sum(r['encoding']=='redacted' for r in rows),1)

    def test_wide_window_allows_tonight_but_stops_after_first_post_slot(self):
        end=TARGET+'T00:00:00.000Z';self.flow.schedule_gate(end)
        self.now=datetime(2026,10,28,tzinfo=UTC)
        with self.assertRaisesRegex(ValueError,'publication_schedule_expired'):self.flow.schedule_gate(end)
        self.ports.image.assert_not_called();self.assertEqual(self.control()['calls']['analysis'],0)

    def test_native_hold_to_real_operator_c_finish_eight_images_and_all_source_verifiers(self):
        from datetime import date
        from src.mina.preparation import PreparationJournal,fingerprint
        from src.mina.request_ledger import RequestLedger
        from src.mina.generation import meal_review_policy_sha256
        from src.mina.meal_review_recovery import review_once
        from src.mina.replacement_ports import PreparationPorts
        from src.mina.replacement_inventory import ExternalInventory,REQUIRED_STATES
        from src.mina.replacement_client import ReplacementReadClient
        from src.mina.replacement_contract import Contract
        from src.mina.replacement_rebuild import ReplacementRebuilder
        from src.mina.replacement_flow import ReplacementFlow
        from src.mina.scoped_rescue_origin import capture as capture_origin
        from src.mina.scoped_rescue_contract import SCHEMA_SHA
        from src.mina.rescue_contract import POLICY,BUDGET,APPROVAL
        from src.mina.instagram_store import InstagramStore
        from src.mina.instagram_media import host_asset as actual_host,verify_public
        from src.mina.domain import ACCOUNT,slot_time
        from tests.mina.scoped_fakes import SyntheticDrive
        data=self.control(reset=True,pending_grant=True);case=data['case']
        root=Path(self.temp.name)/'complete';root.mkdir();directory=root/'daily'/TARGET;directory.mkdir(parents=True)
        self.store=Store(root/'db');InstagramStore(self.store.path);policy=ReplacementPolicy(self.store,build()['agent_policy'],self.clock)
        raw=base64.b64decode(data['old_original_base64']);drive=SyntheticDrive()
        test=self
        class Meal:
            base='https://www.log-eats.com';token=data['token']
            def read(inner,path):
                with urlopen(Request(test.base+path,headers={'Authorization':'Bearer '+inner.token})) as response:return json.load(response)
            def day_state(inner,target):return inner.read('/api/internal/mina/state?date='+target)
            def request_state(inner,key,operation):return inner.read('/api/internal/mina/requests/'+key+'?operation='+operation)
            def json_write(inner,path,payload,method='POST',request_key=None):
                with urlopen(Request(test.base+path,data=canonical(payload),method=method,headers={'Authorization':'Bearer '+inner.token,
                    'Idempotency-Key':request_key,'Content-Type':'application/json'})) as response:return json.load(response)
        meal=Meal();journal=PreparationJournal(self.store);ledger=RequestLedger(self.store)
        draft={'target_date':TARGET,'state':'draft_unreviewed','daily_comment':'旧パスタで約1970kcal',
            'meals':{r['slot']:{'menu':'合成旧食'+r['slot'],'comment':r['comment'],'foods':[{'name':'合成旧食','amount':'1食'}],
                'estimated_calories':650,'image_description':'合成画像'} for r in data['native_records']}}
        saved={'target_date':TARGET,'state':'images_drive_verified','images':{}}
        def capture(url,host):
            state=meal.day_state(TARGET);results=[*state['meals'],*([state['daily_share']] if state['daily_share'] else [])]
            r=next(r for r in results if r['share']['url']==url)
            with urlopen(self.base+'/__ogp?sha='+r['ogp']['image_sha256']) as response:png=response.read()
            pixels=Image.open(io.BytesIO(png));pixels.load();preview=io.BytesIO();pixels.convert('RGB').save(preview,format='JPEG')
            meta={'og:image':r['ogp']['image_url'],'image_sha256':hashlib.sha256(png).hexdigest(),'page_url':url}
            return {'meta':meta,'digest':fingerprint(meta),'review_image':preview.getvalue()}
        ai=Mock();ai.review_ogp.return_value={'status':'publishable','reason_codes':[]}
        ai.review_meal.side_effect=lambda image,plan,response,meta:({'status':'hold','reason_codes':['image_analysis_mismatch']}
            if response['result']['meal']['meal_type']=='lunch' and image==raw else {'status':'publishable','reason_codes':[]})
        for record in data['native_records']:
            slot=record['slot'];path=directory/(slot+'.jpg');path.write_bytes(raw);fid='synthetic_native_'+slot;drive.seed(fid,raw,TARGET+'-'+slot+'.jpg')
            info={'path':str(path),'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'drive':drive.verify(fid,raw,TARGET+'-'+slot+'.jpg'),
                'image_review':{'status':'image_acceptable','reason_codes':[]}}
            saved['images'][slot]=info;journal.run(TARGET,'image:'+slot,{'slot':slot},lambda:info)
            logged=slot_time(date.fromisoformat(TARGET),slot).isoformat();inputs={'sha256':info['sha256'],'logged_at':logged,'comment':record['comment']}
            journal.run(TARGET,'meal_request_key:'+slot,inputs,lambda:{'key':record['uuid'],'expected_state_version':record['expected']})
            payload={'contract_version':'mina-write-v1','meal_type':slot,'logged_at':logged,'comment':record['comment'],
                     'expected_state_version':record['expected'],'filename':TARGET+'-'+slot+'.jpg'}
            ledger.prepare(record['uuid'],'meal_register',TARGET,slot,payload,raw);ledger.record(record['uuid'],'complete',{'persistence':'saved','stage':'completed'})
            journal.run(TARGET,'meal_register:'+slot,inputs,lambda:record['response'])
            snapshot=capture(record['response']['result']['share']['url'],meal.base)
            journal.run(TARGET,'ogp_capture:'+slot,{'share_url':record['response']['result']['share']['url'],'directory':str(directory)},
                lambda:{'digest':snapshot['digest'],'meta':snapshot['meta']})
            review_once(journal,TARGET,'meal_review:'+slot,{'image_sha256':info['sha256'],'meal':draft['meals'][slot],
                'response':record['response'],'ogp_digest':snapshot['digest'],'policy_sha256':meal_review_policy_sha256()},
                lambda:ai.review_meal(raw,draft['meals'][slot],record['response'],snapshot['meta']))
        (directory/'draft.json').write_bytes(canonical(draft));(directory/'drive.json').write_bytes(canonical(saved))
        acquire(self.store,TARGET,OWNER,self.now)
        inventory=ExternalInventory(self.store,lambda ch,b,t,c:{'account':ACCOUNT,'channel':b,'date':t,'covered_states':sorted(REQUIRED_STATES),
            'complete':True,'items':[],'has_next':False},self.clock,{'x':'x','instagram':'ig'})
        ReplacementFlow(self.store,Mock(),policy,inventory,Mock(),self.clock)
        with self.store.transaction() as db:
            db.execute("INSERT INTO replacement_episodes VALUES(?,?,?,?,'generating','{}',NULL,NULL,NULL,?,NULL)",(TARGET,'lunch',OWNER,1,datetime(2026,10,27,11,tzinfo=UTC).timestamp()))
            db.execute('CREATE TABLE replacement_runtime_context(target_date TEXT PRIMARY KEY,context TEXT)')
            db.execute('INSERT INTO replacement_runtime_context VALUES(?,?)',(TARGET,json.dumps({'directory':str(directory),'draft':draft,'saved':saved,'host':'www.log-eats.com'})))
        normal=ReplacementReadClient(meal.base,meal.token,decoder=Contract(self.clock).decode,opener=self.client.opener,clock=self.clock)
        ai.structured.return_value={'menu':'合成別献立','comment':'合成新昼食','estimated_calories':750,
            'foods':[{'name':'合成新献立','amount':'1食'},{'name':'合成副菜','amount':'1皿'}],'image_description':'別献立画像'}
        ai.image.return_value=self.image;ai.execute.return_value={'verdict':'pass','observations':[]}
        ports=PreparationPorts(ai,drive,draft,saved,root/'ports',self.clock)
        flow=ScopedRescueFlow(self.store,self.client,normal,policy,inventory,ports,self.clock,TARGET,'lunch',case['case_uuid']);self.flow=flow
        anchors=case['basis']['other_meals_expected']
        reviews=flow.review_other_meals(meal,ai,capture,draft,saved,'www.log-eats.com',anchors,'synthetic-review-v3',case['basis']['not_after'],OWNER,1)
        pre=normal.preconditions(TARGET);origin=capture_origin(self.store,pre,OWNER,1,self.now,TARGET,'lunch',case['case_uuid'])
        receipt=inventory.check(TARGET);publication={'checked_at':receipt['checked_at'],'all_pages_checked':True,'all_providers_checked':True,'hold_epoch':1,
            'pause_receipt_digest':digest({'target_date':TARGET,'owner':OWNER,'epoch':1}),'dispatching_count':0,'unknown_count':0,'external_accepted_count':0,'published_count':0,'receipt':receipt}
        ledger_proof={'native_origin':origin,'all_sources_enumerated':True,'all_native_meal_requests_terminal':True,'unfinished_native_meal_request_count':0,
            'same_menu_repair_history_preserved':True,'consumed_ordinals':origin['baseline_consumed_ordinals'],'other_meals_reviews':reviews,
            'publication_evidence_sha256':digest(publication),**{k:origin[k] for k in ('original_context_present','original_first_replan_started_at','original_preparation_deadline_at','original_hard_deadline_at')}}
        costs={'history_complete':True,'unresolved_cost_count':0,'waived_approval_digest':APPROVAL,'ledgers':origin['document']['ledgers']}
        basis={**case['basis'],'source_ledger_digest':digest(ledger_proof),'cost_history_digest':digest(costs),'other_meals_reviews':reviews,
               'other_meals_expected':[{**a,'independent_review_digest':digest(next(r for r in reviews if r['target_slot']==a['target_slot']))} for a in anchors]}
        basis.pop('operator_approval_digest');body='858e2bbb4f62a26ec8dc6e0d5310944541d0ec27'
        approval={'action':'issue','approved':True,'case_uuid':case['case_uuid'],'planned_prepare_uuid':case['planned_prepare_uuid'],
            'basis_sha256':digest(basis),'not_after':basis['not_after'],'expected_review_policy_version':'synthetic-review-v3',
            'expected_agent_commit':'a'*40,'expected_body_commit':body,'expected_schema_sha256':SCHEMA_SHA}
        basis['operator_approval_digest']=digest(approval);issue={'contract_version':'mina-rescue-v3','case_uuid':case['case_uuid'],
            'planned_prepare_uuid':case['planned_prepare_uuid'],'attempt_ordinal':1,'basis':basis}
        proof={'source_ledger':ledger_proof,'cost_history':costs,'publication':publication,'integration':{'agent_commit':'a'*40,'body_commit':body,'schema_sha256':SCHEMA_SHA}}
        flow.stage_issue(issue,approval,proof,owner=OWNER,epoch=1,agent_commit='a'*40,body_commit=body)
        self.control(grant_packet=flow.operator_bundle());flow.prepare_candidate();committed=flow.decide()
        from src.mina.instagram_media import host_asset as actual_host,verify_public
        from urllib.parse import parse_qs
        class Response(io.BytesIO):headers={'Content-Type':'image/jpeg'}
        class PublicOpener:
            def open(inner,req,timeout):return Response(drive.files[parse_qs(urlsplit(req.full_url).query)['id'][0]][0])
        def host(asset,client,store,allow_public):return actual_host(asset,client,store,allow_public,verify=lambda a:verify_public(a,PublicOpener()))
        from src.mina.replacement_client import ReplacementReadClient as ActualNormal
        from src.mina.scoped_rescue_contract import ScopedRescueClient as ActualScoped
        def normal_reader(*args,**kwargs):return ActualNormal(*args,**kwargs,opener=self.client.opener)
        def scoped_reader(*args,**kwargs):return ActualScoped(*args,**kwargs,opener=self.client.opener)
        day=date.fromisoformat(TARGET);history={'account':ACCOUNT,'target_date':TARGET,'checked_at':stamp(self.now),'reviewer':'隔離確認',
            'history_source':'合成全履歴','schedule_source':'合成全予約','history_complete':True,'legacy_schedule_clear':True,
            'history_from':(day-timedelta(days=14)).isoformat(),'history_to':(day-timedelta(days=1)).isoformat(),'entries':[]}
        builder=ReplacementRebuilder(self.store,meal,ai,drive,root/'daily',root/'instagram',history,'a'*40,'www.log-eats.com',self.clock,
            api=Mock(),ports_directory=root/'ports',instagram_enabled=True)
        with patch('src.mina.ogp.capture_ogp',side_effect=capture),patch('src.mina.replacement_client.ReplacementReadClient',side_effect=normal_reader),\
             patch('src.mina.scoped_rescue_contract.ScopedRescueClient',side_effect=scoped_reader),patch('src.mina.instagram_media.host_asset',side_effect=host),\
             patch('src.mina.instagram_publisher.instagram_channel',return_value={'id':'ig','serviceId':'synthetic'}):
            finished=flow.finish(committed['request']['request_uuid'],builder)
            from src.mina.domain import Candidate
            from src.mina.m1_contract import verify_source
            for value in finished['x']:verify_source(Candidate(**value),meal,'www.log-eats.com',capture,clock=self.clock)
        self.assertEqual(len(finished['x']),4);self.assertEqual(len(finished['instagram']),4);self.assertEqual(sum(len(c['assets']) for c in finished['instagram']),8)
        self.assertEqual(self.control()['calls']['grant'],1);self.assertEqual(self.control()['calls']['analysis'],1);self.assertEqual(self.control()['history'],1)
        self.assertTrue(finished['publication_hold']);self.assertFalse(finished['discord_sent'])
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM candidates WHERE state=\'approval_pending\'').fetchone()[0],4)
            self.assertEqual(db.execute('SELECT count(*) FROM instagram_candidates WHERE state=\'approval_pending\'').fetchone()[0],4)
            self.assertEqual(db.execute('SELECT state FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone()[0],'saved_pending_review')
            self.assertEqual(db.execute('SELECT stage FROM replacement_episodes WHERE target_date=?',(TARGET,)).fetchone()[0],'new_bundle_pending_approval')
            db.execute("UPDATE candidates SET state='approved',actor='synthetic-user',approved=?",(self.now.timestamp(),))
            db.execute("UPDATE instagram_candidates SET state='approved',actor='synthetic-user',approved=?",(self.now.timestamp(),))
        # 全ユーザー判断後、case期限後・機能OFFでも読取だけの通常承認照合で停止を解除できる。
        self.now+=timedelta(seconds=901);self.control(advance_ms=901000,disabled=True)
        before={k:sum(v for name,v in self.control()['httpCounts'].items() if name.startswith('POST ')) for k in ('post',)}
        reviews_before=(ai.review_meal.call_count,ai.review_ogp.call_count,ai.execute.call_count)
        from src.mina.replacement_runtime import maintain,ReplacementCoordinator
        from src.mina.instagram_media import HOSTS
        normal_flow=ReplacementFlow(self.store,Mock(),policy,inventory,Mock(),self.clock)
        coordinator=ReplacementCoordinator(normal_flow,builder,'www.log-eats.com',HOSTS,
            readiness={k:True for k in ('contract_verified','provider_qa_verified','external_inventory_adapter_verified','budget_allocation_verified')})
        with patch('src.mina.ogp.capture_ogp',side_effect=capture),patch('src.mina.replacement_client.ReplacementReadClient',side_effect=normal_reader),\
             patch('src.mina.scoped_rescue_contract.ScopedRescueClient',side_effect=scoped_reader),\
             patch('src.mina.instagram_media.verify_public',side_effect=lambda a:verify_public(a,PublicOpener())),\
             patch.dict('os.environ',{'MINA_SCOPED_RESCUE_ENABLED':'false'}):
            outcomes=maintain(self.store,self.clock,factory=lambda ctx:coordinator)
        self.assertEqual(outcomes[0]['state'],'approval_checked')
        self.assertEqual(before['post'],sum(v for name,v in self.control()['httpCounts'].items() if name.startswith('POST ')))
        self.assertEqual(reviews_before,(ai.review_meal.call_count,ai.review_ogp.call_count,ai.execute.call_count))
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT state FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone()[0],'released')
            old=db.execute('SELECT deadline,prepare_uuid,commit_uuid FROM replacement_episodes WHERE target_date=?',(TARGET,)).fetchone()
            self.assertEqual(old['deadline'],datetime(2026,10,27,11,tzinfo=UTC).timestamp());self.assertIsNone(old['prepare_uuid']);self.assertIsNone(old['commit_uuid'])

if __name__=='__main__':unittest.main()
