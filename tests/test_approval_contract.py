"""承認トークンの一回利用・期限・HTTP メソッド契約を静的に検証する。"""

from pathlib import Path
import unittest


class ApprovalContractTest(unittest.TestCase):
    def test_approval_rpc_rejects_reused_and_expired_tokens(self):
        sql = Path("supabase_migration.sql").read_text(encoding="utf-8")
        self.assertIn("token_row.used_at IS NOT NULL", sql)
        self.assertIn("token_row.expires_at <= now()", sql)
        self.assertIn("UPDATE public.post_approval_tokens SET used_at=now()", sql)

    def test_get_does_not_mutate_and_post_approves(self):
        route = Path("vercel_api_template.ts").read_text(encoding="utf-8")
        get_body = route.split("export async function GET", 1)[1].split("export async function POST", 1)[0]
        self.assertNotIn(".rpc(", get_body)
        self.assertIn('"cache-control": "no-store"', get_body)
        self.assertIn('"referrer-policy": "no-referrer"', get_body)
        self.assertIn('"x-robots-tag": "noindex"', get_body)
        self.assertIn("export async function POST", route)
        self.assertIn("approve_post_with_token", route)
