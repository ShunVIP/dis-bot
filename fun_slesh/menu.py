# -*- coding: utf-8 -*-
"""
/меню и /меню_админ — живой каталог slash-команд.

В отличие от старого ручного списка:
- подтягивает реальные команды из bot.tree;
- показывает подкоманды групп вроде `токсичность топ`;
- не устаревает после добавления новых модулей;
- делит команды на категории по правилам, а не по захардкоженному списку.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands


@dataclass(frozen=True)
class CategoryStyle:
    emoji: str
    color: discord.Color


CATEGORY_STYLES: dict[str, CategoryStyle] = {
    "🧭 Навигация": CategoryStyle("🧭", discord.Color.light_grey()),
    "🎭 Пародия": CategoryStyle("🎭", discord.Color.purple()),
    "📊 Статистика": CategoryStyle("📊", discord.Color.blurple()),
    "⭐ Репутация": CategoryStyle("⭐", discord.Color.gold()),
    "🎂 Дни рождения": CategoryStyle("🎂", discord.Color.pink()),
    "💰 Экономика": CategoryStyle("💰", discord.Color.green()),
    "🎮 Игры": CategoryStyle("🎮", discord.Color.red()),
    "⏰ Напоминания": CategoryStyle("⏰", discord.Color.teal()),
    "🎲 Рандом": CategoryStyle("🎲", discord.Color.og_blurple()),
    "🔍 Поиск": CategoryStyle("🔍", discord.Color.from_rgb(100, 180, 255)),
    "ℹ️ Информация": CategoryStyle("ℹ️", discord.Color.light_grey()),
    "🎮 Steam": CategoryStyle("🎮", discord.Color.dark_blue()),
    "☢️ Активность": CategoryStyle("☢️", discord.Color.orange()),
    "💬 Болтовня": CategoryStyle("💬", discord.Color.from_rgb(225, 111, 255)),
    "🛡️ Админ": CategoryStyle("🛡️", discord.Color.dark_gold()),
    "🧩 Прочее": CategoryStyle("🧩", discord.Color.dark_grey()),
}

CATEGORY_ORDER = [
    "🧭 Навигация",
    "🎭 Пародия",
    "💬 Болтовня",
    "☢️ Активность",
    "📊 Статистика",
    "⭐ Репутация",
    "💰 Экономика",
    "🎮 Игры",
    "🎮 Steam",
    "⏰ Напоминания",
    "🔍 Поиск",
    "🎲 Рандом",
    "🎂 Дни рождения",
    "ℹ️ Информация",
    "🛡️ Админ",
    "🧩 Прочее",
]

CATEGORY_SUMMARIES = {
    "🧭 Навигация": "Главные входные точки и каталог бота.",
    "🎭 Пародия": "Фразы, батлы, стили, коллажи и обучение моделей.",
    "💬 Болтовня": "Настройки живого общения и внезапных ответов бота.",
    "☢️ Активность": "Токсичность, войс-роли, сводки, игровые реакции и мем-триггеры.",
    "📊 Статистика": "Сообщения, слова, эмодзи, голос и награды.",
    "⭐ Репутация": "Репа, настроение и роль за репутацию.",
    "💰 Экономика": "Монеты, дэйлик, переводы, магазин и баланс.",
    "🎮 Игры": "КНБ, виселица, угадай число и блэкджек.",
    "🎮 Steam": "Привязка Steam, вишлист, общие игры и релизы.",
    "⏰ Напоминания": "Создание, просмотр и удаление напоминаний.",
    "🔍 Поиск": "Вики, PubMed и поиск по Where Winds Meet.",
    "🎲 Рандом": "Монетка, шар, анекдоты, котики, мемы и опросы.",
    "🎂 Дни рождения": "Установка, просмотр и админ-управление ДР.",
    "ℹ️ Информация": "Пинг, пользователь, сервер и ачивки.",
    "🛡️ Админ": "Команды обслуживания, настройки и ручные переключатели.",
    "🧩 Прочее": "Редкие или пока неразобранные команды.",
}

ADMIN_ROOTS = {
    "дообучить",
    "профилактика",
    "индекс_сообщений",
    "др_ад",
    "д-р_ад",
    "выдать_роль",
    "очистить_сироты",
    "штраф",
    "налог_настроить",
    "магазин_добавить",
    "магазин_убрать",
    "награды_настроить",
    "репа_роль_добавить",
    "репа_роль_убрать",
    "репа_роль_постоянная",
    "репа_роль_изменить",
    "репа_роли_вкл",
    "итоги",
}

INFO_COMMANDS = {"ачивки", "кто", "сервер", "пинг"}
RANDOM_COMMANDS = {"монетка", "шар", "кубик", "анекдот", "котик", "опрос", "мем"}
SEARCH_COMMANDS = {"вики", "пабмед", "wwm_search", "wwm_random"}
STATS_COMMANDS = {"топ_актив", "топ_слова", "топ_эмодзи", "voice_топ", "voice_я", "награды_статус"}
ECON_COMMANDS = {"баланс", "дэйлик", "перевод", "налог_статус", "магазин", "купить_роль", "топ_серии", "топ_баланс"}
GAME_COMMANDS = {"кнб", "кнб_дуэль", "кнб_ход", "кнб_отмена", "угадай", "виселица", "виселица_старт", "виселица_буква", "бж", "бж_дуэль"}
REP_COMMANDS = {"репа", "антирепа", "топ_репа", "история_репы", "мое_настроение", "настроение_сегодня", "репа_роли", "моя_репа_роль"}
BIRTHDAY_COMMANDS = {"др", "д-р", "все_др", "когда_др"}
PARODY_COMMANDS = {"пародия", "батл", "коллаж", "эпоха", "тема", "мем_фраза", "профиль_стиля", "модели_статус", "список_пользователей", "дообучить", "профилактика"}
STEAM_ROOTS = {"стим_привязать", "стим_отвязать", "стим", "стим_вишлист", "стим_общие", "релизы"}
ACTIVITY_ROOTS = {"токсичность", "войс_роли", "итоги", "heroes_troll", "sixty_seven"}
REMINDER_ROOTS = {"напоминания"}
CHAT_ROOTS = {"болтовня"}
MENU_ROOTS = {"меню", "меню_админ"}


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

    if root in MENU_ROOTS:
        return "🧭 Навигация"
    if root in CHAT_ROOTS:
        return "💬 Болтовня"
    if root in REMINDER_ROOTS:
        return "⏰ Напоминания"
    if root in SEARCH_COMMANDS or module_name in {"fun_slesh.ai_tools", "fun_slesh.wwm_search_cog"}:
        return "🔍 Поиск"
    if root in STEAM_ROOTS or module_name == "fun_slesh.steam":
        return "🎮 Steam"
    if root in PARODY_COMMANDS or module_name.startswith("fun_slesh.parody_"):
        return "🎭 Пародия"
    if root in STATS_COMMANDS or module_name == "fun_slesh.message_and_voice_stats":
        return "📊 Статистика"
    if root in REP_COMMANDS or module_name in {"fun_slesh.rep_and_mood", "fun_slesh.rep_roles"}:
        return "⭐ Репутация"
    if root in BIRTHDAY_COMMANDS or module_name == "fun_slesh.birthday":
        return "🎂 Дни рождения"
    if root in ECON_COMMANDS or module_name == "fun_slesh.daily":
        return "💰 Экономика"
    if root in GAME_COMMANDS or module_name == "fun_slesh.games":
        return "🎮 Игры"
    if root in RANDOM_COMMANDS:
        return "🎲 Рандом"
    if root in INFO_COMMANDS or module_name in {"fun_slesh.achievements_engine", "fun_slesh.test_hello"}:
        return "ℹ️ Информация"
    if root in ACTIVITY_ROOTS or module_name in {"fun_slesh.toxicity", "fun_slesh.voice_roles", "fun_slesh.daily_summary", "fun_slesh.heroes_troll", "fun_slesh.sixty_seven"}:
        return "☢️ Активность"
    if root in ADMIN_ROOTS:
        return "🛡️ Админ"
    return "🧩 Прочее"


def _mention_for(qualified_name: str, root_id: int | None) -> str:
    if root_id:
        return f"</{qualified_name}:{root_id}>"
    return f"`/{qualified_name}`"


async def _fetch_root_ids(bot: commands.Bot) -> dict[str, int]:
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
        description = (cmd.description or "Без описания").strip()
        collected.append(
            {
                "qualified_name": qualified_name,
                "root_name": root_name,
                "module_name": module_name,
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
    commands_flat = _walk_leaf_commands(bot.tree.get_commands(), root_ids)
    catalog: dict[str, list[dict]] = {}

    for item in commands_flat:
        qualified_name = item["qualified_name"]
        is_admin = item["is_admin"]
        if admin_only and not is_admin:
            continue
        if not admin_only and is_admin:
            continue

        category = _category_for_command(qualified_name, item["module_name"])
        if admin_only and category != "🛡️ Админ":
            category = "🛡️ Админ"
        catalog.setdefault(category, []).append(item)

    for items in catalog.values():
        items.sort(key=lambda row: row["qualified_name"])

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

    intro = (
        "Это живой каталог реальных slash-команд бота.\n"
        "Выбери категорию в выпадающем списке ниже — внутри будут кликабельные команды."
    )
    if admin_only:
        intro += "\n\nСкрыты обычные пользовательские команды: здесь только админские действия."
    else:
        intro += "\n\nАдмин-команды вынесены отдельно в `/меню_админ`, чтобы обычное меню было чище."
    emb.description = intro

    lines = []
    for category, items in catalog.items():
        summary = CATEGORY_SUMMARIES.get(category, "Команды этой категории.")
        lines.append(f"**{category}** — {len(items)}\n{summary}")

    emb.add_field(name="Категории", value="\n\n".join(lines[:8]) or "Категории не найдены.", inline=False)
    if len(lines) > 8:
        emb.add_field(name="Ещё", value="\n\n".join(lines[8:]), inline=False)

    emb.set_footer(text=f"Всего команд: {total} • Каталог собирается автоматически из bot.tree")
    return emb


def _build_embed(category: str, catalog: dict[str, list[dict]], *, admin_only: bool) -> discord.Embed:
    if category == "__overview__":
        return _build_overview_embed(catalog, admin_only=admin_only)

    style = CATEGORY_STYLES.get(category, CATEGORY_STYLES["🧩 Прочее"])
    items = catalog.get(category, [])
    total = sum(len(v) for v in catalog.values())

    title = f"{style.emoji} {category.split(' ', 1)[-1]}"
    emb = discord.Embed(title=title, color=style.color)

    if admin_only:
        emb.description = "Админ-каталог собран из реальных slash-команд бота.\n\n"
    else:
        emb.description = "Живой каталог собран из реальных slash-команд бота.\n\n"

    lines = []
    for item in items:
        mention = _mention_for(item["qualified_name"], item["root_id"])
        lines.append(f"{mention}\n`{item['description']}`")

    emb.description += "\n".join(lines) if lines else "В этой категории пока ничего нет."
    emb.set_footer(text=f"Команд в этом меню: {total} • Нажми на mention, чтобы открыть slash-команду")
    return emb


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
            view=self.view,
        )


class MenuView(discord.ui.View):
    def __init__(self, current: str, catalog: dict[str, list[dict]], *, admin_only: bool):
        super().__init__(timeout=300)
        self.add_item(MenuSelect(current, catalog, admin_only=admin_only))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        self.stop()


class Menu(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="меню", description="Живой каталог всех обычных команд бота")
    async def меню(self, interaction: discord.Interaction):
        catalog = await _build_catalog(self.bot, admin_only=False)
        if not catalog:
            await interaction.response.send_message("❌ Не удалось собрать каталог команд.", ephemeral=True)
            return
        first = "__overview__"
        await interaction.response.send_message(
            embed=_build_embed(first, catalog, admin_only=False),
            view=MenuView(first, catalog, admin_only=False),
            ephemeral=True,
        )

    @app_commands.command(name="меню_админ", description="(Админ) Живой каталог административных команд")
    @app_commands.checks.has_permissions(administrator=True)
    async def меню_админ(self, interaction: discord.Interaction):
        catalog = await _build_catalog(self.bot, admin_only=True)
        if not catalog:
            await interaction.response.send_message("❌ Не удалось собрать каталог админ-команд.", ephemeral=True)
            return
        first = "__overview__"
        await interaction.response.send_message(
            embed=_build_embed(first, catalog, admin_only=True),
            view=MenuView(first, catalog, admin_only=True),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Menu(bot))
