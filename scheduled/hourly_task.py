from apscheduler.schedulers.asyncio import AsyncIOScheduler
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import discord
import os

# ── Часовой пояс для крон-задачи ──────────────────────────────────────────────
MSK = ZoneInfo("Europe/Moscow")

# ── Путь к БД (birthdays.db) ──────────────────────────────────────────────────
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "birthdays.db"))

def setup_birthday_checker(bot: discord.Client):
    # Планировщик живёт в МСК, значит hour=9 → 09:00 МСК
    scheduler = AsyncIOScheduler(timezone=MSK)

    @scheduler.scheduled_job("cron", hour=9, minute=0)
    async def check_birthdays():
        today = datetime.now(MSK).strftime("%d.%m")
        year = datetime.now(MSK).year
        is_leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        print(f"[DEBUG] Проверка ДР на {today} (МСК) | Високосный: {is_leap}")

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            # <-- гарантируем схему на случай чистой установки
            cur.execute("""
                CREATE TABLE IF NOT EXISTS birthdays (
                    user_id INTEGER PRIMARY KEY,
                    birthday TEXT NOT NULL
                )
            """)
            # выборка с учётом 29.02
            if today == "28.02" and not is_leap:
                cur.execute("SELECT user_id FROM birthdays WHERE birthday IN (?, ?)", ("28.02", "29.02"))
            else:
                cur.execute("SELECT user_id FROM birthdays WHERE birthday = ?", (today,))
            rows = cur.fetchall()

        print(f"[DEBUG] Найдено пользователей с ДР: {len(rows)}")

        if not rows:
            return

        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="flood") or guild.system_channel
            print(f"[DEBUG] Сервер: {guild.name}, канал для поздравления: {getattr(channel, 'name', None)}")
            if not channel:
                continue

            for (user_id,) in rows:
                try:
                    user = await guild.fetch_member(user_id)
                    print(f"[DEBUG] Поздравляем user_id={user_id} — найден на сервере")
                except discord.NotFound:
                    print(f"[DEBUG] ❌ Пользователь {user_id} не найден на сервере {guild.name}")
                    continue

                try:
                    await channel.send(f"🎉 С днём рождения, {user.mention}! Спасибо, что не умер в этом году! 🎂")
                    print(f"[DEBUG] ✅ Поздравление отправлено: {user.display_name}")
                except Exception as e:
                    print(f"[DEBUG] ⚠️ Ошибка при отправке поздравления: {e}")

    scheduler.start()
