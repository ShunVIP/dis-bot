# -*- coding: utf-8 -*-
# fun_slesh/daily.py
"""
Экономика сервера:
  /дэйлик          — ежедневная награда
  /баланс          — баланс Сисек
  /перевод         — передать Сиськи игроку
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

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import asyncio

import discord
from discord.ext import commands
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core import daily_service, daily_store
from core.economy import (
    add_coins,
    debit_coins,
    get_balance,
    list_ledger_entries,
    list_wallets,
    transfer_coins,
)
from core.economy_profile import (
    GENDER_FEMALE,
    GENDER_MALE,
    can_receive_currency,
    currency_amount,
    currency_name,
    economy_profile_required_text,
    set_economy_profile,
)
from core.settings_store import set_feature_payload
from utils.events_bus import emit

MSK  = ZoneInfo("Europe/Moscow")
UTC  = timezone.utc

FEATURE_ECONOMY = "economy"

scheduler = AsyncIOScheduler(timezone=MSK)

# Compatibility aliases for callers that reload this cog directly.
_ensure_tables = daily_store.ensure_tables
_compute_reward = daily_service.compute_reward
_tax_config = daily_service.tax_config


def _primary_guild_id(bot: commands.Bot) -> int | None:
    guild = next(iter(bot.guilds), None)
    return int(guild.id) if guild else None


# ── Налог (запускается планировщиком) ─────────────────────────────────────────
async def _run_tax(bot: commands.Bot):
    daily_service.collect_tax(_primary_guild_id(bot))


# ── Cog ───────────────────────────────────────────────────────────────────────
class Daily(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._restore_task: asyncio.Task | None = None
        _ensure_tables()
        self._start_scheduler()

    async def cog_load(self):
        self._restore_task = asyncio.create_task(
            self._restore_temp_roles_after_ready(),
            name="daily-temp-role-restore",
        )

    def cog_unload(self):
        if self._restore_task and not self._restore_task.done():
            self._restore_task.cancel()

    async def _restore_temp_roles_after_ready(self):
        await self.bot.wait_until_ready()
        fallback_guild_id = _primary_guild_id(self.bot)
        now = datetime.now(UTC)
        for item in daily_store.list_temp_roles():
            guild_id = int(item["guild_id"] or fallback_guild_id or 0)
            if not guild_id:
                continue
            run_date = item["expires_at"]
            if run_date <= now:
                run_date = now + timedelta(seconds=1)
            scheduler.add_job(
                self._remove_temp_role,
                "date",
                run_date=run_date,
                args=[item["user_id"], item["role_id"], guild_id],
                replace_existing=True,
                id=f"temprole_{item['user_id']}_{item['role_id']}",
            )

    def _start_scheduler(self):
        cfg = _tax_config(_primary_guild_id(self.bot))
        if cfg["enabled"]:
            self._reschedule_tax(cfg["interval_h"])
        if not scheduler.running:
            scheduler.start()

    def refresh_tax_schedule(self):
        cfg = _tax_config(_primary_guild_id(self.bot))
        if cfg["enabled"]:
            self._reschedule_tax(cfg["interval_h"])
        elif scheduler.get_job("tax_job"):
            scheduler.remove_job("tax_job")

    def _reschedule_tax(self, interval_h: int):
        job_id = "tax_job"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        scheduler.add_job(
            _run_tax, "interval", hours=interval_h,
            args=[self.bot], id=job_id, replace_existing=True
        )

    @app_commands.command(name="экономика_профиль", description="Заполнить профиль для получения валюты 18+")
    @app_commands.describe(
        пол="Как называть твою валюту",
        подтверждаю_18="Подтверждаю, что мне есть 18 лет",
    )
    @app_commands.choices(пол=[
        app_commands.Choice(name="Мужчина - валюта Пенис", value=GENDER_MALE),
        app_commands.Choice(name="Девушка - валюта Сиськи", value=GENDER_FEMALE),
    ])
    async def economy_profile(
        self,
        interaction: discord.Interaction,
        пол: app_commands.Choice[str],
        подтверждаю_18: bool,
    ):
        if not подтверждаю_18:
            await interaction.response.send_message(
                "Без подтверждения 18+ валюта не начисляется.", ephemeral=True
            )
            return
        set_economy_profile(interaction.user.id, пол.value, True)
        await interaction.response.send_message(
            f"Готово. Твоя валюта теперь: **{currency_name(interaction.user.id)}**. "
            f"Репутация отображается как метафорический Размер.",
            ephemeral=True,
        )

    # ── /баланс ───────────────────────────────────────────────────────────────
    @app_commands.command(name="баланс", description="Баланс персональной валюты")
    @app_commands.describe(пользователь="Чей баланс посмотреть")
    async def баланс(self, interaction: discord.Interaction,
                     пользователь: discord.Member | None = None):
        target = пользователь or interaction.user
        bal = get_balance(target.id)

        rows = list_ledger_entries(target.id, limit=5)

        emb = discord.Embed(
            title=f"💰 Баланс: {target.display_name}",
            description=f"**{currency_amount(target.id, bal)}**",
            color=discord.Color.gold()
        )
        if rows:
            lines = []
            for row in rows:
                delta = row["delta"]
                reason = row["reason"]
                sign  = "+" if delta >= 0 else ""
                dt    = datetime.fromisoformat(row["created_at"]).astimezone(MSK).strftime("%d.%m %H:%M")
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
        result = daily_service.claim_daily(interaction.user.id)
        if result["status"] == "profile_required":
            await interaction.response.send_message(economy_profile_required_text(), ephemeral=True)
            return
        if result["status"] == "already_claimed":
            await interaction.response.send_message(
                f"⛔ Уже забрал сегодня. Следующий дэйлик <t:{result['next_claim_timestamp']}:R>",
                ephemeral=True,
            )
            return

        streak = int(result["streak"])
        reward = int(result["reward"])
        new_balance = int(result["balance"])
        await emit("daily_claimed",
                   user_id=interaction.user.id, streak=streak, amount=reward)

        bonus_note = ""
        if streak in (7, 14, 30, 60, 100):
            bonus_note = f"\n🎉 Бонус за серию {streak} дней: **{currency_amount(interaction.user.id, 25)}**!"

        tip = ("Ещё +5 к бонусу завтра." if streak < 7
               else "Серия на максимуме (+35/день).")

        emb = discord.Embed(
            title="🎁 Ежедневная награда",
            color=discord.Color.teal()
        )
        emb.add_field(name="Получено",  value=f"**{currency_amount(interaction.user.id, reward)}**", inline=True)
        emb.add_field(name="Серия",     value=f"**{streak}** дней",  inline=True)
        emb.add_field(name="Баланс",    value=f"**{new_balance}**",  inline=True)
        if bonus_note:
            emb.add_field(name="🎊 Веха!", value=bonus_note, inline=False)
        emb.set_footer(text=tip)
        await interaction.response.send_message(embed=emb)

    # ── /перевод ──────────────────────────────────────────────────────────────
    @app_commands.command(name="перевод", description="Перевести персональную валюту другому участнику")
    @app_commands.describe(
        получатель="Кому переводить",
        сумма="Сколько валюты перевести (минимум 1)",
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
        result = transfer_coins(interaction.user.id, получатель.id, сумма)
        if result["status"] == "recipient_profile_required":
            await interaction.response.send_message(
                f"❌ {получатель.display_name} ещё не заполнил профиль 18+ и не может получать валюту.",
                ephemeral=True,
            )
            return
        if result["status"] == "insufficient":
            await interaction.response.send_message(
                f"❌ Недостаточно {currency_name(interaction.user.id)}. "
                f"Баланс: **{result['sender_balance']}**.",
                ephemeral=True,
            )
            return
        if result["status"] != "transferred":
            await interaction.response.send_message("❌ Перевод не выполнен.", ephemeral=True)
            return

        emb = discord.Embed(
            title="💸 Перевод выполнен",
            color=discord.Color.green()
        )
        emb.add_field(name="От",     value=interaction.user.mention, inline=True)
        emb.add_field(name="Кому",   value=получатель.mention,       inline=True)
        emb.add_field(name="Сумма",  value=f"**{currency_amount(получатель.id, сумма)}**",     inline=True)
        emb.add_field(name="Остаток отправителя",
                      value=f"**{result['sender_balance']}**", inline=True)
        emb.add_field(name="Баланс получателя",
                      value=f"**{result['recipient_balance']}**", inline=True)
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
        result = daily_service.fine_user(
            участник.id,
            сумма,
            moderator_id=interaction.user.id,
            reason=причина,
        )
        bal = result["before"]
        actual = result["actual"]

        emb = discord.Embed(
            title="⚖️ Штраф выписан",
            color=discord.Color.red()
        )
        emb.add_field(name="Участник",  value=участник.mention,   inline=True)
        emb.add_field(name="Штраф",     value=f"**{actual}**",    inline=True)
        emb.add_field(name="Остаток",
                      value=f"**{result['remaining']}**",    inline=True)
        emb.add_field(name="Причина",   value=причина,            inline=False)
        if actual < сумма:
            emb.set_footer(text=f"⚠️ Баланс был {bal}, списано по максимуму.")
        await interaction.response.send_message(embed=emb)

        # Уведомляем участника в ЛС
        try:
            await участник.send(
                f"⚖️ Вам выписан штраф **{actual}** Сисек на сервере.\n"
                f"Причина: {причина}\nОстаток: **{result['remaining']}**"
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
        set_feature_payload(
            interaction.guild.id,
            FEATURE_ECONOMY,
            {
                "tax_enabled": bool(включить),
                "tax_rate_pct": int(ставка),
                "tax_interval_h": int(каждые_часов),
            },
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
        cfg = _tax_config(interaction.guild.id if interaction.guild else None)
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
    @app_commands.command(name="магазин", description="Магазин ролей за персональную валюту")
    async def магазин(self, interaction: discord.Interaction):
        rows = daily_store.list_shop_items()

        if not rows:
            await interaction.response.send_message(
                "🛒 Магазин пуст. Администратор может добавить роли через `/магазин_добавить`.",
                ephemeral=True)
            return

        emb = discord.Embed(title="🛒 Магазин ролей", color=discord.Color.blurple())
        bal = get_balance(interaction.user.id)
        lines = []
        for item in rows:
            shop_id = item["id"]
            role_name = item["role_name"]
            price = item["price"]
            dur = item["duration_h"]
            dur_str = f"{dur}ч" if dur else "навсегда"
            can     = "✅" if bal >= price else "❌"
            lines.append(f"{can} **{role_name}** — {currency_amount(interaction.user.id, price)} ({dur_str})  `ID:{shop_id}`")
        emb.description = "\n".join(lines)
        emb.set_footer(text=f"Твой баланс: {currency_amount(interaction.user.id, bal)} · /купить_роль id:<ID>")
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # ── /купить_роль ──────────────────────────────────────────────────────────
    @app_commands.command(name="купить_роль", description="Купить роль из магазина")
    @app_commands.describe(id="ID роли из /магазин")
    async def купить_роль(self, interaction: discord.Interaction,
                          id: app_commands.Range[int, 1, 999999]):
        item = daily_store.get_shop_item(id)
        if not item:
            await interaction.response.send_message("❌ Роль не найдена в магазине.", ephemeral=True)
            return

        role_id = item["role_id"]
        role_name = item["role_name"]
        price = item["price"]
        dur = item["duration_h"]
        if not can_receive_currency(interaction.user.id):
            await interaction.response.send_message(economy_profile_required_text(), ephemeral=True)
            return
        bal = get_balance(interaction.user.id)
        if bal < price:
            await interaction.response.send_message(
                f"❌ Недостаточно {currency_name(interaction.user.id)}. Нужно **{price}**, у тебя **{bal}**.", ephemeral=True)
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

        debit = debit_coins(
            interaction.user.id,
            price,
            "shop",
            {"role_id": role_id, "role_name": role_name},
        )
        if debit["status"] != "debited":
            await interaction.response.send_message(
                f"❌ Баланс изменился, покупка не выполнена. Сейчас: **{debit.get('balance', bal)}**.",
                ephemeral=True,
            )
            return
        try:
            await interaction.user.add_roles(role, reason="Покупка в магазине")
        except discord.HTTPException:
            add_coins(
                interaction.user.id,
                price,
                "shop_refund",
                {"role_id": role_id, "role_name": role_name},
            )
            await interaction.response.send_message(
                "❌ Discord не выдал роль. Списание автоматически возвращено.",
                ephemeral=True,
            )
            return

        dur_str = f"на {dur}ч" if dur else "навсегда"
        emb = discord.Embed(
            title="🛍️ Покупка совершена!",
            description=f"Получена роль **{role_name}** ({dur_str})\nСписано: **{currency_amount(interaction.user.id, price)}**\nОстаток: **{currency_amount(interaction.user.id, debit['balance'])}**",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=emb)

        # Планируем удаление временной роли
        if dur > 0:
            expires = datetime.now(UTC) + timedelta(hours=dur)
            try:
                daily_store.save_temp_role(
                    interaction.user.id,
                    role_id,
                    interaction.guild.id,
                    expires,
                )
            except Exception:
                try:
                    await interaction.user.remove_roles(role, reason="Откат покупки: срок не сохранён")
                except discord.HTTPException:
                    pass
                add_coins(
                    interaction.user.id,
                    price,
                    "shop_refund",
                    {"role_id": role_id, "role_name": role_name, "reason": "persistence"},
                )
                await interaction.followup.send(
                    "❌ Не удалось сохранить срок роли. Роль снята, списание возвращено.",
                    ephemeral=True,
                )
                return
            scheduler.add_job(
                self._remove_temp_role, "date", run_date=expires,
                args=[interaction.user.id, role_id, interaction.guild.id],
                replace_existing=True,
                id=f"temprole_{interaction.user.id}_{role_id}"
            )

    async def _remove_temp_role(self, user_id: int, role_id: int, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            scheduler.add_job(
                self._remove_temp_role,
                "date",
                run_date=datetime.now(UTC) + timedelta(minutes=5),
                args=[user_id, role_id, guild_id],
                replace_existing=True,
                id=f"temprole_{user_id}_{role_id}",
            )
            return
        try:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    member = None
            role   = guild.get_role(role_id)
            if member and role and role in member.roles:
                await member.remove_roles(role, reason="Временная роль истекла")
                try:
                    await member.send(f"⏰ Временная роль **{role.name}** истекла.")
                except Exception:
                    pass
        except discord.HTTPException:
            scheduler.add_job(
                self._remove_temp_role,
                "date",
                run_date=datetime.now(UTC) + timedelta(minutes=5),
                args=[user_id, role_id, guild_id],
                replace_existing=True,
                id=f"temprole_{user_id}_{role_id}",
            )
            return
        daily_store.delete_temp_role(user_id, role_id)

    # ── /магазин_добавить ─────────────────────────────────────────────────────
    @app_commands.command(name="магазин_добавить",
                          description="(Админ) Добавить роль в магазин")
    @app_commands.describe(
        роль="Роль для добавления",
        цена="Цена в Сиськах",
        длительность_ч="0 = навсегда, иначе кол-во часов",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def магазин_добавить(self, interaction: discord.Interaction,
                                роль: discord.Role,
                                цена: app_commands.Range[int, 1, 1_000_000],
                                длительность_ч: app_commands.Range[int, 0, 8760] = 0):
        daily_store.upsert_shop_item(
            роль.id,
            роль.name,
            цена,
            длительность_ч,
            interaction.user.id,
        )
        dur_str = f"{длительность_ч}ч" if длительность_ч else "навсегда"
        await interaction.response.send_message(
            f"✅ Роль **{роль.name}** добавлена в магазин: **{цена}** валюты ({dur_str}).",
            ephemeral=True)

    # ── /магазин_убрать ───────────────────────────────────────────────────────
    @app_commands.command(name="магазин_убрать",
                          description="(Админ) Убрать роль из магазина")
    @app_commands.describe(id="ID позиции из /магазин")
    @app_commands.checks.has_permissions(administrator=True)
    async def магазин_убрать(self, interaction: discord.Interaction,
                              id: app_commands.Range[int, 1, 999999]):
        item = daily_store.delete_shop_item(id)
        if not item:
            await interaction.response.send_message(
                "❌ Позиция не найдена.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ Роль **{item['role_name']}** убрана из магазина.", ephemeral=True)

    # ── /топ_серии ────────────────────────────────────────────────────────────
    @app_commands.command(name="топ_серии",
                          description="Топ по сериям дэйлика среди участников сервера")
    async def топ_серии(self, interaction: discord.Interaction):
        rows = daily_store.list_daily_streaks(limit=100)

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
        rows = list_wallets(limit=100)

        present = []
        for user_id, bal in rows:
            m = interaction.guild.get_member(int(user_id))
            if m:
                present.append((int(bal), m.display_name, int(user_id)))

        if not present:
            await interaction.response.send_message("😶 Нет кошельков.")
            return

        present.sort(reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"{medals[i] if i < 3 else f'**{i+1}.**'} {name} — **{bal}** {currency_name(user_id)}"
            for i, (bal, name, user_id) in enumerate(present[:10])
        ]
        emb = discord.Embed(
            title="💰 Топ баланса",
            description="\n".join(lines),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=emb)


async def setup(bot: commands.Bot):
    await bot.add_cog(Daily(bot))
