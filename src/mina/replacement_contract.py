"""本番実行契約の型・正規化・候補の意味を検証し、凍結資料を履歴として保持する。"""
import hashlib
import json
import math
from datetime import UTC,timedelta
from pathlib import Path
import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from .replacement_client import timestamp, ReplacementReadFailure

SLOTS = ('breakfast','lunch','dinner')
CONTRACT_ROOT = Path(__file__).parent/'contracts'
FROZEN_DIRECTORY = CONTRACT_ROOT/'replacement_v21'
RUNTIME_SCHEMA = 'replacement_runtime/mina-replace-v1.schema.json'
RUNTIME_SCHEMA_SHA256 = '4f8d2be8f47a741ff8578a25c29bd6422cf88ebd817f8450339eaee76216f6e2'
PROVIDER_COMMIT = '66be0089cbdfb49150ba9271a7e25899e1d81483'
PROVIDER_SCHEMA_PATH = 'docs/marketing/contracts/mina-replace-v1.schema.json'
FROZEN_HASHES = {'CONTRACT.schema.json':'8b5fc6ecce9a9b406e7197f60c6ae49cb8f94edd9702c3b2db33babe78396090',
          'FIXTURES.json':'6561ad1dbdeb755e9a6fca55f2981355dee1b879f404f069489e8c927ecf52c7'}
FORMATS=FormatChecker()

def load_contract_resources(base=None):
    """通信せず同梱実バイトの固定SHAを検証する。履歴やネット取得へ退避しない。"""
    base=Path(base) if base is not None else CONTRACT_ROOT
    history_schema='replacement_runtime_20261018/mina-replace-v1.schema.json'
    history_sha='2c2a44cc3830ef07bd0666182915b0e5e4bbab11239bb6aec6745f0d9d37a4f6'
    hashes={RUNTIME_SCHEMA:RUNTIME_SCHEMA_SHA256,history_schema:history_sha,
            **{'replacement_v21/'+name:sha for name,sha in FROZEN_HASHES.items()}}
    loaded={}
    for relative,sha in hashes.items():
        path=base/relative
        try:
            if any(p.is_symlink() for p in (path,path.parent,base)):
                raise ValueError('replacement_contract_resource_link')
            raw=path.read_bytes()
        except OSError:
            raise ValueError('replacement_contract_resource_missing') from None
        if hashlib.sha256(raw).hexdigest()!=sha:
            raise ValueError('replacement_contract_hash_changed')
        loaded[relative]=raw
    prefix='src/mina/contracts/'
    evidence={'contract_version':'mina-replace-v1','provider_commit':PROVIDER_COMMIT,'provider_path':PROVIDER_SCHEMA_PATH,
        'runtime_schema':{'path':prefix+RUNTIME_SCHEMA,'sha256':RUNTIME_SCHEMA_SHA256},
        'frozen_history':{name:{'path':prefix+'replacement_v21/'+name,'sha256':sha} for name,sha in FROZEN_HASHES.items()}}
    evidence['frozen_history']['runtime-20261018']={'path':prefix+history_schema,'sha256':history_sha}
    return evidence,loaded[RUNTIME_SCHEMA]

@FORMATS.checks('date-time',raises=(ValueError,TypeError))
def valid_time(value):
    timestamp(value)
    return True

def canonical(value):
    return rfc8785.dumps(value)

def digest(value, prefix=''):
    return prefix+hashlib.sha256(canonical(value)).hexdigest()

def source_digest(value):
    """既存準備期限の経過表示を、完成済み投稿の内容版へ混ぜない。policyの版は保持する。"""
    return digest({**{k:value[k] for k in ('date','state_version','meals','summary','daily_share')},
        'policy':{k:value['policy'][k] for k in ('policy_version','budget_policy_digest')}})

def stamp(value):
    if value.tzinfo is None:raise ValueError('replacement_timestamp_invalid')
    return value.astimezone(UTC).isoformat(timespec='milliseconds').replace('+00:00','Z')

