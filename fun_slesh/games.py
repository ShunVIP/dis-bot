# -*- coding: utf-8 -*-
# fun_slesh/games.py
import os
import sqlite3
import random
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from discord import app_commands

from core.economy import add_coins
from utils.events_bus import emit

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))
CHOICES = ("камень", "ножницы", "бумага")

def _ensure_tables() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        # дуэли PvP
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rps_duels (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER NOT NULL,
                channel_id   INTEGER NOT NULL,
                initiator_id INTEGER NOT NULL,
                opponent_id  INTEGER NOT NULL,
                init_choice  TEXT,
                opp_choice   TEXT,
                status       TEXT NOT NULL,      -- open|done|cancelled|expired
                created_at   TEXT NOT NULL,      -- ISO UTC
                expires_at   TEXT NOT NULL       -- ISO UTC
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rps_duels_status ON rps_duels(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rps_duels_channel ON rps_duels(channel_id)")
        conn.commit()

def _rps_result(user: str, bot: str) -> int:
    # 1 = win, 0 = draw, -1 = lose
    if user == bot:
        return 0
    wins = {("камень", "ножницы"), ("ножницы", "бумага"), ("бумага", "камень")}
    return 1 if (user, bot) in wins else -1

class Games(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        _ensure_tables()

    # ─────────────────────  КНБ с ботом  ─────────────────────
    @app_commands.command(name="кнб", description="Камень-ножницы-бумага с ботом")
    @app_commands.describe(выбор="камень | ножницы | бумага")
    async def кнб(self, interaction: discord.Interaction, выбор: str):
        v = выбор.strip().lower()
        if v not in CHOICES:
            await interaction.response.send_message("❌ Варианты: камень | ножницы | бумага", ephemeral=True)
            return
        bot_pick = random.choice(CHOICES)
        res = _rps_result(v, bot_pick)
        if res > 0:
            delta = 10
            new_bal = add_coins(interaction.user.id, delta, reason="rps_win", meta={"user": v, "bot": bot_pick})
            await emit("game_win", user_id=interaction.user.id, game="rps")
            txt = f"✅ Ты победил! `{v}` против `{bot_pick}`\n+{delta} монет → баланс **{new_bal}**"
            color = discord.Color.green()
        elif res == 0:
            txt = f"🤝 Ничья! Оба выбрали `{v}`"
            color = discord.Color.blurple()
        else:
            txt = f"❌ Проигрыш. `{v}` против `{bot_pick}`"
            color = discord.Color.red()

        emb = discord.Embed(title="✊✌️🖐 КНБ", description=txt, color=color)
        await interaction.response.send_message(embed=emb)

    # ─────────────────────  Угадай число  ─────────────────────
    @app_commands.command(name="угадай", description="Угадай число: если попадёшь — получишь монеты")
    @app_commands.describe(число="Твоя попытка", до="Максимум (по умолчанию 10)")
    async def угадай(self, interaction: discord.Interaction, число: app_commands.Range[int, 1, 10000], до: app_commands.Range[int, 2, 10000] = 10):
        if число > до:
            await interaction.response.send_message("❌ Число не может быть больше верхней границы.", ephemeral=True)
            return
        target = random.randint(1, до)
        if число == target:
            base = 10
            bonus = 0
            if   до >= 1000: bonus = 40
            elif до >= 200:  bonus = 20
            elif до >= 50:   bonus = 10
            delta = base + bonus
            new_bal = add_coins(interaction.user.id, delta, reason="guess_win", meta={"max": до})
            await emit("game_win", user_id=interaction.user.id, game="guess")
            emb = discord.Embed(
                title="🎯 Угадай число",
                description=f"✅ Попал! Загаданное число: **{target}**\n+{delta} монет → баланс **{new_bal}**",
                color=discord.Color.green()
            )
        else:
            emb = discord.Embed(
                title="🎯 Угадай число",
                description=f"😬 Не угадал. Загаданное число было **{target}**.",
                color=discord.Color.red()
            )
        await interaction.response.send_message(embed=emb)

    # ======================= PvP: КНБ дуэли =======================

    @app_commands.command(name="кнб_дуэль", description="Создать PvP-дуэль КНБ с участником")
    @app_commands.describe(оппонент="Против кого играем", таймаут_мин="Через сколько минут дуэль истечёт (по умолчанию 15)")
    async def кнб_дуэль(self, interaction: discord.Interaction, оппонент: discord.Member, таймаут_мин: app_commands.Range[int, 1, 120] = 15):
        if оппонент.bot:
            await interaction.response.send_message("🤖 Дуэль с ботами не поддерживается. Используй /кнб для игры с ботом.", ephemeral=True)
            return
        if оппонент.id == interaction.user.id:
            await interaction.response.send_message("❌ Нельзя вызвать на дуэль самого себя.", ephemeral=True)
            return

        _ensure_tables()
        now = datetime.now(timezone.utc)
        exp = now + timedelta(minutes=int(таймаут_мин))

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO rps_duels (guild_id, channel_id, initiator_id, opponent_id, status, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (interaction.guild.id, interaction.channel.id, interaction.user.id, оппонент.id, "open", now.isoformat(), exp.isoformat())
            )
            duel_id = cur.lastrowid
            conn.commit()

        emb = discord.Embed(
            title="⚔️ Дуэль КНБ создана",
            description=(f"ID дуэли: **{duel_id}**\n"
                         f"Участники: {interaction.user.mention} vs {оппонент.mention}\n\n"
                         f"Сделайте ходы командой:\n"
                         f"`/кнб_ход дуэль:{duel_id} выбор:<камень|ножницы|бумага>`\n\n"
                         f"Ходы скрыты (ответы — приватные). Результат появится, когда оба сходят.\n"
                         f"Истекает через {таймаут_мин} мин."),
            color=discord.Color.orange()
        )
        await interaction.response.send_message(content=f"{оппонент.mention}", embed=emb)

    @app_commands.command(name="кнб_ход", description="Сделать скрытый ход в PvP-дуэли КНБ")
    @app_commands.describe(дуэль="ID дуэли", выбор="камень | ножницы | бумага")
    async def кнб_ход(self, interaction: discord.Interaction, дуэль: int, выбор: str):
        v = выбор.strip().lower()
        if v not in CHOICES:
            await interaction.response.send_message("❌ Варианты: камень | ножницы | бумага", ephemeral=True)
            return

        _ensure_tables()
        now = datetime.now(timezone.utc)

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, guild_id, channel_id, initiator_id, opponent_id, init_choice, opp_choice, status, created_at, expires_at FROM rps_duels WHERE id = ?", (дуэль,))
            row = cur.fetchone()

            if not row:
                await interaction.response.send_message("❌ Дуэль не найдена.", ephemeral=True)
                return

            (_id, guild_id, channel_id, initiator_id, opponent_id, init_choice, opp_choice, status, created_at, expires_at) = row

            if status != "open":
                await interaction.response.send_message("⛔ Дуэль уже завершена или отменена.", ephemeral=True)
                return

            try:
                exp = datetime.fromisoformat(expires_at)
            except Exception:
                exp = now - timedelta(seconds=1)

            if now > exp:
                # помечаем как просроченную
                cur.execute("UPDATE rps_duels SET status = 'expired' WHERE id = ? AND status = 'open'", (_id,))
                conn.commit()
                await interaction.response.send_message("⌛ Время дуэли истекло.", ephemeral=True)
                return

            # кто делает ход?
            if interaction.user.id not in (initiator_id, opponent_id):
                await interaction.response.send_message("❌ Вы не участник этой дуэли.", ephemeral=True)
                return

            col = "init_choice" if interaction.user.id == initiator_id else "opp_choice"
            already = init_choice if col == "init_choice" else opp_choice
            if already:
                await interaction.response.send_message("🔒 Ход уже зафиксирован.", ephemeral=True)
                return

            # записываем ход
            cur.execute(f"UPDATE rps_duels SET {col} = ? WHERE id = ?", (v, _id))
            conn.commit()

            # перечитываем состояния
            cur.execute("SELECT init_choice, opp_choice, status FROM rps_duels WHERE id = ?", (_id,))
            c_init, c_opp, c_status = cur.fetchone()

        # приватное подтверждение
        await interaction.response.send_message(f"✅ Ход принят: **{v}**. Ожидаем соперника…", ephemeral=True)

        # если оба сходили — считаем и публикуем в канал
        if c_init and c_opp and c_status == "open":
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.cursor()
                # ещё раз проверим статус, чтобы не удвоить публикацию
                cur.execute("SELECT status FROM rps_duels WHERE id = ?", (_id,))
                st = cur.fetchone()
                if not st or st[0] != "open":
                    return
                cur.execute("UPDATE rps_duels SET status = 'done' WHERE id = ? AND status = 'open'", (_id,))
                conn.commit()

            # вычисляем результат
            res = _rps_result(c_init, c_opp)
            chan = self.bot.get_channel(int(channel_id))
            if not chan:
                # попытка восстановить канал
                try:
                    guild = await self.bot.fetch_guild(int(guild_id))
                    chan = await guild.fetch_channel(int(channel_id))
                except Exception:
                    chan = None

            winner_text = "🤝 Ничья!"
            reward_text = ""
            if res > 0:
                # инициатор победил
                delta = 15
                new_bal = add_coins(int(initiator_id), delta, reason="rps_pvp_win", meta={"duel_id": _id})
                await emit("game_win", user_id=int(initiator_id), game="rps")  # триггерим обычную ачивку
                winner_text = f"🏆 Победитель: <@{initiator_id}>"
                reward_text = f"\n+{delta} монет → теперь **{new_bal}**"
            elif res < 0:
                delta = 15
                new_bal = add_coins(int(opponent_id), delta, reason="rps_pvp_win", meta={"duel_id": _id})
                await emit("game_win", user_id=int(opponent_id), game="rps")
                winner_text = f"🏆 Победитель: <@{opponent_id}>"
                reward_text = f"\n+{delta} монет → теперь **{new_bal}**"

            emb = discord.Embed(
                title="⚔️ Итог дуэли КНБ",
                description=(f"ID: **{_id}**\n"
                             f"<@{initiator_id}> выбрал: **{c_init}**\n"
                             f"<@{opponent_id}> выбрал: **{c_opp}**\n\n"
                             f"{winner_text}{reward_text}"),
                color=discord.Color.gold()
            )
            if chan:
                try:
                    await chan.send(embed=emb)
                except Exception:
                    pass

    @app_commands.command(name="кнб_отмена", description="Отменить свою PvP-дуэль (пока не завершена)")
    @app_commands.describe(дуэль="ID дуэли")
    async def кнб_отмена(self, interaction: discord.Interaction, дуэль: int):
        _ensure_tables()
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, initiator_id, status FROM rps_duels WHERE id = ?", (дуэль,))
            row = cur.fetchone()
            if not row:
                await interaction.response.send_message("❌ Дуэль не найдена.", ephemeral=True)
                return
            _id, initiator_id, status = row
            if status != "open":
                await interaction.response.send_message("⛔ Дуэль уже завершена или отменена.", ephemeral=True)
                return
            if interaction.user.id != int(initiator_id):
                await interaction.response.send_message("❌ Отменить может только создатель дуэли.", ephemeral=True)
                return
            cur.execute("UPDATE rps_duels SET status = 'cancelled' WHERE id = ? AND status = 'open'", (_id,))
            conn.commit()

        emb = discord.Embed(
            title="🛑 Дуэль отменена",
            description=f"ID дуэли: **{дуэль}**",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=emb)

async def setup(bot):
    await bot.add_cog(Games(bot))
