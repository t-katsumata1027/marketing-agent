"""v3の対象日・枠を明示的に束縛する。旧救済の固定契約は変更しない。"""
import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path
from jsonschema import Draft202012Validator
from .replacement_contract import Contract, digest
from .replacement_client import ReplacementClient, timestamp
from .clients import MealClient

VERSION = 'mina-rescue-v3'
SCHEMA_SHA = 'baf76695c55298cd97fe8b24fc488c3c164585c11ae735986da380c4e52397ab'
SCHEMA_PATH = Path(__file__).parent / 'contracts/rescue_v3/mina-rescue-v3.schema.json'
PREFIX = '/api/internal/mina/rescue-v3'


def target_scope(target, slot):
    if date.fromisoformat(target).isoformat() != target or slot not in ('breakfast', 'lunch', 'dinner'):
        raise ValueError('scoped_rescue_target_invalid')
    return target, slot


class ScopedRescueContract(Contract):
    def __init__(self, clock, target=None, slot=None):
        self.clock, self.target, self.slot = clock, target, slot
        if target is not None: target_scope(target, slot)
        if SCHEMA_PATH.is_symlink() or SCHEMA_PATH.parent.is_symlink():
            raise ValueError('scoped_rescue_schema_link')
        raw = SCHEMA_PATH.read_bytes()
        if hashlib.sha256(raw).hexdigest() != SCHEMA_SHA:
            raise ValueError('scoped_rescue_schema_changed')
        self.schema = json.loads(raw)
        Draft202012Validator.check_schema(self.schema)

    def case(self, c, *, active=False):
        self.validate('Case', c)
        b = c['basis']; target_scope(b['target_date'], b['target_slot'])
        first, hard = timestamp(c['first_activated_at']), timestamp(c['hard_deadline_at'])
        if (self.target is not None and (b['target_date'], b['target_slot']) != (self.target, self.slot)
                or b['initial_expected']['target_date'] != b['target_date']
                or b['initial_expected']['target_slot'] != b['target_slot']
                or c['created_at'] != c['first_activated_at']
                or hard != min(first + timedelta(minutes=15), timestamp(b['not_after']))
                or c['attempt_ordinal'] != len(b['baseline_consumed_ordinals']) + 1
                or c['used_ordinals'] != b['baseline_consumed_ordinals'] + [c['attempt_ordinal']]):
            raise ValueError('scoped_rescue_case_invalid')
        self.other_meals(b)
        if any(not 0<=(first-timestamp(r['reviewed_at'])).total_seconds()<=120 for r in b['other_meals_reviews']):
            raise ValueError('scoped_rescue_other_meals_stale')
        if c['state'] == 'completed':
            saved = timestamp(c['saved_at'])
            if not first <= saved <= hard or timestamp(c['ogp_finish_deadline_at']) != min(saved + timedelta(minutes=15), hard):
                raise ValueError('scoped_rescue_saved_deadline_invalid')
        if active and (c['state'] != 'active' or not first <= self.clock() < hard):
            raise ValueError('scoped_rescue_case_not_active')
        return c

    def other_meals(self, basis, *, issuing=False):
        slots = {'breakfast', 'lunch', 'dinner'} - {basis['target_slot']}
        reviews, anchors = basis['other_meals_reviews'], basis['other_meals_expected']
        if len(reviews) != 2 or len(anchors) != 2 or {r['target_slot'] for r in reviews} != slots or {a['target_slot'] for a in anchors} != slots:
            raise ValueError('scoped_rescue_other_meals_invalid')
        for review in reviews:
            self.validate('OtherMealReview', review)
            sha = digest({k: v for k, v in review.items() if k != 'evidence_digest'})
            anchor = next(a for a in anchors if a['target_slot'] == review['target_slot'])
            self.validate('OtherMealAnchor', anchor)
            if (review.get('evidence_digest', sha) != sha or anchor['independent_review_digest'] != sha
                    or review['review_policy_version'] != basis['review_policy_version']
                    or any(review[k] != anchor[k] for k in anchor if k != 'independent_review_digest')):
                raise ValueError('scoped_rescue_other_meals_changed')
            if issuing and not 0 <= (self.clock() - timestamp(review['reviewed_at'])).total_seconds() <= 120:
                raise ValueError('scoped_rescue_other_meals_stale')
        return reviews

    def candidate(self, c, ready=False):
        self.validate('Candidate', c)
        if c['candidate_version'] is None:
            if ready: raise ValueError('replacement_candidate_not_ready')
            return c
        e = c['prepared_preconditions']
        if (self.target is not None and (e['target_date'], e['target_slot']) != (self.target, self.slot)
                or digest(e, 'pc1_') != c['precondition_digest'] or self.analysis(c['analysis']) != c['analysis_digest']):
            raise ValueError('scoped_rescue_candidate_digest_invalid')
        original, stored = c['original'], c['stored']
        payload = {'spec': 'mina-meal-rescue-candidate-v3', 'rescue': c['rescue'],
            'target_date': e['target_date'], 'target_slot': e['target_slot'],
            **{k: c[k] for k in ('prepare_uuid', 'prepared_preconditions', 'prepared_publication_hold',
                'analysis_digest', 'normalized_comment', 'policy_version', 'hard_deadline_at', 'candidate_expires_at', 'attempt_ordinal')},
            'original_sha256': original['sha256'], 'stored_sha256': stored['sha256'],
            'original_mime': original['mime'], 'stored_mime': stored['mime'],
            'original_byte_length': original['byte_length'], 'stored_byte_length': stored['byte_length']}
        if digest(payload, 'cv1_') != c['candidate_version'] or stored['mime'] != 'image/webp':
            raise ValueError('scoped_rescue_candidate_version_invalid')
        for role in ('original', 'stored'):
            if c[role]['authenticated_path'] != f"{PREFIX}/candidates/{c['prepare_uuid']}/images/{role}":
                raise ValueError('scoped_rescue_image_path_invalid')
        if ready and (c['candidate_state'] != 'ready' or not c['effective_usable']
                or self.clock() >= min(timestamp(c['candidate_expires_at']), timestamp(c['hard_deadline_at']))):
            raise ValueError('replacement_candidate_not_usable')
        return c

    def decode(self, kind, value):
        if kind == 'case':
            self.validate('CaseResponse', value); self.fresh(value['freshness']); self.case(value['case'])
            return value
        result = super().decode(kind, value)
        self.case(result['rescue_case'])
        return result


