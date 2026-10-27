"""固定Schemaの構造受入資料を合成値で作る。本番接続や実装受入ではない。"""
import copy
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent
SHA = 'baf76695c55298cd97fe8b24fc488c3c164585c11ae735986da380c4e52397ab'
raw = (ROOT / 'mina-rescue-v3.schema.json').read_bytes()
assert hashlib.sha256(raw).hexdigest() == SHA
schema = json.loads(raw)
Draft202012Validator.check_schema(schema)
fixtures = []

def add(name, definition, value, valid):
    validator = Draft202012Validator({'$defs': schema['$defs'], '$ref': '#/$defs/' + definition}, format_checker=FormatChecker())
    errors = list(validator.iter_errors(value))
    assert (not errors) == valid, (name, [e.message for e in errors])
    fixtures.append({'name': name, 'definition': definition, 'valid': valid, 'value': value})

review = {'target_slot': 'breakfast', 'target_result_version': 'rv1_' + 'a' * 64,
          'original_sha256': 'a' * 64, 'stored_sha256': 'b' * 64,
          'analysis_digest': 'ad1_' + 'c' * 64, 'ogp_input_digest': 'd' * 24,
          'reviewer_role': 'independent_reviewer', 'verdict': 'pass', 'quality_status': 'pass',
          'review_policy_version': 'synthetic-review-v1', 'reviewed_at': '2026-10-28T00:00:00.000Z'}
add('他食点検の正式24桁OGP', 'OtherMealReview', review, True)
with_digest = {**review, 'evidence_digest': 'e' * 64}
add('任意digest付き構造', 'OtherMealReview', with_digest, True)
for key, value, name in [('ogp_input_digest', 'd' * 64, '誤った64桁OGP拒否'),
                         ('review_policy_version', '', '空policy拒否'),
                         ('reviewer_role', 'generator', '生成者の点検拒否'),
                         ('verdict', 'uncertain', '不確実を合格にしない'),
                         ('reviewed_at', 'invalid', '不正時刻拒否'),
                         ('extra', 'ignored', '余剰キー拒否')]:
    add(name, 'OtherMealReview', {**review, key: value}, False)
for key in ('review_policy_version', 'stored_sha256'):
    v = dict(review); del v[key]
    add(key + '欠損拒否', 'OtherMealReview', v, False)

source = ROOT.parents[2] / 'tests/mina/rescue_schema_fixtures.json'
grant = copy.deepcopy(json.loads(source.read_text(encoding='utf-8-sig'))['cases'][0]['value'])
grant['contract_version'] = 'mina-rescue-v3'
basis = grant['basis']
basis.update(target_date='2026-10-28', target_slot='lunch',
             original_first_replan_started_at=None, original_hard_deadline_at=None,
             original_preparation_deadline_at='2026-10-27T11:00:00.000Z',
             not_after='2026-10-28T00:15:00.000Z', exception_kind='operator_scoped_meal_replacement',
             original_context_present=False, original_stored_image_sha256='b' * 64,
             target_analysis_digest='ad1_' + 'c' * 64, review_policy_version='synthetic-review-v1')
basis['initial_expected'].update(target_date='2026-10-28', target_slot='lunch')
basis['other_meals_reviews'] = [review, {**review, 'target_slot': 'dinner'}]
basis['other_meals_expected'] = []
for r in basis['other_meals_reviews']:
    canonical = json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    anchor = {k: r[k] for k in ('target_slot', 'target_result_version', 'original_sha256',
                               'stored_sha256', 'analysis_digest', 'ogp_input_digest')}
    anchor['independent_review_digest'] = hashlib.sha256(canonical).hexdigest()
    basis['other_meals_expected'].append(anchor)
add('合成昼食権限の構造', 'GrantIssueInput', grant, True)
for slot in ('breakfast', 'dinner'):
    v = copy.deepcopy(grant); v['basis']['target_slot'] = slot; v['basis']['initial_expected']['target_slot'] = slot
    # 日・枠の相互整合は構造Schemaだけでは証明できない。
    add(slot + '指定の構造のみ', 'GrantIssueInput', v, True)
for key, value, name in [('original_first_replan_started_at', '2026-10-28T00:00:00.000Z', 'context不在の時刻補完拒否'),
                         ('original_hard_deadline_at', '2026-10-28T00:15:00.000Z', 'context不在のhard補完拒否'),
                         ('target_slot', 'daily', '日次枠拒否'),
                         ('source_requests_terminal', False, '未終端要求拒否')]:
    v = copy.deepcopy(grant); v['basis'][key] = value
    add(name, 'GrantIssueInput', v, False)
v = copy.deepcopy(grant); v['contract_version'] = 'mina-rescue-v1'
add('旧契約混入拒否', 'GrantIssueInput', v, False)
v = copy.deepcopy(grant); v['basis']['original_context_present'] = True
add('既存contextの実時刻欠損拒否', 'GrantIssueInput', v, False)
v = copy.deepcopy(grant); v['attempt_ordinal'] = 3
add('第3試行拒否', 'GrantIssueInput', v, False)

packet = {'scope': '構造Schemaのみ。合成承認値は実行不可。時刻鮮度・digest一致・他食重複・管理権限・実HTTP・DB・公開排他・実画像は未受入。',
          'body_commit': '06f43311a7fc8efa0c15cd29960cd3944ecca05c', 'schema_sha256': SHA, 'cases': fixtures}
(ROOT / 'structural-fixtures.json').write_text(json.dumps(packet, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
(ROOT / 'STRUCTURAL_RESULT.json').write_text(json.dumps({'schema_sha256': SHA, 'cases': len(fixtures),
    'passed': len(fixtures), 'external_calls': 0, 'runtime_acceptance': False}, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'cases': len(fixtures), 'external_calls': 0, 'runtime_acceptance': False}))
