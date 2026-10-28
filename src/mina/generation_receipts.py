"""生成の完了原本と純粋な検査拒否を結び付ける。通信不明を成功へ変換しない。"""
import contextvars
import hashlib
import json
import re
from contextlib import contextmanager
from .preparation_status import QualityHold

CURRENT = contextvars.ContextVar('preparation_generation_receipt', default=None)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@contextmanager
def operation(target, step, input_digest):
    token = CURRENT.set({'target_date': target, 'step': step, 'input_digest': input_digest, 'returns': []})
    try:
        yield
    finally:
        CURRENT.reset(token)


def invocation(job, schema, transmitted):
    """送信前に相関だけを記録する。送信本文・秘密値を保存しない。"""
    current = CURRENT.get()
    return {'spec': 'generation-invocation-v1', 'job': job,
        'preparation': None if current is None else {k: current[k] for k in ('target_date', 'step', 'input_digest')},
        'schema_digest': digest(schema), 'stdin_sha256': hashlib.sha256(transmitted.encode()).hexdigest()}


def completed(root, schema, value, receipt, returncode, writable):
    """実executorの終了値と実ファイルを確認した箇所からだけ呼ぶ。"""
    current = CURRENT.get()
    if current is None or writable:
        return
    result_path, state_path = root / 'result.json', root / 'state.json'
    if (returncode != 0 or receipt['job']!=root.name or receipt['schema_digest']!=digest(schema)
            or receipt['preparation']!={k:current[k] for k in ('target_date','step','input_digest')}
            or result_path.is_symlink() or state_path.is_symlink()
            or json.loads(state_path.read_bytes()) != {'state': 'complete', 'reason': None}):
        raise ValueError('生成完了原本を確認できません')
    raw = result_path.read_bytes()
    if digest(json.loads(raw.decode('utf-8-sig'))) != digest(value):
        raise ValueError('生成結果の実体が変わっています')
    current['returns'].append({'spec': 'completed-generation-v1', 'producer': 'codex_cli',
        'job': root.name, 'returncode': 0, 'schema_digest': digest(schema),
        'result_digest': digest(value), 'result_raw_sha256': hashlib.sha256(raw).hexdigest(),
        'invocation_digest': digest(receipt)})


class GeneratedPlanRejected(QualityHold):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__('完了した生成案を保存前の検査で拒否しました', receipt['step'], receipt['reason_codes'])


def reject_plan(value, schema, reason):
    current = CURRENT.get()
    if (current is None or not re.fullmatch(r'meal_replan:(breakfast|lunch|dinner):[12]', current['step'])
            or not current['returns']):
        raise ValueError('生成案の拒否と完了原本を関連付けられません')
    returned = current['returns'][-1]
    if returned['result_digest'] != digest(value) or returned['schema_digest'] != digest(schema):
        raise ValueError('生成案の完了原本と検査入力が一致しません')
    receipt = {'spec': 'known-rejected-plan-v1', 'target_date': current['target_date'],
        'step': current['step'], 'input_digest': current['input_digest'],
        'reason_codes': [reason], 'external': returned}
    receipt['receipt_digest'] = digest(receipt)
    raise GeneratedPlanRejected(receipt)


def rejection(row):
    """既知拒否も一回として消費する。採用済みdraft・画像roundは作らない。"""
    if row['state'] != 'known_rejected':
        return None
    value = json.loads(row['result'] or '{}')
    required = {'spec', 'target_date', 'step', 'input_digest', 'reason_codes', 'external', 'receipt_digest'}
    if (set(value) != required or value['spec'] != 'known-rejected-plan-v1'
            or not re.fullmatch(r'meal_replan:(breakfast|lunch|dinner):[12]',value['step'])
            or value['target_date'] != row['target_date'] or value['step'] != row['step']
            or value['input_digest'] != row['digest']
            or value['reason_codes'] not in (['daily_energy_out_of_range'], ['generated_plan_invalid'])
            or value['receipt_digest'] != digest({k: v for k, v in value.items() if k != 'receipt_digest'})):
        raise ValueError('既知拒否の消費原本を確認できません')
    ext = value['external']
    if (set(ext) != {'spec', 'producer', 'job', 'returncode', 'schema_digest', 'result_digest', 'result_raw_sha256', 'invocation_digest'}
            or ext['spec'] != 'completed-generation-v1' or ext['producer'] != 'codex_cli'
            or type(ext['returncode']) is not int or ext['returncode'] != 0
            or not re.fullmatch('[a-f0-9]{32}', ext['job'])
            or any(not re.fullmatch('[a-f0-9]{64}', ext[k]) for k in ('schema_digest', 'result_digest', 'result_raw_sha256', 'invocation_digest'))):
        raise ValueError('既知拒否の外部完了原本を確認できません')
    return value