class ScopedRescueClient(ReplacementClient):
    """発行・取消の管理経路を持たず、固定v3経路のみを使用する。"""
    def route(self, path):
        for old, new in (('meal-replacement-candidates', 'candidates'), ('meal-replacement-requests', 'requests'),
                         ('meal-replacement-resumes', 'resumes'), ('meal-replacements', 'commits'), ('requests', 'commits')):
            prefix = '/api/internal/mina/' + old
            if path == prefix or path.startswith(prefix + '/'):
                return PREFIX + '/' + new + path[len(prefix):]
        if path.startswith(PREFIX + '/cases/'): return path
        raise ValueError('scoped_rescue_route_invalid')

    def case(self, key):
        return self._json(PREFIX + '/cases/' + MealClient.request_key(key), 'case')

    def preconditions(self, target, case_uuid=None):
        from datetime import date
        if date.fromisoformat(target).isoformat() != target or case_uuid is None:
            raise ValueError('scoped_rescue_case_required')
        return self._json(PREFIX + '/cases/' + MealClient.request_key(case_uuid) + '/preconditions?date=' + target, 'preconditions')

    def authorize_write(self, operation, body):
        if not self.write_authorized or self.decoder is None or os.getenv('MINA_LIVE_WRITES', 'false').lower() != 'true':
            raise ValueError('scoped_rescue_write_disabled')
        if os.getenv('MINA_MEAL_REPLACEMENT_ENABLED', 'false').lower() != 'true':
            raise ValueError('scoped_rescue_write_disabled')
        if os.getenv('MINA_SCOPED_RESCUE_ENABLED', 'false').lower() == 'true' or operation == 'candidate_discard': return
        if operation == 'replacement_resume':
            value = json.loads(body); self.decoder.__self__.validate('ResumeInput', value)
            prior = self.request(value['original_request_uuid'], 'meal_replace'); c = prior['rescue_case']
            if (prior['request']['persistence'] == 'saved' and prior['request']['stage'] == 'paused'
                    and c['state'] == 'completed' and c['saved_commit_uuid'] == value['original_request_uuid']
                    and value['rescue'] == {'case_uuid': c['case_uuid'], 'authorization_digest': c['authorization_digest'],
                        'expected_authorization_revision': c['authorization_revision']}
                    and self.clock() < timestamp(c['ogp_finish_deadline_at'])): return
        raise ValueError('scoped_rescue_write_disabled')
