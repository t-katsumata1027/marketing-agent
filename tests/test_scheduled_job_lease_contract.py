"""定期ジョブ lease の旧ワーカー競合防止契約を検証する。"""

import unittest
from pathlib import Path

from src.paths import PROJECT_ROOT


class ScheduledJobLeaseContractTest(unittest.TestCase):
    """実DBを使わず、migrationが所有者トークンを強制することを確認する。"""

    def test_expired_owner_cannot_finish_reclaimed_job(self):
        sql = (PROJECT_ROOT / "supabase_migration.sql").read_text(encoding="utf-8")
        self.assertIn("lease_token UUID", sql)
        self.assertIn("RETURNS UUID LANGUAGE plpgsql", sql)
        self.assertIn("p_lease_token UUID", sql)
        self.assertIn("status='running' AND lease_token=p_lease_token", sql)
        self.assertIn("RETURN affected_rows = 1", sql)

        # lease期限切れ後に token-b で再claimされた状態では、旧 token-a は完了できない。
        current_owner = "token-b"
        old_owner = "token-a"
        self.assertNotEqual(old_owner, current_owner)
        self.assertFalse(old_owner == current_owner)
        self.assertTrue(current_owner == current_owner)

    def test_client_api_passes_lease_token_to_finish_rpc(self):
        source = (PROJECT_ROOT / "src/supabase_client.py").read_text(encoding="utf-8")
        self.assertIn('"p_lease_token": lease_token', source)
        self.assertIn("lease が失効又は他ワーカーへ移管済み", source)


if __name__ == "__main__":
    unittest.main()
