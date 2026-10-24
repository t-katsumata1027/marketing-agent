"""本体の実救済ハンドラーへlocalhost HTTPだけで接続する隔離受入。"""
import base64,copy,hashlib,io,json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from datetime import UTC,datetime,timedelta
from urllib.request import Request,urlopen
from urllib.parse import urlsplit
from unittest.mock import Mock,patch
from src.mina.rescue_flow import RescueFlow
from src.mina.rescue_contract import RescueClient,RescueContract,TARGET
from src.mina.replacement_contract import canonical,digest,stamp
from src.mina.replacement_client import ReplacementReadFailure
from src.mina.replacement_policy import ReplacementPolicy
from src.mina.publication_hold import acquire
from src.mina.store import Store
from scripts.build_replacement_waived_policy import build
from tests.mina.test_rescue import FLAGS,OWNER

ROOT=Path(__file__).resolve().parents[2]
REPORT=[]

@unittest.skipUnless(os.getenv('MINA_RESCUE_PROVIDER_ROOT'),'固定本体を使う明示的な隔離HTTP受入')
class RescueHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        source=Path(os.environ['MINA_RESCUE_PROVIDER_ROOT']);snapshot=Path(cls.tmp.name)/'provider';snapshot.mkdir()
        # 固定Git treeからだけ展開し、作業中の未commit変更を混ぜない。
        provider_commit='48e5459de86130ea622a098bf1c316bb11836112'
        archive=subprocess.check_output(['git','-C',str(source),'archive',provider_commit,'--','src','scripts','docs/marketing','tsconfig.json','package.json','package-lock.json'])
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(snapshot,filter='data')
        cls.files={p.relative_to(snapshot).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in snapshot.rglob('*') if p.is_file()}
        if os.name=='nt':
            junction_env={**os.environ,'RESCUE_TEST_LINK':str(snapshot/'node_modules'),'RESCUE_TEST_MODULES':str(source/'node_modules')}
            subprocess.run(['powershell','-NoProfile','-Command',"New-Item -ItemType Junction -Path $env:RESCUE_TEST_LINK -Target $env:RESCUE_TEST_MODULES | Out-Null"],env=junction_env,check=True,capture_output=True)
        else: (snapshot/'node_modules').symlink_to(source/'node_modules',target_is_directory=True)
        cls.provider_commit=provider_commit
        cls.tree_digest=digest(cls.files)
        env={k:os.environ[k] for k in ('PATH','SYSTEMROOT','SystemRoot','WINDIR','COMSPEC','PATHEXT','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP') if k in os.environ}
        env.update(NODE_ENV='test',NEXT_PUBLIC_SITE_URL='https://www.log-eats.com',MINA_AGENT_API_TOKEN='synthetic-rescue-'+40*'x',MINA_AGENT_ACCOUNT_EMAIL='rescue@example.invalid')
        cls.stderr=(Path(cls.tmp.name)/'stderr').open('w+',encoding='utf-8');cls.addClassCleanup(cls.stderr.close)
        cls.process=subprocess.Popen(['node',str(snapshot/'node_modules/tsx/dist/cli.mjs'),'--tsconfig',str(snapshot/'tsconfig.json'),str(ROOT/'scripts/mina_rescue_http.mts'),str(snapshot),cls.tree_digest],cwd=snapshot,env=env,stdout=subprocess.PIPE,stderr=cls.stderr,text=True,encoding='utf-8')
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
        path=os.getenv('MINA_RESCUE_HTTP_REPORT')
        if path:Path(path).write_text(json.dumps({'fixture':getattr(cls,'version',{'started':False}),'provider_commit':cls.provider_commit,
            'provider_files_sha256':cls.files,'cases':REPORT},ensure_ascii=False,indent=2),encoding='utf-8')

    def control(self,**value):
        with urlopen(Request(self.base+'/__fixture',data=json.dumps(value).encode(),headers={'Content-Type':'application/json'}),timeout=10) as response:return json.load(response)

    def setUp(self):
        data=self.control(reset=True);self.now=datetime.fromisoformat(data['now'].replace('Z','+00:00'));self.clock=lambda:self.now
        self.flags=patch.dict('os.environ',FLAGS);self.flags.start();self.addCleanup(self.flags.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'db');acquire(self.store,TARGET,OWNER,self.now)
        self.contract=RescueContract(self.clock)
        def transport(request,timeout):
            parsed=urlsplit(request.full_url)
            if parsed.hostname!='www.log-eats.com':raise AssertionError('隔離origin以外を拒否')
            return urlopen(Request(self.base+parsed.path+('?' + parsed.query if parsed.query else ''),data=request.data,headers=dict(request.header_items()),method=request.method),timeout=timeout)
        self.client=RescueClient('https://www.log-eats.com',data['token'],decoder=self.contract.decode,opener=transport,clock=self.clock,write_authorized=True)
        self.policy=ReplacementPolicy(self.store,build()['agent_policy'],self.clock)
        self.inventory=Mock();self.inventory.check.side_effect=lambda target:{'checked_at':stamp(self.now),'read_started_at':stamp(self.now),'target_date':TARGET,'pages':['合成全ページ']}
        self.ports=Mock()
        self.ports.plan.return_value={'menu':'合成新朝食','foods':[{'name':'合成','amount':'1食'}],'comment':'合成新朝食','estimated_calories':750,'image_description':'隔離'}
        from PIL import Image
        raw=io.BytesIO();Image.new('RGB',(16,16),'white').save(raw,format='JPEG');self.image=raw.getvalue();self.ports.image.return_value=self.image
        self.ports.review.return_value={'verdict':'pass','observations':[]}
        self.flow=RescueFlow(self.store,self.client,Mock(),self.policy,self.inventory,self.ports,self.clock)
        case=data['case'];case['basis']['pause_receipt_digest']=digest({'target_date':TARGET,'owner':OWNER,'epoch':1})
        self.control(case={'basis':case['basis']})
        issue={'contract_version':'mina-rescue-v1','case_uuid':case['case_uuid'],'planned_prepare_uuid':case['planned_prepare_uuid'],'attempt_ordinal':case['attempt_ordinal'],'basis':case['basis']}
        # grantは本体側の合成運用者fixtureが発行済み。Agentによる発行ではない。
        with self.store.transaction() as db:
            db.execute("INSERT INTO rescue_jobs VALUES(?,?,?,?,?,?,NULL,'awaiting_operator',NULL)",(TARGET,OWNER,1,canonical(issue).decode(),case['case_uuid'],case['planned_prepare_uuid']))
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
        self.control(lose='meal-rescue-candidates')
        with self.assertRaises(ReplacementReadFailure):self.flow.prepare_candidate()
        key=self.flow.journal.job()['prepare_uuid'];self.flow.reconcile(key)
        self.assertEqual(self.flow.journal.job()['stage'],'ready');self.assertEqual(self.control()['calls']['analysis'],1)
        with self.assertRaises(ValueError):self.flow.prepare_candidate()
        self.assertEqual(self.control()['calls']['analysis'],1)

    def test_commit_response_loss_reconciles_sticky_saved(self):
        self.flow.prepare_candidate();self.control(lose='meal-rescues')
        with self.assertRaises(ReplacementReadFailure):self.flow.decide()
        with self.store.transaction() as db:key=db.execute("SELECT request_key FROM rescue_requests WHERE operation='meal_replace'").fetchone()[0]
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
        with self.store.transaction() as db:
            db.execute("INSERT INTO rescue_calls VALUES(?,?,'complete','json',?,?)",('manual-failed-review','review',
                canonical({'verdict':'fail','observations':['image_analysis_mismatch']}),stamp(self.now)))
        self.control(case={'state':'expired','authorization_revision':2},disabled=True)
        self.now+=timedelta(minutes=16);self.control(advance_ms=16*60000)
        with patch.dict('os.environ',{'MINA_SAME_DAY_RESCUE_ENABLED':'false'}):
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
        with self.store.transaction() as db:key=db.execute("SELECT request_key FROM rescue_requests WHERE operation='meal_replace'").fetchone()[0]
        self.now+=timedelta(minutes=10,seconds=1);self.control(advance_ms=601000,disabled=True)
        restarted=RescueFlow(self.store,self.client,Mock(),self.policy,self.inventory,Mock(),self.clock)
        with patch.dict('os.environ',{'MINA_SAME_DAY_RESCUE_ENABLED':'false'}):
            result=restarted.resume(key)
        self.assertEqual(result['request']['status'],'succeeded',json.dumps({'request':result['request'],'reason':result.get('reason'),'cooldown':result.get('cooldown')}))
        self.assertEqual(result['request']['stage'],'completed')
        self.assertEqual(self.control()['calls']['render'],1)
        self.assertTrue(restarted.is_saved());self.assertEqual(self.control()['history'],1)

    def test_saved_ogp_failure_is_not_replaced_again(self):
        self.flow.prepare_candidate();self.control(fail_render=True)
        try:self.flow.decide()
        except ReplacementReadFailure:pass
        self.assertTrue(self.flow.is_saved());self.assertEqual(self.control()['history'],1)
        with self.assertRaises(ValueError):self.flow.prepare_candidate()

if __name__=='__main__':unittest.main()
