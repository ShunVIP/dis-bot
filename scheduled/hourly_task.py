from apscheduler.schedulers.asyncio import AsyncIOScheduler
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import discord

from core.birthday_store import ensure_birthday_tables
from core.paths import BIRTHDAYS_DB
from core.settings_store import get_feature_policy, has_feature_setting

# ── Часовой пояс для крон-задачи ──────────────────────────────────────────────
MSK = ZoneInfo("Europe/Moscow")

# ── Путь к БД (birthdays.db) ──────────────────────────────────────────────────
DB_PATH = BIRTHDAYS_DB
FEATURE_BIRTHDAY = "birthday"


def _birthday_channel_id(guild_id: int) -> int | None:
    ensure_birthday_tables()
    policy = get_feature_policy(guild_id, FEATURE_BIRTHDAY)
    if policy.output_channel_id:
        return int(policy.output_channel_id)
    if has_feature_setting(guild_id, FEATURE_BIRTHDAY):
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS birthday_config (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER
            )
        """)
        row = conn.execute("SELECT channel_id FROM birthday_config WHERE guild_id=?", (guild_id,)).fetchone()
        conn.commit()
    return int(row[0]) if row and row[0] else None

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
            ensure_birthday_tables()
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
            configured_channel_id = _birthday_channel_id(guild.id)
            configured_channel = guild.get_channel(configured_channel_id) if configured_channel_id else None
            channel = configured_channel if isinstance(configured_channel, discord.TextChannel) else discord.utils.get(guild.text_channels, name="flood") or guild.system_channel
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
                    await channel.send(
                        f"🎉 {user.mention}, с днём рождения, хотя это грустный праздник, "
                        "ведь каждый год делает тебя старше и приближает к неизбежному финалу. "
                        "Желаю, чтобы этот путь был хотя бы не таким мучительным, "
                        "а в жизни было поменьше дерьма."
                    )
                    print(f"[DEBUG] ✅ Поздравление отправлено: {user.display_name}")
                except Exception as e:
                    print(f"[DEBUG] ⚠️ Ошибка при отправке поздравления: {e}")

    scheduler.start()
