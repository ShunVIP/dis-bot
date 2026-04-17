# fun_slesh/tools.py
"""
/напомни        — гибкое напоминание с повторением, множественными пингами, предупреждением
/мои_напоминания — список с обратным отсчётом
/удалить_напоминание
"""

import discord
from discord.ext import commands
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import sqlite3, os, re

MSK = ZoneInfo("Europe/Moscow")
UTC = timezone.utc

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "reminders.db"))
scheduler = AsyncIOScheduler(timezone=MSK)

# ── Choices ───────────────────────────────────────────────────────────────────
REPEAT_CHOICES = [
    app_commands.Choice(name="Разовое",              value="once"),
    app_commands.Choice(name="Каждый день",           value="daily"),
    app_commands.Choice(name="Каждый понедельник",    value="weekly_mon"),
    app_commands.Choice(name="Каждый вторник",        value="weekly_tue"),
    app_commands.Choice(name="Каждую среду",          value="weekly_wed"),
    app_commands.Choice(name="Каждый четверг",        value="weekly_thu"),
    app_commands.Choice(name="Каждую пятницу",        value="weekly_fri"),
    app_commands.Choice(name="Каждую субботу",        value="weekly_sat"),
    app_commands.Choice(name="Каждое воскресенье",    value="weekly_sun"),
    app_commands.Choice(name="Каждые 2 недели (пн)",  value="biweekly"),
]

REPEAT_LABELS = {
    "once": "разовое", "daily": "каждый день",
    "weekly_mon": "каждый пн", "weekly_tue": "каждый вт",
    "weekly_wed": "каждую ср", "weekly_thu": "каждый чт",
    "weekly_fri": "каждую пт", "weekly_sat": "каждую сб",
    "weekly_sun": "каждое вс", "biweekly": "каждые 2 нед",
}

WEEKDAY_MAP = {
    "weekly_mon": "mon", "weekly_tue": "tue", "weekly_wed": "wed",
    "weekly_thu": "thu", "weekly_fri": "fri", "weekly_sat": "sat",
    "weekly_sun": "sun",
}

