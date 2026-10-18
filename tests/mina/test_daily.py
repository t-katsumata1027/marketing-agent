"""みなの承認と時刻指定公開を実際のSQLite・署名で検証する。"""

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.mina.domain import Candidate, JST, SLOTS, approval_time, slot_time
from src.mina.interactions import ApprovalPolicy, handle
from src.mina.runtime import import_manifest, notify_day, publish_due, send_notifications
from src.mina.store import Store

TARGET = "2026-10-08"
NOW = datetime(2026, 10, 7, 19, tzinfo=JST)
NOTIFY = datetime(2026, 10, 7, 20, tzinfo=JST)
HOST = "logeats.test"


def candidate(slot="breakfast"):
    return Candidate(TARGET, slot, "朝の献立候補です。" if slot == "breakfast" else f"{slot}の献立候補です。",
                     "記録の目安：500kcal #AI食事解析 #LogEats #食事記録 @EatsLog88161",
                     f"https://{HOST}/s/{slot}", "ご飯・魚・野菜", {"calories": 500, "protein": 25, "fat": 20, "carbs": 55},
                     "a" * 64, "b" * 64, "pass", "publishable", True, True, True, "c" * 40,
                     meal_count=3 if slot == "daily" else 1)


class DailyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "state.sqlite3")

    def approve(self, c=None):
        c = c or candidate()
        self.store.save(c, HOST, NOW)
        card = self.store.issue_cards(TARGET, NOTIFY)[0]
        key = card["components"][0]["components"][0]["custom_id"]
        self.store.decide(key, "user", NOTIFY)
        return c, key

    def test_four_fixed_jst_times_and_previous_day_notice(self):
        self.assertEqual([slot_time(date.fromisoformat(TARGET), s).astimezone(JST).strftime("%H:%M") for s in SLOTS], ["07:30", "12:15", "19:15", "21:30"])
        self.assertEqual(approval_time(date.fromisoformat(TARGET)), NOTIFY.astimezone(UTC))

    def test_no_notice_before_20_and_one_notice_per_revision(self):
        self.store.save(candidate(), HOST, NOW)
        self.assertEqual(self.store.issue_cards(TARGET, NOW), [])
        self.assertEqual(len(self.store.issue_cards(TARGET, NOTIFY)), 1)
        self.assertEqual(self.store.issue_cards(TARGET, NOTIFY), [])

    def test_unapproved_candidate_never_claimed(self):
        self.store.save(candidate(), HOST, NOW)
        self.assertEqual(self.store.claim_due(candidate().scheduled_at), [])

    def test_approved_candidate_never_claimed_early(self):
        c, _ = self.approve()
        self.assertEqual(self.store.claim_due(c.scheduled_at - timedelta(seconds=1)), [])

    def test_two_workers_claim_once(self):
        c, _ = self.approve()
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.store.claim_due(c.scheduled_at), range(2)))
        self.assertEqual(sum(map(len, results)), 1)

    def test_old_approval_invalidated_by_changed_content(self):
        self.store.save(candidate(), HOST, NOW)
        card = self.store.issue_cards(TARGET, NOTIFY)[0]
        key = card["components"][0]["components"][0]["custom_id"]
        self.store.save(replace(candidate(), comment="別の献立候補です。"), HOST, NOTIFY)
        with self.assertRaises(ValueError):
            self.store.decide(key, "user", NOTIFY)
        self.assertEqual(self.store.get(TARGET, "breakfast")["state"], "approval_pending")

    def test_replayed_approval_rejected(self):
        _, key = self.approve()
        with self.assertRaises(ValueError):
            self.store.decide(key, "user", NOTIFY)

    def test_late_approval_rejected(self):
        c = candidate()
        self.store.save(c, HOST, NOW)
        key = self.store.issue_cards(TARGET, NOTIFY)[0]["components"][0]["components"][0]["custom_id"]
        with self.assertRaises(ValueError):
            self.store.decide(key, "user", c.scheduled_at)

    def test_late_approved_post_expires(self):
        c, _ = self.approve()
        self.assertEqual(self.store.claim_due(c.scheduled_at + timedelta(seconds=121)), [])
        self.assertEqual(self.store.get(TARGET, c.slot)["state"], "expired")

    def test_unknown_result_never_reposted(self):
        c, _ = self.approve()
        client = Mock()
        client.publish.side_effect = TimeoutError()
        publish_due(self.store, client, c.scheduled_at, HOST, Mock(), clock=lambda: c.scheduled_at)
        publish_due(self.store, client, c.scheduled_at + timedelta(seconds=15), HOST, Mock(), clock=lambda: c.scheduled_at + timedelta(seconds=15))
        self.assertEqual(client.publish.call_count, 1)
        self.assertEqual(self.store.get(TARGET, c.slot)["state"], "outcome_unknown")

    def test_restart_after_dispatch_does_not_reclaim(self):
        c, _ = self.approve()
        self.store.claim_due(c.scheduled_at)
        another = Store(self.store.path)
        self.assertEqual(another.claim_due(c.scheduled_at + timedelta(seconds=15)), [])

    def test_success_records_x_id_and_result_notification(self):
        c, _ = self.approve()
        client = Mock()
        client.publish.return_value = "123"
        self.assertEqual(publish_due(self.store, client, c.scheduled_at, HOST, Mock(), clock=lambda: c.scheduled_at), 1)
        self.assertEqual(self.store.get(TARGET, c.slot)["x_id"], "123")
        self.assertEqual(self.store.get(TARGET, c.slot)["state"], "published")

    def test_wrong_x_account_stops_before_claim(self):
        c, _ = self.approve()
        client = Mock()
        client.verify_account.side_effect = RuntimeError()
        with self.assertRaises(RuntimeError):
            publish_due(self.store, client, c.scheduled_at, HOST, Mock())
        client.publish.assert_not_called()
        self.assertEqual(self.store.get(TARGET, c.slot)["state"], "approved")

    def test_ogp_mismatch_never_sent(self):
        c, _ = self.approve()
        client = Mock()
        publish_due(self.store, client, c.scheduled_at, HOST, Mock(side_effect=ValueError()))
        client.publish.assert_not_called()

    def test_delay_notification_queued_at_20_once(self):
        notify_day(self.store, TARGET, NOW)
        self.assertIsNone(self.store.claim_notice())
        notify_day(self.store, TARGET, NOTIFY)
        notify_day(self.store, TARGET, NOTIFY)
        self.assertEqual(self.store.claim_notice()["kind"], "delay")
        self.assertIsNone(self.store.claim_notice())

    def test_notice_timeout_not_automatically_resent(self):
        self.store.save(candidate(), HOST, NOW)
        self.store.issue_cards(TARGET, NOTIFY)
        client = Mock()
        client.send.side_effect = TimeoutError()
        send_notifications(self.store, client)
        send_notifications(self.store, client)
        self.assertEqual(client.send.call_count, 1)

    def test_fixed_api_text_unchanged(self):
        c = candidate()
        self.assertIn(c.api_text, c.content)
        with self.assertRaises(ValueError):
            replace(c, api_text="改変した本文").validate(HOST, NOW)

    def test_non_pass_and_unverified_results_rejected(self):
        for fields in ({"api_quality": "review_required"}, {"secondary_review": "hold"},
                       {"history_verified": False}, {"ogp_verified": False}, {"legacy_schedule_clear": False}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                replace(candidate(), **fields).validate(HOST, NOW)

    def test_daily_requires_three_meals(self):
        with self.assertRaises(ValueError):
            replace(candidate("daily"), meal_count=2).validate(HOST, NOW)

    def test_wrong_host_and_experiential_claim_rejected(self):
        for fields in ({"share_url": "https://evil.test/s/a"}, {"comment": "食べた"}, {"comment": "改行\nあり"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                replace(candidate(), **fields).validate(HOST, NOW)

    def test_manifest_rejects_partial_day_before_saving(self):
        path = Path(self.tmp.name) / "day.json"
        path.write_text(json.dumps([asdict(candidate())]), encoding="utf-8")
        with self.assertRaises(ValueError):
            import_manifest(self.store, path, HOST, NOW)
        self.assertEqual(self.store.list_day(TARGET), [])

    def test_batch_update_rolls_back_on_dispatched_slot(self):
        c, _ = self.approve()
        self.store.claim_due(c.scheduled_at)
        with self.assertRaises(ValueError):
            self.store.save_many([candidate("lunch"), replace(c, comment="変更")], HOST, NOW)
        self.assertIsNone(self.store.get(TARGET, "lunch"))

    def test_preparation_claim_survives_restart(self):
        self.assertTrue(self.store.claim_preparation(TARGET, NOW))
        self.assertFalse(Store(self.store.path).claim_preparation(TARGET, NOW))

    def test_default_dry_run_does_not_connect(self):
        from src.mina.__main__ import cycle
        with patch.dict(os.environ, {"MINA_LIVE_WRITES": "false"}), patch("src.mina.__main__.XClient") as x:
            result = cycle(self.store, NOTIFY)
        self.assertEqual(result["mode"], "dry_run")
        x.assert_not_called()


class SignatureTest(unittest.TestCase):
    # 準備のみ共通化し、日次テストの重複実行を避ける。
    setUp = DailyTest.setUp
    def signed_request(self, custom=None, user="allowed", timestamp=None, guild="guild"):
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        policy = ApprovalPolicy(public, "app", "guild", "channel", frozenset({"allowed"}))
        payload = {"type": 3, "application_id": "app", "guild_id": guild, "channel_id": "channel",
                   "member": {"user": {"id": user}}, "data": {"custom_id": custom or "invalid"}}
        body = json.dumps(payload).encode()
        timestamp = timestamp or str(int(NOTIFY.timestamp()))
        signature = private.sign(timestamp.encode() + body).hex()
        return body, signature, timestamp, policy

    def test_valid_signature_accepts_allowed_user(self):
        self.store.save(candidate(), HOST, NOW)
        custom = self.store.issue_cards(TARGET, NOTIFY)[0]["components"][0]["components"][0]["custom_id"]
        args = self.signed_request(custom)
        code, _ = handle(*args, self.store, NOTIFY)
        self.assertEqual(code, 200)
        self.assertEqual(self.store.get(TARGET, "breakfast")["state"], "approved")

    def test_modified_body_signature_rejected(self):
        body, signature, stamp, policy = self.signed_request()
        self.assertEqual(handle(body + b" ", signature, stamp, policy, self.store, NOTIFY)[0], 401)

    def test_old_signature_rejected(self):
        args = self.signed_request(timestamp=str(int(NOTIFY.timestamp()) - 301))
        self.assertEqual(handle(*args, self.store, NOTIFY)[0], 401)

    def test_wrong_user_or_guild_rejected(self):
        for kwargs in ({"user": "other"}, {"guild": "other"}):
            self.assertEqual(handle(*self.signed_request(**kwargs), self.store, NOTIFY)[0], 403)


if __name__ == "__main__":
    unittest.main()
