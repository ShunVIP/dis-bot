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
import re

from core import reminder_service, reminder_store

MSK = ZoneInfo("Europe/Moscow")
UTC = timezone.utc

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

REPEAT_LABELS = reminder_service.REPEAT_LABELS
WEEKDAY_MAP = reminder_service.WEEKDAY_MAP

# ── БД ────────────────────────────────────────────────────────────────────────
def _ensure_db():
    reminder_store.ensure_tables()

# ── Вычисление следующего срабатывания ────────────────────────────────────────
def _next_dt(repeat: str, hour: int, minute: int,
             fixed_date: datetime | None = None) -> datetime:
    return reminder_service.next_occurrence(repeat, hour, minute, fixed_date)

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

    _schedule_advance(cog, rid, remind_utc, advance_min)


def _schedule_advance(cog, rid: int, remind_utc: datetime, advance_min: int = 0):
    """Schedule the warning for one concrete occurrence."""
    job_id = f"rem_{rid}"
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
    return reminder_service.countdown(dt_utc)

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
            rows = reminder_store.list_reminders()
        except Exception:
            return
        now_utc = datetime.now(UTC)
        for row in rows:
            try:
                rid = row["id"]
                repeat = row["repeat"]
                adv = row["advance_min"]
                dt = reminder_service.aware_utc(row["remind_at"])
                dt_msk = dt.astimezone(MSK)
                if repeat == "once":
                    if dt > now_utc:
                        _schedule(self, rid, dt, repeat, dt_msk.hour, dt_msk.minute, adv)
                    else:
                        reminder_store.delete_reminder(rid)
                else:
                    nxt = _next_dt(repeat, dt_msk.hour, dt_msk.minute)
                    reminder_store.update_remind_at(rid, nxt)
                    _schedule(self, rid, nxt, repeat, dt_msk.hour, dt_msk.minute, adv)
            except Exception:
                continue

    async def _fire(self, rid: int, is_advance: bool):
        """Отправляет напоминание. is_advance=True — предупреждение заранее."""
        try:
            row = reminder_store.get_reminder(rid)
        except Exception:
            return
        if not row:
            return
        user_id = row["user_id"]
        channel_id = row["channel_id"]
        text = row["text"]
        repeat = row["repeat"]
        adv = row["advance_min"]
        ping_str = reminder_service.ping_text(row)
        label     = REPEAT_LABELS.get(repeat, "")
        rep_note  = f" _(повторяется: {label})_" if repeat != "once" else ""

        if is_advance:
            dt = reminder_service.aware_utc(row["remind_at"])
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
                sent = True
            except Exception:
                pass

        # После основного срабатывания
        if not is_advance:
            if not sent:
                scheduler.add_job(
                    self._fire,
                    "date",
                    run_date=datetime.now(UTC) + timedelta(minutes=5),
                    args=[rid, False],
                    id=f"rem_{rid}_retry",
                    replace_existing=True,
                )
                return
            retry_job = scheduler.get_job(f"rem_{rid}_retry")
            if retry_job:
                scheduler.remove_job(retry_job.id)
            if repeat == "once":
                reminder_store.delete_reminder(rid)
            else:
                dt = reminder_service.aware_utc(row["remind_at"])
                dt_msk  = dt.astimezone(MSK)
                next_dt = _next_dt(repeat, dt_msk.hour, dt_msk.minute)
                reminder_store.update_remind_at(rid, next_dt)
                _schedule_advance(self, rid, next_dt, adv)

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
        канал          = "Куда отправить напоминание (если пусто ? текущий канал)",
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
        канал: discord.TextChannel = None,
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

        channel_id = None if лично else (канал.id if канал else interaction.channel.id)
        created = reminder_store.create_reminder(
            user_id=interaction.user.id,
            channel_id=channel_id,
            ping_users=user_ids,
            ping_roles=role_ids,
            text=текст,
            remind_at=remind_utc,
            repeat=повторение,
            advance_min=за_минут,
        )
        rid = created["id"]

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
        active = reminder_service.active_reminders(
            reminder_store.list_reminders(interaction.user.id)
        )

        if not active:
            await interaction.response.send_message("📝 Нет активных напоминаний.", ephemeral=True)
            return

        lines = []
        for row in active[:15]:
            rid = row["id"]
            text = row["text"]
            dt = reminder_service.aware_utc(row["remind_at"])
            ch_id = row["channel_id"]
            repeat = row["repeat"]
            adv = row["advance_min"]
            when_msk = dt.astimezone(MSK).strftime("%d.%m %H:%M")
            cd       = _countdown(dt)
            rl       = REPEAT_LABELS.get(repeat, repeat)
            dest     = f"<#{ch_id}>" if ch_id else "ЛС"
            pings = [f"<@{uid}>" for uid in row["ping_users"]]
            pings.extend(f"<@&{role_id}>" for role_id in row["ping_roles"])
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
        rows = reminder_store.list_reminders(None if is_admin else interaction.user.id)
        active = reminder_service.active_reminders(rows)

        if not active:
            await interaction.response.send_message(
                "📝 Нет активных напоминаний.", ephemeral=True)
            return

        cog_ref = self
        options = []
        for row in active[:25]:
            rid = row["id"]
            text = row["text"]
            repeat = row["repeat"]
            dt = reminder_service.aware_utc(row["remind_at"])
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
                    owner_id = None if is_admin else interaction.user.id
                    if not reminder_store.delete_reminder(r_id, owner_user_id=owner_id):
                        continue
                    for jid in (f"rem_{r_id}", f"rem_{r_id}_adv", f"rem_{r_id}_retry"):
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