# ── БД ────────────────────────────────────────────────────────────────────────
def _ensure_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                channel_id   INTEGER,
                ping_users   TEXT    NOT NULL DEFAULT '',
                ping_roles   TEXT    NOT NULL DEFAULT '',
                text         TEXT    NOT NULL,
                remind_at    TEXT    NOT NULL,
                repeat       TEXT    NOT NULL DEFAULT 'once',
                advance_min  INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT    NOT NULL DEFAULT ''
            )
        """)
        # Миграция старых схем — добавляем колонки если нет
        existing = {row[1] for row in conn.execute("PRAGMA table_info(reminders)")}
        migrations = {
            "ping_users":  "ALTER TABLE reminders ADD COLUMN ping_users  TEXT NOT NULL DEFAULT ''",
            "ping_roles":  "ALTER TABLE reminders ADD COLUMN ping_roles  TEXT NOT NULL DEFAULT ''",
            "repeat":      "ALTER TABLE reminders ADD COLUMN repeat      TEXT NOT NULL DEFAULT 'once'",
            "advance_min": "ALTER TABLE reminders ADD COLUMN advance_min INTEGER NOT NULL DEFAULT 0",
            "created_at":  "ALTER TABLE reminders ADD COLUMN created_at  TEXT NOT NULL DEFAULT ''",
        }
        # Если есть старые колонки одиночного пинга — мигрируем данные и не трогаем
        for col, sql in migrations.items():
            if col not in existing:
                conn.execute(sql)
        conn.commit()

# ── Вычисление следующего срабатывания ────────────────────────────────────────
def _next_dt(repeat: str, hour: int, minute: int,
             fixed_date: datetime | None = None) -> datetime:
    """Возвращает следующую дату срабатывания в UTC."""
    now_msk = datetime.now(MSK)

    if fixed_date is not None:
        # Конкретная дата
        target = fixed_date.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=MSK)
        return target.astimezone(UTC)

    target = now_msk.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if repeat == "once" or repeat == "daily":
        if target <= now_msk:
            target += timedelta(days=1)
        return target.astimezone(UTC)

    if repeat in WEEKDAY_MAP:
        wd_map = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
        target_wd = wd_map[WEEKDAY_MAP[repeat]]
        days_ahead = (target_wd - now_msk.weekday()) % 7
        if days_ahead == 0 and target <= now_msk:
            days_ahead = 7
        target += timedelta(days=days_ahead)
        return target.astimezone(UTC)

    if repeat == "biweekly":
        days_ahead = (0 - now_msk.weekday()) % 14
        if days_ahead == 0 and target <= now_msk:
            days_ahead = 14
        target += timedelta(days=days_ahead)
        return target.astimezone(UTC)

    return target.astimezone(UTC)

# ── Планирование ──────────────────────────────────────────────────────────────
def _schedule(cog, rid: int, remind_utc: datetime,
              repeat: str, hour: int, minute: int,
              advance_min: int = 0):
    """Регистрирует основную задачу + опциональное предупреждение."""
    job_id = f"rem_{rid}"

    if repeat == "once":
        scheduler.add_job(cog._fire, "date", run_date=remind_utc,
                          args=[rid, False], id=job_id, replace_existing=True)
    elif repeat == "daily":
        scheduler.add_job(cog._fire, CronTrigger(hour=hour, minute=minute, timezone=MSK),
                          args=[rid, False], id=job_id, replace_existing=True)
    elif repeat in WEEKDAY_MAP:
        scheduler.add_job(cog._fire,
                          CronTrigger(day_of_week=WEEKDAY_MAP[repeat], hour=hour, minute=minute, timezone=MSK),
                          args=[rid, False], id=job_id, replace_existing=True)
    elif repeat == "biweekly":
        scheduler.add_job(cog._fire, "interval", weeks=2, start_date=remind_utc,
                          args=[rid, False], id=job_id, replace_existing=True)

    # Предупреждение заранее
    if advance_min > 0:
        adv_utc = remind_utc - timedelta(minutes=advance_min)
        if adv_utc > datetime.now(UTC):
            scheduler.add_job(cog._fire, "date", run_date=adv_utc,
                              args=[rid, True], id=f"{job_id}_adv", replace_existing=True)

# ── Парсинг пользователей/ролей из строки ────────────────────────────────────
def _parse_mentions(text: str, guild: discord.Guild) -> tuple[list[int], list[str]]:
    """
    Принимает строку вида '@user1, @user2, @RoleName'.
    Возвращает (ids, not_found_names).
    Работает для и для пользователей, и для ролей (зависит от контекста вызова).
    """
    if not text or not text.strip():
        return [], []
    ids, not_found = [], []
    for part in re.split(r'[,\s]+', text.strip()):
        part = part.strip().lstrip('@')
        if not part:
            continue
        # Поиск по ID
        if part.isdigit():
            ids.append(int(part))
            continue
        # Поиск среди участников
        m = discord.utils.find(
            lambda x: x.name.lower() == part.lower() or x.display_name.lower() == part.lower(),
            guild.members
        )
        if m:
            ids.append(m.id)
        else:
            not_found.append(part)
    return ids, not_found

def _parse_roles(text: str, guild: discord.Guild) -> tuple[list[int], list[str]]:
    if not text or not text.strip():
        return [], []
    ids, not_found = [], []
    for part in re.split(r'[,\s]+', text.strip()):
        part = part.strip().lstrip('@')
        if not part:
            continue
        if part.isdigit():
            ids.append(int(part))
            continue
        r = discord.utils.find(lambda x: x.name.lower() == part.lower(), guild.roles)
        if r:
            ids.append(r.id)
        else:
            not_found.append(part)
    return ids, not_found

# ── Обратный отсчёт ───────────────────────────────────────────────────────────
def _countdown(dt_utc: datetime) -> str:
    delta = dt_utc - datetime.now(UTC)
    if delta.total_seconds() <= 0:
        return "прямо сейчас"
    d, rem = divmod(int(delta.total_seconds()), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = []
    if d: parts.append(f"{d}д")
    if h: parts.append(f"{h}ч")
    if m: parts.append(f"{m}м")
    return "через " + " ".join(parts) if parts else "меньше минуты"

# ── Cog ───────────────────────────────────────────────────────────────────────
class Tools(commands.Cog):
    reminders_group = app_commands.Group(
        name="напоминания",
        description="Создание и управление напоминаниями"
    )

    def __init__(self, bot):
        self.bot = bot
        _ensure_db()
        self._load()

    def _load(self):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT id, remind_at, repeat, advance_min FROM reminders"
                ).fetchall()
        except Exception:
            return
        now_utc = datetime.now(UTC)
        for rid, remind_at, repeat, adv in rows:
            try:
                dt = datetime.fromisoformat(remind_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                dt_msk = dt.astimezone(MSK)
                if repeat == "once":
                    if dt > now_utc:
                        _schedule(self, rid, dt, repeat, dt_msk.hour, dt_msk.minute, adv or 0)
                    else:
                        with sqlite3.connect(DB_PATH) as c:
                            c.execute("DELETE FROM reminders WHERE id=?", (rid,))
                else:
                    nxt = _next_dt(repeat, dt_msk.hour, dt_msk.minute)
                    _schedule(self, rid, nxt, repeat, dt_msk.hour, dt_msk.minute, adv or 0)
            except Exception:
                continue

    async def _fire(self, rid: int, is_advance: bool):
        """Отправляет напоминание. is_advance=True — предупреждение заранее."""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute(
                    "SELECT user_id, channel_id, ping_users, ping_roles, text, repeat, remind_at, advance_min"
                    " FROM reminders WHERE id=?", (rid,)
                ).fetchone()
        except Exception:
            return
        if not row:
            return
        user_id, channel_id, ping_users_raw, ping_roles_raw, text, repeat, remind_at, adv = row

        # Формируем пинги
        pings = []
        for uid in (ping_users_raw or "").split(","):
            uid = uid.strip()
            if uid.isdigit():
                pings.append(f"<@{uid}>")
        for rid_role in (ping_roles_raw or "").split(","):
            rid_role = rid_role.strip()
            if rid_role.isdigit():
                pings.append(f"<@&{rid_role}>")
        if not pings:
            pings.append(f"<@{user_id}>")

        ping_str  = " ".join(pings)
        label     = REPEAT_LABELS.get(repeat, "")
        rep_note  = f" _(повторяется: {label})_" if repeat != "once" else ""

        if is_advance:
            dt = datetime.fromisoformat(remind_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            when_msk = dt.astimezone(MSK).strftime("%d.%m %H:%M")
            msg = f"⏰ {ping_str} **Напоминание через {adv} мин** (в {when_msk} МСК):\n{text}"
        else:
            msg = f"🔔 {ping_str} **Напоминание**{rep_note}:\n{text}"

        sent = False
        if channel_id:
            ch = self.bot.get_channel(channel_id)
            if ch:
                try:
                    await ch.send(msg)
                    sent = True
                except Exception:
                    pass
        if not sent:
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(msg)
            except Exception:
                pass

        # После основного срабатывания
        if not is_advance:
            if repeat == "once":
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("DELETE FROM reminders WHERE id=?", (rid,))
            else:
                dt = datetime.fromisoformat(remind_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                dt_msk  = dt.astimezone(MSK)
                next_dt = _next_dt(repeat, dt_msk.hour, dt_msk.minute)
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("UPDATE reminders SET remind_at=? WHERE id=?",
                                 (next_dt.isoformat(), rid))

    # ── /напомни ──────────────────────────────────────────────────────────────
    @reminders_group.command(name="создать", description="Установить напоминание")
    @app_commands.describe(
        текст          = "Текст напоминания",
        время          = "Время МСК: ЧЧ:ММ (например 21:00)",
        дата           = "Конкретная дата: ДД.ММ.ГГГГ (только для разового)",
        повторение     = "День недели или ежедневно",
        пользователь_1 = "Пингнуть участника",
        пользователь_2 = "Пингнуть ещё одного участника",
        пользователь_3 = "Пингнуть ещё одного участника",
        роли           = "Роли через запятую (названия или упоминания)",
        лично          = "Отправить в ЛС вместо канала",
        за_минут       = "Предупредить за N минут до события",
    )
    @app_commands.choices(повторение=REPEAT_CHOICES)
    async def напомни(
        self,
        interaction: discord.Interaction,
        текст: str,
        время: str,
        дата: str = "",
        повторение: str = "once",
        пользователь_1: discord.Member = None,
        пользователь_2: discord.Member = None,
        пользователь_3: discord.Member = None,
        роли: str = "",
        лично: bool = False,
        за_минут: int = 0,
    ):
        # Парсим время
        m = re.match(r"^(\d{1,2}):(\d{2})$", время.strip())
        if not m:
            await interaction.response.send_message(
                "❌ Формат времени: `ЧЧ:ММ` (например `21:00`)", ephemeral=True)
            return
        hour, minute = int(m.group(1)), int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            await interaction.response.send_message("❌ Некорректное время.", ephemeral=True)
            return

        # Парсим дату
        fixed_date = None
        if дата.strip():
            try:
                fixed_date = datetime.strptime(дата.strip(), "%d.%m.%Y")
            except ValueError:
                await interaction.response.send_message(
                    "❌ Формат даты: `ДД.ММ.ГГГГ` (например `15.04.2025`)", ephemeral=True)
                return

        remind_utc = _next_dt(повторение, hour, minute, fixed_date)

        if повторение == "once" and remind_utc <= datetime.now(UTC):
            await interaction.response.send_message(
                "❌ Это время уже прошло. Укажи будущую дату или время.", ephemeral=True)
            return

        # Собираем пользователей из Member пикеров
        user_ids = []
        for member in (пользователь_1, пользователь_2, пользователь_3):
            if member and member.id not in user_ids:
                user_ids.append(member.id)

        # Разрешаем роли из строки
        role_ids, r_nf = _parse_roles(роли, interaction.guild)
        u_nf = []

        # Если никого не указано — пингуем вызвавшего
        if not user_ids and not role_ids:
            user_ids = [interaction.user.id]

        channel_id = None if лично else interaction.channel.id

        ping_users_str = ",".join(str(x) for x in user_ids)
        ping_roles_str = ",".join(str(x) for x in role_ids)

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute(
                "INSERT INTO reminders(user_id, channel_id, ping_users, ping_roles, text,"
                " remind_at, repeat, advance_min, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (interaction.user.id, channel_id, ping_users_str, ping_roles_str,
                 текст, remind_utc.isoformat(), повторение,
                 за_минут, datetime.now(UTC).isoformat())
            )
            rid = cur.lastrowid

        _schedule(self, rid, remind_utc, повторение, hour, minute, за_минут)

        # Подтверждение
        when_msk     = remind_utc.astimezone(MSK).strftime("%d.%m.%Y %H:%M")
        repeat_label = REPEAT_LABELS.get(повторение, повторение)
        dest         = "в ЛС" if лично else f"в <#{channel_id}>"

        # Кого пингнем
        ping_preview = []
        for uid  in user_ids:  ping_preview.append(f"<@{uid}>")
        for rid_ in role_ids:  ping_preview.append(f"<@&{rid_}>")

        lines = [
            f"✅ Напоминание **#{rid}** создано",
            f"📅 {when_msk} МСК · {repeat_label}",
            f"👥 {', '.join(ping_preview)}  |  📍 {dest}",
            f"💬 {текст}",
            f"⏱ {_countdown(remind_utc)}",
        ]
        if за_минут > 0:
            lines.append(f"⚠️ Предупреждение за **{за_минут} мин** до события")
        if u_nf:
            lines.append(f"⚠️ Не найдены пользователи: {', '.join(u_nf)}")
        if r_nf:
            lines.append(f"⚠️ Не найдены роли: {', '.join(r_nf)}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ── /мои_напоминания ──────────────────────────────────────────────────────
    @reminders_group.command(name="мои", description="Мои активные напоминания с обратным отсчётом")
    async def мои_напоминания(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT id, text, remind_at, channel_id, ping_users, ping_roles, repeat, advance_min"
                " FROM reminders WHERE user_id=? ORDER BY remind_at ASC",
                (interaction.user.id,)
            ).fetchall()

        now_utc = datetime.now(UTC)
        active = []
        for rid, text, ts, ch_id, pu, pr, repeat, adv in rows:
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                if repeat != "once" or dt > now_utc:
                    active.append((rid, text, dt, ch_id, pu, pr, repeat, adv))
            except Exception:
                continue

        if not active:
            await interaction.response.send_message("📝 Нет активных напоминаний.", ephemeral=True)
            return

        lines = []
        for rid, text, dt, ch_id, pu, pr, repeat, adv in active[:15]:
            when_msk = dt.astimezone(MSK).strftime("%d.%m %H:%M")
            cd       = _countdown(dt)
            rl       = REPEAT_LABELS.get(repeat, repeat)
            dest     = f"<#{ch_id}>" if ch_id else "ЛС"
            pings    = []
            for uid in (pu or "").split(","):
                if uid.strip().isdigit(): pings.append(f"<@{uid.strip()}>")
            for rid_ in (pr or "").split(","):
                if rid_.strip().isdigit(): pings.append(f"<@&{rid_.strip()}>")
            adv_note = f" · ⚠️ за {adv}м" if adv else ""
            ping_str = ("  👥 " + " ".join(pings) + "\n") if pings else ""
            lines.append(
                f"\U0001f514 **#{rid}** \u00b7 {when_msk} \u041c\u0421\u041a \u00b7 _{cd}_\n"
                f"  \U0001f4c5 {rl} \u00b7 \U0001f4cd {dest}{adv_note}\n"
                + ping_str
                + f"  \U0001f4ac {text[:80]}{'...' if len(text)>80 else ''}"
            )

        embed = discord.Embed(
            title=f"📋 Напоминания ({len(active)})",
            description="\n\n".join(lines),
            color=discord.Color.blurple()
        )
        if len(active) > 15:
            embed.set_footer(text=f"Показано 15 из {len(active)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /удалить_напоминание ──────────────────────────────────────────────────
    @reminders_group.command(name="удалить",
                             description="Выбрать и удалить своё напоминание")
    async def удалить_напоминание(self, interaction: discord.Interaction):
        is_admin = interaction.user.guild_permissions.administrator
        with sqlite3.connect(DB_PATH) as conn:
            if is_admin:
                rows = conn.execute(
                    "SELECT id, text, remind_at, repeat, user_id"
                    " FROM reminders ORDER BY remind_at ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, text, remind_at, repeat, user_id"
                    " FROM reminders WHERE user_id=? ORDER BY remind_at ASC",
                    (interaction.user.id,)
                ).fetchall()

        now_utc = datetime.now(UTC)
        active = []
        for rid, text, ts, repeat, uid in rows:
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                if repeat != "once" or dt > now_utc:
                    active.append((rid, text, dt, repeat))
            except Exception:
                continue

        if not active:
            await interaction.response.send_message(
                "📝 Нет активных напоминаний.", ephemeral=True)
            return

        cog_ref = self
        options = []
        for rid, text, dt, repeat in active[:25]:
            when = dt.astimezone(MSK).strftime("%d.%m %H:%M")
            rl   = REPEAT_LABELS.get(repeat, repeat)
            options.append(discord.SelectOption(
                label=f"#{rid} · {when} · {rl}"[:100],
                description=text[:100],
                value=str(rid),
                emoji="🔔",
            ))

        class DeleteSelect(discord.ui.Select):
            def __init__(self):
                super().__init__(
                    placeholder="Выбери напоминание(я) для удаления...",
                    options=options,
                    min_values=1,
                    max_values=min(len(options), 5),
                )

            async def callback(self, inter: discord.Interaction):
                deleted = []
                for val in self.values:
                    r_id = int(val)
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute("DELETE FROM reminders WHERE id=?", (r_id,))
                    for jid in (f"rem_{r_id}", f"rem_{r_id}_adv"):
                        if scheduler.get_job(jid):
                            scheduler.remove_job(jid)
                    deleted.append(f"#{r_id}")
                await inter.response.edit_message(
                    content=f"🗑️ Удалено: {', '.join(deleted)}",
                    view=None,
                )

        class DeleteView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.add_item(DeleteSelect())

            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                self.stop()

        extra = f" (показано 25 из {len(active)})" if len(active) > 25 else ""
        await interaction.response.send_message(
            f"Выбери напоминания для удаления{extra}:",
            view=DeleteView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    if not scheduler.running:
        scheduler.start()
    await bot.add_cog(Tools(bot))
