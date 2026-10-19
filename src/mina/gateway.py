"""Discordへ外向きに常時接続する承認受付。公開HTTPは不要。"""

import asyncio
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import discord

from .interactions import handle_gateway


class ApprovalGateway(discord.Client):
    """特権Intent・メッセージ本文の受信を使わず、ボタン操作だけを扱う。"""

    def __init__(self, store, policy):
        super().__init__(intents=discord.Intents.none())
        self.store, self.policy = store, policy

    async def setup_hook(self):
        app = await self.application_info()
        if str(app.id) != self.policy.application_id or not secrets.compare_digest(app.verify_key.lower(), self.policy.public_key.lower()):
            raise ValueError("Discord Applicationの登録値が一致しません")
        if app.interactions_endpoint_url:
            raise ValueError("HTTP受付が設定済みです。受信方式を照合してください")

    async def on_ready(self):
        print('{"event":"mina_gateway_ready"}', flush=True)

    async def on_error(self, event_method, *args, **kwargs):
        # SDK例外の応答原文・Interaction tokenをログへ出さない。
        print('{"event":"mina_gateway_event_failed"}', flush=True)

    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        if os.getenv("MINA_APPROVAL_ENABLED", "false").lower() != "true":
            return await interaction.response.send_message("承認受付は準備中です。現在の操作は記録していません。", ephemeral=True,
                                                           allowed_mentions=discord.AllowedMentions.none())
        # 他BotのカードやDMを承認対象に含めない。
        if not self.user or not interaction.message or interaction.message.author.id != self.user.id:
            return
        payload = {"application_id": str(interaction.application_id), "type": 3,
                   "guild_id": str(interaction.guild_id), "channel_id": str(interaction.channel_id),
                   "member": {"user": {"id": str(interaction.user.id)}}, "data": interaction.data}
        # DBの待機中もDiscordの応答期限に間に合わせる。確定結果は本人だけへ返す。
        await interaction.response.defer(ephemeral=True, thinking=True)
        code, result = await asyncio.to_thread(handle_gateway, payload, interaction.created_at,
                                               self.policy, self.store, datetime.now(UTC))
        content = result.get("data", {}).get("content") if code == 200 else None
        await interaction.followup.send(content or "この操作は許可されていません。", ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    async def health_loop(self):
        path = Path(os.getenv("MINA_GATEWAY_HEARTBEAT", "/var/lib/mina-agent/gateway.heartbeat"))
        path.parent.mkdir(parents=True, exist_ok=True)
        while not self.is_closed():
            if self.is_ready():
                path.write_text(str(datetime.now(UTC).timestamp()), encoding="ascii")
            await asyncio.sleep(15)


def run_gateway(store, policy):
    """ログイン情報をログへ出さず、切断時はSDKの再接続を使用する。"""
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token:
        raise ValueError("Discord Bot設定が必要です")
    client = ApprovalGateway(store, policy)

    async def run():
        async with client:
            # SDK初期状態はclosedなので、先にログインしてから監視を開始する。
            await client.login(token)
            health = asyncio.create_task(client.health_loop())
            try:
                await client.connect(reconnect=True)
            finally:
                health.cancel()
                await asyncio.gather(health, return_exceptions=True)

    try:
        asyncio.run(run())
    except Exception:
        print('{"event":"mina_gateway_start_failed"}', flush=True)
        raise SystemExit(1) from None
