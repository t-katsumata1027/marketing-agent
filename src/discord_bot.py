"""Discord 上の管理者フィードバックで投稿下書きをリライトする Bot。"""

import asyncio
import re

import discord
try:
    from src.logging_utils import get_logger, log_event
except ModuleNotFoundError:
    from logging_utils import get_logger, log_event  # type: ignore

try:
    from src.config import config
    from src.rewrite_workflow import process_rewrite
    from src.heartbeat import write_heartbeat
except ModuleNotFoundError:
    from config import config
    from rewrite_workflow import process_rewrite
    from heartbeat import write_heartbeat


POST_ID_PATTERN = re.compile(r"\[ID:\s*([^\]\s]+)\]")
logger = get_logger(__name__)


def extract_post_id(message_content: str) -> str | None:
    """Webhook 通知本文から投稿 ID タグを取得する。"""
    match = POST_ID_PATTERN.search(message_content)
    return match.group(1) if match else None


intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


async def heartbeat_loop() -> None:
    """Discord接続中も定期的に生存時刻を更新する。"""
    while not bot.is_closed():
        write_heartbeat("discord-bot")
        await asyncio.sleep(30)


@bot.event
async def on_ready() -> None:
    write_heartbeat("discord-bot")
    asyncio.create_task(heartbeat_loop())
    """Bot の接続完了を通知する。"""
    print(f"Discord Bot として接続しました: {bot.user}")


@bot.event
async def on_message(message: discord.Message) -> None:
    """Webhook 通知への管理者返信を受け取り、リライトを実行する。"""
    if message.author.bot or message.reference is None or len(message.content) > 1000:
        return
    if not all((config.DISCORD_ALLOWED_GUILD_ID, config.DISCORD_ALLOWED_CHANNEL_ID, config.DISCORD_ALLOWED_ROLE_ID)):
        return
    if not message.guild or str(message.guild.id) != config.DISCORD_ALLOWED_GUILD_ID or str(message.channel.id) != config.DISCORD_ALLOWED_CHANNEL_ID:
        return
    if config.DISCORD_ALLOWED_ROLE_ID not in {str(role.id) for role in getattr(message.author, "roles", [])}:
        return
    if not isinstance(message.author, discord.Member) or not message.author.guild_permissions.administrator:
        return

    original_message = message.reference.resolved
    if not isinstance(original_message, discord.Message):
        try:
            original_message = await message.channel.fetch_message(
                message.reference.message_id
            )
        except discord.DiscordException:
            return

    post_id = extract_post_id(original_message.content)
    instruction = message.content.strip()
    if not post_id or not instruction:
        return

    log_event(logger, "discord_rewrite_requested", post_id=post_id,
              guild_id=str(message.guild.id), channel_id=str(message.channel.id),
              actor_id=str(message.author.id))
    processing_message = await message.channel.send(
        f"🔄 修正指示に基づいて投稿 ID {post_id} をリライトしています...",
        allowed_mentions=discord.AllowedMentions.none(),
    )
    try:
        result = await asyncio.to_thread(process_rewrite, post_id, instruction)
        new_content = result["rewritten_content"]
    except Exception as error:
        log_event(logger, "discord_rewrite_failed", post_id=post_id,
                  actor_id=str(message.author.id), error=str(error))
        error = RuntimeError("リライト処理に失敗しました。運用ログを確認してください")
        await processing_message.edit(content=f"❌ リライトに失敗しました: {error}")
        return

    await processing_message.edit(
        content=(
            "✅ リライトが完了しました。新しい投稿案:\n"
            f"```text\n{new_content}\n```\n"
            f"※ [ID: {post_id}] の下書きを上書きしました。"
        ),
        allowed_mentions=discord.AllowedMentions.none(),
    )


    log_event(logger, "discord_rewrite_completed", post_id=post_id,
              actor_id=str(message.author.id))


def run_discord_bot() -> None:
    """Doppler で注入したトークンを使って Discord Bot を起動する。"""
    if not config.DISCORD_BOT_TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN が未設定です。Doppler 経由で実行してください。")
    write_heartbeat("discord-bot")
    bot.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    run_discord_bot()
