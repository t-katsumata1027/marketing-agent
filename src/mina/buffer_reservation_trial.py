"""停止中のBufferで、既知の試験下書き一件だけの日時指定と下書き復帰を検証する。"""

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

if __package__:
    from .buffer_trial import BufferTrialClient, ProbeError, TEST_TEXT, FIELDS, validate_draft
else:
    from buffer_trial import BufferTrialClient, ProbeError, TEST_TEXT, FIELDS, validate_draft

EDIT = '''mutation($input: EditPostInput!) {
 editPost(input: $input) { __typename
 ... on PostActionSuccess { post { ''' + FIELDS + ''' } }
 ... on MutationError { message }
 }
}'''


def reservation_trial(client, journal_path, clock=lambda: datetime.now(UTC)):
    path = Path(journal_path)
    if path.name != 'buffer-trial.sqlite3' or not path.is_file():
        raise ProbeError('verified_trial_database_required')
    channel = client.channel()
    if channel.get('isQueuePaused') is not True:
        raise ProbeError('buffer_pause_required')
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        row = db.execute('SELECT digest,state,post_id FROM trial WHERE singleton=1').fetchone()
        if not row or not row[2] or row[1] != 'draft_verified':
            raise ProbeError('verified_private_draft_required')
        digest=hashlib.sha256((channel['id']+'\n'+channel['serviceId']+'\n'+TEST_TEXT).encode()).hexdigest()
        if row[0] != digest:
            raise ProbeError('trial_target_changed')
        post_id = row[2]
        db.execute('CREATE TABLE IF NOT EXISTS reservation_steps (step TEXT PRIMARY KEY, state TEXT NOT NULL, due_at TEXT NOT NULL)')
        steps = dict(db.execute('SELECT step,state FROM reservation_steps'))
        if steps.get('schedule') == steps.get('return_to_draft') == 'confirmed':
            if steps.get('verification') != 'confirmed':
                raise ProbeError('buffer_reservation_proof_incomplete')
            validate_draft(client.post(post_id), channel['id'], post_id)
            return {'reservation_roundtrip_verified': True, 'cached': True,
                    'queue_paused': True, 'currently_draft': True, 'published': False,
                    'api_calls': client.calls}
        # 既知の取消だけは再開可能。日時指定の要求を二度送らない。
        if 'schedule' not in steps:
            validate_draft(client.post(post_id), channel['id'], post_id)
        due = (clock().astimezone(UTC) + timedelta(days=7)).replace(microsecond=0)
        def edit_once(step, values):
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT state,due_at FROM reservation_steps WHERE step=?', (step,)).fetchone()
            if existing:
                db.commit()
                if existing[0] != 'confirmed':
                    raise ProbeError('buffer_edit_reconciliation_required')
                return existing[1]
            deadline = due.isoformat().replace('+00:00','Z')
            db.execute('INSERT INTO reservation_steps VALUES (?, ?, ?)', (step, 'attempting', deadline))
            db.commit()
            try:
                result = client._request(EDIT, {'input': {'id': post_id, **values}}).get('editPost')
                if not isinstance(result, dict) or result.get('__typename') != 'PostActionSuccess':
                    raise ProbeError('buffer_edit_unconfirmed')
                if not isinstance(result.get('post'), dict) or result['post'].get('id') != post_id:
                    raise ProbeError('buffer_edit_target_unknown')
            except Exception:
                db.execute("UPDATE reservation_steps SET state='outcome_unknown' WHERE step=?", (step,))
                raise
            db.execute("UPDATE reservation_steps SET state='confirmed' WHERE step=?", (step,))
            return deadline
        # 自動公開は停止中で、予約日も七日先に限定する。
        # 実APIでは本文を省いた予約変更がInvalidInputErrorとなったため、同じ本文も明示する。
        intended = edit_once('schedule', {'mode': 'customScheduled',
            'dueAt': due.isoformat().replace('+00:00','Z'), 'schedulingType': 'automatic',
            'saveToDraft': False, 'text': TEST_TEXT, 'assets': []})
        observed = client.post(post_id)
        scheduled_ok = (isinstance(observed, dict) and observed.get('status') == 'scheduled'
            and observed.get('id') == post_id and observed.get('text') == TEST_TEXT
            and observed.get('channel') == {'id': channel['id'], 'service': 'twitter'}
            and observed.get('sentAt') is None and not observed.get('externalLink'))
        try:
            same_time = datetime.fromisoformat(observed.get('dueAt', '').replace('Z', '+00:00')) == datetime.fromisoformat(intended.replace('Z','+00:00'))
        except (ValueError, AttributeError):
            same_time = False
        # 取消は削除ではなく、既知の下書きへ戻す操作だけに限定する。
        if client.channel().get('isQueuePaused') is not True:
            raise ProbeError('buffer_pause_lost')
        edit_once('return_to_draft', {'saveToDraft': True, 'text': TEST_TEXT})
        validate_draft(client.post(post_id), channel['id'], post_id)
        if not scheduled_ok or not same_time:
            raise ProbeError('buffer_schedule_mismatch_returned_to_draft')
        db.execute("INSERT INTO reservation_steps VALUES ('verification','confirmed',?)", (intended,))
    return {'reservation_roundtrip_verified': True, 'cached': False,
            'schedule_time_matches': True, 'queue_paused': True, 'currently_draft': True,
            'published': False, 'api_calls': client.calls}


def main():
    parser = argparse.ArgumentParser(description='停止済みチャンネルの試験下書きで予約と下書き復帰を一回だけ確認する。')
    parser.add_argument('--journal', required=True)
    args = parser.parse_args()
    try:
        result = reservation_trial(BufferTrialClient(os.getenv('BUFFER_API_KEY')), args.journal)
    except ProbeError as error:
        result = {'ok': False, 'reason': str(error), 'automatic_retry': False}
    except Exception:
        result = {'ok': False, 'reason': 'buffer_reservation_unclassified', 'automatic_retry': False}
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get('ok') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())
