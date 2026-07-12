# -*- coding: utf-8 -*-
import discord
from discord import app_commands
from discord.ext import commands, tasks
from config import APP_BASE_URL

from core.settings_store import get_feature_policy, is_channel_allowed
from core.web_app_store import (
    add_chat_message,
    claim_pending_outbox,
    issue_login_code,
    mark_outbox_failed,
    mark_outbox_sent,
    upsert_web_user,
)


class WebBridge(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.flush_web_outbox.start()

    def cog_unload(self):
        self.flush_web_outbox.cancel()

    @tasks.loop(seconds=5)
    async def flush_web_outbox(self):
        for item in claim_pending_outbox(20):
            try:
                guild = self.bot.get_guild(int(item["guild_id"]))
                if not guild:
                    mark_outbox_failed(item["id"], "guild not found")
                    continue
                channel = guild.get_channel(int(item["channel_id"]))
                if not isinstance(channel, discord.TextChannel):
                    mark_outbox_failed(item["id"], "channel not found")
                    continue
                content = (
                    f"**{item['author_name']}** через web/app:\n"
                    f"{item['content']}"
                )
                await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
                mark_outbox_sent(item["id"])
            except Exception as exc:
                mark_outbox_failed(item["id"], str(exc))

    @flush_web_outbox.before_loop
    async def before_flush_web_outbox(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="приложение", description="Получить одноразовый код входа в ViPik app")
    async def приложение(self, interaction: discord.Interaction):
        user = interaction.user
        upsert_web_user(
            user.id,
            username=user.name,
            global_name=getattr(user, "global_name", None) or user.display_name,
            avatar=str(user.display_avatar.url) if user.display_avatar else "",
        )
        code = issue_login_code(user.id)
        url = APP_BASE_URL or "http://100.90.24.117:3000"
        await interaction.response.send_message(
            f"🔐 Код входа: `{code}`\nОткрой {url} и введи код. Он действует 10 минут и используется один раз.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not isinstance(message.channel, discord.TextChannel):
            return
        policy = get_feature_policy(message.guild.id, "web_chat")
        if not policy.output_channel_id and not policy.allowed_channel_ids:
            return
        if policy.output_channel_id != message.channel.id and not is_channel_allowed(
            message.guild.id, "web_chat", message.channel.id
        ):
            return
        try:
            add_chat_message(
                message.author.id,
                message.author.display_name,
                message.content or "",
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                source="discord",
            )
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(WebBridge(bot))
