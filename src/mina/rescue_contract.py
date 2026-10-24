"""10/24限定救済の固定契約。通常契約の受理範囲や期限を変更しない。"""
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from jsonschema import Draft202012Validator
from .replacement_contract import Contract, FORMATS, digest
from .replacement_client import timestamp, ReplacementClient

SCHEMA_SHA = '1532cae8d201be23fbbee8ee1a8bd11d3740a527cf5a0e188734ff65b5c36082'
SCHEMA_PATH = Path(__file__).parent/'contracts/rescue_v1/mina-rescue-v1.schema.json'
TARGET = '2026-10-24'
POLICY = 'mina_gemini_waived_20261023_v1'
BUDGET = '244918faac42ea4b91b96116c75b251c9361b9a1389a88503b7ba6aedb07e79b'
APPROVAL = '8747bbcffcedfe18baa09e59ea0d25985d3c8472c47bad31e93554cb305d6083'


def resources():
    if SCHEMA_PATH.is_symlink() or SCHEMA_PATH.parent.is_symlink():
        raise ValueError('rescue_schema_link')
    raw = SCHEMA_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SCHEMA_SHA:
        raise ValueError('rescue_schema_changed')
    return raw


class RescueContract(Contract):
    def __init__(self, clock):
        self.clock = clock
        self.schema = json.loads(resources())
        Draft202012Validator.check_schema(self.schema)

    def case(self, value, *, active=False):
        self.validate('Case', value)
        basis = value['basis']
        first = timestamp(value['first_activated_at'])
        hard = timestamp(value['hard_deadline_at'])
        if (basis['target_date'] != TARGET or basis['target_slot'] != 'breakfast'
                or basis['policy_version'] != POLICY or basis['budget_policy_digest'] != BUDGET
                or basis['cost_approval_digest'] != APPROVAL
                or value['created_at'] != value['first_activated_at']
                or hard != min(timestamp(basis['not_after']), first + timedelta(minutes=15))
                or value['used_ordinals'] != basis['baseline_consumed_ordinals'] + [value['attempt_ordinal']]
                or value['attempt_ordinal'] != len(basis['baseline_consumed_ordinals']) + 1
                or value['attempt_ordinal'] > 2):
            raise ValueError('rescue_case_invalid')
        if value['state']=='completed':
            saved=timestamp(value['saved_at'])
            if not first<=saved<=hard or timestamp(value['ogp_finish_deadline_at'])!=min(saved+timedelta(minutes=15),hard):
                raise ValueError('rescue_saved_deadline_invalid')
        if active and (value['state'] != 'active' or not first <= self.clock() < hard):
            raise ValueError('rescue_case_not_active')
        return value

    def candidate(self, c, ready=False):
        self.validate('Candidate', c)
        if c['candidate_version'] is None:
            if ready: raise ValueError('replacement_candidate_not_ready')
            return c
        e = c['prepared_preconditions']
        if (e['target_date'] != TARGET or e['target_slot'] != 'breakfast'
                or digest(e, 'pc1_') != c['precondition_digest']
                or self.analysis(c['analysis']) != c['analysis_digest']):
            raise ValueError('rescue_candidate_digest_invalid')
        original, stored = c['original'], c['stored']
        payload = {'spec':'mina-meal-rescue-candidate-v1', 'rescue':c['rescue'],
            'target_date':e['target_date'], 'target_slot':e['target_slot'],
            **{k:c[k] for k in ('prepare_uuid','prepared_preconditions','prepared_publication_hold',
                'analysis_digest','normalized_comment','policy_version','hard_deadline_at',
                'candidate_expires_at','attempt_ordinal')},
            'original_sha256':original['sha256'], 'stored_sha256':stored['sha256'],
            'original_mime':original['mime'], 'stored_mime':stored['mime'],
            'original_byte_length':original['byte_length'], 'stored_byte_length':stored['byte_length']}
        if digest(payload, 'cv1_') != c['candidate_version'] or stored['mime'] != 'image/webp':
            raise ValueError('rescue_candidate_version_invalid')
        for role in ('original','stored'):
            if c[role]['authenticated_path'] != f"/api/internal/mina/meal-rescue-candidates/{c['prepare_uuid']}/images/{role}":
                raise ValueError('rescue_image_path_invalid')
        if ready and (c['candidate_state'] != 'ready' or not c['effective_usable']
                or self.clock() >= min(timestamp(c['candidate_expires_at']), timestamp(c['hard_deadline_at']))):
            raise ValueError('rescue_candidate_not_usable')
        return c

    def decode(self, kind, value):
        if kind == 'case':
            self.validate('CaseResponse', value)
            self.fresh(value['freshness'])
            self.case(value['case'])
            return value
        result = super().decode(kind, value)
        self.case(result['rescue_case'])
        return result


class RescueClient(ReplacementClient):
    """発行・取消メソッドを持たない。専用経路だけに接続する。"""
    def route(self, path):
        if path.startswith('/api/internal/mina/requests/'):
            return path.replace('/api/internal/mina/requests/', '/api/internal/mina/meal-rescues/', 1)
        return path.replace('meal-replacement-candidates', 'meal-rescue-candidates').replace(
            'meal-replacement-requests', 'meal-rescue-requests').replace(
            'meal-replacement-resumes', 'meal-rescue-resumes').replace('meal-replacements', 'meal-rescues')

    def case(self, key):
        from .clients import MealClient
        return self._json('/api/internal/mina/meal-rescue-cases/' + MealClient.request_key(key), 'case')

    def preconditions(self, target, case_uuid=None):
        from .clients import MealClient
        if target != TARGET or case_uuid is None:
            raise ValueError('rescue_case_required')
        return self._json('/api/internal/mina/meal-rescue-cases/' + MealClient.request_key(case_uuid)
                          + '/preconditions?date=' + TARGET, 'preconditions')

    def authorize_write(self, operation, body):
        import os
        if not self.write_authorized or self.decoder is None or os.getenv('MINA_LIVE_WRITES','false').lower()!='true':
            raise ValueError('rescue_write_disabled')
        enabled=all(os.getenv(k,'false').lower()=='true' for k in ('MINA_SAME_DAY_RESCUE_ENABLED','MINA_MEAL_REPLACEMENT_ENABLED'))
        if enabled or operation=='candidate_discard': return
        if operation=='replacement_resume':
            value=json.loads(body);self.decoder.__self__.validate('ResumeInput',value)
            original=self.request(value['original_request_uuid'],'meal_replace')
            case=original['rescue_case']
            if (original['request']['persistence']=='saved' and original['request']['stage']=='paused'
                    and case['state']=='completed' and case['saved_commit_uuid']==value['original_request_uuid']
                    and value['rescue']=={'case_uuid':case['case_uuid'],'authorization_digest':case['authorization_digest'],
                        'expected_authorization_revision':case['authorization_revision']}
                    and self.clock()<timestamp(case['ogp_finish_deadline_at'])): return
        raise ValueError('rescue_write_disabled')
