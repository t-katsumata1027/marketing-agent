"""CLIへ秘密値を渡さず、停止時の証跡を保全する境界を検証する。"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.mina.codex_client import CodexClient, generation_client
from src.mina.preparation import PreparationJournal
from src.mina.store import Store
from src.mina.generation import IMAGE_STYLE


class CodexClientTest(unittest.TestCase):
    def test_image_client_adds_style_once_at_tool_boundary(self):
        client=CodexClient()
        client.execute=Mock(side_effect=RuntimeError('生成前に停止'))
        with self.assertRaisesRegex(RuntimeError,'生成前に停止'):
            client.image('料理と食品別の分量')
        self.assertEqual(client.execute.call_args.args[0],IMAGE_STYLE+'\n料理と量：料理と食品別の分量')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.environment = patch.dict(os.environ, {"MINA_CODEX_BIN": "codex-test", "MINA_AI_ENABLED": "true",
                                                   "MINA_CODEX_WORKDIR": str(self.directory), "OPENAI_API_KEY": "private-key",
                                                   "CODEX_API_KEY": "private-code-key", "DOPPLER_TOKEN": "private-doppler"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_no_api_or_business_credentials_are_inherited(self):
        environment = CodexClient().environment()
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("CODEX_API_KEY", environment)
        self.assertNotIn("DOPPLER_TOKEN", environment)

    def test_cli_failure_preserves_state_without_raw_error(self):
        process = Mock(returncode=1)
        process.communicate.return_value = ("", "private-key: usage limit reached")
        with patch("src.mina.codex_client.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "^codex_usage_limit_failed$"):
                CodexClient().execute("文案を作成", {"type": "object"})
        state = next(self.directory.glob("*/state.json"))
        raw = state.read_text(encoding="utf-8")
        self.assertEqual(json.loads(raw)["reason"], "usage_limit")
        self.assertNotIn("private-key", raw)
        from src.mina.audit import safe_failure,checked_details
        diagnostic=json.loads((state.parent/'diagnostic.json').read_text())
        self.assertEqual(diagnostic['exit_code'],1);self.assertFalse(diagnostic['result_exists'])
        self.assertNotIn('private-key',json.dumps(diagnostic))

    def test_all_failed_output_paths_keep_auditable_job_reference(self):
        from src.mina.audit import safe_failure,checked_details
        for kind in ('start','timeout','invalid'):
            with self.subTest(kind=kind):
                def launch(command,**kwargs):
                    if kind=='start':raise OSError('private-secret')
                    path=Path(command[command.index('--output-last-message')+1])
                    process=Mock(returncode=0)
                    if kind=='timeout':process.communicate.side_effect=subprocess.TimeoutExpired('cli',1)
                    else:path.write_text('invalid-json');process.communicate.return_value=('','')
                    return process
                with patch('src.mina.codex_client.subprocess.Popen',side_effect=launch),patch.object(CodexClient,'stop_process'):
                    try:CodexClient().execute('入力秘密',{'type':'object'},timeout=1)
                    except Exception as error:
                        reason,details=safe_failure(error);safe=json.loads(checked_details(details))
                        self.assertRegex(safe['cli_job_id'],'^[0-9a-f]{32}$');self.assertIn('cli_result_exists',safe)
                        self.assertTrue((self.directory/safe['cli_job_id']/'diagnostic.json').is_file())
                    else:self.fail('failure expected')

    def test_timeout_terminates_process_and_preserves_working_directory(self):
        process = Mock(returncode=1)
        process.communicate.side_effect = subprocess.TimeoutExpired("codex-test", 1)
        with patch("src.mina.codex_client.subprocess.Popen", return_value=process), patch.object(CodexClient, "stop_process") as stop:
            with self.assertRaisesRegex(RuntimeError, "^codex_execution_timeout$"):
                CodexClient().execute("文案を作成", {"type": "object"}, timeout=1)
        stop.assert_called_once_with(process)
        state = next(self.directory.glob("*/state.json"))
        self.assertEqual(json.loads(state.read_text())["state"], "outcome_unknown")
        self.assertTrue((state.parent / "schema.json").exists())

    def test_invalid_cli_json_is_not_reported_as_complete(self):
        def launch(command, **kwargs):
            path = Path(command[command.index("--output-last-message") + 1])
            path.write_text("invalid", encoding="utf-8")
            process = Mock(returncode=0)
            process.communicate.return_value = ("", "")
            return process
        with patch("src.mina.codex_client.subprocess.Popen", side_effect=launch):
            with self.assertRaises(ValueError):
                CodexClient().execute("文案を作成", {"type": "object"})
        state = next(self.directory.glob("*/state.json"))
        self.assertEqual(json.loads(state.read_text())["state"], "outcome_unknown")

    def test_api_is_not_an_automatic_fallback(self):
        with patch.dict(os.environ, {"MINA_AI_BACKEND": "openai_api"}):
            with self.assertRaises(ValueError):
                generation_client()


class PreparationJournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "state.sqlite3")
        self.journal = PreparationJournal(self.store)

    def test_claim_is_committed_before_side_effect_and_complete_is_cached(self):
        def action():
            with self.store.transaction() as db:
                state = db.execute("SELECT state FROM preparation_steps").fetchone()[0]
            self.assertEqual(state, "started")
            return {"ok": True}
        operation = Mock(side_effect=action)
        self.assertEqual(self.journal.run("2026-10-08", "registration", {"sha": "abc"}, operation), {"ok": True})
        self.assertEqual(self.journal.run("2026-10-08", "registration", {"sha": "abc"}, operation), {"ok": True})
        self.assertEqual(operation.call_count, 1)

    def test_unknown_external_result_is_not_retried(self):
        operation = Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            self.journal.run("2026-10-08", "registration", {}, operation)
        with self.assertRaises(RuntimeError):
            self.journal.run("2026-10-08", "registration", {}, operation)
        self.assertEqual(operation.call_count, 1)

    def test_changed_input_never_reuses_old_result(self):
        self.journal.run("2026-10-08", "registration", {"sha": "old"}, lambda: {"ok": True})
        operation = Mock()
        with self.assertRaises(ValueError):
            self.journal.run("2026-10-08", "registration", {"sha": "new"}, operation)
        operation.assert_not_called()
