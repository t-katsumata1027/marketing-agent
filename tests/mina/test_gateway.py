"""常時接続の承認でも許可リストと版の検証を省略しないことを確認する。"""

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from src.mina.gateway import ApprovalGateway
from src.mina.interactions import ApprovalPolicy, handle_gateway


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.policy = ApprovalPolicy("a" * 64, "app", "guild", "channel", frozenset({"user"}))
        self.store = Mock()
        self.store.decide.return_value = "approved"
        self.now = datetime.now(UTC)
        self.payload = {"application_id": "app", "type": 3, "guild_id": "guild", "channel_id": "channel",
                        "member": {"user": {"id": "user"}}, "data": {"custom_id": "opaque-action"}}

    def test_valid_gateway_event_records_decision(self):
        code, result = handle_gateway(self.payload, self.now, self.policy, self.store, self.now)
        self.assertEqual(code, 200)
        self.assertEqual(result["data"]["flags"], 64)
        self.store.decide.assert_called_once_with("opaque-action", "user", self.now)

    def test_wrong_application_guild_channel_and_user_never_record(self):
        for key in ("application_id", "guild_id", "channel_id", "member"):
            with self.subTest(key=key):
                payload = {**self.payload, key: {"user": {"id": "outsider"}} if key == "member" else "other"}
                code, _ = handle_gateway(payload, self.now, self.policy, self.store, self.now)
                self.assertEqual(code, 403)
        self.store.decide.assert_not_called()

    def test_stale_event_never_records(self):
        code, _ = handle_gateway(self.payload, self.now - timedelta(minutes=6), self.policy, self.store, self.now)
        self.assertEqual(code, 403)
        self.store.decide.assert_not_called()


class GatewayStartupTest(unittest.IsolatedAsyncioTestCase):
    async def test_webhook_endpoint_and_mismatched_credentials_block_startup(self):
        policy = ApprovalPolicy("a" * 64, "123", "guild", "channel", frozenset({"user"}))
        client = ApprovalGateway(Mock(), policy)
        for app in (SimpleNamespace(id=123, verify_key="a" * 64, interactions_endpoint_url="https://example.test/interactions"),
                    SimpleNamespace(id=999, verify_key="a" * 64, interactions_endpoint_url=None),
                    SimpleNamespace(id=123, verify_key="b" * 64, interactions_endpoint_url=None)):
            client.application_info = AsyncMock(return_value=app)
            with self.assertRaises(ValueError):
                await client.setup_hook()
        await client.close()

    async def test_paused_gateway_never_records_approval(self):
        policy = ApprovalPolicy("a" * 64, "123", "guild", "channel", frozenset({"user"}))
        store = Mock()
        client = ApprovalGateway(store, policy)
        interaction = SimpleNamespace(type=discord.InteractionType.component,
                                      response=SimpleNamespace(send_message=AsyncMock()))
        with patch.dict("os.environ", {"MINA_APPROVAL_ENABLED": "false"}):
            await client.on_interaction(interaction)
        store.decide.assert_not_called()
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])
        await client.close()
