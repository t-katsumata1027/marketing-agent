"""P3の移行可能性・起動前安全性を外部接続なしで検証する。"""

import os
import subprocess
import sys
import unittest

from src.paths import PROJECT_ROOT, project_config_path


class P3OperabilityTest(unittest.TestCase):
    """作業ディレクトリに依存しない設定探索と運用成果物を確認する。"""

    def test_project_config_path_is_root_based(self):
        self.assertEqual(project_config_path("LogEats"), PROJECT_ROOT / "projects" / "logeats.yaml")
        with self.assertRaises(ValueError):
            project_config_path("../secret")

    def test_operational_artifacts_exist(self):
        self.assertTrue((PROJECT_ROOT / ".python-version").is_file())
        self.assertTrue((PROJECT_ROOT / ".github/workflows/ci.yml").is_file())
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("profiles: [production]", compose)
        self.assertIn("preflight", compose)

    def test_preflight_rejects_missing_values_without_printing_values(self):
        script = PROJECT_ROOT / "scripts/verify_environment.py"
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""}
        result = subprocess.run([sys.executable, str(script)], env=environment, text=True, capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("必須環境変数", result.stderr)


if __name__ == "__main__":
    unittest.main()
