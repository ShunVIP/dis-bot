# fun_slesh/rep_and_mood.py
import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from utils.events_bus import emit  # 👈 добавлено

MSK = ZoneInfo("Europe/Moscow")
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))

class RepAndMood(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # 🏆 /репа
    @app_commands.command(name="репа", description="Дать репутацию участнику (раз в день)")
    @app_commands.describe(пользователь="Пользователь, которому хотите дать репутацию")
    async def репа(self, interaction: discord.Interaction, пользователь: discord.Member):
        if пользователь.id == interaction.user.id:
            await interaction.response.send_message("❌ Нельзя дать репутацию самому себе.", ephemeral=True)
            return

        today = datetime.now(MSK).date().isoformat()

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS reputation (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, given_by INTEGER NOT NULL, date TEXT NOT NULL)")
            conn.commit()

            cur.execute("SELECT 1 FROM reputation WHERE given_by = ? AND date = ?", (interaction.user.id, today))
            if cur.fetchone():
                await interaction.response.send_message("❌ Вы уже давали репутацию сегодня.", ephemeral=True)
                return

            cur.execute(
                "INSERT INTO reputation (user_id, given_by, date) VALUES (?, ?, ?)",
                (пользователь.id, interaction.user.id, today)
            )
            conn.commit()

        # 👉 событие для ачивок
        await emit("rep_given", user_id=пользователь.id, given_by=interaction.user.id, date=today)

        await interaction.response.send_message(f"🏆 {interaction.user.mention} выдал +1 репутации {пользователь.mention}!")

    # 🥇 /топ_репа
    @app_commands.command(name="топ_репа", description="Показать топ по репутации на сервере")
    async def топ_репа(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS reputation (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, given_by INTEGER NOT NULL, date TEXT NOT NULL)")
            cur.execute("SELECT user_id, COUNT(*) as total FROM reputation GROUP BY user_id ORDER BY total DESC LIMIT 10")
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message("😶 Пока никто не получил репутации.")
            return

        lines = []
        for i, (user_id, total) in enumerate(rows, start=1):
            try:
                member = await interaction.guild.fetch_member(user_id)
                name = member.display_name
            except discord.NotFound:
                name = f"ID {user_id}"
            lines.append(f"**{i}.** {name} — {total} реп.")

        embed = discord.Embed(
            title="🏆 Топ по репутации",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)

    # 😄 /мое_настроение
    @app_commands.command(name="мое_настроение", description="Оцените своё настроение от 1 до 10 (сохраняется на сегодня, МСК)")
    @app_commands.describe(оценка="Настроение от 1 (ужас) до 10 (отлично)")
    async def мое_настроение(self, interaction: discord.Interaction, оценка: app_commands.Range[int, 1, 10]):
        today = datetime.now(MSK).date().isoformat()

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS mood (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, mood INTEGER NOT NULL, date TEXT NOT NULL)")
            # Проверяем, есть ли уже запись на сегодня (МСК)
            cur.execute("SELECT 1 FROM mood WHERE user_id = ? AND date = ?", (interaction.user.id, today))
            if cur.fetchone():
                await interaction.response.send_message("❌ Сегодня вы уже оценивали своё настроение.", ephemeral=True)
                return

            cur.execute(
                "INSERT INTO mood (user_id, mood, date) VALUES (?, ?, ?)",
                (interaction.user.id, оценка, today)
            )
            conn.commit()

        await interaction.response.send_message(f"😄 Настроение сохранено: {оценка}/10 на {today} (МСК)")

    # 📊 /настроение_сегодня
    @app_commands.command(name="настроение_сегодня", description="Показать настроение участников сервера за сегодня (МСК)")
    async def настроение_сегодня(self, interaction: discord.Interaction):
        today = datetime.now(MSK).date().isoformat()

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS mood (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, mood INTEGER NOT NULL, date TEXT NOT NULL)")
            cur.execute("SELECT user_id, mood FROM mood WHERE date = ?", (today,))
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message("📭 Сегодня ещё никто не оставил оценку настроения.", ephemeral=True)
            return

        # Оставляем только тех, кто на этом сервере
        display = []
        for user_id, mood in rows:
            try:
                member = await interaction.guild.fetch_member(user_id)
            except discord.NotFound:
                continue
            display.append((member.display_name, mood))

        if not display:
            await interaction.response.send_message("📭 На сервере нет оценок за сегодня.", ephemeral=True)
            return

        # Сортировка по убыванию настроения
        display.sort(key=lambda x: x[1], reverse=True)

        lines = [f"• **{name}** — {mood}/10" for name, mood in display[:25]]
        avg = sum(m for _, m in display) / len(display)
        embed = discord.Embed(
            title=f"🙂 Настроение сегодня — {today} (МСК)",
            description="\n".join(lines),
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Среднее по серверу: {avg:.2f}/10 • Голосов: {len(display)}")
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(RepAndMood(bot))
