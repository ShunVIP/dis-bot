"""Discord commands and passive listener for public Instagram videos."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from core.instagram_service import (
    InstagramError,
    InstagramMediaService,
    PreparedInstagramMedia,
    UnsupportedInstagramUrl,
    find_instagram_urls,
    normalize_instagram_url,
)
from utils.logger import log as _base_log


log = _base_log.bind(src="instagram")


def _upload_limit(guild: discord.Guild, fallback: int) -> int:
    discovered = int(getattr(guild, "filesize_limit", 0) or 0)
    return discovered if discovered > 0 else fallback


def _media_message(author: discord.abc.User, media: PreparedInstagramMedia) -> str:
    metadata = media.metadata
    lines = [
        f"📹 Instagram-видео по запросу {author.mention}",
        f"Источник: {metadata.source_url}",
    ]
    if metadata.uploader and metadata.uploader != "неизвестен":
        uploader = metadata.uploader.lstrip("@")
        lines.append(f"Instagram-автор: @{uploader}")
    if metadata.caption:
        caption = " ".join(metadata.caption.split())
        lines.append(f"Подпись: {caption[:600]}")
    return "\n".join(lines)[:1900]


def _discord_upload_error(error: discord.HTTPException) -> str:
    if getattr(error, "code", 0) == 40005:
        return "Видео слишком большое для отправки в Discord."
    return InstagramError.user_message


class Instagram(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.service = InstagramMediaService()
        if self.service.available:
            log.info(
                "Instagram media enabled auto_download={} max_duration={} max_concurrent={}",
                self.service.config.auto_download,
                self.service.config.max_duration_seconds,
                self.service.config.max_concurrent_downloads,
            )
        else:
            log.warning("Instagram media disabled: yt-dlp or FFmpeg is unavailable")

    @staticmethod
    def _can_send(
        channel: discord.abc.GuildChannel | discord.Thread,
        guild: discord.Guild,
    ) -> bool:
        member = guild.me
        if member is None:
            return False
        permissions = channel.permissions_for(member)
        return bool(permissions.view_channel and permissions.send_messages and permissions.attach_files)

    async def _send_error_to_message(self, status: discord.Message, error: Exception) -> None:
        text = error.user_message if isinstance(error, InstagramError) else InstagramError.user_message
        try:
            await status.edit(content=f"❌ {text}")
        except discord.HTTPException:
            pass

    async def _process_message(self, message: discord.Message, url: str) -> None:
        if not self._can_send(message.channel, message.guild):
            return
        try:
            status = await message.channel.send("⏳ Загружаю видео…")
        except discord.Forbidden:
            return
        try:
            async with self.service.prepare(
                url,
                user_id=message.author.id,
                upload_limit_bytes=_upload_limit(message.guild, self.service.fallback_upload_limit_bytes()),
            ) as media:
                await message.channel.send(
                    _media_message(message.author, media),
                    file=discord.File(media.path, filename=media.filename),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except discord.Forbidden:
            try:
                await status.edit(content="❌ У бота недостаточно прав для отправки файла.")
            except discord.HTTPException:
                pass
            log.warning(
                "Instagram upload forbidden guild_id={} channel_id={}",
                message.guild.id,
                message.channel.id,
            )
            return
        except discord.HTTPException as exc:
            try:
                await status.edit(content=f"❌ {_discord_upload_error(exc)}")
            except discord.HTTPException:
                pass
            log.warning("Instagram Discord upload failed status={}", getattr(exc, "status", 0))
            return
        except Exception as exc:
            await self._send_error_to_message(status, exc)
            log.exception(
                "Instagram message processing failed guild_id={} channel_id={} error={}",
                message.guild.id,
                message.channel.id,
                type(exc).__name__,
            )
            return
        try:
            await status.delete()
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            not self.service.config.auto_download
            or message.author.bot
            or message.guild is None
            or not isinstance(message.channel, (discord.TextChannel, discord.Thread))
        ):
            return
        urls = find_instagram_urls(message.content)
        if not urls:
            return
        # Intentionally process only the first supported Instagram URL per message.
        await self._process_message(message, urls[0])

    @app_commands.command(
        name="instagram",
        description="Скачать публичный Reel или видеопубликацию Instagram",
    )
    @app_commands.describe(url="Ссылка на Instagram Reel или видеопубликацию")
    @app_commands.guild_only()
    async def instagram(self, interaction: discord.Interaction, url: str):
        if interaction.guild is None or not isinstance(
            interaction.channel,
            (discord.TextChannel, discord.Thread),
        ):
            await interaction.response.send_message("❌ Эта команда работает только на сервере.", ephemeral=True)
            return
        try:
            normalized = normalize_instagram_url(url)
        except UnsupportedInstagramUrl as exc:
            await interaction.response.send_message(f"❌ {exc.user_message}", ephemeral=True)
            return
        if not self._can_send(interaction.channel, interaction.guild):
            await interaction.response.send_message("❌ У бота недостаточно прав для отправки файла.", ephemeral=True)
            return

        await interaction.response.send_message("⏳ Загружаю видео…")
        try:
            async with self.service.prepare(
                normalized,
                user_id=interaction.user.id,
                upload_limit_bytes=_upload_limit(interaction.guild, self.service.fallback_upload_limit_bytes()),
            ) as media:
                await interaction.followup.send(
                    _media_message(interaction.user, media),
                    file=discord.File(media.path, filename=media.filename),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except discord.Forbidden:
            await interaction.edit_original_response(content="❌ У бота недостаточно прав для отправки файла.")
            return
        except discord.HTTPException as exc:
            await interaction.edit_original_response(content=f"❌ {_discord_upload_error(exc)}")
            log.warning("Instagram slash upload failed status={}", getattr(exc, "status", 0))
            return
        except Exception as exc:
            text = exc.user_message if isinstance(exc, InstagramError) else InstagramError.user_message
            await interaction.edit_original_response(content=f"❌ {text}")
            log.exception(
                "Instagram slash processing failed guild_id={} channel_id={} error={}",
                interaction.guild.id,
                interaction.channel.id,
                type(exc).__name__,
            )
            return
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Instagram(bot))