class Contract:
    def __init__(self, clock):
        self.clock=clock
        self.resources,raw=load_contract_resources()
        self.schema=json.loads(raw)
        Draft202012Validator.check_schema(self.schema)

    def validate(self, name, value):
        schema={**self.schema,'$ref':'#/$defs/'+name}
        Draft202012Validator(schema,format_checker=FORMATS).validate(value)
        canonical(value)
        return value

    def fresh(self, value, started=None):
        self.validate('Freshness',value)
        now=self.clock();begin=timestamp(value['read_started_at']);checked=timestamp(value['checked_at']);until=timestamp(value['valid_until'])
        if not begin<=checked<=now<until or until>checked+timedelta(seconds=120) or (now-(started or begin)).total_seconds()>120:
            raise ValueError('replacement_freshness_invalid')

    def analysis(self, value):
        self.validate('Analysis',value)
        for field,summary in zip(('calories','protein','fat','carbs'),('totalCalories','totalProtein','totalFat','totalCarbs')):
            total=math.fsum(food[field] for food in value['foods'])
            if not math.isclose(total,value['summary'][summary],abs_tol=1e-8,rel_tol=1e-12):
                raise ValueError('replacement_analysis_sum_invalid')
        return digest(value,'ad1_')

    def preconditions(self, value, target):
        self.validate('PreconditionResponse',value);self.fresh(value['freshness'])
        if value['date']!=target or {m['expected']['target_slot'] for m in value['meals']}!=set(SLOTS):
            raise ValueError('replacement_precondition_target_invalid')
        for meal in value['meals']:
            e=meal['expected']
            if e['target_date']!=target or e['state_version']!=value['state_version'] or digest(e,'pc1_')!=meal['precondition_digest'] or self.analysis(meal['analysis'])!=meal['analysis_digest'] or meal['share_status']!='active':
                raise ValueError('replacement_precondition_invalid')
        for field in ('totalCalories','totalProtein','totalFat','totalCarbs'):
            if not math.isclose(math.fsum(m['analysis']['summary'][field] for m in value['meals']),value['summary'][field],abs_tol=1e-8):
                raise ValueError('replacement_day_sum_invalid')
        return value

    def candidate(self, c, ready=False):
        self.validate('Candidate',c)
        if c['candidate_version'] is None:
            if ready:raise ValueError('replacement_candidate_not_ready')
            return c
        e=c['prepared_preconditions'];original=c['original'];stored=c['stored']
        if digest(e,'pc1_')!=c['precondition_digest'] or self.analysis(c['analysis'])!=c['analysis_digest']:
            raise ValueError('replacement_candidate_digest_invalid')
        payload={'spec':'mina-meal-replacement-candidate-v1','target_date':e['target_date'],'target_slot':e['target_slot'],
            **{k:c[k] for k in ('prepare_uuid','prepared_preconditions','prepared_publication_hold','analysis_digest','normalized_comment','policy_version','hard_deadline_at','candidate_expires_at','attempt_ordinal')},
            'original_sha256':original['sha256'],'stored_sha256':stored['sha256'],'original_mime':original['mime'],'stored_mime':stored['mime'],
            'original_byte_length':original['byte_length'],'stored_byte_length':stored['byte_length']}
        if digest(payload,'cv1_')!=c['candidate_version'] or stored['mime']!='image/webp':
            raise ValueError('replacement_candidate_version_invalid')
        for role,info in (('original',original),('stored',stored)):
            if info['authenticated_path']!=f"/api/internal/mina/meal-replacement-candidates/{c['prepare_uuid']}/images/{role}":raise ValueError('replacement_image_path_invalid')
        if ready and (c['candidate_state']!='ready' or not c['effective_usable'] or not self.clock()<min(timestamp(c['candidate_expires_at']),timestamp(c['hard_deadline_at']))):
            raise ValueError('replacement_candidate_not_usable')
        return c

    def decode(self, kind, value):
        name={'preconditions':'PreconditionResponse','candidate':'CandidateResponse','candidate_prepare':'ControlLookupResponse',
              'candidate_discard':'ControlLookupResponse','meal_replace':'CommitResponse','replacement_resume':'ResumeResponse'}[kind]
        if 'error' in value:
            self.validate('Error',value)
            from .replacement_control import ReplacementPaused
            raise ReplacementPaused(value)
        self.validate(name,value);self.fresh(value['freshness'])
        if value.get('candidate'):self.candidate(value['candidate'])
        return value

    def nutrition(self, p, c):
        self.candidate(c,ready=True);self.preconditions(p,c['prepared_preconditions']['target_date'])
        own=next(m for m in p['meals'] if m['expected']['target_slot']==c['prepared_preconditions']['target_slot'])
        if own['expected']!=c['prepared_preconditions']:raise ValueError('replacement_preconditions_changed')
        others=[next(m for m in p['meals'] if m['expected']['target_slot']==slot) for slot in SLOTS if slot!=own['expected']['target_slot']]
        other_total=math.fsum(m['analysis']['summary']['totalCalories'] for m in others);candidate_total=c['analysis']['summary']['totalCalories'];total=other_total+candidate_total
        if not 1800<=total<=2000:raise ValueError('nutrition_budget_failed')
        value={'precondition_digest':c['precondition_digest'],'candidate_analysis_digest':c['analysis_digest'],
            'other_meals_analysis_digests':[m['analysis_digest'] for m in others], 'other_meals_total_calories':other_total,
            'candidate_total_calories':candidate_total,'total_calories':total,'min_calories':1800,'max_calories':2000,'checked_at':stamp(self.clock())}
        value['evidence_digest']=digest(value);self.validate('NutritionCheck',value);return value
