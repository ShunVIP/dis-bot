"""Pure projection rules for the Discord command catalog.

This module deliberately knows nothing about ``discord.py`` objects.  The
Discord cog collects command metadata and hands plain dictionaries to this
service; classification, filtering and ordering can then be tested without a
running bot.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from typing import Any


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
    "пародия_исключить_канал",
    "пародия_вернуть_канал",
    "пародия_исключения",
}

INFO_COMMANDS = {"ачивки", "кто", "сервер", "пинг"}
RANDOM_COMMANDS = {"монетка", "шар", "кубик", "анекдот", "котик", "опрос", "мем"}
SEARCH_COMMANDS = {"вики", "пабмед", "wwm_search", "wwm_random"}
STATS_COMMANDS = {"топ_актив", "топ_слова", "топ_эмодзи", "voice_топ", "voice_я", "награды_статус"}
ECON_COMMANDS = {
    "баланс",
    "дэйлик",
    "перевод",
    "налог_статус",
    "магазин",
    "купить_роль",
    "топ_серии",
    "топ_баланс",
    "экономика_профиль",
}
GAME_COMMANDS = {"кнб", "кнб_дуэль", "угадай", "виселица", "виселица_старт", "виселица_буква", "бж", "бж_дуэль"}
REP_COMMANDS = {
    "размер",
    "уменьшить_размер",
    "топ_размер",
    "история_размера",
    "мое_настроение",
    "настроение_сегодня",
    "размер_роли",
    "моя_размер_роль",
}
BIRTHDAY_COMMANDS = {"др", "д-р", "все_др", "когда_др"}
PARODY_COMMANDS = {
    "пародия",
    "батл",
    "коллаж",
    "эпоха",
    "тема",
    "мем_фраза",
    "профиль_стиля",
    "модели_статус",
    "список_пользователей",
    "дообучить",
    "профилактика",
}
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


def category_for_command(qualified_name: str, module_name: str) -> str:
    """Return the user-facing category for one leaf command."""

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


def is_replaced_by_section_button(
    category: str,
    item: Mapping[str, Any],
    callbacks_by_category: Mapping[str, set[str]],
) -> bool:
    callback_name = item.get("callback_name")
    return bool(callback_name and callback_name in callbacks_by_category.get(category, set()))


def mention_for(qualified_name: str, root_id: int | None) -> str:
    if root_id:
        return f"</{qualified_name}:{root_id}>"
    return f"`/{qualified_name}`"


def build_catalog_projection(
    commands_flat: Iterable[Mapping[str, Any]],
    *,
    admin_only: bool,
    hidden_command_names: set[str] | frozenset[str],
    menu_only_items: Iterable[Mapping[str, Any]] = (),
    callbacks_by_category: Mapping[str, set[str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Filter and order plain command rows for the Discord presentation."""

    callbacks = callbacks_by_category or {}
    catalog: dict[str, list[dict[str, Any]]] = {}

    for source_item in commands_flat:
        item = dict(source_item)
        qualified_name = str(item["qualified_name"])
        root_name = str(item["root_name"])
        item["hidden_from_slash"] = root_name in hidden_command_names
        is_admin = bool(item["is_admin"])
        if admin_only != is_admin:
            continue

        category = category_for_command(qualified_name, str(item.get("module_name", "")))
        if admin_only and category != "🛡️ Админ":
            category = "🛡️ Админ"
        if not admin_only and category in {"💬 Болтовня", "☢️ Активность", "🛡️ Админ", "🧩 Прочее"}:
            continue
        if not admin_only and is_replaced_by_section_button(category, item, callbacks):
            continue
        catalog.setdefault(category, []).append(item)

    if not admin_only:
        for source_item in menu_only_items:
            item = dict(source_item)
            catalog.setdefault(str(item["category"]), []).append(item)

    for items in catalog.values():
        items.sort(key=lambda row: (not row.get("menu_only", False), row["qualified_name"]))

    ordered: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for category in CATEGORY_ORDER:
        if category in catalog:
            ordered[category] = catalog[category]
    for category, items in catalog.items():
        if category not in ordered:
            ordered[category] = items
    return dict(ordered)
