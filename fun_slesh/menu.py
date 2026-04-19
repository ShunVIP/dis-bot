# -*- coding: utf-8 -*-
# fun_slesh/menu.py
"""
/меню и /меню_админ — интерактивный справочник команд.
Select Menu вместо кнопок — нет лимита на количество категорий.
"""

import discord
from discord import app_commands
from discord.ext import commands

# ─── Пользовательские категории ───────────────────────────────────────────────

CATEGORIES = {
    "🎭 Пародия": {
        "color": discord.Color.purple(),
        "emoji": "🎭",
        "commands": [
            ("/пародия",            "Фраза в стиле пользователя (Мем / Разум / Автор / Нейро)"),
            ("/батл",               "7 раундов между двумя пользователями"),
            ("/коллаж",             "Смешать стиль двух пользователей"),
            ("/эпоха",              "Фраза из определённого года или промежутка"),
            ("/тема",               "Фраза по ключевому слову"),
            ("/мем_фраза",          "Короткая мем-фраза ЗАГЛАВНЫМИ — лучшая из N"),
            ("/профиль_стиля",      "Паспорт стиля речи пользователя"),
        ]
    },
    "📊 Статистика": {
        "color": discord.Color.blurple(),
        "emoji": "📊",
        "commands": [
            ("/топ_актив",          "Топ по количеству сообщений за N дней"),
            ("/топ_слова",          "Топ по количеству слов за N дней"),
            ("/топ_эмодзи",         "Топ по использованию эмодзи за N дней"),
            ("/voice_топ",          "Топ по времени в голосовых каналах"),
            ("/voice_я",            "Моя статистика по голосу"),
        ]
    },
    "⭐ Репутация": {
        "color": discord.Color.gold(),
        "emoji": "⭐",
        "commands": [
            ("/репа",               "Дать +1 репутацию участнику (раз в день)"),
            ("/антирепа",           "Снизить репутацию участнику (раз в день)"),
            ("/топ_репа",           "Топ репутации на сервере"),
            ("/история_репы",       "Кто и когда давал репутацию"),
            ("/репа_роли",          "Пороги репутации для получения роли"),
            ("/моя_репа_роль",      "Моя текущая роль за репутацию"),
            ("/мое_настроение",     "Оценить своё настроение от 1 до 10"),
            ("/настроение_сегодня", "Настроение участников за сегодня"),
        ]
    },
    "🎂 Дни рождения": {
        "color": discord.Color.pink(),
        "emoji": "🎂",
        "commands": [
            ("/др",                 "Установить свой день рождения (ДД.ММ)"),
            ("/д-р",                "Удалить свой день рождения"),
            ("/когда_др",           "Узнать дату рождения пользователя"),
            ("/все_др",             "Все дни рождения на сервере"),
        ]
    },
    "💰 Экономика": {
        "color": discord.Color.green(),
        "emoji": "💰",
        "commands": [
            ("/дэйлик",             "Ежедневная награда монет"),
            ("/баланс",             "Баланс монет (своё или чужое)"),
            ("/перевод",            "Передать монеты другому участнику"),
            ("/магазин",            "Магазин ролей за монеты"),
            ("/купить_роль",        "Купить роль из магазина"),
            ("/топ_баланс",         "Топ богатейших на сервере"),
            ("/топ_серии",          "Топ по сериям дэйлика"),
            ("/налог_статус",       "Текущие настройки налога"),
        ]
    },
    "🎮 Игры": {
        "color": discord.Color.red(),
        "emoji": "🎮",
        "commands": [
            ("/кнб",                "Камень-ножницы-бумага с ботом"),
            ("/кнб_дуэль",          "PvP дуэль КНБ"),
            ("/кнб_ход",            "Сделать ход в дуэли"),
            ("/кнб_отмена",         "Отменить свой вызов"),
            ("/угадай",             "Угадай число — выиграй монеты"),
            ("/виселица",           "Соло виселица (бот загадывает или своё слово)"),
            ("/виселица_старт",     "Мульти виселица — загадать слово для сервера"),
            ("/виселица_буква",     "Угадать букву в мульти виселице"),
            ("/бж",                 "Блэкджек против бота со ставкой"),
            ("/бж_дуэль",           "Блэкджек PvP — кто ближе к 21"),
            ("/шар",                "Магический шар — ответ на вопрос"),
        ]
    },
    "⏰ Напоминания": {
        "color": discord.Color.teal(),
        "emoji": "⏰",
        "commands": [
            ("/напоминания создать", "Разовое или повторяющееся напоминание (роли, пользователи)"),
            ("/напоминания мои",     "Активные напоминания с обратным отсчётом"),
            ("/напоминания удалить", "Удалить напоминание по ID"),
        ]
    },
    "🎲 Рандом": {
        "color": discord.Color.og_blurple(),
        "emoji": "🎲",
        "commands": [
            ("/монетка",            "Орёл или решка"),
            ("/кубик",              "Бросить кубик"),
            ("/анекдот",            "Случайный анекдот"),
            ("/котик",              "Случайное фото котика"),
            ("/мем",                "Случайный мем"),
        ]
    },
    "🔍 Поиск": {
        "color": discord.Color.from_rgb(100, 180, 255),
        "emoji": "🔍",
        "commands": [
            ("/вики",               "Поиск в Википедии"),
            ("/пабмед",             "Поиск в PubMed"),
            ("/wwm_search",         "Поиск в базе знаний WWM"),
        ]
    },
    "ℹ️ Информация": {
        "color": discord.Color.light_grey(),
        "emoji": "ℹ️",
        "commands": [
            ("/ачивки",             "Мои достижения"),
            ("/активные_роли",      "Мои активные роли на сервере"),
            ("/кто",                "Информация об участнике"),
            ("/сервер",             "Информация о сервере"),
            ("/пинг",               "Задержка бота"),
            ("/награды_статус",     "Настройки пассивных наград за активность"),
        ]
    },
    "🎮 Steam": {
        "color": discord.Color.dark_blue(),
        "emoji": "🎮",
        "commands": [
            ("/стим_привязать",     "Привязать Steam профиль"),
            ("/стим_отвязать",      "Отвязать Steam профиль"),
            ("/стим",               "Статистика Steam профиля"),
            ("/стим_вишлист",       "Вишлист Steam участника"),
            ("/стим_общие",         "Общие игры с другим участником"),
        ]
    },
    "☢️ Активность": {
        "color": discord.Color.orange(),
        "emoji": "☢️",
        "commands": [
            ("/токсичность топ",    "Топ токсичных участников за неделю"),
            ("/итог_дня",           "Итог дня с хокку и статистикой"),
            ("/войс_роли статус",   "Текущие авто-роли голосовых каналов"),
        ]
    },
}

