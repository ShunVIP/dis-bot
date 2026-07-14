# -*- coding: utf-8 -*-
# fun_slesh/rep_roles.py
"""
Система ролей по Размера:
  - Бот создаёт роль из характерных слов корпуса при достижении порога
  - Одна активная Размер-роль на человека
  - Роль живёт 7 дней, потом обновляется (та же или новая если порог вырос)
  - Админ может сделать роль постоянной

Команды:
  /репа_роли           — список настроенных порогов
  /репа_роль_добавить  — (Админ) добавить порог
  /репа_роль_убрать    — (Админ) убрать порог
  /репа_роль_постоянная — (Админ) сделать чью-то Размер-роль постоянной
  /моя_репа_роль       — посмотреть свою текущую роль и когда обновится
"""

import random
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.rep_roles_service import generate_role_name as _generate_role_name
from core.rep_roles_store import (
    best_threshold as _best_threshold,
    delete_threshold,
    ensure_rep_roles_storage,
    extend_active_role,
    get_active_role,
    get_reputation as _get_rep,
    get_threshold,
    list_expiring_roles,
    list_thresholds,
    make_role_permanent,
    next_threshold,
    rep_roles_enabled,
    save_active_role,
    set_rep_roles_enabled,
    update_threshold,
    upsert_threshold,
)

UTC      = timezone.utc

ROLE_DURATION_DAYS = 7

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# ── БД ────────────────────────────────────────────────────────────────────────
# ── Выдать / обновить роль участнику ─────────────────────────────────────────
async def assign_rep_role(bot: commands.Bot, guild_id: int, user_id: int):
    """
    Основная логика: определяем порог, генерируем название,
    создаём роль, выдаём, сохраняем. Старую удаляем.
    """
    threshold_row = _best_threshold(guild_id, _get_rep(user_id))
    if not threshold_row:
        return  # Размера не хватает ни на один порог

    threshold, label = threshold_row

    # Проверяем что система включена
    if not rep_roles_enabled(guild_id):
        return  # система отключена

    guild  = bot.get_guild(guild_id)
    member = guild.get_member(user_id) if guild else None
    if not guild or not member:
        return

    # Проверяем текущую роль
    existing = get_active_role(user_id, guild_id)

    # Если постоянная — не трогаем
    if existing and existing[2]:  # permanent=1
        return

    # Если тот же порог — просто продлеваем срок
    if existing and existing[1] == threshold:
        old_role = guild.get_role(existing[0])
        expires  = datetime.now(UTC) + timedelta(days=ROLE_DURATION_DAYS)
        extend_active_role(user_id, guild_id, expires)
        # Если роль почему-то слетела — выдаём заново
        if old_role and old_role not in member.roles:
            await member.add_roles(old_role, reason="Размер-роль продлена")
        return

    # Удаляем старую роль если есть и порог сменился
    if existing:
        old_role = guild.get_role(existing[0])
        if old_role:
            try:
                await member.remove_roles(old_role, reason="Размер-роль заменена")
                await old_role.delete(reason="Размер-роль заменена новой")
            except Exception:
                pass

    # Генерируем название и создаём новую роль
    role_name = _generate_role_name(user_id, threshold, label)
    try:
        new_role = await guild.create_role(
            name=role_name,
            color=discord.Color.from_hsv(random.random(), 0.6, 0.9),
            reason=f"Размер-роль: {threshold} очков"
        )
        await member.add_roles(new_role, reason="Размер-роль выдана")
    except discord.Forbidden:
        return  # Нет прав создавать роли
    except Exception:
        return

    expires = datetime.now(UTC) + timedelta(days=ROLE_DURATION_DAYS)
    save_active_role(user_id, guild_id, new_role.id, threshold, expires)

    # Уведомляем в ЛС
    try:
        await member.send(
            f"🎖️ Ты получил роль **{role_name}** за Размер {threshold}+ на сервере!\n"
            f"Роль действует {ROLE_DURATION_DAYS} дней и обновится автоматически."
        )
    except Exception:
        pass


