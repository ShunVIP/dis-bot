# -*- coding: utf-8 -*-
# fun_slesh/daily.py
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands
from discord import app_commands

from core.economy import add_coins, get_balance
from utils.events_bus import emit  # <-- добавлено

MSK = ZoneInfo("Europe/Moscow")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))

def _ensure_tables() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        # streaks
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_rewards (
                user_id        INTEGER PRIMARY KEY,
                last_claim_msk TEXT NOT NULL,   -- YYYY-MM-DD (MSK)
                streak         INTEGER NOT NULL DEFAULT 0
            )
        """)
        # economy tables обеспечиваются в core/economy.py по запросу,
        # здесь их не создаём, только используем.
        conn.commit()

def _milestone_bonus(streak: int) -> int:
    return 25 if streak in (7, 14, 30, 60, 100) else 0

def _compute_reward(streak: int) -> int:
    base = 25
    series = 5 * min(max(streak - 1, 0), 7)
    return base + series + _milestone_bonus(streak)

class Daily(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        _ensure_tables()

    # ─────────────────────  Баланс  ─────────────────────
    @app_commands.command(name="баланс", description="Показать баланс монет")
    async def баланс(self, interaction: discord.Interaction, пользователь: discord.Member | None = None):
        target = пользователь or interaction.user
        bal = get_balance(target.id)
        await interaction.response.send_message(f"💰 Баланс {target.mention}: **{bal}**")

    # ─────────────────────  Дэйлик  ─────────────────────
    @app_commands.command(name="дэйлик", description="Забрать ежедневную награду (по МСК)")
    async def дэйлик(self, interaction: discord.Interaction):
        _ensure_tables()
        today_msk = datetime.now(MSK).date()
        yesterday_msk = today_msk - timedelta(days=1)

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT last_claim_msk, streak FROM daily_rewards WHERE user_id = ?", (interaction.user.id,))
            row = cur.fetchone()

            if row:
                last_str, streak = row
                try:
                    last_date = datetime.fromisoformat(last_str).date()
                except Exception:
                    last_date = yesterday_msk - timedelta(days=1)
                if last_date == today_msk:
                    await interaction.response.send_message("⛔ Ты уже забирал сегодня. Возвращайся завтра!")
                    return
                if last_date == yesterday_msk:
                    streak = int(streak) + 1
                else:
                    streak = 1
            else:
                streak = 1

            reward = _compute_reward(streak)
            cur.execute(
                "INSERT INTO daily_rewards (user_id, last_claim_msk, streak) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET last_claim_msk = excluded.last_claim_msk, streak = excluded.streak",
                (interaction.user.id, today_msk.isoformat(), streak)
            )
            conn.commit()

        new_balance = add_coins(interaction.user.id, reward, reason="daily", meta={"streak": streak})

        # 👉 сообщаем движку ачивок о выдаче дэйлика
        await emit("daily_claimed", user_id=interaction.user.id, streak=streak, amount=reward)  # <-- добавлено

        next_tip = "Ещё +5 к бонусу завтра (первые 7 дней дают +5/день)." if streak < 7 else "Серия на максимальном бонусе по +5."
        emb = discord.Embed(
            title="🎁 Ежедневная награда",
            description=(f"Получено: **{reward}** монет\n"
                         f"Серия: **{streak}**\n"
                         f"Текущий баланс: **{new_balance}**\n\n"
                         f"{next_tip}"),
            color=discord.Color.teal()
        )
        await interaction.response.send_message(embed=emb)

    # ────────────────  Топ по сериям (по текущему серверу)  ────────────────
    @app_commands.command(name="топ_серии", description="Топ по текущим сериям дэйлика среди участников сервера")
    async def топ_серии(self, interaction: discord.Interaction):
        _ensure_tables()
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id, streak FROM daily_rewards ORDER BY streak DESC LIMIT 100")
            rows = cur.fetchall()

        # фильтруем тех, кто реально на этом сервере
        present: list[tuple[int, int, str]] = []
        for user_id, streak in rows:
            try:
                member = await interaction.guild.fetch_member(int(user_id))
            except discord.NotFound:
                continue
            present.append((int(user_id), int(streak), member.display_name))

        if not present:
            await interaction.response.send_message("😶 На сервере ещё ни у кого нет серии.")
            return

        present.sort(key=lambda t: t[1], reverse=True)
        top = present[:10]
        lines = [f"**{i+1}.** {name} — серия **{st}**" for i, (_, st, name) in enumerate(top)]
        emb = discord.Embed(
            title="🏆 Топ серий (дэйлик)",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=emb)

    # ────────────────  Топ по балансу (по текущему серверу)  ────────────────
    @app_commands.command(name="топ_баланс", description="Топ кошельков среди участников сервера")
    async def топ_баланс(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id, balance FROM coins_wallet ORDER BY balance DESC LIMIT 100")
            rows = cur.fetchall()

        present: list[tuple[int, int, str]] = []
        for user_id, bal in rows:
            try:
                member = await interaction.guild.fetch_member(int(user_id))
            except discord.NotFound:
                continue
            present.append((int(user_id), int(bal), member.display_name))

        if not present:
            await interaction.response.send_message("😶 На сервере ещё нет кошельков с балансом.")
            return

        present.sort(key=lambda t: t[1], reverse=True)
        top = present[:10]
        lines = [f"**{i+1}.** {name} — **{bal}** монет" for i, (_, bal, name) in enumerate(top)]
        emb = discord.Embed(
            title="💰 Топ баланса",
            description="\n".join(lines),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=emb)

async def setup(bot):
    await bot.add_cog(Daily(bot))