ADMIN_CATEGORIES = {
    "🎭 Пародия (адм)": {
        "color": discord.Color.purple(),
        "emoji": "🎭",
        "commands": [
            ("/дообучить",              "Обучить модели: Markovify / Persona, GPT защищён на VPS"),
            ("/профилактика",           "Полный цикл, по умолчанию защищён от запуска на VPS"),
            ("/собрать_сообщения",      "Собрать сообщения из всех каналов"),
            ("/сбросить_чекпоинты",     "Перечитать все каналы с начала"),
            ("/модели_статус",          "Статус обученных моделей"),
            ("/список_пользователей",   "Все пользователи в базе"),
            ("/индекс_сообщений",       "Индексация истории канала батчами"),
        ]
    },
    "⚙️ Фильтры пародии": {
        "color": discord.Color.dark_grey(),
        "emoji": "⚙️",
        "commands": [
            ("/фильтр_канал_добавить",  "Исключить канал из сбора сообщений"),
            ("/фильтр_канал_убрать",    "Вернуть канал в сбор"),
            ("/фильтр_слово_блок",      "Полностью убрать слово из профиля стиля"),
            ("/фильтр_слово_понизить",  "Снизить приоритет слова (выбор %)"),
            ("/фильтр_слово_убрать",    "Убрать слово из любого фильтра"),
            ("/фильтр_список",          "Показать все активные фильтры"),
        ]
    },
    "🎂 Дни рождения (адм)": {
        "color": discord.Color.pink(),
        "emoji": "🎂",
        "commands": [
            ("/др_ад",                  "Установить день рождения другому пользователю"),
            ("/д-р_ад",                 "Удалить день рождения пользователя"),
        ]
    },
    "🛡️ Роли и порядок": {
        "color": discord.Color.dark_blue(),
        "emoji": "🛡️",
        "commands": [
            ("/выдать_роль",            "Выдать роль участнику"),
            ("/очистить_сироты",        "Удалить роли без участников"),
            ("/магазин_добавить",       "Добавить роль в магазин"),
            ("/магазин_убрать",         "Убрать роль из магазина"),
            ("/штраф",                  "Оштрафовать участника"),
            ("/налог_настроить",        "Включить/выключить налог"),
            ("/награды_настроить",      "Настроить монеты/репу за активность"),
            ("/репа_роль_добавить",     "Добавить порог репы для роли"),
            ("/репа_роль_убрать",       "Убрать порог репы"),
            ("/репа_роль_постоянная",   "Сделать репа-роль участника постоянной"),
            ("/репа_роль_изменить",     "Изменить порог или метку уровня"),
            ("/репа_роли_вкл",          "Включить / выключить систему репа-ролей"),
        ]
    },
    "⚙️ Система": {
        "color": discord.Color.greyple(),
        "emoji": "⚙️",
        "commands": [
            ("/токсичность вкл",        "Включить/выключить детектор токсичности"),
            ("/токсичность порог",      "Уровень чувствительности детектора"),
            ("/токсичность канал",      "Каналы мониторинга токсичности"),
            ("/итог_дня_канал",         "Канал для авто-постинга итога дня"),
            ("/итог_дня_вкл",           "Включить/выключить авто-постинг итога"),
            ("/войс_роли вкл",          "Включить/выключить авто-роли войса"),
            ("/релизы канал",           "Канал уведомлений о релизах/скидках Steam"),
            ("/релизы проверить",       "Проверить вишлисты Steam прямо сейчас"),
        ]
    },
}