# ── Планировщик: обновление ролей каждые 6 часов ─────────────────────────────
async def _refresh_all_roles(bot: commands.Bot):
    now = datetime.now(UTC)
    rows = list_expiring_roles()

    for user_id, guild_id, role_id, expires_at in rows:
        if not expires_at:
            continue
        try:
            expires = datetime.fromisoformat(expires_at)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
        except Exception:
            continue

        if now >= expires:
            # Переназначаем роль (обновляем срок или меняем если порог вырос)
            await assign_rep_role(bot, guild_id, user_id)


# ── Cog ───────────────────────────────────────────────────────────────────────
class RepRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_rep_roles_storage()
        if not scheduler.running:
            scheduler.start()
        scheduler.add_job(
            _refresh_all_roles, "interval", hours=6,
            args=[bot], id="rep_roles_refresh", replace_existing=True
        )

    # ── /репа_роли ────────────────────────────────────────────────────────────
    @app_commands.command(name="размер_роли",
                          description="Пороги Размера для получения роли")
    async def репа_роли(self, interaction: discord.Interaction):
        rows = list_thresholds(interaction.guild.id)

        my_rep = _get_rep(interaction.user.id)
        emb = discord.Embed(
            title="🎖️ Размер-роли",
            description=f"Твоя Размер: **{my_rep}** ⭐",
            color=discord.Color.gold()
        )

        if not rows:
            emb.add_field(
                name="Пороги не настроены",
                value="Администратор может добавить через `/размер_роль_добавить`"
            )
        else:
            lines = []
            for rid, min_rep, label in rows:
                status = "✅" if my_rep >= min_rep else "🔒"
                tag    = f" · {label}" if label else ""
                lines.append(f"{status} **{min_rep}** Размера{tag} `(ID:{rid})`")
            emb.add_field(name="Пороги", value="\n".join(lines), inline=False)
            emb.set_footer(text="Роль выдаётся автоматически при достижении порога")

        await interaction.response.send_message(embed=emb)

    # ── /репа_роль_добавить ───────────────────────────────────────────────────
    @app_commands.command(name="размер_роль_добавить",
                          description="(Админ) Добавить порог Размера для роли")
    @app_commands.describe(
        порог="Сколько Размера нужно для получения роли",
        метка="Короткое описание для этого уровня (напр. 'ветеран')",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def репа_роль_добавить(self, interaction: discord.Interaction,
                                  порог: app_commands.Range[int, 1, 100000],
                                  метка: str = ""):
        upsert_threshold(interaction.guild.id, порог, метка)
        tag = f" · `{метка}`" if метка else ""
        await interaction.response.send_message(
            f"✅ Порог **{порог}** Размера добавлен{tag}.\n"
            f"Роль выдаётся автоматически и обновляется каждые {ROLE_DURATION_DAYS} дней.",
            ephemeral=True
        )

    # ── /репа_роль_убрать ─────────────────────────────────────────────────────
    @app_commands.command(name="размер_роль_убрать",
                          description="(Админ) Убрать порог Размера")
    @app_commands.describe(id="ID порога из /размер_роли")
    @app_commands.checks.has_permissions(administrator=True)
    async def репа_роль_убрать(self, interaction: discord.Interaction,
                                id: app_commands.Range[int, 1, 999999]):
        row = delete_threshold(interaction.guild.id, id)
        if not row:
            await interaction.response.send_message("❌ Порог не найден.", ephemeral=True)
            return
        tag = f" · `{row[1]}`" if row[1] else ""
        await interaction.response.send_message(
            f"✅ Порог **{row[0]}** Размера{tag} удалён.", ephemeral=True)

    # ── /репа_роль_постоянная ─────────────────────────────────────────────────
    @app_commands.command(name="размер_роль_постоянная",
                          description="(Админ) Сделать Размер-роль участника постоянной")
    @app_commands.describe(участник="Кому сделать роль постоянной")
    @app_commands.checks.has_permissions(administrator=True)
    async def репа_роль_постоянная(self, interaction: discord.Interaction,
                                    участник: discord.Member):
        row = make_role_permanent(участник.id, interaction.guild.id)
        if not row:
            await interaction.response.send_message(
                f"❌ У {участник.display_name} нет активной Размер-роли.", ephemeral=True)
            return
        if row[1]:
            await interaction.response.send_message("⚠️ Роль уже постоянная.", ephemeral=True)
            return

        role = interaction.guild.get_role(row[0])
        role_name = role.name if role else f"<роль {row[0]}>"
        await interaction.response.send_message(
            f"✅ Роль **{role_name}** для {участник.mention} теперь постоянная.",
            ephemeral=True
        )

    # ── /моя_репа_роль ────────────────────────────────────────────────────────
    # ── /репа_роль_изменить ──────────────────────────────────────────────────
    @app_commands.command(name="размер_роль_изменить",
                          description="(Админ) Изменить порог или метку существующего уровня")
    @app_commands.describe(
        id="ID порога из /размер_роли",
        новый_порог="Новое значение Размера (0 = не менять)",
        новая_метка="Новое название уровня (пусто = не менять)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def репа_роль_изменить(self, interaction: discord.Interaction,
                                  id: app_commands.Range[int, 1, 999999],
                                  новый_порог: app_commands.Range[int, 0, 100000] = 0,
                                  новая_метка: str = ""):
        row = get_threshold(interaction.guild.id, id)
        if not row:
            await interaction.response.send_message("❌ Порог не найден.", ephemeral=True)
            return
        cur_rep, cur_label = row
        upd_rep = новый_порог if новый_порог > 0 else cur_rep
        upd_label = новая_метка.strip()[:50] if новая_метка.strip() else cur_label
        if not update_threshold(interaction.guild.id, id, upd_rep, upd_label):
            await interaction.response.send_message(
                f"❌ Порог **{upd_rep}** уже существует.", ephemeral=True)
            return

        tag = f" · `{upd_label}`" if upd_label else ""
        await interaction.response.send_message(
            f"✅ Порог обновлён: **{upd_rep}** Размера{tag}.", ephemeral=True)

    # ── /репа_роли_вкл ────────────────────────────────────────────────────────
    @app_commands.command(name="размер_роли_вкл",
                          description="(Админ) Включить или выключить систему Размер-ролей")
    @app_commands.describe(включить="Включить или выключить")
    @app_commands.checks.has_permissions(administrator=True)
    async def репа_роли_вкл(self, interaction: discord.Interaction, включить: bool):
        set_rep_roles_enabled(interaction.guild.id, включить)
        status = "✅ Включена" if включить else "⛔ Выключена"
        note   = "" if включить else "\nСуществующие роли остаются у участников до истечения срока."
        await interaction.response.send_message(
            f"{status} система Размер-ролей.{note}", ephemeral=True)

    @app_commands.command(name="моя_размер_роль",
                          description="Твоя текущая Размер-роль и когда обновится")
    async def моя_репа_роль(self, interaction: discord.Interaction):
        my_rep = _get_rep(interaction.user.id)
        row = get_active_role(interaction.user.id, interaction.guild.id)
        next_t = next_threshold(interaction.guild.id, my_rep)

        emb = discord.Embed(title="🎖️ Твоя Размер-роль", color=discord.Color.gold())
        emb.add_field(name="Размер", value=f"**{my_rep}** ⭐", inline=True)

        if row:
            role       = interaction.guild.get_role(row[0])
            role_name  = role.name if role else "удалена"
            permanent  = row[2]
            expires_at = row[3]
            emb.add_field(name="Роль", value=f"**{role_name}**", inline=True)
            if permanent:
                emb.add_field(name="Статус", value="🔒 Постоянная", inline=True)
            elif expires_at:
                try:
                    exp = datetime.fromisoformat(expires_at)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=UTC)
                    ts = int(exp.timestamp())
                    emb.add_field(name="Обновится", value=f"<t:{ts}:R>", inline=True)
                except Exception:
                    pass
        else:
            emb.add_field(name="Роль", value="Нет (порог не достигнут)", inline=True)

        if next_t:
            need = next_t[0] - my_rep
            tag  = f" `{next_t[1]}`" if next_t[1] else ""
            emb.add_field(
                name="До следующего уровня",
                value=f"Ещё **{need}** Размера → порог **{next_t[0]}**{tag}",
                inline=False
            )

        await interaction.response.send_message(embed=emb, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RepRoles(bot))
