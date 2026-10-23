"""全日・全枠共通の照会待機と明示resume。GET巡回から書込みを再開しない。"""
import json
from .replacement_client import ReplacementReadFailure, timestamp


class ReplacementPaused(ReplacementReadFailure):
    def __init__(self, envelope):
        self.envelope = envelope
        super().__init__('replacement_control_lookup_required')


def initialize(store):
    with store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS replacement_control_wait (singleton INTEGER PRIMARY KEY CHECK(singleton=1),retry_at TEXT,recovery_required INTEGER NOT NULL,envelope TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS replacement_resume_claims (original_uuid TEXT NOT NULL,stage_version INTEGER NOT NULL,control_uuid TEXT NOT NULL UNIQUE,PRIMARY KEY(original_uuid,stage_version))')


def remember(store, envelope):
    """mutation_started:falseも未保存の根拠にせず、元の成否と別に待機を記録する。"""
    initialize(store)
    cooldown = envelope.get('cooldown') or (envelope.get('request') or {}).get('cooldown')
    if not cooldown:
        return
    with store.transaction() as db:
        previous = db.execute('SELECT * FROM replacement_control_wait WHERE singleton=1').fetchone()
        retry = cooldown['retry_at']
        if previous and previous['retry_at'] and (retry is None or timestamp(previous['retry_at']) > timestamp(retry)):
            retry = previous['retry_at']
        recovery = bool(cooldown['recovery_required']) or bool(previous and previous['recovery_required'])
        db.execute('INSERT OR REPLACE INTO replacement_control_wait VALUES(1,?,?,?)', (retry, int(recovery), json.dumps(envelope, ensure_ascii=False)))


def require_available(store, now):
    initialize(store)
    with store.transaction() as db:
        row = db.execute('SELECT * FROM replacement_control_wait WHERE singleton=1').fetchone()
        if row and (row['recovery_required'] or row['retry_at'] and now < timestamp(row['retry_at'])):
            raise ValueError('replacement_recovery_required' if row['recovery_required'] else 'replacement_cooldown')


def observe_circuit(store, precondition, now):
    """実GETのclosed確認だけで共通待機を解消し、時間経過や再起動で解除しない。"""
    initialize(store)
    circuit = precondition.get('circuit')
    if circuit and (circuit['recovery_required'] or circuit['state']=='half_open' or circuit['state']=='open' and (not circuit['retry_at'] or now<timestamp(circuit['retry_at']))):
        raise ValueError('replacement_recovery_required' if circuit['recovery_required'] else 'replacement_cooldown')
    if circuit and circuit['state'] == 'closed' and circuit['recovery_required'] is False:
        with store.transaction() as db:
            row = db.execute('SELECT retry_at FROM replacement_control_wait WHERE singleton=1').fetchone()
            if row and row['retry_at'] and now < timestamp(row['retry_at']):
                raise ValueError('replacement_cooldown')
            db.execute('DELETE FROM replacement_control_wait WHERE singleton=1')


def resume_paused(flow, target):
    """運用者の明示操作で、既知pausedの未発行工程だけを一度制御する。"""
    import uuid
    from .replacement_contract import canonical
    e = flow.episode(target)
    original = e['commit_uuid'] or e['prepare_uuid']
    if not original or e['discard_uuid'] and not e['commit_uuid']:
        raise ValueError('replacement_resume_not_supported')
    row = flow.journal.get(original)
    value = flow.client.request(original, row['operation'])
    flow.accept(target, original, value)
    request = value['request']
    if (request['request_uuid'] != original or request['operation'] != row['operation']
            or request['stage'] != 'paused' or type(value.get('resume_stage_version')) is not int):
        raise ValueError('replacement_resume_not_paused')
    if row['operation'] == 'meal_replace':
        if request['persistence'] != 'saved' or 'saved_at' not in request:
            raise ValueError('replacement_resume_commit_unknown')
        from datetime import timedelta
        if flow.clock() >= timestamp(request['saved_at']) + timedelta(minutes=15):
            raise ValueError('deadline_exceeded')
    else:
        flow.checkpoint(e)
    precondition = flow.contract.preconditions(flow.client.preconditions(target), target)
    observe_circuit(flow.store, precondition, flow.clock())
    require_available(flow.store, flow.clock())
    with flow.store.transaction() as db:
        wire = db.execute('SELECT metadata FROM replacement_wire WHERE request_key=?', (original,)).fetchone()
    if not wire:
        raise ValueError('replacement_resume_input_missing')
    metadata = json.loads(wire[0])
    key = str(uuid.uuid4())
    body = {'request_uuid': key, 'original_request_uuid': original,
            'expected_stage_version': value['resume_stage_version'], 'expected_input_sha256': metadata['input_image_sha256'],
            'policy_version': flow.policy.policy['policy_version'], 'budget_policy_digest': flow.policy.policy_digest,
            'publication_hold': flow.external(e)}
    flow.contract.validate('ResumeInput', body)
    initialize(flow.store)
    with flow.store.transaction() as db:
        if db.execute('SELECT 1 FROM replacement_resume_claims WHERE original_uuid=? AND stage_version=?', (original, value['resume_stage_version'])).fetchone():
            raise ValueError('replacement_resume_lookup_only')
        db.execute('INSERT INTO replacement_resume_claims VALUES(?,?,?)', (original, value['resume_stage_version'], key))
    flow.journal.prepare(key, 'replacement_resume', target, e['slot'], e['epoch'], e['owner'], canonical(body))
    raw = flow.journal.start(key)
    try:
        result = flow.client.send('replacement_resume', key, raw, 'application/json')
    except ReplacementPaused as error:
        remember(flow.store, error.envelope)
        raise
    remember(flow.store, result)
    if result['request'].get('original_request_uuid') != original:
        raise ValueError('replacement_resume_target_changed')
    flow.journal.envelope(key, result)
    return flow.reconcile(target)
