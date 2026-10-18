"""診断の秘密情報非表示と読み取り専用の境界を検証する。"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.mina.diagnostics import diagnose
from src.mina.domain import JST
from src.mina.store import Store


class DiagnosticsTest(unittest.TestCase):
    def test_restored_database_requires_reconciliation_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'database';store=Store(path)
            with store.transaction() as db:
                db.execute('CREATE TABLE mina_recovery_guard(blocked INTEGER NOT NULL)')
                db.execute('INSERT INTO mina_recovery_guard VALUES(1)')
            before=path.read_bytes()
            report=diagnose(path,Path(directory)/'heartbeat',datetime(2026,10,12,19,tzinfo=JST),{})
            self.assertTrue(report['database']['recovery_reconciliation_required'])
            self.assertTrue(report['manual_reconciliation_needed'])
            self.assertEqual(path.read_bytes(),before)

    def test_cli_configuration_does_not_require_openai_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            report = diagnose(Path(directory) / 'missing', Path(directory) / 'heartbeat',
                              datetime(2026, 10, 7, 19, tzinfo=JST),
                              {'MINA_CODEX_BIN': '/opt/cli/codex', 'MINA_CODEX_HOME': '/var/lib/mina-agent/.codex'})
        self.assertEqual(report['ai_backend'], 'codex_cli')
        self.assertTrue(report['connections']['ai_generation']['configured'])
        self.assertFalse(report['connections']['drive_storage']['configured'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 10, 7, 19, tzinfo=JST)

    def test_missing_database_is_not_created_and_secret_is_not_printed(self):
        path = self.root / "missing.sqlite3"
        report = diagnose(path, self.root / "heartbeat", self.now, {"DISCORD_BOT_TOKEN": "secret-token-test"})
        self.assertFalse(path.exists())
        self.assertFalse(report["database"]["readable"])
        self.assertNotIn("secret-token-test", json.dumps(report))
        self.assertEqual(report["connections"]["discord_notification"]["missing"], ["DISCORD_ALLOWED_CHANNEL_ID"])

    def test_unknown_state_is_counted_without_exposing_payload(self):
        path = self.root / "state.sqlite3"
        store = Store(path)
        with store.transaction() as db:
            db.execute("INSERT INTO notices(target_date,slot,revision,kind,payload,state,created) VALUES(?,?,?,?,?,?,?)",
                       ("2026-10-08", "all", 0, "delay", '{"token":"private-body"}', "outcome_unknown", self.now.timestamp()))
        before = path.read_bytes()
        report = diagnose(path, self.root / "heartbeat", self.now, {})
        self.assertTrue(report["manual_reconciliation_needed"])
        self.assertEqual(report["database"]["tomorrow_candidate_count"], 0)
        self.assertNotIn("private-body", json.dumps(report))
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_and_future_heartbeat_are_not_healthy(self):
        heartbeat = self.root / "heartbeat"
        for value in ("nan", "inf", str(self.now.timestamp() + 60), "invalid"):
            heartbeat.write_text(value, encoding="ascii")
            self.assertFalse(diagnose(self.root / "missing", heartbeat, self.now, {})["heartbeat"]["fresh"])
        heartbeat.write_text(str(self.now.timestamp() - 15), encoding="ascii")
        self.assertTrue(diagnose(self.root / "missing", heartbeat, self.now, {})["heartbeat"]["fresh"])

    def test_preparation_hold_is_reported_without_raw_reason(self):
        path = self.root / 'state.sqlite3'
        store = Store(path)
        with store.transaction() as db:
            db.execute("INSERT INTO preparation VALUES(?,'held:registration',?)", ('2026-10-08', self.now.timestamp()))
        report = diagnose(path, self.root / 'heartbeat', self.now, {})
        self.assertTrue(report['manual_reconciliation_needed'])
        self.assertEqual(report['database']['preparation_holds'], 1)
