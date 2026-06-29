# -*- coding: utf-8 -*-
# fun_slesh/rep_and_mood.py
"""
/Размер            — +1 Размер (раз в день)
/антирепа        — -1 Размер (раз в день, не ниже 0)
/топ_репа        — топ Размера
/история_репы    — кто кому давал (своя история)
/мое_настроение  — оценить настроение 1-10
/настроение_сегодня — настроение сервера за сегодня
"""

import discord
from discord.ext import commands
from discord import app_commands
import sqlite3, os
from datetime import datetime
from zoneinfo import ZoneInfo
from core.economy import add_coins
from core.economy_profile import can_receive_currency, currency_amount, economy_profile_required_text, size_name
from utils.events_bus import emit
# Импортируем assign_rep_role лениво чтобы избежать circular import
def _try_assign_role(bot, guild_id, user_id):
    import asyncio
    try:
        from fun_slesh.rep_roles import assign_rep_role
        asyncio.create_task(assign_rep_role(bot, guild_id, user_id))
    except Exception:
        pass

MSK     = ZoneInfo("Europe/Moscow")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))

# Сисек за полученную Размер
REP_REWARD   = 5
# Сисек штраф за антирепу (для получателя)
ANTIREP_COST = 3


def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS reputation (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  INTEGER NOT NULL,
                given_by INTEGER NOT NULL,
                delta    INTEGER NOT NULL DEFAULT 1,
                date     TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mood (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  INTEGER NOT NULL,
                mood     INTEGER NOT NULL,
                date     TEXT    NOT NULL
            );
        """)
        # Миграция: добавить колонку delta если нет
        existing = {r[1] for r in conn.execute("PRAGMA table_info(reputation)")}
        if "delta" not in existing:
            conn.execute("ALTER TABLE reputation ADD COLUMN delta INTEGER NOT NULL DEFAULT 1")
        conn.commit()


def _get_rep(user_id: int) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT SUM(delta) FROM reputation WHERE user_id=?", (user_id,)
        ).fetchone()
    val = row[0] if row and row[0] is not None else 0
    return max(0, val)   # не ниже 0 для отображения


class RepAndMood(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._game_rep_cache: dict = {}
        _ensure_tables()
        # Подписываемся на game_played через events_bus
        from utils.events_bus import subscribe
        subscribe("game_played", self._on_game_played_handler)

    # ── /Размер ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="размер", description="Дать +1 Размер участнику (раз в день)")
    @app_commands.describe(пользователь="Кому дать Размер")
    async def размер(self, interaction: discord.Interaction,
                   пользователь: discord.Member):
        if пользователь.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ Нельзя давать Размер самому себе.", ephemeral=True)
            return
        if пользователь.bot:
            await interaction.response.send_message(
                "❌ Боты не заслуживают Размера.", ephemeral=True)
            return
        if not can_receive_currency(пользователь.id):
            await interaction.response.send_message(
                f"❌ {пользователь.display_name} ещё не заполнил профиль 18+.\n{economy_profile_required_text()}",
                ephemeral=True,
            )
            return

        today = datetime.now(MSK).date().isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            # Проверка: уже давал кому-либо сегодня? (один подарок в день)
            given = conn.execute(
                "SELECT COUNT(*) FROM reputation WHERE given_by=? AND date=? AND delta>0",
                (interaction.user.id, today)
            ).fetchone()[0]
            if given:
                await interaction.response.send_message(
                    "❌ Ты уже давал Размер сегодня. Возвращайся завтра.", ephemeral=True)
                return

            conn.execute(
                "INSERT INTO reputation(user_id, given_by, delta, date) VALUES(?,?,1,?)",
                (пользователь.id, interaction.user.id, today)
            )

        new_rep = _get_rep(пользователь.id)
        # Награда в Сиськах получателю
        new_bal = add_coins(пользователь.id, REP_REWARD, "rep",
                            {"from": interaction.user.id, "type": "plus"})

        await emit("rep_given", user_id=пользователь.id,
                   given_by=interaction.user.id, date=today)
        # Проверяем не пора ли выдать/обновить роль
        _try_assign_role(self.bot, interaction.guild.id, пользователь.id)

        emb = discord.Embed(
            title="🏆 Размер выдана!",
            color=discord.Color.gold()
        )
        emb.add_field(name="Получатель", value=пользователь.mention, inline=True)
        emb.add_field(name=size_name(пользователь.id),  value=f"**{new_rep}** ⭐",  inline=True)
        emb.add_field(name="Бонус",
                      value=f"+{currency_amount(пользователь.id, REP_REWARD)} (баланс: {new_bal})", inline=True)
        await interaction.response.send_message(embed=emb)

    # ── /антирепа ─────────────────────────────────────────────────────────────
    @app_commands.command(name="уменьшить_размер",
                          description="Дать -1 Размер участнику (раз в день, не ниже 0)")
    @app_commands.describe(
        пользователь="Кому снизить Размер",
        причина="Причина (видна в истории)"
    )
    async def антирепа(self, interaction: discord.Interaction,
                       пользователь: discord.Member,
                       причина: str = ""):
        if пользователь.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ Нельзя снижать Размер самому себе.", ephemeral=True)
            return
        if пользователь.bot:
            await interaction.response.send_message(
                "❌ Боты вне системы Размера.", ephemeral=True)
            return
        if not can_receive_currency(пользователь.id):
            await interaction.response.send_message(
                f"❌ {пользователь.display_name} ещё не заполнил профиль 18+.\n{economy_profile_required_text()}",
                ephemeral=True,
            )
            return

        today = datetime.now(MSK).date().isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            given = conn.execute(
                "SELECT COUNT(*) FROM reputation WHERE given_by=? AND date=? AND delta<0",
                (interaction.user.id, today)
            ).fetchone()[0]
            if given:
                await interaction.response.send_message(
                    "❌ Ты уже снижал Размер сегодня.", ephemeral=True)
                return

            # Проверяем — уже на нуле?
            current = _get_rep(пользователь.id)
            if current <= 0:
                await interaction.response.send_message(
                    f"❌ У {пользователь.display_name} уже 0 Размера — ниже некуда.",
                    ephemeral=True)
                return

            conn.execute(
                "INSERT INTO reputation(user_id, given_by, delta, date) VALUES(?,?,-1,?)",
                (пользователь.id, interaction.user.id, today)
            )

        new_rep = _get_rep(пользователь.id)
        # Штраф в Сиськах получателю антирепы
        if ANTIREP_COST > 0:
            add_coins(пользователь.id, -min(ANTIREP_COST, max(0, new_rep)),
                     "rep", {"from": interaction.user.id, "type": "minus"})

        emb = discord.Embed(
            title="👎 Размер уменьшен",
            color=discord.Color.red()
        )
        emb.add_field(name="Получатель", value=пользователь.mention, inline=True)
        emb.add_field(name=size_name(пользователь.id),  value=f"**{new_rep}** ⭐",  inline=True)
        if причина:
            emb.add_field(name="Причина", value=причина, inline=False)
        await interaction.response.send_message(embed=emb)

    # ── /топ_репа ─────────────────────────────────────────────────────────────
    @app_commands.command(name="топ_размер", description="Топ Размера на сервере")
    async def топ_репа(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT user_id, SUM(delta) as total FROM reputation"
                " GROUP BY user_id HAVING total > 0 ORDER BY total DESC LIMIT 50"
            ).fetchall()

        present = []
        for user_id, total in rows:
            m = interaction.guild.get_member(user_id)
            if m:
                present.append((int(total), m.display_name, m.mention))

        if not present:
            await interaction.response.send_message("😶 Ещё никто не получил Размера.")
            return

        present.sort(reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"{medals[i] if i < 3 else f'**{i+1}.**'} {name} — **{rep}** ⭐"
            for i, (rep, name, _) in enumerate(present[:10])
        ]
        emb = discord.Embed(
            title="⭐ Топ Размера",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=emb)

    # ── /история_репы ─────────────────────────────────────────────────────────
    @app_commands.command(name="история_размера",
                          description="История Размера — кто и когда давал")
    @app_commands.describe(
        пользователь="Чью историю посмотреть (по умолчанию свою)",
        тип="Показать полученные или отданные"
    )
    @app_commands.choices(тип=[
        app_commands.Choice(name="Полученные", value="received"),
        app_commands.Choice(name="Отданные",   value="given"),
    ])
    async def история_репы(self, interaction: discord.Interaction,
                           пользователь: discord.Member | None = None,
                           тип: str = "received"):
        target = пользователь or interaction.user

        with sqlite3.connect(DB_PATH) as conn:
            if тип == "received":
                rows = conn.execute(
                    "SELECT given_by, delta, date FROM reputation"
                    " WHERE user_id=? ORDER BY date DESC LIMIT 20",
                    (target.id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT user_id, delta, date FROM reputation"
                    " WHERE given_by=? ORDER BY date DESC LIMIT 20",
                    (target.id,)
                ).fetchall()

        if not rows:
            await interaction.response.send_message("📭 История пуста.", ephemeral=True)
            return

        lines = []
        for other_id, delta, date in rows:
            m    = interaction.guild.get_member(other_id)
            name = m.display_name if m else f"<@{other_id}>"
            sign = "⭐ +" if delta > 0 else "👎 "
            lines.append(f"`{date}` {sign}{abs(delta)} — {name}")

        total = _get_rep(target.id)
        emb = discord.Embed(
            title=f"📋 История Размера: {target.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple()
        )
        emb.set_footer(text=f"Текущая Размер: {total} ⭐")
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # ── /мое_настроение ───────────────────────────────────────────────────────
    @app_commands.command(name="мое_настроение",
                          description="Оцени своё настроение от 1 до 10")
    @app_commands.describe(оценка="Настроение: 1 — всё плохо, 10 — прекрасно")
    async def мое_настроение(self, interaction: discord.Interaction,
                              оценка: app_commands.Range[int, 1, 10]):
        today = datetime.now(MSK).date().isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            if conn.execute(
                "SELECT 1 FROM mood WHERE user_id=? AND date=?",
                (interaction.user.id, today)
            ).fetchone():
                await interaction.response.send_message(
                    "❌ Настроение уже выставлено сегодня.", ephemeral=True)
                return
            conn.execute(
                "INSERT INTO mood(user_id, mood, date) VALUES(?,?,?)",
                (interaction.user.id, оценка, today)
            )

        emoji_map = {
            1: "😭", 2: "😢", 3: "😕", 4: "😐",
            5: "🙂", 6: "😊", 7: "😄", 8: "😁", 9: "🤩", 10: "🔥"
        }
        emb = discord.Embed(
            title=f"{emoji_map.get(оценка, '😶')} Настроение сохранено",
            description=f"Оценка: **{оценка}/10** на {today}",
            color=discord.Color.from_rgb(
                max(0, 255 - оценка * 20), min(255, оценка * 25), 100
            )
        )
        await interaction.response.send_message(embed=emb)

    # ── /настроение_сегодня ───────────────────────────────────────────────────
    @app_commands.command(name="настроение_сегодня",
                          description="Настроение участников сервера за сегодня")
    async def настроение_сегодня(self, interaction: discord.Interaction):
        today = datetime.now(MSK).date().isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT user_id, mood FROM mood WHERE date=?", (today,)
            ).fetchall()

        display = []
        for user_id, mood in rows:
            m = interaction.guild.get_member(user_id)
            if m:
                display.append((m.display_name, mood))

        if not display:
            await interaction.response.send_message(
                "📭 Сегодня ещё никто не оставил оценку настроения.", ephemeral=True)
            return

        display.sort(key=lambda x: x[1], reverse=True)
        emoji_map = {1:"😭",2:"😢",3:"😕",4:"😐",5:"🙂",
                     6:"😊",7:"😄",8:"😁",9:"🤩",10:"🔥"}
        lines = [
            f"{emoji_map.get(m, '😶')} **{name}** — {m}/10"
            for name, m in display[:25]
        ]
        avg = sum(m for _, m in display) / len(display)
        emb = discord.Embed(
            title=f"😊 Настроение сегодня — {today}",
            description="\n".join(lines),
            color=discord.Color.green()
        )
        emb.set_footer(text=f"Среднее: {avg:.1f}/10 · Голосов: {len(display)}")
        await interaction.response.send_message(embed=emb)


    # ── Размер за активность в играх ───────────────────────────────────────────
    async def _on_game_played_handler(self, user_id: int, guild_id: int, game: str):
        """Даёт +1 Размер за факт участия в игре. Кулдаун 30 минут на игру."""
        import time
        key = (user_id, guild_id, game)
        now = time.time()
        if now - self._game_rep_cache.get(key, 0) < 1800:
            return
        if not can_receive_currency(user_id):
            return
        self._game_rep_cache[key] = now
        today = datetime.now(MSK).date().isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO reputation(user_id, given_by, delta, date) VALUES(?,?,1,?)",
                (user_id, 0, today)
            )
        _try_assign_role(self.bot, guild_id, user_id)

async def setup(bot):
    await bot.add_cog(RepAndMood(bot))
