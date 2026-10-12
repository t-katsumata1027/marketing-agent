"""最終引継ぎに旧版・代替証跡・本番QA流用・秘密値を採用しないことを確認する。"""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.mina.handoff_packet import BASE_EVIDENCE, PRODUCTION_EVIDENCE, inspect_handoff


class HandoffPacketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.contract = Path(self.tmp.name)/'contract.md'
        self.contract.write_text('試験用契約', encoding='utf-8')
        self.commit = 'a'*40
        self.packet = {'format':'mina-handoff-v1','contract_version':'mina-write-v1',
            'code_commit':self.commit,'contract_sha256':hashlib.sha256(self.contract.read_bytes()).hexdigest(),
            'environment':'isolated_qa','api_base_url':'https://isolated.example.test',
            'credential_reference':{'provider':'doppler','project':'logeats-qa','config':'qa',
                                    'secret_name':'MINA_AGENT_API_TOKEN'},
            'runbook_reference':'試験用の停止復旧文書',
            'evidence':{name:{'code_commit':self.commit,'result':'passed','kind':'real',
                             'reference':'合成証跡・実受入ではない'} for name in BASE_EVIDENCE}}

    def inspect(self, packet=None):
        return inspect_handoff(self.packet if packet is None else packet, self.contract, self.commit)

    def test_structure_success_never_grants_start_or_claims_verified_contents(self):
        result = self.inspect()
        self.assertTrue(result['packet_structure_valid'])
        self.assertFalse(result['production_start_allowed'])
        self.assertFalse(result['external_calls_performed'])
        self.assertFalse(result['evidence_contents_verified'])

    def test_each_missing_old_head_mock_and_failed_evidence_is_rejected(self):
        for name in BASE_EVIDENCE:
            for changes in ({'kind':'mock'},{'code_commit':'b'*40},{'result':'failed'}):
                packet=copy.deepcopy(self.packet)
                packet['evidence'][name].update(changes)
                self.assertIn(name,self.inspect(packet)['missing_real_evidence'])
        packet=copy.deepcopy(self.packet)
        packet['evidence'].pop('cleanup')
        self.assertFalse(self.inspect(packet)['packet_structure_valid'])

    def test_production_requires_release_activation_and_cleanup_evidence(self):
        self.packet['environment']='production'
        self.packet['api_base_url']='https://www.log-eats.com'
        self.assertEqual(self.inspect()['missing_real_evidence'],list(PRODUCTION_EVIDENCE))
        for name in PRODUCTION_EVIDENCE:
            self.packet['evidence'][name]={'code_commit':self.commit,'result':'passed','kind':'real','reference':'合成記録'}
        self.assertTrue(self.inspect()['packet_structure_valid'])
        self.assertFalse(self.inspect()['production_start_allowed'])

    def test_secret_fields_urls_qa_production_settings_and_contract_changes_rejected(self):
        for changes in ({'api_base_url':'https://www.log-eats.com'},
                        {'api_base_url':'https://user:private-token@example.test'},
                        {'api_base_url':'https://example.test?token=private-token'},
                        {'token':'private-token'}, {'code_commit':'b'*40}):
            result=self.inspect({**self.packet,**changes})
            self.assertFalse(result['packet_structure_valid'])
            self.assertNotIn('private-token',json.dumps(result))
        packet=copy.deepcopy(self.packet)
        packet['credential_reference'].update(project='marketing-agent',config='dev')
        self.assertIn('production_credentials_in_qa',self.inspect(packet)['problems'])
        self.contract.write_text('変更',encoding='utf-8')
        self.assertIn('contract_hash_mismatch',self.inspect()['problems'])

    def test_cli_does_not_construct_database_or_external_clients(self):
        from src.mina.__main__ import main
        packet=Path(self.tmp.name)/'packet.json'
        packet.write_text(json.dumps(self.packet))
        database=Path(self.tmp.name)/'missing.sqlite3'
        with patch('sys.argv',['mina','inspect-handoff','--manifest',str(packet),'--contract-file',str(self.contract),
                              '--producer-commit',self.commit,'--database',str(database)]), \
             patch('src.mina.__main__.Store') as store, patch('builtins.print'):
            main()
        store.assert_not_called()
        self.assertFalse(database.exists())
