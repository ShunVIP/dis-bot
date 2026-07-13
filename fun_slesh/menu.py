# -*- coding: utf-8 -*-
"""
/команды и /админ — живой каталог скрытых slash-действий.

В отличие от старого ручного списка:
- подтягивает реальные команды из bot.menu_catalog_commands;
- показывает подкоманды групп вроде `токсичность топ`;
- не устаревает после добавления новых модулей;
- делит команды на категории по правилам, а не по захардкоженному списку.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

import discord
from discord import app_commands
from discord.ext import commands
from core.economy import get_balance
from core.economy_profile import can_receive_currency, currency_amount, economy_profile_required_text
from core.runtime_policy import WEB_ADMIN_CHANNEL_ID, WEB_ADMIN_CHANNEL_NAME, get_web_admin_url
from core.settings_store import get_feature_payload, get_feature_runtime_state, set_feature_payload
from utils.logger import log as base_log


log = base_log.bind(src="menu")


@dataclass(frozen=True)
class CategoryStyle:
    emoji: str
    color: discord.Color


@dataclass(frozen=True)
class MenuOnlyAction:
    action_id: str
    label: str
    description: str
    category: str
    method_name: str
    emoji: str


@dataclass(frozen=True)
class QuickButtonAction:
    action_id: str
    label: str
    emoji: str
    cog_name: str
    method_name: str
    kwargs: dict
    row: int


@dataclass(frozen=True)
class SectionAction:
    action_id: str
    label: str
    emoji: str
    kind: str
    cog_name: str | None = None
    method_name: str | None = None
    kwargs: dict | None = None
    row: int = 1


CATEGORY_STYLES: dict[str, CategoryStyle] = {
    "👤 Профиль": CategoryStyle("👤", discord.Color.gold()),
    "🎭 Пародия": CategoryStyle("🎭", discord.Color.purple()),
    "📊 Топы и итоги": CategoryStyle("📊", discord.Color.blurple()),
    "💰 Кошелек и магазин": CategoryStyle("💰", discord.Color.green()),
    "🎲 Развлечения": CategoryStyle("🎲", discord.Color.red()),
    "⏰ Напоминания": CategoryStyle("⏰", discord.Color.teal()),
    "🔍 Поиск": CategoryStyle("🔍", discord.Color.from_rgb(100, 180, 255)),
    "🕹️ Игры": CategoryStyle("🕹️", discord.Color.from_rgb(80, 190, 210)),
    "☢️ Активность": CategoryStyle("☢️", discord.Color.orange()),
    "💬 Болтовня": CategoryStyle("💬", discord.Color.from_rgb(225, 111, 255)),
    "🛡️ Админ": CategoryStyle("🛡️", discord.Color.dark_gold()),
    "🧩 Прочее": CategoryStyle("🧩", discord.Color.dark_grey()),
}

CATEGORY_ORDER = [
    "👤 Профиль",
    "💰 Кошелек и магазин",
    "📊 Топы и итоги",
    "🕹️ Игры",
    "🎲 Развлечения",
    "🎭 Пародия",
    "⏰ Напоминания",
    "🔍 Поиск",
    "🛡️ Админ",
    "💬 Болтовня",
    "☢️ Активность",
    "🧩 Прочее",
]

CATEGORY_SUMMARIES = {
    "👤 Профиль": "Единое окно: личная карточка, ДР, настроение, ачивки, Steam, Riot/LoL и WWM.",
    "🎭 Пародия": "Markov-фразы, мемные фразы и статистический паспорт стиля.",
    "💬 Болтовня": "Настройки живого общения и внезапных ответов бота.",
    "☢️ Активность": "Токсичность, войс-роли, сводки, игровые реакции и мем-триггеры.",
    "📊 Топы и итоги": "Единое окно топов, статистики голоса, активности и итогов сервера.",
    "💰 Кошелек и магазин": "Валюта, дэйлик, магазин ролей и переводы через компактные окна.",
    "🎲 Развлечения": "Игры, дуэли, случайные штуки и смешные публичные итоги.",
    "🕹️ Игры": "Один игровой хаб: мини-игры, Steam, LoL, WWM и игровые профили.",
    "⏰ Напоминания": "Создание, просмотр и удаление напоминаний.",
    "🔍 Поиск": "Разные источники поиска: WWM-база, Википедия и PubMed.",
    "🛡️ Админ": "Админские действия переезжают в отдельную web-панель.",
    "🧩 Прочее": "Редкие или пока неразобранные команды.",
}

ADMIN_ROOTS = {
    "дообучить",
    "профилактика",
    "индекс_сообщений",
    "др_ад",
    "д-р_ад",
    "др_канал",
    "выдать_роль",
    "очистить_сироты",
    "штраф",
    "налог_настроить",
    "магазин_добавить",
    "магазин_убрать",
    "награды_настроить",
    "стат_исключить",
    "стат_вернуть",
    "стат_исключения",
    "размер_роль_добавить",
    "размер_роль_убрать",
    "размер_роль_постоянная",
    "размер_роль_изменить",
    "размер_роли_вкл",
}

ADMIN_ROOTS.update({
    "пародия_исключить_канал",
    "пародия_вернуть_канал",
    "пародия_исключения",
})

INFO_COMMANDS = {"ачивки", "кто", "сервер", "пинг"}
RANDOM_COMMANDS = {"монетка", "шар", "кубик", "анекдот", "котик", "опрос", "мем"}
SEARCH_COMMANDS = {"вики", "пабмед", "wwm_search", "wwm_random"}
STATS_COMMANDS = {"топ_актив", "топ_слова", "топ_эмодзи", "voice_топ", "voice_я", "награды_статус"}
ECON_COMMANDS = {"баланс", "дэйлик", "перевод", "налог_статус", "магазин", "купить_роль", "топ_серии", "топ_баланс", "экономика_профиль"}
GAME_COMMANDS = {"кнб", "кнб_дуэль", "угадай", "виселица", "виселица_старт", "виселица_буква", "бж", "бж_дуэль"}
REP_COMMANDS = {"размер", "уменьшить_размер", "топ_размер", "история_размера", "мое_настроение", "настроение_сегодня", "размер_роли", "моя_размер_роль"}
BIRTHDAY_COMMANDS = {"др", "д-р", "все_др", "когда_др"}
PARODY_COMMANDS = {"пародия", "батл", "коллаж", "эпоха", "тема", "мем_фраза", "профиль_стиля", "модели_статус", "список_пользователей", "дообучить", "профилактика"}
STEAM_ROOTS = {"стим_привязать", "стим_отвязать", "стим", "стим_вишлист", "стим_общие", "релизы"}
GAME_PROFILE_ROOTS = {"lol"}
WWM_ROOTS = {"wwm"}
ACTIVITY_STATS_ROOTS = {"токсичность", "итоги"}
ACTIVITY_ADMIN_ROOTS = {"войс_роли"}
ACTIVITY_HIDDEN_ROOTS = {"heroes_troll", "sixty_seven"}
ACTIVITY_ROOTS = ACTIVITY_STATS_ROOTS | ACTIVITY_ADMIN_ROOTS | ACTIVITY_HIDDEN_ROOTS
REMINDER_ROOTS = {"напоминания"}
CHAT_ROOTS = {"болтовня"}
MENU_ROOTS = {"команды", "админ"}
FEATURE_ADMIN_PANEL_ENTRY = "admin_panel_entry"

MENU_ONLY_ACTIONS: tuple[MenuOnlyAction, ...] = (
    MenuOnlyAction("ping", "Пинг", "Быстрый ответ с текущей задержкой бота.", "👤 Профиль", "menu_ping", "🏓"),
    MenuOnlyAction("server", "Сервер", "Карточка сервера без отдельного slash-ввода.", "📊 Топы и итоги", "menu_server", "🏰"),
    MenuOnlyAction("coinflip", "Монетка", "Подбросить монетку прямо из меню.", "🎲 Развлечения", "menu_coinflip", "🪙"),
    MenuOnlyAction("meme", "Мем", "Случайный мем без отдельной slash-команды.", "🎲 Развлечения", "menu_meme", "😂"),
)
MENU_ONLY_BY_ID = {item.action_id: item for item in MENU_ONLY_ACTIONS}
QUICK_BUTTON_ACTIONS: tuple[QuickButtonAction, ...] = (
)
QUICK_BUTTON_BY_ID = {item.action_id: item for item in QUICK_BUTTON_ACTIONS}


SECTION_ACTIONS: dict[str, tuple[SectionAction, ...]] = {
    "👤 Профиль": (
        SectionAction("profile_me", "Мой профиль", "👤", "profile_hub", row=1),
        SectionAction("profile_accounts", "Привязки", "🔗", "accounts_hub", row=1),
        SectionAction("profile_birthday", "День рождения", "🎂", "birthday_hub", row=1),
        SectionAction("profile_size", "Размер", "📏", "size_action_select", row=1),
        SectionAction("profile_mood", "Настроение", "🙂", "mood_modal", row=2),
        SectionAction("profile_achievements", "Ачивки", "🏅", "call", "AchievementsEngine", "ачивки", row=2),
    ),
    "💰 Кошелек и магазин": (
        SectionAction("wallet_status", "Моя валюта", "💰", "wallet_status", row=1),
        SectionAction("shop_hub", "Магазин", "🛒", "shop_hub", row=1),
        SectionAction("wallet_daily", "Дэйлик", "🎁", "call", "Daily", "дэйлик", row=1),
        SectionAction("wallet_transfer", "Перевод", "💸", "transfer_modal", row=1),
    ),
    "📊 Топы и итоги": (
        SectionAction("top_hub", "Все топы", "🏆", "top_hub", row=1),
        SectionAction("summary_hub", "Итоги", "🗓️", "summary_hub", row=1),
        SectionAction("stats_voice_me", "Мой голос", "🎧", "call", "MessageAndVoiceStats", "voice_me", row=1),
        SectionAction("stats_rewards", "Награды", "🎁", "call", "MessageAndVoiceStats", "награды_статус", row=1),
    ),
    "🎲 Развлечения": (
        SectionAction("fun_random_hub", "Случайное", "🎲", "random_hub", row=2),
    ),
    "🎭 Пародия": (
        SectionAction("parody_phrase", "Фраза в стиле участника", "🎭", "user_select", "ParodyEngine", "пародия", row=1),
        SectionAction("parody_profile", "Паспорт стиля", "📊", "user_select", "ParodyEngine", "профиль_стиля", row=1),
        SectionAction("parody_topic", "Фраза на тему", "🎯", "parody_topic_modal", row=1),
        SectionAction("parody_meme", "Мемная фраза", "🤣", "user_select", "ParodyEngine", "мем_фраза", row=1),
        SectionAction("parody_users", "Кого знает бот", "👥", "call", "ParodyEngine", "список_пользователей", row=2),
    ),
    "🕹️ Игры": (
        SectionAction("games_hub", "Игровой хаб", "🎮", "games_hub", row=1),
        SectionAction("steam_hub", "Steam", "🎮", "steam_hub", row=1),
        SectionAction("game_lol", "League of Legends", "🧬", "game_lol_hub", row=1),
        SectionAction("wwm_hub", "WWM", "🌿", "wwm_hub", row=1),
    ),
    "🔍 Поиск": (
        SectionAction("search_wwm", "WWM база", "🔎", "wwm_search_modal", row=1),
        SectionAction("search_wwm_random", "Случайная WWM статья", "🎲", "call", "WWMSearchCog", "wwm_random", row=1),
        SectionAction("search_wiki", "Википедия", "📚", "wiki_modal", row=2),
        SectionAction("search_pubmed", "PubMed статьи", "🧬", "pubmed_modal", row=2),
    ),
    "⏰ Напоминания": (
        SectionAction("reminders_create", "Создать", "➕", "reminder_modal", row=1),
        SectionAction("reminders_my", "Мои напоминания", "📋", "call", "Tools", "мои_напоминания", row=1),
        SectionAction("reminders_delete", "Удалить", "🗑️", "call", "Tools", "удалить_напоминание", row=1),
    ),
}
SECTION_ACTION_BY_ID = {
    action.action_id: action
    for actions in SECTION_ACTIONS.values()
    for action in actions
}
SECTION_ACTION_CALLBACKS_BY_CATEGORY = {
    category: {
        action.method_name
        for action in actions
        if action.kind == "call" and action.method_name
    }
    for category, actions in SECTION_ACTIONS.items()
}


def _has_admin_permission_check(cmd: app_commands.Command) -> bool:
    for check in getattr(cmd, "checks", []):
        for cell in getattr(check, "__closure__", []) or []:
            content = getattr(cell, "cell_contents", None)
            if isinstance(content, dict) and content.get("administrator") is True:
                return True
    return False


def _is_admin_command(cmd: app_commands.Command, qualified_name: str) -> bool:
    root = qualified_name.split()[0]
    if root in ADMIN_ROOTS:
        return True
    if _has_admin_permission_check(cmd):
        return True

    description = (cmd.description or "").strip().lower()
    return description.startswith("(админ)")


def _category_for_command(qualified_name: str, module_name: str) -> str:
    root = qualified_name.split()[0]

    if root in {"топ_серии", "топ_баланс", "топ_размер", "настроение_сегодня"}:
        return "📊 Топы и итоги"
    if root in MENU_ROOTS:
        return "🧩 Прочее"
    if root in CHAT_ROOTS:
        return "💬 Болтовня"
    if root in REMINDER_ROOTS:
        return "⏰ Напоминания"
    if root in WWM_ROOTS or module_name == "fun_slesh.wwm_guild":
        return "🕹️ Игры"
    if root in SEARCH_COMMANDS or module_name in {"fun_slesh.ai_tools", "fun_slesh.wwm_search_cog"}:
        return "🔍 Поиск"
    if root in STEAM_ROOTS or module_name == "fun_slesh.steam":
        return "🕹️ Игры"
    if root in GAME_PROFILE_ROOTS or module_name == "fun_slesh.lol_profile":
        return "🕹️ Игры"
    if root in PARODY_COMMANDS or module_name.startswith("fun_slesh.parody_"):
        return "🎭 Пародия"
    if root in ACTIVITY_STATS_ROOTS or module_name in {"fun_slesh.toxicity", "fun_slesh.daily_summary"}:
        return "📊 Топы и итоги"
    if root in ACTIVITY_ADMIN_ROOTS or module_name == "fun_slesh.voice_roles":
        return "🛡️ Админ"
    if root in ACTIVITY_HIDDEN_ROOTS or module_name in {"fun_slesh.heroes_troll", "fun_slesh.sixty_seven"}:
        return "🎲 Развлечения"
    if root in STATS_COMMANDS or module_name == "fun_slesh.message_and_voice_stats":
        return "📊 Топы и итоги"
    if root in REP_COMMANDS or module_name in {"fun_slesh.rep_and_mood", "fun_slesh.rep_roles"}:
        return "👤 Профиль"
    if root in BIRTHDAY_COMMANDS or module_name == "fun_slesh.birthday":
        return "👤 Профиль"
    if root in ECON_COMMANDS or module_name == "fun_slesh.daily":
        return "💰 Кошелек и магазин"
    if root in GAME_COMMANDS or module_name == "fun_slesh.games":
        return "🎲 Развлечения"
    if root in RANDOM_COMMANDS:
        return "🎲 Развлечения"
    if root in INFO_COMMANDS or module_name in {"fun_slesh.achievements_engine", "fun_slesh.test_hello"}:
        return "👤 Профиль"
    if root in ACTIVITY_ROOTS:
        return "📊 Топы и итоги"
    if root in ADMIN_ROOTS:
        return "🛡️ Админ"
    return "🧩 Прочее"


def _is_replaced_by_section_button(category: str, item: dict) -> bool:
    callback_name = item.get("callback_name")
    return bool(callback_name and callback_name in SECTION_ACTION_CALLBACKS_BY_CATEGORY.get(category, set()))


def _mention_for(qualified_name: str, root_id: int | None) -> str:
    if root_id:
        return f"</{qualified_name}:{root_id}>"
    return f"`/{qualified_name}`"


async def _fetch_root_ids(bot: commands.Bot) -> dict[str, int]:
    if getattr(bot, "menu_commands_hidden_from_slash", False):
        return {}

    root_ids = {}
    for cmd in bot.tree.get_commands():
        cmd_id = getattr(cmd, "id", None)
        if cmd_id:
            root_ids[cmd.name] = cmd_id

    if root_ids:
        return root_ids

    try:
        fetched = await bot.tree.fetch_commands()
        root_ids = {cmd.name: cmd.id for cmd in fetched}
    except Exception:
        pass
    return root_ids


def _walk_leaf_commands(tree_commands: list[app_commands.Command | app_commands.Group], root_ids: dict[str, int]):
    collected: list[dict] = []

    def visit(cmd: app_commands.Command | app_commands.Group):
        if isinstance(cmd, app_commands.Group):
            for sub in cmd.commands:
                visit(sub)
            return

        qualified_name = cmd.qualified_name
        root_name = qualified_name.split()[0]
        callback = getattr(cmd, "callback", None)
        module_name = getattr(callback, "__module__", "") if callback else ""
        callback_name = getattr(callback, "__name__", "") if callback else ""
        description = (cmd.description or "Без описания").strip()
        collected.append(
            {
                "qualified_name": qualified_name,
                "root_name": root_name,
                "module_name": module_name,
                "callback_name": callback_name,
                "description": description,
                "root_id": root_ids.get(root_name),
                "is_admin": _is_admin_command(cmd, qualified_name),
            }
        )

    for command in tree_commands:
        visit(command)
    return collected


async def _build_catalog(bot: commands.Bot, *, admin_only: bool) -> dict[str, list[dict]]:
    root_ids = await _fetch_root_ids(bot)
    source_commands = getattr(bot, "menu_catalog_commands", None) or bot.tree.get_commands()
    commands_flat = _walk_leaf_commands(source_commands, root_ids)
    hidden_command_names = getattr(bot, "menu_hidden_command_names", set())
    catalog: dict[str, list[dict]] = {}

    for item in commands_flat:
        qualified_name = item["qualified_name"]
        root_name = item["root_name"]
        item["hidden_from_slash"] = root_name in hidden_command_names
        is_admin = item["is_admin"]
        if admin_only and not is_admin:
            continue
        if not admin_only and is_admin:
            continue

        category = _category_for_command(qualified_name, item["module_name"])
        if admin_only and category != "🛡️ Админ":
            category = "🛡️ Админ"
        if not admin_only and category in {"💬 Болтовня", "☢️ Активность", "🛡️ Админ", "🧩 Прочее"}:
            continue
        if not admin_only and _is_replaced_by_section_button(category, item):
            continue
        catalog.setdefault(category, []).append(item)

    if not admin_only:
        for action in MENU_ONLY_ACTIONS:
            catalog.setdefault(action.category, []).append(
                {
                    "qualified_name": action.label,
                    "root_name": action.action_id,
                    "module_name": "fun_slesh.menu",
                    "description": action.description,
                    "root_id": None,
                    "is_admin": False,
                    "menu_only": True,
                    "action_id": action.action_id,
                    "button_label": action.label,
                    "emoji": action.emoji,
                }
            )

    for items in catalog.values():
        items.sort(key=lambda row: (not row.get("menu_only", False), row["qualified_name"]))

    ordered: "OrderedDict[str, list[dict]]" = OrderedDict()
    for category in CATEGORY_ORDER:
        if category in catalog:
            ordered[category] = catalog[category]

    for category, items in catalog.items():
        if category not in ordered:
            ordered[category] = items
    return dict(ordered)


def _build_overview_embed(catalog: dict[str, list[dict]], *, admin_only: bool) -> discord.Embed:
    total = sum(len(v) for v in catalog.values())
    color = discord.Color.dark_gold() if admin_only else discord.Color.blurple()
    title = "🛡️ Меню администратора" if admin_only else "🧭 Меню команд"
    emb = discord.Embed(title=title, color=color)

    intro = "Выбери категорию в выпадающем списке ниже."
    if admin_only:
        intro += "\nЗдесь собраны админские действия и настройки сервера."
    else:
        intro += "\nЗдесь собраны основные действия бота."
    emb.description = intro

    lines = []
    for category, items in catalog.items():
        summary = CATEGORY_SUMMARIES.get(category, "Команды этой категории.")
        lines.append(f"**{category}** — {len(items)}\n{summary}")

    emb.add_field(name="Категории", value="\n\n".join(lines[:8]) or "Категории не найдены.", inline=False)
    if len(lines) > 8:
        emb.add_field(name="Ещё", value="\n\n".join(lines[8:]), inline=False)

    emb.set_footer(text=f"Всего пунктов: {total}")
    return emb


def _format_entry(item: dict) -> str:
    if item.get("menu_only"):
        emoji = item.get("emoji", "🖱️")
        label = item.get("button_label", item["qualified_name"])
        return f"{emoji} **{label}** *(только через меню)*\n`{item['description']}`"

    if item.get("hidden_from_slash"):
        return f"**{item['qualified_name']}** *(через меню)*\n`{item['description']}`"

    mention = _mention_for(item["qualified_name"], item["root_id"])
    return f"{mention}\n`{item['description']}`"


def _build_embed(category: str, catalog: dict[str, list[dict]], *, admin_only: bool) -> discord.Embed:
    if category == "__overview__":
        return _build_overview_embed(catalog, admin_only=admin_only)

    style = CATEGORY_STYLES.get(category, CATEGORY_STYLES["🧩 Прочее"])
    items = catalog.get(category, [])
    total = sum(len(v) for v in catalog.values())

    title = f"{style.emoji} {category.split(' ', 1)[-1]}"
    emb = discord.Embed(title=title, color=style.color)

    if admin_only:
        emb.description = "Админ-каталог собран из внутренних действий бота.\n\n"
    else:
        emb.description = "Живой каталог собран из внутренних действий бота.\n\n"

    lines = [_format_entry(item) for item in items]

    emb.description += "\n".join(lines) if lines else "В этой категории пока ничего нет."
    emb.set_footer(text=f"Пунктов в этом меню: {total} • Команды скрыты из / и будут вызываться через меню")
    return emb


async def _run_menu_only_action(bot: commands.Bot, interaction: discord.Interaction, action_id: str):
    action = MENU_ONLY_BY_ID.get(action_id)
    if not action:
        await interaction.response.send_message("❌ Действие меню не найдено.", ephemeral=True)
        return

    fun_cog = bot.get_cog("FunAndInfo")
    if fun_cog is None:
        await interaction.response.send_message("❌ Модуль простых команд не загружен.", ephemeral=True)
        return

    handler = getattr(fun_cog, action.method_name, None)
    if handler is None:
        await interaction.response.send_message("❌ Для этой кнопки не найден обработчик.", ephemeral=True)
        return

    await handler(interaction)


async def _run_quick_button_action(bot: commands.Bot, interaction: discord.Interaction, action_id: str):
    action = QUICK_BUTTON_BY_ID.get(action_id)
    if not action:
        await interaction.response.send_message("❌ Быстрое действие не найдено.", ephemeral=True)
        return

    cog = bot.get_cog(action.cog_name)
    if cog is None:
        await interaction.response.send_message("❌ Нужный модуль сейчас не загружен.", ephemeral=True)
        return

    handler = getattr(cog, action.method_name, None)
    if handler is None:
        await interaction.response.send_message("❌ Для этой кнопки не найден обработчик.", ephemeral=True)
        return

    callback = getattr(handler, "callback", None)
    if callable(callback):
        await callback(cog, interaction, **action.kwargs)
        return

    await handler(interaction, **action.kwargs)


async def _invoke_cog_action(
    bot: commands.Bot,
    interaction: discord.Interaction,
    cog_name: str,
    method_name: str,
    **kwargs: Any,
):
    cog = bot.get_cog(cog_name)
    if cog is None:
        await interaction.response.send_message("❌ Нужный модуль сейчас не загружен.", ephemeral=True)
        return

    handler = getattr(cog, method_name, None)
    if handler is None:
        await interaction.response.send_message("❌ Для этого действия не найден обработчик.", ephemeral=True)
        return

    callback = getattr(handler, "callback", None)
    if callable(callback):
        await callback(cog, interaction, **kwargs)
        return

    await handler(interaction, **kwargs)


class SingleFieldModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        title: str,
        label: str,
        placeholder: str,
        action: Callable[[discord.Interaction, str], Any],
        default: str = "",
    ):
        super().__init__(title=title)
        self.action = action
        self.value_input = discord.ui.TextInput(
            label=label,
            placeholder=placeholder,
            default=default,
            required=True,
            max_length=100,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.action(interaction, str(self.value_input.value).strip())


class TransferModal(discord.ui.Modal, title="Перевод валюты"):
    recipient = discord.ui.TextInput(
        label="Получатель",
        placeholder="@ник или Discord ID",
        required=True,
        max_length=80,
    )
    amount = discord.ui.TextInput(
        label="Сумма",
        placeholder="Например: 100",
        required=True,
        max_length=12,
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Это действие работает только на сервере.", ephemeral=True)
            return
        raw_user = str(self.recipient.value).strip()
        raw_amount = str(self.amount.value).strip()
        try:
            amount = int(raw_amount)
        except ValueError:
            await interaction.response.send_message("❌ Сумма должна быть числом.", ephemeral=True)
            return
        user_id = _parse_user_id(raw_user)
        member = interaction.guild.get_member(user_id) if user_id else None
        if member is None:
            await interaction.response.send_message("❌ Не нашел участника. Укажи @упоминание или Discord ID.", ephemeral=True)
            return
        await _invoke_cog_action(self.bot, interaction, "Daily", "перевод", получатель=member, сумма=amount)


class BuyRoleModal(discord.ui.Modal, title="Купить роль"):
    role_id = discord.ui.TextInput(
        label="ID позиции из магазина",
        placeholder="Посмотри ID в разделе Магазин",
        required=True,
        max_length=12,
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        try:
            shop_id = int(str(self.role_id.value).strip())
        except ValueError:
            await interaction.response.send_message("❌ ID должен быть числом.", ephemeral=True)
            return
        await _invoke_cog_action(self.bot, interaction, "Daily", "купить_роль", id=shop_id)


class ReminderCreateModal(discord.ui.Modal, title="Создать напоминание"):
    text = discord.ui.TextInput(
        label="Текст",
        placeholder="Что напомнить?",
        required=True,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )
    time = discord.ui.TextInput(
        label="Время МСК",
        placeholder="Например: 21:00",
        required=True,
        max_length=5,
    )
    date = discord.ui.TextInput(
        label="Дата, если разово",
        placeholder="ДД.ММ.ГГГГ или пусто",
        required=False,
        max_length=10,
    )
    repeat = discord.ui.TextInput(
        label="Повтор",
        placeholder="once, daily, mon, tue, wed, thu, fri, sat, sun, biweekly",
        default="once",
        required=True,
        max_length=12,
    )
    advance = discord.ui.TextInput(
        label="Предупредить за минут",
        placeholder="0, 10, 30...",
        default="0",
        required=True,
        max_length=4,
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        try:
            advance_min = int(str(self.advance.value).strip() or "0")
        except ValueError:
            await interaction.response.send_message("❌ Предупреждение должно быть числом минут.", ephemeral=True)
            return
        await _invoke_cog_action(
            self.bot,
            interaction,
            "Tools",
            "напомни",
            текст=str(self.text.value).strip(),
            время=str(self.time.value).strip(),
            дата=str(self.date.value).strip(),
            повторение=str(self.repeat.value).strip() or "once",
            лично=False,
            за_минут=advance_min,
        )


class ActionSelect(discord.ui.Select):
    def __init__(self, placeholder: str, options: list[discord.SelectOption], callbacks: dict[str, Callable[[discord.Interaction], Any]]):
        self.callbacks = callbacks
        super().__init__(placeholder=placeholder, options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        action = self.callbacks.get(self.values[0])
        if not action:
            await interaction.response.send_message("❌ Действие не найдено.", ephemeral=True)
            return
        await action(interaction)


class ActionSelectView(discord.ui.View):
    def __init__(self, placeholder: str, options: list[discord.SelectOption], callbacks: dict[str, Callable[[discord.Interaction], Any]]):
        super().__init__(timeout=120)
        self.add_item(ActionSelect(placeholder, options, callbacks))


class UserActionSelect(discord.ui.UserSelect):
    def __init__(self, action: SectionAction):
        self.action = action
        super().__init__(
            placeholder=f"{action.label}: выбери участника",
            min_values=1,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if view is None:
            await interaction.response.send_message("❌ Выбор уже недоступен.", ephemeral=True)
            return
        member = self.values[0]
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("❌ Нужно выбрать участника сервера.", ephemeral=True)
            return
        kwargs = dict(self.action.kwargs or {})
        target_param = kwargs.pop("__target_param", "пользователь")
        await _invoke_cog_action(
            view.bot,
            interaction,
            self.action.cog_name or "",
            self.action.method_name or "",
            **kwargs,
            **{target_param: member},
        )


class UserActionView(discord.ui.View):
    def __init__(self, bot: commands.Bot, action: SectionAction):
        super().__init__(timeout=120)
        self.bot = bot
        self.add_item(UserActionSelect(action))


def _parse_user_id(raw: str) -> int | None:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


async def _send_action_picker(
    interaction: discord.Interaction,
    *,
    title: str,
    description: str,
    placeholder: str,
    options: list[discord.SelectOption],
    callbacks: dict[str, Callable[[discord.Interaction], Any]],
):
    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    await interaction.response.send_message(
        embed=embed,
        view=ActionSelectView(placeholder, options, callbacks),
        ephemeral=True,
    )


async def _send_profile_hub(bot: commands.Bot, interaction: discord.Interaction):
    async def history(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "RepAndMood", "история_репы")

    async def mood(next_interaction: discord.Interaction):
        async def submit_mood(modal_interaction: discord.Interaction, value: str):
            try:
                mood_value = int(value)
            except ValueError:
                await modal_interaction.response.send_message("❌ Оценка должна быть числом от 1 до 10.", ephemeral=True)
                return
            await _invoke_cog_action(bot, modal_interaction, "RepAndMood", "мое_настроение", оценка=mood_value)
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Мое настроение",
            label="Оценка 1-10",
            placeholder="Например: 8",
            action=submit_mood,
        ))

    async def achievements(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "AchievementsEngine", "ачивки")

    async def accounts(next_interaction: discord.Interaction):
        await _send_accounts_hub(bot, next_interaction)

    async def birthday(next_interaction: discord.Interaction):
        await _send_birthday_hub(bot, next_interaction)

    await _send_action_picker(
        interaction,
        title="👤 Мой профиль",
        description="Единое окно профиля: личная инфа, ДР, настроение, ачивки и привязки игровых аккаунтов.",
        placeholder="Что показать?",
        options=[
            discord.SelectOption(label="Привязки аккаунтов", value="accounts", emoji="🔗"),
            discord.SelectOption(label="День рождения", value="birthday", emoji="🎂"),
            discord.SelectOption(label="Моя история Размера", value="history", emoji="📜"),
            discord.SelectOption(label="Указать настроение", value="mood", emoji="🙂"),
            discord.SelectOption(label="Мои ачивки", value="achievements", emoji="🏅"),
        ],
        callbacks={"accounts": accounts, "birthday": birthday, "history": history, "mood": mood, "achievements": achievements},
    )


async def _send_accounts_hub(bot: commands.Bot, interaction: discord.Interaction):
    async def steam_link(next_interaction: discord.Interaction):
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Привязать Steam",
            label="Ссылка, vanity или SteamID64",
            placeholder="https://steamcommunity.com/id/...",
            action=lambda modal_interaction, value: _invoke_cog_action(
                bot, modal_interaction, "Steam", "steam_привязать", профиль=value
            ),
        ))

    async def steam_profile(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "Steam", "steam_профиль")

    async def lol_link(next_interaction: discord.Interaction):
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Привязать Riot ID",
            label="Riot ID",
            placeholder="Name#TAG",
            action=lambda modal_interaction, value: _invoke_cog_action(
                bot, modal_interaction, "LolProfile", "link", riot_id=value
            ),
        ))

    async def lol_profile(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "LolProfile", "profile")

    async def wwm_nick(next_interaction: discord.Interaction):
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Игровой ник WWM",
            label="Ник в Where Winds Meet",
            placeholder="Например: ShunVIP",
            action=lambda modal_interaction, value: _invoke_cog_action(
                bot, modal_interaction, "WWMGuild", "wwm_ник", игровой_ник=value
            ),
        ))

    async def wwm_card(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "WWMGuild", "wwm_карточка")

    await _send_action_picker(
        interaction,
        title="🔗 Привязки профиля",
        description="Здесь собираются внешние аккаунты и игровые профили. Это будущая основа одной карточки пользователя.",
        placeholder="Что открыть?",
        options=[
            discord.SelectOption(label="Привязать Steam", value="steam_link", emoji="🎮"),
            discord.SelectOption(label="Мой Steam", value="steam_profile", emoji="👤"),
            discord.SelectOption(label="Привязать Riot ID", value="lol_link", emoji="🧬"),
            discord.SelectOption(label="Мой LoL", value="lol_profile", emoji="📊"),
            discord.SelectOption(label="Указать WWM-ник", value="wwm_nick", emoji="🌿"),
            discord.SelectOption(label="Моя WWM-карточка", value="wwm_card", emoji="🎴"),
        ],
        callbacks={
            "steam_link": steam_link,
            "steam_profile": steam_profile,
            "lol_link": lol_link,
            "lol_profile": lol_profile,
            "wwm_nick": wwm_nick,
            "wwm_card": wwm_card,
        },
    )


async def _send_birthday_hub(bot: commands.Bot, interaction: discord.Interaction):
    async def set_birthday(next_interaction: discord.Interaction):
        async def submit_birthday(modal_interaction: discord.Interaction, value: str):
            await _invoke_cog_action(bot, modal_interaction, "Birthday", "др", дата=value)
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Установить день рождения",
            label="Дата в формате ДД.ММ",
            placeholder="Например: 20.04",
            action=submit_birthday,
        ))

    async def delete_birthday(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "Birthday", "д_р")

    async def when_birthday(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "Birthday", "когда_др")

    async def all_birthdays(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "Birthday", "все_др")

    await _send_action_picker(
        interaction,
        title="🎂 День рождения",
        description="Личная дата рождения и общий список. Админские правки дат переезжают в админ-панель.",
        placeholder="Что сделать?",
        options=[
            discord.SelectOption(label="Установить мой ДР", value="set", emoji="🎂"),
            discord.SelectOption(label="Удалить мой ДР", value="delete", emoji="🗑️"),
            discord.SelectOption(label="Когда мой ДР", value="when", emoji="🔎"),
            discord.SelectOption(label="Все дни рождения", value="all", emoji="📅"),
        ],
        callbacks={"set": set_birthday, "delete": delete_birthday, "when": when_birthday, "all": all_birthdays},
    )


async def _send_size_picker(bot: commands.Bot, interaction: discord.Interaction):
    async def add_size(next_interaction: discord.Interaction):
        await next_interaction.response.send_message(
            "Выбери участника, которому хочешь увеличить Размер.",
            view=UserActionView(bot, SectionAction("size_plus_user", "Увеличить Размер", "📈", "user_select", "RepAndMood", "Размер")),
            ephemeral=True,
        )

    async def remove_size(next_interaction: discord.Interaction):
        await next_interaction.response.send_message(
            "Выбери участника, у которого хочешь уменьшить Размер.",
            view=UserActionView(bot, SectionAction("size_minus_user", "Уменьшить Размер", "📉", "user_select", "RepAndMood", "антирепа")),
            ephemeral=True,
        )

    await _send_action_picker(
        interaction,
        title="📏 Размер",
        description="Размер — персональная репутация. Валюта и название зависят от 18+ профиля пользователя.",
        placeholder="Выбери действие",
        options=[
            discord.SelectOption(label="Увеличить Размер", value="plus", emoji="📈"),
            discord.SelectOption(label="Уменьшить Размер", value="minus", emoji="📉"),
        ],
        callbacks={"plus": add_size, "minus": remove_size},
    )


async def _send_community_info_picker(bot: commands.Bot, interaction: discord.Interaction):
    async def top_size(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "RepAndMood", "топ_репа")

    async def mood_today(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "RepAndMood", "настроение_сегодня")

    async def rep_roles(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "RepRoles", "репа_роли")

    await _send_action_picker(
        interaction,
        title="🌐 Общая инфа",
        description="Общие показатели сервера по Размеру, настроению и достижениям.",
        placeholder="Что вывести?",
        options=[
            discord.SelectOption(label="Топ Размера", value="top", emoji="🏆"),
            discord.SelectOption(label="Настроение всех сегодня", value="mood", emoji="😊"),
            discord.SelectOption(label="Размер-роли", value="roles", emoji="🎖️"),
        ],
        callbacks={"top": top_size, "mood": mood_today, "roles": rep_roles},
    )


async def _send_top_hub(bot: commands.Bot, interaction: discord.Interaction):
    async def active(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "MessageAndVoiceStats", "топ_актив")

    async def words(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "MessageAndVoiceStats", "топ_слова")

    async def emojis(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "MessageAndVoiceStats", "топ_эмодзи")

    async def voice(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "MessageAndVoiceStats", "voice_top")

    async def balance(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "Daily", "топ_баланс")

    async def streaks(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "Daily", "топ_серии")

    async def size(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "RepAndMood", "топ_репа")

    async def mood(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "RepAndMood", "настроение_сегодня")

    await _send_action_picker(
        interaction,
        title="🏆 Все топы",
        description="Один вход для рейтингов сервера: активность, голос, валюта, Размер и настроение.",
        placeholder="Какой топ показать?",
        options=[
            discord.SelectOption(label="Активность", value="active", emoji="📊"),
            discord.SelectOption(label="Слова", value="words", emoji="📝"),
            discord.SelectOption(label="Эмодзи", value="emojis", emoji="😎"),
            discord.SelectOption(label="Голос", value="voice", emoji="🎙️"),
            discord.SelectOption(label="Баланс", value="balance", emoji="💰"),
            discord.SelectOption(label="Серия дэйлика", value="streaks", emoji="🔥"),
            discord.SelectOption(label="Размер", value="size", emoji="⭐"),
            discord.SelectOption(label="Настроение", value="mood", emoji="🙂"),
        ],
        callbacks={
            "active": active,
            "words": words,
            "emojis": emojis,
            "voice": voice,
            "balance": balance,
            "streaks": streaks,
            "size": size,
            "mood": mood,
        },
    )


async def _send_summary_hub(bot: commands.Bot, interaction: discord.Interaction):
    async def day(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "DailySummary", "итог_дня")

    async def week(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "DailySummary", "итог_недели")

    async def month(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "DailySummary", "итог_месяца")

    await _send_action_picker(
        interaction,
        title="🗓️ Итоги сервера",
        description="Сводки остаются пользовательской фичей, а расписание и каналы публикации переезжают в админку.",
        placeholder="Какой итог показать?",
        options=[
            discord.SelectOption(label="Итог дня", value="day", emoji="🌙"),
            discord.SelectOption(label="Итог недели", value="week", emoji="🗓️"),
            discord.SelectOption(label="Итог месяца", value="month", emoji="📅"),
        ],
        callbacks={"day": day, "week": week, "month": month},
    )


async def _send_wallet_status(bot: commands.Bot, interaction: discord.Interaction):
    guild_id = int(interaction.guild.id) if interaction.guild else 0
    payload = get_feature_payload(guild_id, "economy")
    state = get_feature_runtime_state(guild_id, "economy")
    enabled = bool(payload.get("tax_enabled", False))
    rate_pct = max(1, min(50, int(payload.get("tax_rate_pct", 10))))
    interval_h = max(1, min(720, int(payload.get("tax_interval_h", 168))))
    last_run = str(state.get("tax_last_run") or "")
    tax_text = f"{'включен' if enabled else 'выключен'} · {rate_pct}% · каждые {interval_h}ч"
    if last_run:
        tax_text += f"\nПоследний запуск: `{last_run}`"

    profile_note = "Профиль заполнен." if can_receive_currency(interaction.user.id) else economy_profile_required_text()
    embed = discord.Embed(
        title="💰 Моя валюта",
        description=profile_note,
        color=discord.Color.green(),
    )
    embed.add_field(name="Баланс", value=f"**{currency_amount(interaction.user.id, get_balance(interaction.user.id))}**", inline=True)
    embed.add_field(name="Ежедневное задание", value="пока очищено, будет заполняться через программу бота", inline=False)
    embed.add_field(name="Налог", value=tax_text, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def _send_shop_hub(bot: commands.Bot, interaction: discord.Interaction):
    async def show_shop(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "Daily", "магазин")

    async def buy_role(next_interaction: discord.Interaction):
        await next_interaction.response.send_modal(BuyRoleModal(bot))

    async def transfer(next_interaction: discord.Interaction):
        await next_interaction.response.send_modal(TransferModal(bot))

    await _send_action_picker(
        interaction,
        title="🛒 Магазин",
        description="Здесь тратится и переводится персональная валюта: роли, покупки и переводы.",
        placeholder="Выбери действие",
        options=[
            discord.SelectOption(label="Посмотреть магазин", value="show", emoji="🛒"),
            discord.SelectOption(label="Купить роль", value="buy", emoji="🛍️"),
            discord.SelectOption(label="Перевести валюту", value="transfer", emoji="💸"),
        ],
        callbacks={"show": show_shop, "buy": buy_role, "transfer": transfer},
    )


async def _send_lol_game_hub(bot: commands.Bot, interaction: discord.Interaction):
    async def my_profile(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "LolProfile", "profile")

    async def member_profile(next_interaction: discord.Interaction):
        await next_interaction.response.send_message(
            "Выбери участника, чей LoL-профиль нужно показать.",
            view=UserActionView(
                bot,
                SectionAction(
                    "lol_profile_user",
                    "LoL профиль",
                    "🧬",
                    "user_select",
                    "LolProfile",
                    "profile",
                    {"__target_param": "пользователь"},
                ),
            ),
            ephemeral=True,
        )

    async def refresh(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "LolProfile", "refresh")

    async def link(next_interaction: discord.Interaction):
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Привязать Riot ID",
            label="Riot ID",
            placeholder="Name#TAG",
            action=lambda modal_interaction, value: _invoke_cog_action(
                bot,
                modal_interaction,
                "LolProfile",
                "link",
                riot_id=value,
            ),
        ))

    async def unlink(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "LolProfile", "unlink")

    await _send_action_picker(
        interaction,
        title="🧬 League of Legends",
        description="Привязка Riot ID, обновление статистики, карточка игрока и первый типаж игрока по матчам Riot API.",
        placeholder="Выбери действие LoL",
        options=[
            discord.SelectOption(label="Мой LoL профиль", value="my_profile", emoji="👤"),
            discord.SelectOption(label="Профиль участника", value="member_profile", emoji="🧑"),
            discord.SelectOption(label="Обновить статистику", value="refresh", emoji="🔄"),
            discord.SelectOption(label="Привязать Riot ID", value="link", emoji="🔗"),
            discord.SelectOption(label="Отвязать Riot ID", value="unlink", emoji="❌"),
        ],
        callbacks={
            "my_profile": my_profile,
            "member_profile": member_profile,
            "refresh": refresh,
            "link": link,
            "unlink": unlink,
        },
    )


async def _send_random_hub(bot: commands.Bot, interaction: discord.Interaction):
    async def ball(next_interaction: discord.Interaction):
        async def submit_ball(modal_interaction: discord.Interaction, value: str):
            await _invoke_cog_action(bot, modal_interaction, "FunAndInfo", "шар", вопрос=value)
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Магический шар",
            label="Вопрос",
            placeholder="Спроси что-нибудь",
            action=submit_ball,
        ))

    async def dice(next_interaction: discord.Interaction):
        async def submit_dice(modal_interaction: discord.Interaction, value: str):
            try:
                sides = int(value)
            except ValueError:
                await modal_interaction.response.send_message("❌ Количество граней должно быть числом.", ephemeral=True)
                return
            await _invoke_cog_action(bot, modal_interaction, "FunAndInfo", "кубик", граней=sides)
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Бросить кубик",
            label="Количество граней",
            placeholder="Например: 6 или 20",
            action=submit_dice,
            default="6",
        ))

    async def joke(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "FunAndInfo", "анекдот")

    async def cat(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "FunAndInfo", "котик")

    async def coin(next_interaction: discord.Interaction):
        await _run_menu_only_action(bot, next_interaction, "coinflip")

    async def meme(next_interaction: discord.Interaction):
        await _run_menu_only_action(bot, next_interaction, "meme")

    await _send_action_picker(
        interaction,
        title="🎲 Случайное",
        description="Рандомные действия. Опрос убран: это теперь нативная функция Discord.",
        placeholder="Что сделать?",
        options=[
            discord.SelectOption(label="Магический шар", value="ball", emoji="🎱"),
            discord.SelectOption(label="Кубик", value="dice", emoji="🎲"),
            discord.SelectOption(label="Анекдот", value="joke", emoji="😂"),
            discord.SelectOption(label="Картинка котика", value="cat", emoji="🐱"),
            discord.SelectOption(label="Монетка", value="coin", emoji="🪙"),
            discord.SelectOption(label="Мем", value="meme", emoji="😂"),
        ],
        callbacks={"ball": ball, "dice": dice, "joke": joke, "cat": cat, "coin": coin, "meme": meme},
    )


async def _send_rps_hub(bot: commands.Bot, interaction: discord.Interaction):
    async def solo(next_interaction: discord.Interaction):
        await next_interaction.response.send_message(
            "Выбери ход против бота.",
            view=ActionSelectView(
                "Твой ход",
                [
                    discord.SelectOption(label="Камень", value="rock", emoji="🪨"),
                    discord.SelectOption(label="Ножницы", value="scissors", emoji="✂️"),
                    discord.SelectOption(label="Бумага", value="paper", emoji="📄"),
                ],
                {
                    "rock": lambda i: _invoke_cog_action(bot, i, "Games", "кнб", выбор="камень"),
                    "scissors": lambda i: _invoke_cog_action(bot, i, "Games", "кнб", выбор="ножницы"),
                    "paper": lambda i: _invoke_cog_action(bot, i, "Games", "кнб", выбор="бумага"),
                },
            ),
            ephemeral=True,
        )

    async def duel(next_interaction: discord.Interaction):
        await next_interaction.response.send_message(
            "Выбери соперника. После выбора бот создаст публичное приглашение на КНБ-дуэль.",
            view=UserActionView(
                bot,
                SectionAction(
                    "rps_duel_user",
                    "КНБ дуэль",
                    "⚔️",
                    "user_select",
                    "Games",
                    "кнб_дуэль",
                    {"таймаут_мин": 15, "__target_param": "оппонент"},
                ),
            ),
            ephemeral=True,
        )

    await _send_action_picker(
        interaction,
        title="✊ КНБ",
        description="Соло-режим и дуэль работают через кнопки: приглашение, принятие, скрытый выбор ходов и публичный итог.",
        placeholder="Выбери режим",
        options=[
            discord.SelectOption(label="Соло против бота", value="solo", emoji="🤖"),
            discord.SelectOption(label="Дуэль с участником", value="duel", emoji="⚔️"),
        ],
        callbacks={"solo": solo, "duel": duel},
    )


async def _send_steam_hub(bot: commands.Bot, interaction: discord.Interaction):
    async def profile(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "Steam", "steam_профиль")

    async def link(next_interaction: discord.Interaction):
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Привязать Steam",
            label="Ссылка, vanity или SteamID64",
            placeholder="https://steamcommunity.com/id/...",
            action=lambda modal_interaction, value: _invoke_cog_action(
                bot, modal_interaction, "Steam", "steam_привязать", профиль=value
            ),
        ))

    async def random_game(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "Steam", "steam_рандом")

    async def challenge(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "Steam", "steam_челлендж")

    async def wishlist(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "Steam", "steam_вишлист")

    await _send_action_picker(
        interaction,
        title="🎮 Steam",
        description="Профиль, библиотека, wishlist и игровые подсказки. Релизы/канал уведомлений настраиваются в админке.",
        placeholder="Что открыть?",
        options=[
            discord.SelectOption(label="Мой профиль", value="profile", emoji="👤"),
            discord.SelectOption(label="Привязать Steam", value="link", emoji="🔗"),
            discord.SelectOption(label="Во что сыграть", value="random", emoji="🎲"),
            discord.SelectOption(label="Челлендж", value="challenge", emoji="🏁"),
            discord.SelectOption(label="Вишлист", value="wishlist", emoji="🎁"),
        ],
        callbacks={"profile": profile, "link": link, "random": random_game, "challenge": challenge, "wishlist": wishlist},
    )


async def _send_wwm_hub(bot: commands.Bot, interaction: discord.Interaction):
    async def nick(next_interaction: discord.Interaction):
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Игровой ник WWM",
            label="Ник в Where Winds Meet",
            placeholder="Например: ShunVIP",
            action=lambda modal_interaction, value: _invoke_cog_action(
                bot, modal_interaction, "WWMGuild", "wwm_ник", игровой_ник=value
            ),
        ))

    async def card(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "WWMGuild", "wwm_карточка")

    async def roster(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "WWMGuild", "wwm_состав")

    async def search(next_interaction: discord.Interaction):
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Where Winds Meet KB",
            label="Search query",
            placeholder="English in-game terms work best",
            action=lambda modal_interaction, value: _invoke_cog_action(
                bot, modal_interaction, "WWMSearchCog", "wwm_search", query=value
            ),
        ))

    async def random_article(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "WWMSearchCog", "wwm_random")

    await _send_action_picker(
        interaction,
        title="🌿 Where Winds Meet",
        description="WWM-ник, карточки гильдии и база знаний. Каналы приветствия и приёмной уходят в админку.",
        placeholder="Что открыть?",
        options=[
            discord.SelectOption(label="Указать ник", value="nick", emoji="📝"),
            discord.SelectOption(label="Моя карточка", value="card", emoji="🎴"),
            discord.SelectOption(label="Состав", value="roster", emoji="👥"),
            discord.SelectOption(label="Поиск по базе", value="search", emoji="🔎"),
            discord.SelectOption(label="Случайная статья", value="random", emoji="🎲"),
        ],
        callbacks={"nick": nick, "card": card, "roster": roster, "search": search, "random": random_article},
    )


async def _send_games_hub(bot: commands.Bot, interaction: discord.Interaction):
    async def rps(next_interaction: discord.Interaction):
        await _send_rps_hub(bot, next_interaction)

    async def guess(next_interaction: discord.Interaction):
        async def submit_guess(modal_interaction: discord.Interaction, value: str):
            try:
                number = int(value)
            except ValueError:
                await modal_interaction.response.send_message("❌ Число должно быть числом.", ephemeral=True)
                return
            await _invoke_cog_action(bot, modal_interaction, "Games", "угадай", число=number, до=10)
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Угадай число",
            label="Твоя попытка",
            placeholder="Число от 1 до 10",
            action=submit_guess,
        ))

    async def blackjack(next_interaction: discord.Interaction):
        async def submit_bj(modal_interaction: discord.Interaction, value: str):
            try:
                bet = int(value)
            except ValueError:
                await modal_interaction.response.send_message("❌ Ставка должна быть числом.", ephemeral=True)
                return
            await _invoke_cog_action(bot, modal_interaction, "Games", "бж", ставка=bet)
        await next_interaction.response.send_modal(SingleFieldModal(
            title="Блэкджек",
            label="Ставка",
            placeholder="Минимум 5",
            action=submit_bj,
            default="5",
        ))

    async def hangman(next_interaction: discord.Interaction):
        await _invoke_cog_action(bot, next_interaction, "Games", "виселица")

    async def steam(next_interaction: discord.Interaction):
        await _send_steam_hub(bot, next_interaction)

    async def lol(next_interaction: discord.Interaction):
        await _send_lol_game_hub(bot, next_interaction)

    async def wwm(next_interaction: discord.Interaction):
        await _send_wwm_hub(bot, next_interaction)

    await _send_action_picker(
        interaction,
        title="🎮 Игровой хаб",
        description="Один вход для мини-игр, Steam, LoL и WWM.",
        placeholder="Выбери игровой раздел",
        options=[
            discord.SelectOption(label="КНБ", value="rps", emoji="✊"),
            discord.SelectOption(label="Угадай число", value="guess", emoji="🔢"),
            discord.SelectOption(label="Блэкджек", value="blackjack", emoji="🃏"),
            discord.SelectOption(label="Виселица", value="hangman", emoji="🔤"),
            discord.SelectOption(label="Steam", value="steam", emoji="🎮"),
            discord.SelectOption(label="League of Legends", value="lol", emoji="🧬"),
            discord.SelectOption(label="WWM", value="wwm", emoji="🌿"),
        ],
        callbacks={
            "rps": rps,
            "guess": guess,
            "blackjack": blackjack,
            "hangman": hangman,
            "steam": steam,
            "lol": lol,
            "wwm": wwm,
        },
    )


async def _run_section_action(bot: commands.Bot, interaction: discord.Interaction, action_id: str):
    action = SECTION_ACTION_BY_ID.get(action_id)
    if not action:
        await interaction.response.send_message("❌ Действие раздела не найдено.", ephemeral=True)
        return

    if action.kind == "call":
        await _invoke_cog_action(
            bot,
            interaction,
            action.cog_name or "",
            action.method_name or "",
            **(action.kwargs or {}),
        )
        return

    if action.kind == "menu_only":
        mapped = {"fun_coin": "coinflip", "fun_meme": "meme", "profile_ping": "ping"}.get(action.action_id)
        if mapped:
            await _run_menu_only_action(bot, interaction, mapped)
        else:
            await interaction.response.send_message("❌ Действие меню не настроено.", ephemeral=True)
        return

    if action.kind == "profile_hub":
        await _send_profile_hub(bot, interaction)
        return

    if action.kind == "accounts_hub":
        await _send_accounts_hub(bot, interaction)
        return

    if action.kind == "birthday_hub":
        await _send_birthday_hub(bot, interaction)
        return

    if action.kind == "size_action_select":
        await _send_size_picker(bot, interaction)
        return

    if action.kind == "community_info_select":
        await _send_community_info_picker(bot, interaction)
        return

    if action.kind == "wallet_status":
        await _send_wallet_status(bot, interaction)
        return

    if action.kind == "shop_hub":
        await _send_shop_hub(bot, interaction)
        return

    if action.kind == "top_hub":
        await _send_top_hub(bot, interaction)
        return

    if action.kind == "summary_hub":
        await _send_summary_hub(bot, interaction)
        return

    if action.kind == "games_hub":
        await _send_games_hub(bot, interaction)
        return

    if action.kind == "steam_hub":
        await _send_steam_hub(bot, interaction)
        return

    if action.kind == "wwm_hub":
        await _send_wwm_hub(bot, interaction)
        return

    if action.kind == "game_lol_hub":
        await _send_lol_game_hub(bot, interaction)
        return

    if action.kind == "random_hub":
        await _send_random_hub(bot, interaction)
        return

    if action.kind == "rps_hub":
        await _send_rps_hub(bot, interaction)
        return

    if action.kind == "reminder_modal":
        await interaction.response.send_modal(ReminderCreateModal(bot))
        return

    if action.kind == "transfer_modal":
        await interaction.response.send_modal(TransferModal(bot))
        return

    if action.kind == "buy_role_modal":
        await interaction.response.send_modal(BuyRoleModal(bot))
        return

    if action.kind == "mood_modal":
        async def submit_mood(modal_interaction: discord.Interaction, value: str):
            try:
                mood = int(value)
            except ValueError:
                await modal_interaction.response.send_message("❌ Оценка должна быть числом от 1 до 10.", ephemeral=True)
                return
            await _invoke_cog_action(bot, modal_interaction, "RepAndMood", "мое_настроение", оценка=mood)
        await interaction.response.send_modal(SingleFieldModal(
            title="Мое настроение",
            label="Оценка 1-10",
            placeholder="Например: 8",
            action=submit_mood,
        ))
        return

    if action.kind == "birthday_modal":
        async def submit_birthday(modal_interaction: discord.Interaction, value: str):
            await _invoke_cog_action(bot, modal_interaction, "Birthday", "др", дата=value)
        await interaction.response.send_modal(SingleFieldModal(
            title="Установить день рождения",
            label="Дата в формате ДД.ММ",
            placeholder="Например: 20.04",
            action=submit_birthday,
        ))
        return

    if action.kind == "magic_ball_modal":
        async def submit_ball(modal_interaction: discord.Interaction, value: str):
            await _invoke_cog_action(bot, modal_interaction, "FunAndInfo", "шар", вопрос=value)
        await interaction.response.send_modal(SingleFieldModal(
            title="Магический шар",
            label="Вопрос",
            placeholder="Спроси что-нибудь",
            action=submit_ball,
        ))
        return

    if action.kind == "dice_modal":
        async def submit_dice(modal_interaction: discord.Interaction, value: str):
            try:
                sides = int(value)
            except ValueError:
                await modal_interaction.response.send_message("❌ Количество граней должно быть числом.", ephemeral=True)
                return
            await _invoke_cog_action(bot, modal_interaction, "FunAndInfo", "кубик", граней=sides)
        await interaction.response.send_modal(SingleFieldModal(
            title="Бросить кубик",
            label="Количество граней",
            placeholder="Например: 6 или 20",
            action=submit_dice,
            default="6",
        ))
        return

    if action.kind == "guess_modal":
        async def submit_guess(modal_interaction: discord.Interaction, value: str):
            try:
                number = int(value)
            except ValueError:
                await modal_interaction.response.send_message("❌ Число должно быть числом.", ephemeral=True)
                return
            await _invoke_cog_action(bot, modal_interaction, "Games", "угадай", число=number, до=10)
        await interaction.response.send_modal(SingleFieldModal(
            title="Угадай число",
            label="Твоя попытка",
            placeholder="Число от 1 до 10",
            action=submit_guess,
        ))
        return

    if action.kind == "blackjack_modal":
        async def submit_bj(modal_interaction: discord.Interaction, value: str):
            try:
                bet = int(value)
            except ValueError:
                await modal_interaction.response.send_message("❌ Ставка должна быть числом.", ephemeral=True)
                return
            await _invoke_cog_action(bot, modal_interaction, "Games", "бж", ставка=bet)
        await interaction.response.send_modal(SingleFieldModal(
            title="Блэкджек",
            label="Ставка",
            placeholder="Минимум 5",
            action=submit_bj,
            default="5",
        ))
        return

    if action.kind == "steam_link_modal":
        async def submit_steam(modal_interaction: discord.Interaction, value: str):
            await _invoke_cog_action(bot, modal_interaction, "Steam", "steam_привязать", профиль=value)
        await interaction.response.send_modal(SingleFieldModal(
            title="Привязать Steam",
            label="Ссылка, vanity или SteamID64",
            placeholder="https://steamcommunity.com/id/...",
            action=submit_steam,
        ))
        return

    if action.kind == "lol_link_modal":
        async def submit_lol(modal_interaction: discord.Interaction, value: str):
            await _invoke_cog_action(bot, modal_interaction, "LolProfile", "link", riot_id=value)
        await interaction.response.send_modal(SingleFieldModal(
            title="Привязать Riot ID",
            label="Riot ID",
            placeholder="Name#TAG",
            action=submit_lol,
        ))
        return

    if action.kind == "wwm_nick_modal":
        async def submit_wwm_nick(modal_interaction: discord.Interaction, value: str):
            await _invoke_cog_action(bot, modal_interaction, "WWMGuild", "wwm_ник", игровой_ник=value)
        await interaction.response.send_modal(SingleFieldModal(
            title="Игровой ник WWM",
            label="Ник в Where Winds Meet",
            placeholder="Например: ShunVIP",
            action=submit_wwm_nick,
        ))
        return

    if action.kind == "wiki_modal":
        async def submit_wiki(modal_interaction: discord.Interaction, value: str):
            await _invoke_cog_action(bot, modal_interaction, "AITools", "вики", запрос=value)
        await interaction.response.send_modal(SingleFieldModal(
            title="Поиск в Википедии",
            label="Запрос",
            placeholder="Например: Данте",
            action=submit_wiki,
        ))
        return

    if action.kind == "pubmed_modal":
        async def submit_pubmed(modal_interaction: discord.Interaction, value: str):
            await _invoke_cog_action(bot, modal_interaction, "AITools", "пабмед", запрос=value)
        await interaction.response.send_modal(SingleFieldModal(
            title="Поиск PubMed",
            label="Запрос",
            placeholder="Например: vitamin D sleep",
            action=submit_pubmed,
        ))
        return

    if action.kind == "wwm_search_modal":
        async def submit_wwm(modal_interaction: discord.Interaction, value: str):
            await _invoke_cog_action(bot, modal_interaction, "WWMSearchCog", "wwm_search", query=value)
        await interaction.response.send_modal(SingleFieldModal(
            title="Where Winds Meet KB",
            label="Search query",
            placeholder="English in-game terms work best",
            action=submit_wwm,
        ))
        return

    if action.kind == "parody_topic_modal":
        async def submit_topic(modal_interaction: discord.Interaction, value: str):
            await modal_interaction.response.send_message(
                "Выбери пользователя для тематической пародии.",
                view=UserActionView(
                    bot,
                    SectionAction(
                        "parody_topic_user",
                        "Тема",
                        "🎯",
                        "user_select",
                        "ParodyEngine",
                        "тема",
                        {"ключевое_слово": value},
                    ),
                ),
                ephemeral=True,
            )
        await interaction.response.send_modal(SingleFieldModal(
            title="Пародия на тему",
            label="Ключевое слово",
            placeholder="Например: боссы, работа, сон",
            action=submit_topic,
        ))
        return

    if action.kind == "user_select":
        await interaction.response.send_message(
            f"{action.emoji} **{action.label}**",
            view=UserActionView(bot, action),
            ephemeral=True,
        )
        return

    await interaction.response.send_message("❌ Этот тип действия пока не поддержан.", ephemeral=True)


class MenuSelect(discord.ui.Select):
    def __init__(self, current: str, catalog: dict[str, list[dict]], *, admin_only: bool):
        self.catalog = catalog
        self.admin_only = admin_only
        options = [
            discord.SelectOption(
                label="Обзор",
                emoji="🧭",
                value="__overview__",
                description="Все категории и как пользоваться",
                default=(current == "__overview__"),
            )
        ]
        for name, items in catalog.items():
            style = CATEGORY_STYLES.get(name, CATEGORY_STYLES["🧩 Прочее"])
            options.append(
                discord.SelectOption(
                    label=name,
                    emoji=style.emoji,
                    value=name,
                    description=f"{len(items)} команд",
                    default=(name == current),
                )
            )
        super().__init__(
            placeholder="Выбери обзор или категорию...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        for option in self.options:
            option.default = option.value == chosen
        await interaction.response.edit_message(
            embed=_build_embed(chosen, self.catalog, admin_only=self.admin_only),
            view=MenuView(self.view.bot, chosen, self.catalog, admin_only=self.admin_only) if self.view else None,
        )


class MenuActionButton(discord.ui.Button):
    def __init__(self, action: MenuOnlyAction):
        self.action = action
        super().__init__(
            label=action.label,
            emoji=action.emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"menu_action:{action.action_id}",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if view is None:
            await interaction.response.send_message("❌ Меню уже недоступно.", ephemeral=True)
            return
        await _run_menu_only_action(view.bot, interaction, self.action.action_id)


class QuickActionButton(discord.ui.Button):
    def __init__(self, action: QuickButtonAction):
        self.action = action
        super().__init__(
            label=action.label,
            emoji=action.emoji,
            style=discord.ButtonStyle.primary,
            custom_id=f"quick_action:{action.action_id}",
            row=action.row,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if view is None:
            await interaction.response.send_message("❌ Меню уже недоступно.", ephemeral=True)
            return
        await _run_quick_button_action(view.bot, interaction, self.action.action_id)


class SectionActionButton(discord.ui.Button):
    def __init__(self, action: SectionAction):
        self.action = action
        super().__init__(
            label=action.label,
            emoji=action.emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"section_action:{action.action_id}",
            row=action.row,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if view is None:
            await interaction.response.send_message("❌ Меню уже недоступно.", ephemeral=True)
            return
        await _run_section_action(view.bot, interaction, self.action.action_id)


class MenuView(discord.ui.View):
    def __init__(self, bot: commands.Bot, current: str, catalog: dict[str, list[dict]], *, admin_only: bool):
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(MenuSelect(current, catalog, admin_only=admin_only))
        if not admin_only:
            section_actions = SECTION_ACTIONS.get(current, ())
            if section_actions:
                for action in section_actions:
                    self.add_item(SectionActionButton(action))
            elif current == "__overview__":
                for action in MENU_ONLY_ACTIONS:
                    self.add_item(MenuActionButton(action))
                for action in QUICK_BUTTON_ACTIONS:
                    self.add_item(QuickActionButton(action))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        self.stop()


class AdminPanelLinkView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Админ-панель", style=discord.ButtonStyle.link, url=url))


def _admin_panel_entry_content() -> str:
    return "\u200b"


async def _find_admin_panel_channel(bot: commands.Bot) -> discord.TextChannel | None:
    if WEB_ADMIN_CHANNEL_ID:
        channel = bot.get_channel(WEB_ADMIN_CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            return channel

    wanted = WEB_ADMIN_CHANNEL_NAME.strip().lower()
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.name.lower() == wanted:
                return channel
    return None


async def _ensure_admin_panel_entry(bot: commands.Bot) -> bool:
    channel = await _find_admin_panel_channel(bot)
    if not channel:
        log.warning(
            "admin panel channel not found: id={} name={}",
            WEB_ADMIN_CHANNEL_ID,
            WEB_ADMIN_CHANNEL_NAME,
        )
        return False

    payload = get_feature_payload(channel.guild.id, FEATURE_ADMIN_PANEL_ENTRY)
    message_id = int(payload.get("message_id") or 0)
    view = AdminPanelLinkView(get_web_admin_url())

    if message_id:
        try:
            message = await channel.fetch_message(message_id)
            if message.author == bot.user:
                await message.edit(content=_admin_panel_entry_content(), embed=None, view=view)
                log.info("admin panel entry updated: channel={} message={}", channel.id, message.id)
                return True
        except discord.NotFound:
            log.info("admin panel entry missing, creating new message: channel={} message={}", channel.id, message_id)
        except discord.Forbidden:
            log.warning("admin panel entry cannot be fetched or edited: channel={}", channel.id)
            return False
        except discord.HTTPException as exc:
            log.warning("admin panel entry update failed: channel={} error={}", channel.id, exc)

    try:
        message = await channel.send(content=_admin_panel_entry_content(), view=view)
    except discord.HTTPException as exc:
        log.warning("admin panel entry create failed: channel={} error={}", channel.id, exc)
        return False

    set_feature_payload(
        channel.guild.id,
        FEATURE_ADMIN_PANEL_ENTRY,
        {"channel_id": channel.id, "message_id": message.id},
    )
    log.info("admin panel entry created: channel={} message={}", channel.id, message.id)
    return True


async def ensure_admin_panel_entry(bot: commands.Bot) -> bool:
    if getattr(bot, "admin_panel_entry_ready", False):
        return True

    lock = getattr(bot, "admin_panel_entry_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        bot.admin_panel_entry_lock = lock

    async with lock:
        if getattr(bot, "admin_panel_entry_ready", False):
            return True
        ok = await _ensure_admin_panel_entry(bot)
        if ok:
            bot.admin_panel_entry_ready = True
        return ok


class Menu(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._admin_panel_entry_ready = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._admin_panel_entry_ready:
            return
        self._admin_panel_entry_ready = True
        await ensure_admin_panel_entry(self.bot)


    @app_commands.command(name="команды", description="Живой каталог всех обычных команд бота")
    async def команды(self, interaction: discord.Interaction):
        catalog = await _build_catalog(self.bot, admin_only=False)
        if not catalog:
            await interaction.response.send_message("❌ Не удалось собрать каталог команд.", ephemeral=True)
            return
        first = "__overview__"
        await interaction.response.send_message(
            embed=_build_embed(first, catalog, admin_only=False),
            view=MenuView(self.bot, first, catalog, admin_only=False),
            ephemeral=True,
        )

    @app_commands.command(name="админ", description="(Админ) Открыть админ-панель")
    @app_commands.checks.has_permissions(administrator=True)
    async def админ(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            view=AdminPanelLinkView(get_web_admin_url()),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Menu(bot))