# ─── Embed ────────────────────────────────────────────────────────────────────

def _build_embed(category: str, cmd_ids: dict, categories: dict) -> discord.Embed:
    data = categories[category]
    emb  = discord.Embed(title=category, color=data["color"])
    lines = []
    for cmd, desc in data["commands"]:
        name    = cmd.lstrip("/")
        cmd_id  = cmd_ids.get(name)
        cmd_fmt = f"</{name}:{cmd_id}>" if cmd_id else f"`{cmd}`"
        lines.append(f"{cmd_fmt} — {desc}")
    emb.description = "\n".join(lines)
    total = sum(len(v["commands"]) for v in categories.values())
    emb.set_footer(text=f"Всего команд: {total}")
    return emb


# ─── Select Menu View ─────────────────────────────────────────────────────────

class MenuSelect(discord.ui.Select):
    def __init__(self, current: str, cmd_ids: dict, categories: dict):
        self.cmd_ids    = cmd_ids
        self.categories = categories
        options = [
            discord.SelectOption(
                label=name,
                emoji=data.get("emoji"),
                value=name,
                default=(name == current),
            )
            for name, data in categories.items()
        ]
        super().__init__(
            placeholder="Выбери категорию...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        # Обновляем default в options
        for opt in self.options:
            opt.default = (opt.value == chosen)
        await interaction.response.edit_message(
            embed=_build_embed(chosen, self.cmd_ids, self.categories),
            view=self.view,
        )


class MenuView(discord.ui.View):
    def __init__(self, current: str, cmd_ids: dict, categories: dict):
        super().__init__(timeout=300)
        self.add_item(MenuSelect(current, cmd_ids, categories))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        self.stop()


# ─── Получение ID команд ──────────────────────────────────────────────────────

async def _fetch_cmd_ids(bot: commands.Bot) -> dict:
    cmd_ids = {cmd.name: cmd.id for cmd in bot.tree.get_commands() if hasattr(cmd, "id") and cmd.id}
    if not cmd_ids:
        try:
            fetched = await bot.tree.fetch_commands()
            cmd_ids = {c.name: c.id for c in fetched}
        except Exception:
            pass
    return cmd_ids


# ─── Cog ──────────────────────────────────────────────────────────────────────

class Menu(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="меню", description="Справочник всех команд бота по категориям")
    async def меню(self, interaction: discord.Interaction):
        cmd_ids = await _fetch_cmd_ids(self.bot)
        first   = next(iter(CATEGORIES))
        await interaction.response.send_message(
            embed=_build_embed(first, cmd_ids, CATEGORIES),
            view=MenuView(first, cmd_ids, CATEGORIES),
            ephemeral=True,
        )

    @app_commands.command(name="меню_админ", description="(Админ) Справочник административных команд")
    @app_commands.checks.has_permissions(administrator=True)
    async def меню_админ(self, interaction: discord.Interaction):
        cmd_ids = await _fetch_cmd_ids(self.bot)
        first   = next(iter(ADMIN_CATEGORIES))
        await interaction.response.send_message(
            embed=_build_embed(first, cmd_ids, ADMIN_CATEGORIES),
            view=MenuView(first, cmd_ids, ADMIN_CATEGORIES),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Menu(bot))
