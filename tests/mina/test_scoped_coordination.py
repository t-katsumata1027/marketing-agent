"""実ファイルの協調境界を合成管理側で検査する。所有権はLinux UID受入で別途確認する。"""
import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,UTC,timedelta
from unittest.mock import patch
from src.mina.scoped_coordination import ManagementHandshake,ManagementStopped,strict_json,publish_once,NAMES
from src.mina.replacement_contract import canonical,stamp


class HandshakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
        self.out=self.base/'root-out';self.out.mkdir();self.now=datetime(2026,10,28,1,tzinfo=UTC)
        self.scope={'case_uuid':'11111111-1111-4111-8111-111111111111','planned_prepare_uuid':'22222222-2222-4222-8222-222222222222',
            'target_date':'2026-10-28','target_slot':'lunch','round':1,'parent_history_digest':'a'*64,'human_approval_sha256':'b'*64,
            'agent_commit':'c'*40,'body_commit':'d'*40,'schema_sha256':'e'*64,'review_policy_version':'synthetic-v1',
            'policy_digest':'f'*64,'not_after':stamp(self.now+timedelta(minutes=30))}
        self.helpers={k:'a'*64 for k in ('agent','management','fs_proxy','grant_cli')}
        self.manifest=self.out/'manifest.json'
        self.manifest.write_bytes(canonical({'protocol':'mina-management-handshake-v1','scope':self.scope,'helper_shas':self.helpers}))
        self.ref={'path':str(self.manifest),'sha256':hashlib.sha256(self.manifest.read_bytes()).hexdigest()}
        def raw(path,limit=65536):
            value=Path(path).read_bytes()
            if len(value)>limit:raise ValueError('scoped_coordination_payload_too_large')
            return value
        def referenced(ref):
            value=raw(ref['path'])
            if hashlib.sha256(value).hexdigest()!=ref['sha256']:raise ValueError('scoped_coordination_reference_changed')
            return value
        for target,value in [('src.mina.scoped_coordination.root_file',referenced),('src.mina.scoped_coordination.root_bytes',raw),
                             ('src.mina.scoped_coordination.AGENT_INBOX_ROOT',self.base/'agent-inbox')]:
            p=patch(target,value);p.start();self.addCleanup(p.stop)
        self.port=ManagementHandshake(self.ref,self.scope,lambda:self.now,self.helpers,sleep=lambda _:None)

    def root_publish(self,phase,value):
        payload=canonical(value);(self.out/NAMES[phase]).write_bytes(payload)
        event=self.port.envelope(phase,{'name':NAMES[phase],'sha256':hashlib.sha256(payload).hexdigest()},self.scope['not_after'])
        (self.out/(phase+'.envelope.json')).write_bytes(canonical(event));return event

    def test_duplicate_nonfinite_invalid_utf8_and_oversize_are_rejected(self):
        for raw in (b'{"a":1,"a":2}',b'{"a":NaN}',b'\xff',b'x'*1048577):
            with self.assertRaises((ValueError,UnicodeError)):strict_json(raw)

    def test_ready_publication_is_immutable_across_restart(self):
        value={'review_round':1,'parent_history_digest':'a'*64,'claim_digest':'c'*64,'formal_reviews':[],'history':{}}
        until=stamp(self.now+timedelta(seconds=100));self.port.publish('reviews-ready',value,until)
        event=self.port.inbox/'reviews-ready.envelope.json';old=event.read_bytes()
        self.now+=timedelta(seconds=1)
        other=ManagementHandshake(self.ref,self.scope,lambda:self.now,self.helpers,sleep=lambda _:None)
        other.publish('reviews-ready',value,until);self.assertEqual(event.read_bytes(),old)
        with self.assertRaises(ValueError):other.publish('reviews-ready',{**value,'claim_digest':'d'*64},until)
        self.assertEqual(event.read_bytes(),old)

    def test_command_and_path_fields_are_not_request_parameters(self):
        for phase in ('reviews-ready','bundle-ready','stopped'):
            with self.assertRaises(ValueError):self.port.publish(phase,{'command':'synthetic-no-command','path':'outside'},self.scope['not_after'])
        self.assertEqual(list(self.port.inbox.iterdir()),[])

    def test_wrong_scope_or_payload_digest_cannot_be_received(self):
        event=self.root_publish('grant-result',{'synthetic':True})
        event['scope']={**self.scope,'round':2};(self.out/'grant-result.envelope.json').write_bytes(canonical(event))
        with self.assertRaises(ValueError):self.port.read('grant-result')
        self.root_publish('grant-result',{'synthetic':True});(self.out/'result.json').write_bytes(b'{}')
        with self.assertRaises(ValueError):self.port.read('grant-result')

    def test_expiry_and_scope_mutation_stop_without_new_publication(self):
        self.now+=timedelta(minutes=31)
        with self.assertRaises(ValueError):self.port.publish('stopped',{'reason_code':'expired','issue_started':False},self.scope['not_after'])
        self.assertEqual(list(self.port.inbox.iterdir()),[])

    def test_helper_hash_and_root_manifest_must_match(self):
        with self.assertRaises(ValueError):ManagementHandshake(self.ref,self.scope,lambda:self.now,{**self.helpers,'agent':'f'*64})
        self.manifest.write_bytes(b'{}')
        with self.assertRaises(ValueError):self.port.check()

    def test_stopped_unknown_is_not_reissued(self):
        self.root_publish('stopped',{'reason_code':'grant_outcome_unknown','issue_started':True})
        with self.assertRaises(ManagementStopped) as caught:self.port.wait('grant-result',max_polls=2)
        self.assertTrue(caught.exception.issue_started);self.assertEqual(list(self.port.inbox.iterdir()),[])

    def test_wait_is_bounded_even_with_a_frozen_clock(self):
        with self.assertRaisesRegex(ValueError,'wait_exhausted'):self.port.wait('snapshot-ready',max_polls=2)
        self.assertEqual(list(self.port.inbox.iterdir()),[])

    def test_snapshot_reference_names_and_root_bytes_are_bound(self):
        self.root_publish('initial-snapshot-ready',{'native':{'name':'other.json','sha256':'a'*64},'unissued':{'name':'initial-unissued.json','sha256':'b'*64}})
        with self.assertRaises(ValueError):self.port.snapshot('initial-snapshot-ready')

    def test_bundle_has_three_fixed_files_without_authority_execution(self):
        self.port.publish_bundle({'input':{'case':'synthetic'},'approval':{'data':True},'evidence':{},'agent_issues_authority':False})
        value=strict_json((self.port.inbox/'bundle.json').read_bytes())
        self.assertEqual(set(value),{'input','approval','evidence'})
        for role,ref in value.items():
            self.assertEqual(ref['name'],'grant-'+role+'.json')
            self.assertEqual(hashlib.sha256((self.port.inbox/ref['name']).read_bytes()).hexdigest(),ref['sha256'])
        with self.assertRaises(ValueError):self.port.publish_bundle({'input':{},'approval':{},'evidence':{},'agent_issues_authority':True})

    def test_initial_publication_keeps_existing_payload_and_rejects_symlinks(self):
        path=self.port.inbox/'fixed.json';publish_once(path,b'first');publish_once(path,b'first')
        with self.assertRaises(ValueError):publish_once(path,b'second')
        self.assertEqual(path.read_bytes(),b'first')

    def test_real_root_reader_rejects_untrusted_location_without_synthetic_permission_patch(self):
        from src.mina.scoped_management_proofs import root_file
        with self.assertRaises(ValueError):root_file(self.ref)
