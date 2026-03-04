# -*- coding: utf-8 -*-
# fun_slesh/mem_roles.py
import discord
from discord.ext import commands
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import sqlite3, os

UTC = timezone.utc
MSK = ZoneInfo("Europe/Moscow")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "reminders.db"))

def _ensure_table():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS temp_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            role_name TEXT NOT NULL,
            remove_at_utc TEXT NOT NULL
        )
        """)
        conn.commit()

def _has_manage_roles(user: discord.abc.User) -> bool:
    perms = getattr(user, "guild_permissions", None)
    return bool(perms and perms.manage_roles)

async def _boost_role_for_visibility(guild: discord.Guild, role: discord.Role):
    """Включить 'Показывать отдельно' и поднять роль максимально высоко (ниже топ-роли бота)."""
    # 1) Включаем hoist (отображать отдельно в списке справа)
    try:
        if not role.hoist:
            await role.edit(hoist=True, reason="Временная роль: показывать отдельно")
    except Exception:
        pass

    # 2) Поднимаем позицию роли
    try:
        bot_top = guild.me.top_role.position
        target_position = max(1, bot_top - 1)
        # role.position меняем только если реально ниже
        if role.position < target_position:
            await role.edit(position=target_position, reason="Временная роль: поднять выше для видимости")
    except Exception:
        # Если иерархия/права не позволяют — просто пропускаем
        pass

class RoleControlView(discord.ui.View):
    def __init__(self, rec_id: int, guild_id: int, user_id: int, role_id: int, role_name: str):
        super().__init__(timeout=300)
        self.rec_id = rec_id
        self.guild_id = guild_id
        self.user_id = user_id
        self.role_id = role_id
        self.role_name = role_name

    @discord.ui.button(label="Снять у всех", style=discord.ButtonStyle.danger)
    async def remove_all(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not _has_manage_roles(interaction.user):
            await interaction.followup.send("⛔ Нужны права **Manage Roles**.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ Только на сервере.", ephemeral=True)
            return

        role = guild.get_role(self.role_id)
        if not role:
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM temp_roles WHERE guild_id=? AND role_id=?", (self.guild_id, self.role_id))
                conn.commit()
            await interaction.followup.send("ℹ️ Роль уже отсутствует. Записи в БД по ней удалены.", ephemeral=True)
            return

        for m in list(role.members):
            try:
                await m.remove_roles(role, reason="Снято: кнопка (все)")
            except Exception:
                pass
        try:
            await role.delete(reason="Удалено: кнопка (все)")
        except Exception:
            pass

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM temp_roles WHERE guild_id=? AND role_id=?", (self.guild_id, self.role_id))
            conn.commit()

        await interaction.followup.send(f"🗑️ Роль **{self.role_name}** снята со всех и удалена.", ephemeral=True)

    @discord.ui.button(label="Снять у пользователя", style=discord.ButtonStyle.secondary)
    async def remove_user(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not _has_manage_roles(interaction.user):
            await interaction.followup.send("⛔ Нужны права **Manage Roles**.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ Только на сервере.", ephemeral=True)
            return

        role = guild.get_role(self.role_id)
        member = guild.get_member(self.user_id)
        if not role or not member:
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM temp_roles WHERE id=?", (self.rec_id,))
                conn.commit()
            await interaction.followup.send("ℹ️ Роль/пользователь отсутствуют. Запись удалена из БД.", ephemeral=True)
            return

        try:
            await member.remove_roles(role, reason="Снято: кнопка (user)")
        except Exception as e:
            await interaction.followup.send(f"⚠️ Ошибка снятия: {e}", ephemeral=True)
            return

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM temp_roles WHERE id=?", (self.rec_id,))
            conn.commit()

        await interaction.followup.send(f"✅ С {member.mention} снята роль **{self.role_name}**.", ephemeral=True)

class CleanupView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(label="Почистить сироты", style=discord.ButtonStyle.primary, emoji="🧹")
    async def cleanup_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not _has_manage_roles(interaction.user):
            await interaction.followup.send("⛔ Нужны права **Manage Roles**.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ Только на сервере.", ephemeral=True)
            return

        removed = 0
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, user_id, role_id FROM temp_roles WHERE guild_id=?", (self.guild_id,))
            rows = cur.fetchall()

            for rec_id, user_id, role_id in rows:
                role = guild.get_role(role_id)
                member = guild.get_member(user_id)
                if (role is None) or (member is None) or (role not in getattr(member, "roles", [])):
                    cur.execute("DELETE FROM temp_roles WHERE id=?", (rec_id,))
                    removed += 1
            conn.commit()

        await interaction.followup.send(f"🧹 Готово. Удалено сиротских записей: **{removed}**.", ephemeral=True)

class TempRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_table()
        self.scheduler = AsyncIOScheduler(timezone=UTC)
        self.scheduler.start()
        self._restore_jobs()

    def _restore_jobs(self):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id,guild_id,user_id,role_id,remove_at_utc FROM temp_roles")
            rows = cur.fetchall()

        now = datetime.now(UTC)
        for rec_id, gid, uid, rid, ts in rows:
            try:
                when = datetime.fromisoformat(ts)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
            except Exception:
                with sqlite3.connect(DB_PATH) as conn:
                    cur = conn.cursor()
                    cur.execute("DELETE FROM temp_roles WHERE id=?", (rec_id,))
                    conn.commit()
                continue

            if when > now:
                self.scheduler.add_job(self._expire, "date", run_date=when, args=[rec_id, gid, uid, rid])
            else:
                self.bot.loop.create_task(self._expire(rec_id, gid, uid, rid))

    async def _expire(self, rec_id: int, guild_id: int, user_id: int, role_id: int):
        guild = self.bot.get_guild(guild_id)
        role = guild.get_role(role_id) if guild else None
        if guild and role:
            try:
                member = await guild.fetch_member(user_id)
                await member.remove_roles(role, reason="⏳ Срок временной роли истёк")
            except Exception:
                pass
            try:
                if len(role.members) == 0:
                    await role.delete(reason="⏳ Автоудаление временной роли")
            except Exception:
                pass

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM temp_roles WHERE id=?", (rec_id,))
            conn.commit()

    @app_commands.command(name="выдать_роль", description="Создать/выдать временную роль пользователю")
    @app_commands.describe(
        пользователь="Кому выдать роль",
        название="Название роли (создастся, если нет)",
        минут="На сколько минут (по умолчанию 60)",
        цвет="Цвет роли: HEX (#FF0000) или имя (red, blue, green...)"
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def выдать_роль(
        self,
        interaction: discord.Interaction,
        пользователь: discord.Member,
        название: str,
        минут: app_commands.Range[int, 1, 60 * 24 * 30] = 60,
        цвет: str | None = None,
    ):
        await interaction.response.defer(ephemeral=False)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ Эта команда доступна только на сервере.", ephemeral=True)
            return

        # Цвет роли
        role_color = discord.Color.default()
        if цвет:
            try:
                if цвет.startswith("#"):
                    role_color = discord.Color(int(цвет[1:], 16))
                else:
                    role_color = getattr(discord.Color, цвет.lower())()
            except Exception:
                await interaction.followup.send("⚠️ Неверный цвет. Используй HEX (#RRGGBB) или имя (red, blue...).", ephemeral=True)
                return

        # Ищем/создаём роль
        role = discord.utils.get(guild.roles, name=название)
        if not role:
            try:
                role = await guild.create_role(
                    name=название,
                    colour=role_color,
                    mentionable=True,
                    reason=f"Временная роль для {пользователь} (создано /выдать_роль)",
                )
            except discord.Forbidden:
                await interaction.followup.send("❌ У бота нет прав создавать роли.", ephemeral=True)
                return
            except discord.HTTPException as e:
                await interaction.followup.send(f"⚠️ Не удалось создать роль: {e}", ephemeral=True)
                return

        # Включаем отображение отдельно + поднимаем позицию
        await _boost_role_for_visibility(guild, role)

        # Выдаём роль
        try:
            await пользователь.add_roles(role, reason="Временная роль")
        except discord.Forbidden:
            await interaction.followup.send("❌ У бота нет прав выдать эту роль (иерархия).", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"⚠️ Не удалось выдать роль: {e}", ephemeral=True)
            return

        # Планирование снятия
        remove_at_msk = datetime.now(MSK) + timedelta(minutes=int(минут))
        remove_at_utc = remove_at_msk.astimezone(UTC)

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO temp_roles (guild_id,user_id,role_id,role_name,remove_at_utc) VALUES (?,?,?,?,?)",
                (guild.id, пользователь.id, role.id, role.name, remove_at_utc.isoformat()),
            )
            rec_id = cur.lastrowid
            conn.commit()

        self.scheduler.add_job(self._expire, "date", run_date=remove_at_utc, args=[rec_id, guild.id, пользователь.id, role.id])

        await interaction.followup.send(
            f"✅ {пользователь.mention} получил {role.mention} на **{минут} мин.** "
            f"(до **{remove_at_msk:%Y-%m-%d %H:%M МСК}**). Роль подсвечена и поднята в списке."
        )

    @app_commands.command(
        name="активные_роли",
        description="Список активных временных ролей (с кнопками: снять у всех/у пользователя + очистка сирот)"
    )
    async def активные_роли(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id,user_id,role_id,role_name,remove_at_utc FROM temp_roles WHERE guild_id=? ORDER BY remove_at_utc",
                (interaction.guild.id,),
            )
            rows = cur.fetchall()

        if not rows:
            await interaction.followup.send("📭 Нет активных временных ролей.", ephemeral=True)
            return

        sent = 0
        for rec_id, uid, role_id, name, ts in rows:
            if sent >= 10:
                break
            try:
                when = datetime.fromisoformat(ts)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                when_msk = when.astimezone(MSK).strftime("%Y-%m-%d %H:%M")
            except Exception:
                when_msk = "н/д"

            member = interaction.guild.get_member(uid)
            role = interaction.guild.get_role(role_id)
            line = f"• {member.mention if member else f'<@{uid}>'} — {role.mention if role else f'**{name}**'} до {when_msk} МСК"

            view = RoleControlView(rec_id, interaction.guild.id, uid, role_id, name)
            try:
                await interaction.channel.send(line, view=view, silent=True)
                sent += 1
            except Exception:
                await interaction.followup.send(line, ephemeral=True)

        await interaction.channel.send("🧹 Обслуживание БД временных ролей:", view=CleanupView(interaction.guild.id))
        tail = "" if sent == len(rows) else f"\n… и ещё {len(rows) - sent}"
        await interaction.followup.send(f"📋 Активные роли выведены выше.{tail}", ephemeral=True)

    @app_commands.command(name="очистить_сироты", description="Очистить сиротские записи temp_roles (роль/пользователь отсутствуют)")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def очистить_сироты(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ Только на сервере.", ephemeral=True)
            return

        removed = 0
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, user_id, role_id FROM temp_roles WHERE guild_id=?", (guild.id,))
            rows = cur.fetchall()

            for rec_id, user_id, role_id in rows:
                role = guild.get_role(role_id)
                member = guild.get_member(user_id)
                if (role is None) or (member is None) or (role not in getattr(member, "roles", [])):
                    cur.execute("DELETE FROM temp_roles WHERE id=?", (rec_id,))
                    removed += 1
            conn.commit()

        await interaction.followup.send(f"🧹 Готово. Удалено сиротских записей: **{removed}**.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(TempRoles(bot))
