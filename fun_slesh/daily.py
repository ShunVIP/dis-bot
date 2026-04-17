# -*- coding: utf-8 -*-
# fun_slesh/daily.py
"""
Экономика сервера:
  /дэйлик          — ежедневная награда
  /баланс          — баланс монет
  /перевод         — передать монеты игроку
  /топ_баланс      — топ кошельков
  /топ_серии       — топ серий дэйлика

  /магазин         — просмотр магазина ролей
  /купить_роль     — купить роль из магазина
  /магазин_добавить   — (Админ) добавить роль в магазин
  /магазин_убрать     — (Админ) убрать роль из магазина

  /штраф           — (Админ) оштрафовать участника
  /налог_настроить — (Админ) включить/выключить/изменить налог
  /налог_статус    — текущие настройки налога
"""

import os, sqlite3, json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.economy import add_coins, get_balance
from utils.events_bus import emit

MSK  = ZoneInfo("Europe/Moscow")
UTC  = timezone.utc

DB_PATH  = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))
ECO_PATH = DB_PATH  # всё в одном файле

scheduler = AsyncIOScheduler(timezone=MSK)

# ── БД ────────────────────────────────────────────────────────────────────────
def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_rewards (
                user_id        INTEGER PRIMARY KEY,
                last_claim_msk TEXT    NOT NULL,
                streak         INTEGER NOT NULL DEFAULT 0
            );

            -- Магазин ролей
            CREATE TABLE IF NOT EXISTS role_shop (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                role_id     INTEGER NOT NULL UNIQUE,
                role_name   TEXT    NOT NULL,
                price       INTEGER NOT NULL,
                duration_h  INTEGER NOT NULL DEFAULT 0,  -- 0 = навсегда
                added_by    INTEGER NOT NULL,
                added_at    TEXT    NOT NULL
            );

            -- Купленные временные роли (для автоудаления)
            CREATE TABLE IF NOT EXISTS temp_roles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                role_id     INTEGER NOT NULL,
                expires_at  TEXT    NOT NULL
            );

            -- Настройки налога
            CREATE TABLE IF NOT EXISTS tax_config (
                id          INTEGER PRIMARY KEY DEFAULT 1,
                enabled     INTEGER NOT NULL DEFAULT 0,
                rate_pct    INTEGER NOT NULL DEFAULT 10,
                interval_h  INTEGER NOT NULL DEFAULT 168,
                last_run    TEXT    NOT NULL DEFAULT ''
            );
        """)
        # Вставляем строку конфига если нет
        conn.execute(
            "INSERT OR IGNORE INTO tax_config(id, enabled, rate_pct, interval_h, last_run)"
            " VALUES(1, 0, 10, 168, '')"
        )
        conn.commit()


# ── Вспомогательные ───────────────────────────────────────────────────────────
def _milestone_bonus(streak: int) -> int:
    return 25 if streak in (7, 14, 30, 60, 100) else 0

def _compute_reward(streak: int) -> int:
    base   = 25
    series = 5 * min(max(streak - 1, 0), 7)
    return base + series + _milestone_bonus(streak)

def _tax_config() -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT enabled, rate_pct, interval_h, last_run FROM tax_config WHERE id=1"
        ).fetchone()
    if not row:
        return {"enabled": 0, "rate_pct": 10, "interval_h": 168, "last_run": ""}
    return {"enabled": row[0], "rate_pct": row[1], "interval_h": row[2], "last_run": row[3]}


# ── Налог (запускается планировщиком) ─────────────────────────────────────────
async def _run_tax(bot: commands.Bot):
    cfg = _tax_config()
    if not cfg["enabled"]:
        return

    with sqlite3.connect(DB_PATH) as conn:
        wallets = conn.execute(
            "SELECT user_id, balance FROM coins_wallet WHERE balance > 0"
        ).fetchall()

    total_collected = 0
    for user_id, balance in wallets:
        tax = max(1, int(balance * cfg["rate_pct"] / 100))
        add_coins(user_id, -tax, reason="tax", meta={"rate": cfg["rate_pct"]})
        total_collected += tax

    # Обновляем last_run
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE tax_config SET last_run=? WHERE id=1",
            (datetime.now(UTC).isoformat(),)
        )


# ── Cog ───────────────────────────────────────────────────────────────────────
class Daily(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_tables()
        self._start_scheduler()

    def _start_scheduler(self):
        cfg = _tax_config()
        if cfg["enabled"]:
            self._reschedule_tax(cfg["interval_h"])
        if not scheduler.running:
            scheduler.start()

    def _reschedule_tax(self, interval_h: int):
        job_id = "tax_job"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        scheduler.add_job(
            _run_tax, "interval", hours=interval_h,
            args=[self.bot], id=job_id, replace_existing=True
        )

    # ── /баланс ───────────────────────────────────────────────────────────────
    @app_commands.command(name="баланс", description="Баланс монет (свой или другого участника)")
    @app_commands.describe(пользователь="Чей баланс посмотреть")
    async def баланс(self, interaction: discord.Interaction,
                     пользователь: discord.Member | None = None):
        target = пользователь or interaction.user
        bal = get_balance(target.id)

        # История последних операций
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT delta, reason, created_at FROM coin_ledger"
                " WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
                (target.id,)
            ).fetchall()

        emb = discord.Embed(
            title=f"💰 Баланс: {target.display_name}",
            description=f"**{bal}** монет",
            color=discord.Color.gold()
        )
        if rows:
            lines = []
            for delta, reason, ts in rows:
                sign  = "+" if delta >= 0 else ""
                dt    = datetime.fromisoformat(ts).astimezone(MSK).strftime("%d.%m %H:%M")
                label = {"daily": "дэйлик", "tax": "налог", "transfer_out": "перевод →",
                         "transfer_in": "← перевод", "fine": "штраф",
                         "shop": "магазин", "rep": "репутация",
                         "game_win": "игра 🎉", "game_lose": "игра 💸"}.get(reason, reason)
                lines.append(f"`{dt}` {sign}{delta} — {label}")
            emb.add_field(name="Последние операции", value="\n".join(lines), inline=False)

        await interaction.response.send_message(embed=emb)

    # ── /дэйлик ───────────────────────────────────────────────────────────────
    @app_commands.command(name="дэйлик", description="Забрать ежедневную награду (по МСК)")
    async def дэйлик(self, interaction: discord.Interaction):
        _ensure_tables()
        today_msk     = datetime.now(MSK).date()
        yesterday_msk = today_msk - timedelta(days=1)

        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT last_claim_msk, streak FROM daily_rewards WHERE user_id=?",
                (interaction.user.id,)
            ).fetchone()

            if row:
                last_str, streak = row
                try:
                    last_date = datetime.fromisoformat(last_str).date()
                except Exception:
                    last_date = yesterday_msk - timedelta(days=1)
                if last_date == today_msk:
                    next_msk = datetime.now(MSK).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ) + timedelta(days=1)
                    ts = int(next_msk.timestamp())
                    await interaction.response.send_message(
                        f"⛔ Уже забрал сегодня. Следующий дэйлик <t:{ts}:R>",
                        ephemeral=True
                    )
                    return
                streak = int(streak) + 1 if last_date == yesterday_msk else 1
            else:
                streak = 1

            reward = _compute_reward(streak)
            conn.execute(
                "INSERT INTO daily_rewards(user_id, last_claim_msk, streak) VALUES(?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET"
                " last_claim_msk=excluded.last_claim_msk, streak=excluded.streak",
                (interaction.user.id, today_msk.isoformat(), streak)
            )

        new_balance = add_coins(
            interaction.user.id, reward,
            reason="daily", meta={"streak": streak}
        )
        await emit("daily_claimed",
                   user_id=interaction.user.id, streak=streak, amount=reward)

        bonus_note = ""
        if streak in (7, 14, 30, 60, 100):
            bonus_note = f"\n🎉 Бонус за серию {streak} дней: **+25** монет!"

        tip = ("Ещё +5 к бонусу завтра." if streak < 7
               else "Серия на максимуме (+35/день).")

        emb = discord.Embed(
            title="🎁 Ежедневная награда",
            color=discord.Color.teal()
        )
        emb.add_field(name="Получено",  value=f"**{reward}** монет", inline=True)
        emb.add_field(name="Серия",     value=f"**{streak}** дней",  inline=True)
        emb.add_field(name="Баланс",    value=f"**{new_balance}**",  inline=True)
        if bonus_note:
            emb.add_field(name="🎊 Веха!", value=bonus_note, inline=False)
        emb.set_footer(text=tip)
        await interaction.response.send_message(embed=emb)

    # ── /перевод ──────────────────────────────────────────────────────────────
    @app_commands.command(name="перевод", description="Перевести монеты другому участнику")
    @app_commands.describe(
        получатель="Кому переводить",
        сумма="Сколько монет (минимум 1)",
    )
    async def перевод(self, interaction: discord.Interaction,
                      получатель: discord.Member,
                      сумма: app_commands.Range[int, 1, 1_000_000]):
        if получатель.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ Нельзя переводить самому себе.", ephemeral=True)
            return
        if получатель.bot:
            await interaction.response.send_message(
                "❌ Нельзя переводить ботам.", ephemeral=True)
            return

        bal = get_balance(interaction.user.id)
        if bal < сумма:
            await interaction.response.send_message(
                f"❌ Недостаточно монет. Баланс: **{bal}**.", ephemeral=True)
            return

        add_coins(interaction.user.id, -сумма, "transfer_out",
                  {"to": получатель.id})
        new_bal = add_coins(получатель.id,  сумма, "transfer_in",
                  {"from": interaction.user.id})

        emb = discord.Embed(
            title="💸 Перевод выполнен",
            color=discord.Color.green()
        )
        emb.add_field(name="От",     value=interaction.user.mention, inline=True)
        emb.add_field(name="Кому",   value=получатель.mention,       inline=True)
        emb.add_field(name="Сумма",  value=f"**{сумма}** монет",     inline=True)
        emb.add_field(name="Остаток отправителя",
                      value=f"**{get_balance(interaction.user.id)}**", inline=True)
        emb.add_field(name="Баланс получателя",
                      value=f"**{new_bal}**", inline=True)
        await interaction.response.send_message(embed=emb)

    # ── /штраф ────────────────────────────────────────────────────────────────
    @app_commands.command(name="штраф", description="(Админ) Оштрафовать участника")
    @app_commands.describe(
        участник="Кого штрафовать",
        сумма="Размер штрафа",
        причина="Причина штрафа",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def штраф(self, interaction: discord.Interaction,
                    участник: discord.Member,
                    сумма: app_commands.Range[int, 1, 1_000_000],
                    причина: str = "Нарушение правил"):
        bal = get_balance(участник.id)
        actual = min(сумма, bal)   # не уходим в минус
        if actual > 0:
            add_coins(участник.id, -actual, "fine",
                      {"by": interaction.user.id, "reason": причина})

        emb = discord.Embed(
            title="⚖️ Штраф выписан",
            color=discord.Color.red()
        )
        emb.add_field(name="Участник",  value=участник.mention,   inline=True)
        emb.add_field(name="Штраф",     value=f"**{actual}**",    inline=True)
        emb.add_field(name="Остаток",
                      value=f"**{get_balance(участник.id)}**",    inline=True)
        emb.add_field(name="Причина",   value=причина,            inline=False)
        if actual < сумма:
            emb.set_footer(text=f"⚠️ Баланс был {bal}, списано по максимуму.")
        await interaction.response.send_message(embed=emb)

        # Уведомляем участника в ЛС
        try:
            await участник.send(
                f"⚖️ Вам выписан штраф **{actual}** монет на сервере.\n"
                f"Причина: {причина}\nОстаток: **{get_balance(участник.id)}**"
            )
        except Exception:
            pass

    # ── /налог_настроить ──────────────────────────────────────────────────────
    @app_commands.command(name="налог_настроить",
                          description="(Админ) Включить/выключить налог и задать ставку")
    @app_commands.describe(
        включить="Включить налог",
        ставка="Процент списания (1–50%)",
        каждые_часов="Интервал взимания в часах (минимум 1)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def налог_настроить(self, interaction: discord.Interaction,
                               включить: bool,
                               ставка: app_commands.Range[int, 1, 50] = 10,
                               каждые_часов: app_commands.Range[int, 1, 720] = 168):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE tax_config SET enabled=?, rate_pct=?, interval_h=? WHERE id=1",
                (int(включить), ставка, каждые_часов)
            )

        if включить:
            self._reschedule_tax(каждые_часов)
            if not scheduler.running:
                scheduler.start()
            status = f"✅ Налог включён: **{ставка}%** каждые **{каждые_часов}ч**"
        else:
            job_id = "tax_job"
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
            status = "⛔ Налог выключен."

        await interaction.response.send_message(status, ephemeral=True)

    # ── /налог_статус ─────────────────────────────────────────────────────────
    @app_commands.command(name="налог_статус", description="Текущие настройки налога")
    async def налог_статус(self, interaction: discord.Interaction):
        cfg = _tax_config()
        enabled = "✅ Включён" if cfg["enabled"] else "⛔ Выключен"
        last    = cfg["last_run"] or "ещё не запускался"
        if cfg["last_run"]:
            try:
                last = datetime.fromisoformat(cfg["last_run"]).astimezone(MSK).strftime("%d.%m.%Y %H:%M МСК")
            except Exception:
                pass
        emb = discord.Embed(title="💸 Налог", color=discord.Color.orange())
        emb.add_field(name="Статус",       value=enabled,               inline=True)
        emb.add_field(name="Ставка",       value=f"{cfg['rate_pct']}%", inline=True)
        emb.add_field(name="Интервал",     value=f"{cfg['interval_h']}ч", inline=True)
        emb.add_field(name="Последний раз", value=last,                 inline=False)
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # ── /магазин ──────────────────────────────────────────────────────────────
    @app_commands.command(name="магазин", description="Магазин ролей за монеты")
    async def магазин(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT id, role_id, role_name, price, duration_h FROM role_shop ORDER BY price ASC"
            ).fetchall()

        if not rows:
            await interaction.response.send_message(
                "🛒 Магазин пуст. Администратор может добавить роли через `/магазин_добавить`.",
                ephemeral=True)
            return

        emb = discord.Embed(title="🛒 Магазин ролей", color=discord.Color.blurple())
        bal = get_balance(interaction.user.id)
        lines = []
        for shop_id, role_id, role_name, price, dur in rows:
            dur_str = f"{dur}ч" if dur else "навсегда"
            can     = "✅" if bal >= price else "❌"
            lines.append(f"{can} **{role_name}** — {price} монет ({dur_str})  `ID:{shop_id}`")
        emb.description = "\n".join(lines)
        emb.set_footer(text=f"Твой баланс: {bal} монет · /купить_роль id:<ID>")
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # ── /купить_роль ──────────────────────────────────────────────────────────
    @app_commands.command(name="купить_роль", description="Купить роль из магазина")
    @app_commands.describe(id="ID роли из /магазин")
    async def купить_роль(self, interaction: discord.Interaction,
                          id: app_commands.Range[int, 1, 999999]):
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT role_id, role_name, price, duration_h FROM role_shop WHERE id=?", (id,)
            ).fetchone()
        if not row:
            await interaction.response.send_message("❌ Роль не найдена в магазине.", ephemeral=True)
            return

        role_id, role_name, price, dur = row
        bal = get_balance(interaction.user.id)
        if bal < price:
            await interaction.response.send_message(
                f"❌ Недостаточно монет. Нужно **{price}**, у тебя **{bal}**.", ephemeral=True)
            return

        role = interaction.guild.get_role(role_id)
        if not role:
            await interaction.response.send_message(
                "❌ Роль не существует на сервере. Сообщи администратору.", ephemeral=True)
            return

        # Проверяем не купил ли уже
        if role in interaction.user.roles:
            await interaction.response.send_message(
                f"❌ У тебя уже есть роль **{role_name}**.", ephemeral=True)
            return

        await interaction.user.add_roles(role, reason="Покупка в магазине")
        add_coins(interaction.user.id, -price, "shop", {"role_id": role_id, "role_name": role_name})

        dur_str = f"на {dur}ч" if dur else "навсегда"
        emb = discord.Embed(
            title="🛍️ Покупка совершена!",
            description=f"Получена роль **{role_name}** ({dur_str})\nСписано: **{price}** монет\nОстаток: **{get_balance(interaction.user.id)}**",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=emb)

        # Планируем удаление временной роли
        if dur > 0:
            expires = datetime.now(UTC) + timedelta(hours=dur)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "INSERT INTO temp_roles(user_id, role_id, expires_at) VALUES(?,?,?)",
                    (interaction.user.id, role_id, expires.isoformat())
                )
            scheduler.add_job(
                self._remove_temp_role, "date", run_date=expires,
                args=[interaction.user.id, role_id, interaction.guild.id],
                replace_existing=True,
                id=f"temprole_{interaction.user.id}_{role_id}"
            )

    async def _remove_temp_role(self, user_id: int, role_id: int, guild_id: int):
        try:
            guild  = self.bot.get_guild(guild_id)
            member = guild.get_member(user_id)
            role   = guild.get_role(role_id)
            if member and role and role in member.roles:
                await member.remove_roles(role, reason="Временная роль истекла")
                try:
                    await member.send(f"⏰ Временная роль **{role.name}** истекла.")
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "DELETE FROM temp_roles WHERE user_id=? AND role_id=?",
                    (user_id, role_id)
                )

    # ── /магазин_добавить ─────────────────────────────────────────────────────
    @app_commands.command(name="магазин_добавить",
                          description="(Админ) Добавить роль в магазин")
    @app_commands.describe(
        роль="Роль для добавления",
        цена="Цена в монетах",
        длительность_ч="0 = навсегда, иначе кол-во часов",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def магазин_добавить(self, interaction: discord.Interaction,
                                роль: discord.Role,
                                цена: app_commands.Range[int, 1, 1_000_000],
                                длительность_ч: app_commands.Range[int, 0, 8760] = 0):
        with sqlite3.connect(DB_PATH) as conn:
            try:
                conn.execute(
                    "INSERT INTO role_shop(role_id, role_name, price, duration_h, added_by, added_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (роль.id, роль.name, цена, длительность_ч,
                     interaction.user.id, datetime.now(UTC).isoformat())
                )
            except sqlite3.IntegrityError:
                conn.execute(
                    "UPDATE role_shop SET price=?, duration_h=? WHERE role_id=?",
                    (цена, длительность_ч, роль.id)
                )
        dur_str = f"{длительность_ч}ч" if длительность_ч else "навсегда"
        await interaction.response.send_message(
            f"✅ Роль **{роль.name}** добавлена в магазин: **{цена}** монет ({dur_str}).",
            ephemeral=True)

    # ── /магазин_убрать ───────────────────────────────────────────────────────
    @app_commands.command(name="магазин_убрать",
                          description="(Админ) Убрать роль из магазина")
    @app_commands.describe(id="ID позиции из /магазин")
    @app_commands.checks.has_permissions(administrator=True)
    async def магазин_убрать(self, interaction: discord.Interaction,
                              id: app_commands.Range[int, 1, 999999]):
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT role_name FROM role_shop WHERE id=?", (id,)
            ).fetchone()
            if not row:
                await interaction.response.send_message(
                    "❌ Позиция не найдена.", ephemeral=True)
                return
            conn.execute("DELETE FROM role_shop WHERE id=?", (id,))
        await interaction.response.send_message(
            f"✅ Роль **{row[0]}** убрана из магазина.", ephemeral=True)

    # ── /топ_серии ────────────────────────────────────────────────────────────
    @app_commands.command(name="топ_серии",
                          description="Топ по сериям дэйлика среди участников сервера")
    async def топ_серии(self, interaction: discord.Interaction):
        _ensure_tables()
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT user_id, streak FROM daily_rewards ORDER BY streak DESC LIMIT 100"
            ).fetchall()

        present = []
        for user_id, streak in rows:
            m = interaction.guild.get_member(int(user_id))
            if m:
                present.append((int(streak), m.display_name))

        if not present:
            await interaction.response.send_message("😶 Ни у кого нет серии.")
            return

        present.sort(reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"{medals[i] if i < 3 else f'**{i+1}.**'} {name} — **{st}** дней"
            for i, (st, name) in enumerate(present[:10])
        ]
        emb = discord.Embed(
            title="🔥 Топ серий (дэйлик)",
            description="\n".join(lines),
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=emb)

    # ── /топ_баланс ───────────────────────────────────────────────────────────
    @app_commands.command(name="топ_баланс",
                          description="Топ богатейших участников сервера")
    async def топ_баланс(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT user_id, balance FROM coins_wallet ORDER BY balance DESC LIMIT 100"
            ).fetchall()

        present = []
        for user_id, bal in rows:
            m = interaction.guild.get_member(int(user_id))
            if m:
                present.append((int(bal), m.display_name))

        if not present:
            await interaction.response.send_message("😶 Нет кошельков.")
            return

        present.sort(reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"{medals[i] if i < 3 else f'**{i+1}.**'} {name} — **{bal}** монет"
            for i, (bal, name) in enumerate(present[:10])
        ]
        emb = discord.Embed(
            title="💰 Топ баланса",
            description="\n".join(lines),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=emb)


async def setup(bot: commands.Bot):
    await bot.add_cog(Daily(bot))
