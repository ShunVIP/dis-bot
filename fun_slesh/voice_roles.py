# -*- coding: utf-8 -*-
# fun_slesh/voice_roles.py
"""
Авто-роли по голосовым каналам:
  - Зашёл в канал "Valorant" → роль "Valorant" создаётся (если нет) и выдаётся
  - Вышел → роль снимается
  - Канал опустел → роль удаляется с сервера

Команды:
  /войс_роли_вкл   — (Админ) включить/выключить систему
  /войс_роли_статус — посмотреть текущие авто-роли
"""

import sqlite3
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands

from core.paths import SOCIAL_DB
from core.settings_store import (
    clear_feature_channel,
    get_feature_policy,
    set_feature_channel,
    set_feature_enabled,
)

DB_PATH = SOCIAL_DB
FEATURE_VOICE_ROLES = "voice_roles"
UTC     = timezone.utc


def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            -- Отслеживаем какие роли мы создали для каналов
            CREATE TABLE IF NOT EXISTS voice_auto_roles (
                guild_id   INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                role_id    INTEGER NOT NULL,
                PRIMARY KEY (guild_id, channel_id)
            );
        """)


def _is_enabled(guild_id: int) -> bool:
    return get_feature_policy(guild_id, FEATURE_VOICE_ROLES).enabled


def _excluded_channel_ids(guild_id: int) -> set[int]:
    return set(get_feature_policy(guild_id, FEATURE_VOICE_ROLES).excluded_channel_ids)


def _excluded_rows(guild_id: int) -> list[tuple[int, str]]:
    policy = get_feature_policy(guild_id, FEATURE_VOICE_ROLES)
    return [(int(channel_id), "admin panel") for channel_id in sorted(policy.excluded_channel_ids)]


def _is_excluded(guild_id: int, channel_id: int) -> bool:
    return channel_id in _excluded_channel_ids(guild_id)


async def _get_or_create_role(guild: discord.Guild, channel: discord.VoiceChannel) -> discord.Role | None:
    """Возвращает роль для канала, создаёт если нет."""
    role_name = channel.name

    # Проверяем в БД
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT role_id FROM voice_auto_roles WHERE guild_id=? AND channel_id=?",
            (guild.id, channel.id)
        ).fetchone()

    if row:
        role = guild.get_role(row[0])
        if role:
            return role
        # Роль удалена вручную — убираем запись
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "DELETE FROM voice_auto_roles WHERE guild_id=? AND channel_id=?",
                (guild.id, channel.id)
            )

    # Создаём роль
    try:
        role = await guild.create_role(
            name=role_name,
            color=discord.Color.from_hsv(hash(role_name) % 1000 / 1000, 0.5, 0.85),
            reason=f"Авто-роль для канала {role_name}",
            mentionable=True,
        )
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO voice_auto_roles(guild_id, channel_id, role_id)"
                " VALUES(?,?,?)",
                (guild.id, channel.id, role.id)
            )
        return role
    except discord.Forbidden:
        return None
    except Exception:
        return None


async def _cleanup_role(guild: discord.Guild, channel: discord.VoiceChannel):
    """Удаляет роль если канал опустел."""
    if len(channel.members) > 0:
        return  # ещё есть люди

    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT role_id FROM voice_auto_roles WHERE guild_id=? AND channel_id=?",
            (guild.id, channel.id)
        ).fetchone()
        if not row:
            return
        conn.execute(
            "DELETE FROM voice_auto_roles WHERE guild_id=? AND channel_id=?",
            (guild.id, channel.id)
        )

    role = guild.get_role(row[0])
    if role:
        try:
            await role.delete(reason="Канал опустел — авто-роль удалена")
        except Exception:
            pass


class VoiceRoles(commands.Cog):
    voice_roles_group = app_commands.Group(
        name="войс_роли",
        description="Авто-роли для голосовых каналов"
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_tables()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return
        guild = member.guild
        if not _is_enabled(guild.id):
            return

        # Вышел из канала
        if before.channel and before.channel != after.channel:
            # Снимаем роль старого канала
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute(
                    "SELECT role_id FROM voice_auto_roles WHERE guild_id=? AND channel_id=?",
                    (guild.id, before.channel.id)
                ).fetchone()
            if row:
                role = guild.get_role(row[0])
                if role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="Вышел из голосового канала")
                    except Exception:
                        pass
            # Чистим роль если канал опустел
            await _cleanup_role(guild, before.channel)

        # Вошёл в канал
        if after.channel and before.channel != after.channel:
            if _is_excluded(guild.id, after.channel.id):
                return
            role = await _get_or_create_role(guild, after.channel)
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason=f"Вошёл в канал {after.channel.name}")
                except Exception:
                    pass

    # ── /войс_роли_вкл ────────────────────────────────────────────────────────
    @voice_roles_group.command(name="вкл",
                               description="(Админ) Включить/выключить авто-роли по голосовым каналам")
    @app_commands.describe(включить="Включить или выключить")
    @app_commands.checks.has_permissions(administrator=True)
    async def войс_роли_вкл(self, interaction: discord.Interaction, включить: bool):
        set_feature_enabled(interaction.guild.id, FEATURE_VOICE_ROLES, включить)
        status = "✅ Включены" if включить else "⛔ Выключены"
        await interaction.response.send_message(
            f"{status} авто-роли по голосовым каналам.", ephemeral=True)

    # ── /войс_роли_статус ─────────────────────────────────────────────────────
    @voice_roles_group.command(name="статус",
                               description="Текущие авто-роли по голосовым каналам")
    async def войс_роли_статус(self, interaction: discord.Interaction):
        enabled = _is_enabled(interaction.guild.id)
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT channel_id, role_id FROM voice_auto_roles WHERE guild_id=?",
                (interaction.guild.id,)
            ).fetchall()
        excluded = _excluded_rows(interaction.guild.id)

        emb = discord.Embed(
            title="🎙️ Авто-роли голосовых каналов",
            color=discord.Color.green() if enabled else discord.Color.red()
        )
        emb.description = "✅ Система включена" if enabled else "⛔ Система выключена"

        if rows:
            lines = []
            for ch_id, role_id in rows:
                ch   = interaction.guild.get_channel(ch_id)
                role = interaction.guild.get_role(role_id)
                ch_name   = ch.name if ch else f"канал {ch_id}"
                role_name = role.mention if role else f"роль {role_id} (удалена)"
                count = len(ch.members) if ch else 0
                lines.append(f"🔊 **{ch_name}** → {role_name} · {count} чел.")
            emb.add_field(name="Активные роли", value="\n".join(lines), inline=False)
        else:
            emb.add_field(
                name="Активных ролей нет",
                value="Роли создаются автоматически когда кто-то заходит в войс",
                inline=False
            )

        if excluded:
            excluded_lines = []
            for ch_id, reason in excluded:
                ch = interaction.guild.get_channel(ch_id)
                ch_name = ch.name if ch else f"канал {ch_id}"
                note = f" — {reason}" if reason else ""
                excluded_lines.append(f"⛔ **{ch_name}**{note}")
            emb.add_field(name="Исключенные каналы", value="\n".join(excluded_lines[:15]), inline=False)

        await interaction.response.send_message(embed=emb, ephemeral=True)

    @voice_roles_group.command(name="исключить",
                               description="(Админ) Исключить голосовой канал из авто-ролей")
    @app_commands.describe(
        канал="Голосовой канал, где авто-роль не нужна",
        причина="Короткая пометка для админов"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def войс_роли_исключить(self, interaction: discord.Interaction, канал: discord.VoiceChannel, причина: str = ""):
        set_feature_channel(interaction.guild.id, FEATURE_VOICE_ROLES, канал.id, "exclude", причина.strip()[:120])
        await interaction.response.send_message(f"✅ {канал.mention} исключен из авто-ролей.", ephemeral=True)

    @voice_roles_group.command(name="вернуть",
                               description="(Админ) Вернуть голосовой канал в авто-роли")
    @app_commands.describe(канал="Голосовой канал, который снова нужно учитывать")
    @app_commands.checks.has_permissions(administrator=True)
    async def войс_роли_вернуть(self, interaction: discord.Interaction, канал: discord.VoiceChannel):
        clear_feature_channel(interaction.guild.id, FEATURE_VOICE_ROLES, канал.id, "exclude")
        await interaction.response.send_message(f"✅ {канал.mention} снова участвует в авто-ролях.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceRoles(bot))
