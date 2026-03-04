import discord
from discord.ext import commands
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import sqlite3
import os

# ── Часовые пояса ──────────────────────────────────────────────────────────────
MSK = ZoneInfo("Europe/Moscow")
UTC = timezone.utc

# ── Путь к БД (reminders.db) ───────────────────────────────────────────────────
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "reminders.db"))

# ── Планировщик в МСК ─────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone=MSK)
scheduler.start()


class Tools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.load_reminders()

    # ── Поднять напоминания из БД при старте ───────────────────────────────────
    def load_reminders(self):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            # гарантируем таблицы
            cur.execute("""CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER,
                text TEXT NOT NULL,
                remind_at TEXT NOT NULL
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS timers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event TEXT NOT NULL,
                target_date TEXT NOT NULL
            )""")
            conn.commit()

            cur.execute("SELECT id, remind_at FROM reminders")
            rows = cur.fetchall()

            now_utc = datetime.now(UTC)
            for reminder_id, remind_at in rows:
                try:
                    dt = datetime.fromisoformat(remind_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)  # совместимость со старыми записями
                except Exception:
                    cur.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
                    continue

                if dt > now_utc:
                    scheduler.add_job(self.send_reminder, "date", run_date=dt, args=[reminder_id])
                else:
                    cur.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            conn.commit()

    # ── Отправка напоминания и удаление записи ─────────────────────────────────
    async def send_reminder(self, reminder_id: int):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id, channel_id, text FROM reminders WHERE id = ?", (reminder_id,))
            row = cur.fetchone()
            if not row:
                return
            user_id, channel_id, text = row

        user = await self.bot.fetch_user(user_id)
        channel = self.bot.get_channel(channel_id) if channel_id else None
        msg = f"🔔 Напоминание для {user.mention}:\n{text}"

        try:
            if channel is not None:
                await channel.send(msg)
            else:
                await user.send(msg)
        except Exception:
            pass

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            conn.commit()

    # ── /напомни ───────────────────────────────────────────────────────────────
    @app_commands.command(name="напомни", description="Установить напоминание (МСК)")
    @app_commands.describe(
        текст="О чём напомнить?",
        через_минут="Через сколько минут (МСК) напомнить (если 0 — используй точное время)",
        дата_время="Точное время в МСК, формат: ГГГГ-ММ-ДД ЧЧ:ММ",
        лично="Если true — напомню в личку, иначе в этот канал"
    )
    async def напомни(
        self,
        interaction: discord.Interaction,
        текст: str,
        через_минут: int = 0,
        дата_время: str = "",
        лично: bool = False
    ):
        now_msk = datetime.now(MSK)

        if через_минут > 0:
            remind_msk = now_msk + timedelta(minutes=через_минут)
        elif дата_время:
            try:
                naive = datetime.strptime(дата_время, "%Y-%m-%d %H:%M")
                remind_msk = naive.replace(tzinfo=MSK)
            except ValueError:
                await interaction.response.send_message(
                    "❌ Неверный формат. Используй: `ГГГГ-ММ-ДД ЧЧ:ММ` (МСК).",
                    ephemeral=True
                )
                return
        else:
            await interaction.response.send_message(
                "❌ Укажи либо `через_минут`, либо `дата_время`.",
                ephemeral=True
            )
            return

        remind_utc = remind_msk.astimezone(UTC)
        if remind_utc <= datetime.now(UTC):
            await interaction.response.send_message("❌ Время уже прошло. Укажи будущее время.", ephemeral=True)
            return

        # Если лично=True — сохраняем channel_id как NULL (None) => отправка в DM
        channel_id = None if лично else interaction.channel.id

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO reminders (user_id, channel_id, text, remind_at) VALUES (?, ?, ?, ?)",
                (interaction.user.id, channel_id, текст, remind_utc.isoformat())
            )
            reminder_id = cur.lastrowid
            conn.commit()

        scheduler.add_job(self.send_reminder, "date", run_date=remind_utc, args=[reminder_id])

        dest = "ЛС" if лично else f"канал <#{interaction.channel.id}>"
        await interaction.response.send_message(
            f"⏰ Напоминание установлено на **{remind_msk.strftime('%Y-%m-%d %H:%M')} МСК** "
            f"(UTC: {remind_utc.strftime('%Y-%m-%d %H:%M')}). Место доставки: **{dest}**."
        )

    # ── /мои_напоминания ───────────────────────────────────────────────────────
    @app_commands.command(name="мои_напоминания", description="Показать активные напоминания (по МСК)")
    async def мои_напоминания(self, interaction: discord.Interaction):
        now_utc = datetime.now(UTC)
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, text, remind_at, channel_id FROM reminders WHERE user_id = ? ORDER BY remind_at ASC",
                (interaction.user.id,)
            )
            rows = cur.fetchall()

        # Фильтруем просроченные
        cleaned = []
        for rid, text, ts, ch_id in rows:
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                if dt > now_utc:
                    cleaned.append((rid, text, dt, ch_id))
            except Exception:
                continue

        if not cleaned:
            await interaction.response.send_message("📝 У тебя нет активных напоминаний.", ephemeral=True)
            return

        lines = []
        for rid, text, dt, ch_id in cleaned[:15]:
            when_msk = dt.astimezone(MSK).strftime("%Y-%m-%d %H:%M")
            dest = "ЛС" if ch_id is None else f"<#{ch_id}>"
            lines.append(f"• `#{rid}` — **{when_msk} МСК** — {text} — → {dest}")

        more = f"\n… и ещё {len(cleaned) - 15}" if len(cleaned) > 15 else ""
        embed = discord.Embed(
            title="📋 Мои напоминания",
            description="\n".join(lines) + more,
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /удалить_напоминание ───────────────────────────────────────────────────
    @app_commands.command(name="удалить_напоминание", description="Удалить своё напоминание по ID")
    @app_commands.describe(id="ID напоминания (см. /мои_напоминания)")
    async def удалить_напоминание(self, interaction: discord.Interaction, id: int):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM reminders WHERE id = ?", (id,))
            row = cur.fetchone()
            if not row:
                await interaction.response.send_message("❌ Напоминание не найдено.", ephemeral=True)
                return
            if row[0] != interaction.user.id:
                await interaction.response.send_message("❌ Это напоминание принадлежит не вам.", ephemeral=True)
                return
            cur.execute("DELETE FROM reminders WHERE id = ?", (id,))
            conn.commit()

        await interaction.response.send_message(f"🗑️ Напоминание `#{id}` удалено.", ephemeral=True)

    # ── /счетчик (разовый расчёт) ──────────────────────────────────────────────
    @app_commands.command(name="счетчик", description="Сколько дней до события (по МСК)")
    @app_commands.describe(событие="Название события", дата="Дата события в формате ГГГГ-ММ-ДД (МСК)")
    async def счетчик(self, interaction: discord.Interaction, событие: str, дата: str):
        try:
            target_naive = datetime.strptime(дата, "%Y-%m-%d")
            target = target_naive.replace(tzinfo=MSK).date()
        except ValueError:
            await interaction.response.send_message("❌ Неверный формат даты. Используй ГГГГ-ММ-ДД.", ephemeral=True)
            return

        today_msk = datetime.now(MSK).date()
        delta = (target - today_msk).days

        if delta > 0:
            await interaction.response.send_message(f"📆 До события **{событие}** осталось: **{delta}** дн.")
        elif delta == 0:
            await interaction.response.send_message(f"🎉 Сегодня событие **{событие}**!")
        else:
            await interaction.response.send_message(f"⌛ Событие **{событие}** прошло {abs(delta)} дн. назад.")

    # ── /счетчик_создать (персистентный) ───────────────────────────────────────
    @app_commands.command(name="счетчик_создать", description="Создать постоянный счётчик (хранится в БД)")
    @app_commands.describe(событие="Название события", дата="Дата события в формате ГГГГ-ММ-ДД (МСК)")
    async def счетчик_создать(self, interaction: discord.Interaction, событие: str, дата: str):
        try:
            target_naive = datetime.strptime(дата, "%Y-%m-%d")
            target_date = target_naive.replace(tzinfo=MSK).date().isoformat()
        except ValueError:
            await interaction.response.send_message("❌ Неверный формат даты. Используй ГГГГ-ММ-ДД.", ephemeral=True)
            return

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO timers (user_id, event, target_date) VALUES (?, ?, ?)",
                (interaction.user.id, событие, target_date)
            )
            tid = cur.lastrowid
            conn.commit()

        await interaction.response.send_message(f"✅ Счётчик `#{tid}` создан: **{событие}** → {target_date} (МСК)")

    # ── /мои_счетчики ──────────────────────────────────────────────────────────
    @app_commands.command(name="мои_счетчики", description="Показать сохранённые счётчики")
    async def мои_счетчики(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, event, target_date FROM timers WHERE user_id = ? ORDER BY target_date ASC",
                (interaction.user.id,)
            )
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message("🗒️ У тебя нет сохранённых счётчиков.", ephemeral=True)
            return

        today = datetime.now(MSK).date()
        lines = []
        for tid, event, target_iso in rows[:25]:
            try:
                tdate = datetime.fromisoformat(target_iso).date()
            except Exception:
                tdate = None
            if tdate:
                delta = (tdate - today).days
                if delta > 0:
                    status = f"через {delta} дн."
                elif delta == 0:
                    status = "сегодня!"
                else:
                    status = f"{abs(delta)} дн. назад"
            else:
                status = "некорректная дата"
            lines.append(f"• `#{tid}` — **{event}** → {target_iso} ({status})")

        more = f"\n… и ещё {len(rows) - 25}" if len(rows) > 25 else ""
        embed = discord.Embed(
            title="⏱️ Мои счётчики",
            description="\n".join(lines) + more,
            color=discord.Color.teal()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /счетчик_удалить ───────────────────────────────────────────────────────
    @app_commands.command(name="счетчик_удалить", description="Удалить свой счётчик по ID")
    @app_commands.describe(id="ID счётчика (см. /мои_счетчики)")
    async def счетчик_удалить(self, interaction: discord.Interaction, id: int):
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM timers WHERE id = ?", (id,))
            row = cur.fetchone()
            if not row:
                await interaction.response.send_message("❌ Счётчик не найден.", ephemeral=True)
                return
            if row[0] != interaction.user.id:
                await interaction.response.send_message("❌ Этот счётчик принадлежит не вам.", ephemeral=True)
                return
            cur.execute("DELETE FROM timers WHERE id = ?", (id,))
            conn.commit()

        await interaction.response.send_message(f"🗑️ Счётчик `#{id}` удалён.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Tools(bot))
