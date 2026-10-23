"""正式Schema・凍結履歴・実契約経路と配布物の同一性を外部書込みなしで検証する。"""
import copy,hashlib,json,shutil,tarfile,tempfile,unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock,patch

from jsonschema import Draft202012Validator
from scripts.build_mina_release import verify_archive_contract
from src.mina.replacement_contract import (
    CONTRACT_ROOT,FROZEN_DIRECTORY,FROZEN_HASHES,RUNTIME_SCHEMA,RUNTIME_SCHEMA_SHA256,
    Contract,FORMATS,digest,load_contract_resources)
from src.mina.replacement_runtime import build_coordinator
from src.mina.store import Store
from tests.mina import test_replacement_integration as integration_fixture
from tests.mina.test_replacement_integration import NOW,SAMPLES,TARGET,candidate_digest


class RuntimeSchemaTests(unittest.TestCase):
    def setUp(self):
        self.contract=Contract(lambda:NOW)

    def test_runtime_url_vectors_and_unmodified_historical_definitions(self):
        vectors=json.loads((Path(__file__).parent/'fixtures/replacement_runtime_urls.json').read_text(encoding='utf-8'))
        for url in vectors['valid']:
            with self.subTest(url=url):self.contract.validate('ShareURL',url)
        for url in vectors['invalid']:
            with self.subTest(url=url),self.assertRaises(Exception):self.contract.validate('ShareURL',url)
        frozen=json.loads((FROZEN_DIRECTORY/'CONTRACT.schema.json').read_text(encoding='utf-8'))
        history=json.loads((CONTRACT_ROOT/'replacement_runtime_20261018/mina-replace-v1.schema.json').read_bytes())
        for name,definition in frozen['$defs'].items():
            if name!='ShareURL':self.assertEqual(definition,history['$defs'][name])
        for name in ('PrepareInput','DiscardInput','CommitInput','Candidate','MealResult','DailyResult'):
            self.assertEqual(history['$defs'][name],self.contract.schema['$defs'][name])
        with self.assertRaises(Exception):
            Draft202012Validator({**frozen,'$ref':'#/$defs/ShareURL'},format_checker=FORMATS).validate(vectors['valid'][1])

    def test_runtime_and_frozen_resources_have_exact_independent_hashes(self):
        evidence,raw=load_contract_resources()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),RUNTIME_SCHEMA_SHA256)
        self.assertEqual(evidence['provider_commit'],'66be0089cbdfb49150ba9271a7e25899e1d81483')
        self.assertEqual(self.contract.schema['$id'],'urn:logeats:mina-meal-replacement:runtime:v1:20261018')
        for name,sha in FROZEN_HASHES.items():
            self.assertEqual(hashlib.sha256((FROZEN_DIRECTORY/name).read_bytes()).hexdigest(),sha)

    def test_missing_modified_or_frozen_substitute_schema_stops_before_network(self):
        for operation in ('missing','modified','frozen'):
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'contracts'
                shutil.copytree(CONTRACT_ROOT,root)
                path=root/RUNTIME_SCHEMA
                if operation=='missing':path.unlink()
                elif operation=='modified':path.write_bytes(path.read_bytes()+b' ')
                else:path.write_bytes((root/'replacement_v21/CONTRACT.schema.json').read_bytes())
                with patch('src.mina.replacement_contract.CONTRACT_ROOT',root),self.assertRaises(ValueError):
                    Contract(lambda:NOW)

    def test_precondition_and_candidate_versions_accept_symbols_without_rebasing(self):
        for suffix in ('a_b','a-b','a_b-c'):
            p=copy.deepcopy(SAMPLES['precondition_success'])
            p['freshness']=integration_fixture.fresh(NOW)
            own=p['meals'][2]
            own['expected']['target_share_url']='https://www.log-eats.com/s/'+suffix
            own['precondition_digest']=digest(own['expected'],'pc1_')
            self.contract.preconditions(p,TARGET)
            c=copy.deepcopy(SAMPLES['prepare_ready']['candidate'])
            c['prepared_preconditions']=copy.deepcopy(own['expected'])
            c['precondition_digest']=own['precondition_digest']
            c['hard_deadline_at']=integration_fixture.stamp(NOW+integration_fixture.timedelta(minutes=15))
            c['candidate_expires_at']=c['hard_deadline_at']
            c['candidate_version']=candidate_digest(c)
            self.contract.candidate(c,ready=True)
            broken=copy.deepcopy(c);broken['prepared_preconditions']['target_share_url']+='x'
            with self.assertRaises(ValueError):self.contract.candidate(broken,ready=True)

    def integration(self):
        case=integration_fixture.IntegrationTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        for meal in case.server.pre['meals']:
            meal['expected']['target_share_url']='https://www.log-eats.com/s/'+meal['expected']['target_slot']+'_a-b'
            meal['precondition_digest']=digest(meal['expected'],'pc1_')
        return case

    def test_symbol_urls_pass_real_prepare_discard_prepare_commit_and_lookups(self):
        case=self.integration()
        case.ports.review.side_effect=[{'verdict':'fail','observations':['image_analysis_mismatch']},{'verdict':'pass','observations':[]}]
        case.flow.begin(TARGET,'dinner',quality_hold=case.hold())
        case.flow.decide_candidate(TARGET)
        case.flow.reconcile(TARGET)
        case.flow.next_candidate(TARGET)
        case.flow.decide_candidate(TARGET)
        case.flow.reconcile(TARGET)
        self.assertEqual(case.flow.episode(TARGET)['stage'],'saved')
        self.assertEqual(sum(method=='POST' for method,_ in case.server.calls),4)
        with case.store.transaction() as db:
            wires=[json.loads(r[0]) for r in db.execute('SELECT metadata FROM replacement_wire')]
        for value in wires:
            if 'expected' in value:self.assertIn('_a-b',value['expected']['target_share_url'])
        # 結果型内の共有URLも正式契約で受ける。M1版文字列の再計算はしない。
        response=copy.deepcopy(case.server.requests[case.flow.episode(TARGET)['commit_uuid']])
        def replace_url(value):
            if isinstance(value,dict):return {k:replace_url(v) for k,v in value.items()}
            if isinstance(value,list):return [replace_url(v) for v in value]
            if isinstance(value,str) and value.startswith('https://www.log-eats.com/s/'):
                return 'https://www.log-eats.com/s/result_a-b'
            return value
        self.contract.decode('meal_replace',replace_url(response))

    def test_old_readiness_hash_is_rejected_and_new_hash_does_not_approve_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json';store=Store(Path(tmp)/'db')
            readiness={k:True for k in ('contract_verified','provider_qa_verified','external_inventory_adapter_verified',
                'budget_allocation_verified','provider_deployment_verified')}
            readiness.update(qa_evidence_digest='a'*64,deployment_commit='synthetic')
            with patch.dict('os.environ',{'MINA_MEAL_REPLACEMENT_ENABLED':'true','MINA_REPLACEMENT_AGENT_CONFIG':str(path)}):
                for sha,reason in ((FROZEN_HASHES['CONTRACT.schema.json'],'replacement_provider_not_verified'),
                                   (RUNTIME_SCHEMA_SHA256,'budget_unconfigured')):
                    readiness['contract_schema_sha256']=sha
                    path.write_text(json.dumps({'readiness':readiness,'policy':{}}),encoding='utf-8')
                    with self.assertRaisesRegex(ValueError,reason):
                        build_coordinator(store,'synthetic',{}, {},{},'commit','host',None,None,None,lambda:NOW)
                readiness['external_inventory_adapter_verified']=False
                path.write_text(json.dumps({'readiness':readiness,'policy':{}}),encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'integration_not_enabled'):
                    build_coordinator(store,'synthetic',{}, {},{},'commit','host',None,None,None,lambda:NOW)

    def archive(self,changes=None,missing=None):
        evidence,_=load_contract_resources()
        buffer=BytesIO()
        with tarfile.open(fileobj=buffer,mode='w') as archive:
            for entry in (evidence['runtime_schema'],*evidence['frozen_history'].values()):
                name=entry['path']
                if name==missing:continue
                raw=(Path(__file__).resolve().parents[2]/name).read_bytes()
                raw=(changes or {}).get(name,raw)
                member=tarfile.TarInfo(name);member.size=len(raw)
                archive.addfile(member,BytesIO(raw))
        buffer.seek(0)
        return evidence,buffer

    def test_archive_requires_both_runtime_and_historical_exact_bytes(self):
        evidence,buffer=self.archive()
        with tarfile.open(fileobj=buffer) as archive:verify_archive_contract(archive,evidence)
        for entry in (evidence['runtime_schema'],*evidence['frozen_history'].values()):
            for changed,missing in (({entry['path']:b'changed'},None),({},entry['path'])):
                expected,raw=self.archive(changes=changed,missing=missing)
                with tarfile.open(fileobj=raw) as archive,self.assertRaises(ValueError):verify_archive_contract(archive,expected)


if __name__=='__main__':unittest.main()
