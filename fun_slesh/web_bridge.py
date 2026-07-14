# -*- coding: utf-8 -*-
import discord
from discord import app_commands
from discord.ext import commands, tasks
from config import APP_ALLOWED_GUILD_IDS, APP_BASE_URL

from core.settings_store import get_feature_policy, is_channel_allowed
from core.platform_store import (
    add_general_chat_message,
    claim_pending_discord_outbox,
    mark_discord_outbox_failed,
    mark_discord_outbox_sent,
)
from core.web_app_store import issue_login_code, upsert_web_user


class WebBridge(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.flush_web_outbox.start()

    def cog_unload(self):
        self.flush_web_outbox.cancel()

    @tasks.loop(seconds=5)
    async def flush_web_outbox(self):
        for item in claim_pending_discord_outbox(20):
            try:
                guild = self.bot.get_guild(int(item["guild_id"]))
                if not guild:
                    mark_discord_outbox_failed(item["id"], "guild not found")
                    continue
                channel = guild.get_channel(int(item["channel_id"]))
                if not isinstance(channel, discord.TextChannel):
                    mark_discord_outbox_failed(item["id"], "channel not found")
                    continue
                content = (
                    f"**{item['author_name']}** через web/app:\n"
                    f"{item['content']}"
                )
                await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
                mark_discord_outbox_sent(item["id"])
            except Exception as exc:
                mark_discord_outbox_failed(item["id"], str(exc))

    @flush_web_outbox.before_loop
    async def before_flush_web_outbox(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="приложение", description="Получить одноразовый код входа в ViPik app")
    @app_commands.guild_only()
    async def приложение(self, interaction: discord.Interaction):
        allowed_guilds = {
            int(item.strip()) for item in APP_ALLOWED_GUILD_IDS.split(",")
            if item.strip().isdigit()
        }
        if not interaction.guild or not allowed_guilds or interaction.guild.id not in allowed_guilds:
            await interaction.response.send_message("Вход в приложение для этого сервера закрыт.", ephemeral=True)
            return
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
            add_general_chat_message(
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
