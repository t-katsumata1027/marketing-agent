"""要求入力を送信前に固定し、失敗後は照会だけで保存状態を記録する。"""

import hashlib
import json
from datetime import UTC, date, datetime

from .clients import MealClient, MealWriteFailure, OutcomeUnknown
from .m1_contract import request_report, safe_write_state
from .preparation import fingerprint


class RequestLedger:
    """原本画像も保存する。成否不明からの再送・自動採用は行わない。"""

    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS mina_requests (
                request_key TEXT PRIMARY KEY, operation TEXT NOT NULL, target_date TEXT NOT NULL,
                slot TEXT NOT NULL, input_json TEXT NOT NULL, input_digest TEXT NOT NULL,
                image BLOB, image_sha256 TEXT, phase TEXT NOT NULL, observation TEXT, updated REAL NOT NULL)''')

    def prepare(self, key, operation, target, slot, payload, image=None):
        MealClient.request_key(key)
        date.fromisoformat(target)
        if operation not in ('meal_register', 'meal_correction', 'daily_share') or slot not in ('breakfast', 'lunch', 'dinner', 'daily'):
            raise ValueError('要求の処理種別・枠が不正です')
        if image is not None and (not isinstance(image, bytes) or len(image) > 4 * 1024 * 1024):
            raise ValueError('要求原本の容量・形式が不正です')
        sha = hashlib.sha256(image).hexdigest() if image is not None else None
        envelope = {'operation': operation, 'target_date': target, 'slot': slot, 'payload': payload, 'image_sha256': sha}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(raw.encode()) > 131072:
            raise ValueError('要求入力が大きすぎます')
        digest = fingerprint(envelope)
        with self.store.transaction() as db:
            previous = db.execute('SELECT input_digest FROM mina_requests WHERE request_key=?', (key,)).fetchone()
            if previous:
                if previous['input_digest'] != digest:
                    raise ValueError('同じUUIDの要求入力を変更できません')
                return
            db.execute("INSERT INTO mina_requests VALUES(?,?,?,?,?,?,?,?,'prepared',NULL,?)",
                       (key, operation, target, slot, raw, digest, image, sha, datetime.now(UTC).timestamp()))

    def restore(self, key):
        """送信せず要求内容を復元し、保存原本と入力ハッシュを検証する。"""
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM mina_requests WHERE request_key=?', (key,)).fetchone()
        if not row:
            raise ValueError('要求台帳がありません')
        result = dict(row)
        result['payload'] = json.loads(result.pop('input_json'))
        sha = hashlib.sha256(result['image']).hexdigest() if result['image'] is not None else None
        envelope = {name: result[name] for name in ('operation', 'target_date', 'slot', 'payload', 'image_sha256')}
        if sha != result['image_sha256'] or fingerprint(envelope) != result['input_digest']:
            raise ValueError('要求入力・原本の証跡が不一致です')
        return result

    def record(self, key, phase, observation):
        with self.store.transaction() as db:
            db.execute('UPDATE mina_requests SET phase=?,observation=?,updated=? WHERE request_key=?',
                       (phase, json.dumps(observation), datetime.now(UTC).timestamp(), key))

    def reconcile(self, key, client):
        """同UUIDのGETのみ。404・不正応答・読取失敗をunknownへ保留する。"""
        request = self.restore(key)
        try:
            observation = request_report(client.request_state(key, request['operation']), key, request['operation'])
        except Exception:
            observation = {'persistence': 'unknown', 'stage': 'unrecognized', 'reason': 'lookup_unavailable',
                           'automatic_retry_allowed': False, 'ledger_updated': False}
        previous = json.loads(request['observation']) if request['observation'] else {}
        # 保存済み証拠を、後の取得失敗や矛盾したnot_savedで降格させない。
        if previous.get('persistence') == 'saved' and observation.get('persistence') != 'saved':
            observation['persistence'] = 'saved'
            observation['reason'] = 'lookup_unavailable_or_inconsistent'
        self.record(key, 'held', observation)
        return {**observation, 'ledger_updated': True}

    def execute_once(self, key, client, operation):
        request = self.restore(key)
        with self.store.transaction() as db:
            claimed = db.execute("UPDATE mina_requests SET phase='dispatching' WHERE request_key=? AND phase='prepared'", (key,)).rowcount
        if claimed != 1:
            raise OutcomeUnknown('request_reconciliation_required')
        try:
            response = operation()
        except Exception as error:
            observation = error.write_state if isinstance(error, MealWriteFailure) else {'persistence': 'unknown'}
            self.record(key, 'held', observation)
            self.reconcile(key, client)
            raise OutcomeUnknown('request_reconciliation_required') from None
        # HTTP成功だけで完了・保存を推測しない。後続のUUID照合で確定する。
        self.record(key, 'response_received', safe_write_state(response.get('request', {})))
        return response
