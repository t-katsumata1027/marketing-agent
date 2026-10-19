"""保留枠の新時刻承認・二重送信拒否・他の承認維持を検証する。"""

from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from src.mina.clients import ClockSkewError
from src.mina.instagram_domain import InstagramCandidate
from src.mina.instagram_media import HOSTS
from src.mina.instagram_publisher import publish_once, request_for
from src.mina.instagram_recovery import CHAT_ACTOR, approve_recovery_instruction, prepare_recovery, publication_window
import test_instagram as legacy
from test_instagram import API, NOW, candidate


class RecoveryTests(unittest.TestCase):
    setUp = legacy.InstagramTests.setUp
    accepted = legacy.InstagramTests.accepted

    def approval(self, c, now=NOW):
        self.store.save(c, now, HOSTS)
        row = next(r for r in self.store.rows() if r['slot'] == c.slot)
        self.store.queue_approval(row)
        button = self.button(c.slot, row['revision'])
        self.store.decide(button, 'user', now)
        return button

    def button(self, slot, revision):
        with self.store.transaction() as db:
            payload = json.loads(db.execute("SELECT payload FROM instagram_notices WHERE slot=? AND revision=? AND kind='approval'",
                                            (slot, revision)).fetchone()[0])
        return payload['components'][0]['components'][0]['custom_id']

    def held(self):
        c = candidate()
        self.approval(c)
        row = self.store.claim_due(c.scheduled_at)
        self.store.mark(row, 'held')
        return c, self.store.rows()[0], c.scheduled_at + timedelta(minutes=20)

    def recovery(self, row, now, verify=lambda _: None):
        return prepare_recovery(self.store, row, now, now + timedelta(minutes=40), HOSTS, verify, lambda _: None)

    def test_new_approval_publishes_at_actual_recovery_time_and_keeps_other_approvals(self):
        c, row, now = self.held()
        for slot in ('lunch', 'dinner', 'daily'):
            self.approval(candidate(slot))
        others = self.store.rows()[1:]
        checked = []
        recovered = self.recovery(row, now, lambda value: checked.append(value.digest))
        self.assertEqual(checked, [c.digest])
        self.assertEqual(self.store.rows()[1:], others)
        self.assertEqual(recovered['revision'], 2)
        self.assertEqual(recovered['state'], 'approval_pending')
        self.assertIsNone(self.store.claim_due(now))
        self.store.queue_approval(recovered)
        with self.store.transaction() as db:
            notice = json.loads(db.execute("SELECT payload FROM instagram_notices WHERE slot='breakfast' AND revision=2").fetchone()[0])
        self.assertIn('承認後に投稿', notice['content'])
        self.assertNotIn('予定 ', notice['content'])
        with self.assertRaises(ValueError):
            self.store.decide(self.button('breakfast', 1), 'user', now)
        approved = now + timedelta(minutes=2)
        self.store.decide(self.button('breakfast', 2), 'user', approved)
        ready = self.store.claim_due(approved + timedelta(seconds=15))
        self.assertEqual(ready['scheduled'], approved.timestamp())
        recovery_candidate = InstagramCandidate(**json.loads(ready['payload']))
        self.accepted(c)
        class LateAPI(API):
            def _request(self, query, variables=None):
                result = super()._request(query, variables)
                if 'createPost(' in query:
                    result['createPost']['post']['sentAt'] = (approved + timedelta(seconds=20)).isoformat()
                return result
        api = LateAPI(recovery_candidate)
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true', 'MINA_INSTAGRAM_PUBLISH_ENABLED': 'true'}):
            publish_once(self.store, ready, lambda _: None, HOSTS, api,
                         clock=lambda: approved + timedelta(seconds=15), verify_media=lambda _: None)
        self.assertEqual(len(api.mutations), 1)
        self.assertEqual(self.store.rows()[0]['state'], 'published')
        self.assertEqual(self.store.rows()[1:], others)
        self.assertIsNone(self.store.claim_due(approved + timedelta(seconds=30)))

    def test_preflight_failure_before_recovery_mutation_keeps_old_row(self):
        c, row, now = self.held()
        def fail(_): raise ValueError('正本変更')
        with self.assertRaises(ValueError):
            self.recovery(row, now, fail)
        self.assertEqual(self.store.rows()[0], row)

    def test_attempt_and_concurrent_change_cannot_be_recovered(self):
        c, row, now = self.held()
        def concurrent(_):
            with self.store.transaction() as db:
                db.execute("UPDATE instagram_candidates SET reason='changed'")
        with self.assertRaises(ValueError):
            self.recovery(row, now, concurrent)
        changed = self.store.rows()[0]
        with self.store.transaction() as db:
            db.execute("INSERT INTO instagram_attempts(target_date,slot,revision,digest,state,request_json,started) VALUES(?,?,1,?,'outcome_unknown','{}',?)",
                       (c.target_date, c.slot, c.digest, now.timestamp()))
        with self.assertRaises(ValueError):
            self.recovery(changed, now)

    def test_deadline_crossing_other_slot_and_unknown_state_are_rejected(self):
        c, row, now = self.held()
        for at, until in ((now, now + timedelta(hours=2)), (now - timedelta(days=1), now),
                          (candidate('lunch').scheduled_at - timedelta(minutes=10), candidate('lunch').scheduled_at + timedelta(minutes=5))):
            with self.assertRaises(ValueError):
                prepare_recovery(self.store, row, at, until, HOSTS, lambda _: None, lambda _: None)
        with self.assertRaises(ValueError):
            self.recovery({**row, 'state': 'outcome_unknown'}, now)

    def test_expired_card_cannot_be_approved_and_forged_recovery_cannot_be_imported(self):
        c, row, now = self.held()
        recovered = self.recovery(row, now)
        rc = InstagramCandidate(**json.loads(recovered['payload']))
        with self.assertRaises(ValueError):
            self.store.save(rc, now, HOSTS)
        self.store.queue_approval(recovered)
        with self.assertRaises(ValueError):
            self.store.decide(self.button(c.slot, 2), 'user', now + timedelta(minutes=40))
        with self.store.transaction() as db:
            db.execute('DELETE FROM instagram_recoveries')
        with self.assertRaises(ValueError):
            self.store.decide(self.button(c.slot, 2), 'user', now)

    def test_changed_recovery_content_or_scheduled_time_fails_attestation(self):
        _, row, now = self.held()
        recovered = self.recovery(row, now)
        c = InstagramCandidate(**json.loads(recovered['payload']))
        changed = replace(c, caption='本文変更')
        with self.assertRaises(ValueError):
            publication_window(self.store, {**recovered, 'digest': changed.digest}, changed)
        with self.assertRaises(ValueError):
            publication_window(self.store, {**recovered, 'scheduled': now.timestamp()}, c)

    def test_recovery_submission_unknown_is_never_retried(self):
        c, row, now = self.held()
        recovered = self.recovery(row, now)
        self.store.queue_approval(recovered)
        self.store.decide(self.button(c.slot, 2), 'user', now)
        ready = self.store.claim_due(now)
        rc = InstagramCandidate(**json.loads(ready['payload']))
        self.accepted(c)
        api = API(rc, fail=True)
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true', 'MINA_INSTAGRAM_PUBLISH_ENABLED': 'true'}):
            with self.assertRaises(TimeoutError):
                publish_once(self.store, ready, lambda _: None, HOSTS, api, clock=lambda: now, verify_media=lambda _: None)
        self.assertEqual(self.store.rows()[0]['state'], 'outcome_unknown')
        self.assertIsNone(self.store.claim_due(now + timedelta(seconds=10)))
        self.assertEqual(len(api.mutations), 1)

    def test_safe_clock_diagnostic_is_persisted_without_exception_text(self):
        c = candidate()
        self.approval(c)
        self.accepted(c)
        row = self.store.claim_due(c.scheduled_at)
        def fail(_):
            error = ClockSkewError(2)
            raise error
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true', 'MINA_INSTAGRAM_PUBLISH_ENABLED': 'true'}):
            with self.assertRaises(ClockSkewError):
                publish_once(self.store, row, fail, HOSTS, API(c), clock=lambda: c.scheduled_at, verify_media=lambda _: None)
        self.assertEqual(json.loads(self.store.rows()[0]['reason'])['reason'], 'clock_ahead')

    def instruction_ready(self):
        c, row, now = self.held()
        row = prepare_recovery(self.store, row, now, now + timedelta(minutes=10), HOSTS, lambda _: None, lambda _: None)
        return c, row, now

    def test_explicit_human_recovery_instruction_is_separate_from_discord_approval(self):
        c, row, now = self.instruction_ready()
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true', 'MINA_INSTAGRAM_APPROVAL_ENABLED': 'true'}):
            ready = approve_recovery_instruction(self.store, row, now, '投稿されませんでした。復旧して下さい', now)
            with self.assertRaises(ValueError):
                approve_recovery_instruction(self.store, row, now, '投稿されませんでした。復旧して下さい', now)
        self.assertEqual(ready['actor'], CHAT_ACTOR)
        self.assertEqual(ready['scheduled'], now.timestamp())
        self.assertIsNone(ready['nonce_hash'])
        rc = InstagramCandidate(**json.loads(ready['payload']))
        publication_window(self.store, ready, rc)
        self.assertIsNotNone(self.store.claim_due(now))
        with self.store.transaction() as db:
            db.execute("UPDATE instagram_recovery_instructions SET instruction='別の内容'")
        with self.assertRaises(ValueError):
            publication_window(self.store, ready, rc)

    def test_instruction_expiry_and_missing_prior_content_approval_block_recovery(self):
        c, row, now = self.instruction_ready()
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true', 'MINA_INSTAGRAM_APPROVAL_ENABLED': 'true'}):
            with self.assertRaises(ValueError):
                approve_recovery_instruction(self.store, row, now, '復旧して下さい', now - timedelta(minutes=6))
            with self.assertRaises(ValueError):
                approve_recovery_instruction(self.store, row, now, '', now)
            with self.store.transaction() as db:
                old = json.loads(db.execute('SELECT original_row FROM instagram_recoveries').fetchone()[0])
                old['actor'] = None
                db.execute('UPDATE instagram_recoveries SET original_row=?', (json.dumps(old),))
            with self.assertRaises(ValueError):
                approve_recovery_instruction(self.store, row, now, '復旧して下さい', now)
        self.assertEqual(self.store.rows()[0]['state'], 'approval_pending')

    def test_missing_instruction_proof_blocks_chat_approved_publish(self):
        c, row, now = self.instruction_ready()
        with patch.dict('os.environ', {'MINA_LIVE_WRITES': 'true', 'MINA_INSTAGRAM_APPROVAL_ENABLED': 'true'}):
            ready = approve_recovery_instruction(self.store, row, now, '復旧して下さい', now)
        with self.store.transaction() as db:
            db.execute('DELETE FROM instagram_recovery_instructions')
        with self.assertRaises(ValueError):
            publication_window(self.store, ready, InstagramCandidate(**json.loads(ready['payload'])))


if __name__ == '__main__':
    unittest.main()
