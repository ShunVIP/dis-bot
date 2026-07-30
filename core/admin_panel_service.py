"""Business configuration and form projections for the web admin panel."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _feature(
    feature_id: str,
    title: str,
    group: str,
    description: str,
    channel_modes: tuple[str, ...] = (),
    settings_help: str = "",
    *,
    restart_on_change: bool = False,
) -> dict[str, Any]:
    item = {
        "id": feature_id,
        "title": title,
        "group": group,
        "description": description,
        "channel_modes": channel_modes,
        "settings_help": settings_help,
    }
    if restart_on_change:
        item["restart_on_change"] = True
    return item


FEATURE_REGISTRY = (
    _feature(
        "daily_summary", "Итоги сервера", "Настройки сервера",
        "Автопостинг итогов дня, недели и месяца.", ("output",),
        "Канал, куда бот отправляет итоги. Расписание пока хранится в коде планировщика.",
    ),
    _feature(
        "birthday", "Дни рождения", "Настройки сервера",
        "Поздравления, канал поздравлений и пользовательские даты.", ("output",),
        "Канал поздравлений и общий список дат рождения ниже на этой странице.",
    ),
    _feature(
        "activity_tracker", "Игровые активности", "Настройки сервера",
        "Тихий presence-трекинг и статистика игровых сессий без автоуведомлений.", (),
        "Функция только собирает статистику для итогов и команды топа; сама в канал не пишет.",
    ),
    _feature(
        "raid_schedule", "Расписание рейдов", "Игры",
        "Еженедельное расписание Люси и Рейми с подходящими днями рейда.", ("output",),
        "Полная неделя публикуется при настройке, затем по понедельникам в 09:00 МСК.",
    ),
    _feature(
        "wwm_guild", "WWM гильдия", "Настройки сервера",
        "Ники WWM, карточки, приветствие и приемная.", ("output", "allow", "exclude"),
        "Канал приветствия, приемная и ограничения по каналам для WWM-сценариев.",
    ),
    _feature(
        "steam", "Steam-релизы", "Настройки сервера",
        "Steam-профили, вишлисты, релизы и уведомления.", ("output",),
        "Канал уведомлений и минимальная скидка для подборок.",
    ),
    _feature(
        "toxicity", "Токсичность", "Модерация",
        "Детектор токсичности, пороги и исключения каналов.", ("allow", "exclude"),
        "Где проверять сообщения и где проверку отключить. Порог можно поправить в дополнительных настройках.",
    ),
    _feature(
        "social_chat", "Болтовня", "Модерация",
        "Ответы по обращению и добровольно включаемые разговорные каналы.", ("allow", "exclude"),
        "Без списка разрешённых каналов бот отвечает только на упоминание, имя или ответ на его сообщение.",
    ),
    _feature(
        "voice_roles", "Голосовые роли", "Экономика и роли",
        "Авто-роли по голосовым каналам и исключения.", ("allow", "exclude"),
        "Ограничения для автоматических ролей, связанных с голосовыми каналами.",
    ),
    _feature(
        "economy", "Экономика", "Экономика и роли",
        "Налоги, магазин, награды и персональная валюта.", (),
        "Налоги, магазин ролей, награды активности и персональная валюта.",
    ),
    _feature(
        "activity_rewards", "Награды за активность", "Экономика и роли",
        "Монеты и репутация за сообщения и время в голосе.", (),
        "Включение хранится здесь; интервалы и суммы задаются командой /награды_настроить.",
    ),
    _feature(
        "message_stats", "Статистика сообщений", "Настройки сервера",
        "Суточные агрегаты сообщений, слов, эмодзи и исключения каналов.", ("exclude",),
        "Исключённые каналы не участвуют в статистике и пассивных наградах.",
    ),
    _feature(
        "heroes_troll", "Heroes troll", "Игры",
        "Шутливые сообщения о запуске и завершении Heroes of Might and Magic.", ("output",),
        "Канал сообщений; история игровых сессий хранится отдельно от настройки.",
    ),
    _feature(
        "rep_roles", "Размер-роли", "Экономика и роли",
        "Автоматические временные роли по порогам репутации.", (),
        "Здесь систему можно отключить; пороги редактируются Discord-командами.",
    ),
    _feature(
        "parody_training", "Пародии и модели", "Модели и пародии",
        "Markov-модели, фильтры корпуса и безопасное обучение.", ("allow", "exclude"),
        "Каналы для сбора/использования пародийных ответов и безопасные флаги моделей.",
        restart_on_change=True,
    ),
    _feature(
        "maintenance", "Обслуживание", "Обслуживание",
        "Сбор сообщений, индексация, профилактика и ручные проверки.", (),
        "Тяжелые сервисные действия. Опасные операции оставлены за подтверждением и перезапуском.",
        restart_on_change=True,
    ),
    _feature(
        "fallback_platform", "Сайт и запасной чат", "Сайт и app",
        "Сайт/app, чат, комнаты, голосовые комнаты и демонстрации экрана.", (),
        "Настройки запасной площадки, когда Discord недоступен или нужен веб-чат.",
        restart_on_change=True,
    ),
)

FEATURES_BY_ID = {item["id"]: item for item in FEATURE_REGISTRY}

SUMMARY_TEXT_FIELDS = (
    ("daily_title_template", "Заголовок итога дня", "🌙 Итог дня — {date}",
     "Можно вставить: {date} - дата итога, {guild} - сервер, {haiku} - автоматическое хокку."),
    ("daily_description_template", "Текст под заголовком дня", "*{haiku}*",
     "Можно вставить: {haiku} - автоматическое хокку, {date} - дата, {guild} - сервер."),
    ("daily_footer_template", "Подпись итога дня", "Увидимся завтра 👋",
     "Можно вставить: {date} - дата, {guild} - сервер, {haiku} - хокку."),
    ("weekly_title_template", "Заголовок недели", "🏆 Итоги недели — {start}–{end}",
     "Можно вставить: {start} - начало периода, {end} - конец периода, {guild} - сервер, {period} - тип периода."),
    ("monthly_title_template", "Заголовок месяца", "📅 Итоги месяца — {start}–{end}",
     "Можно вставить: {start} - начало периода, {end} - конец периода, {guild} - сервер, {period} - тип периода."),
    ("period_footer_template", "Подпись недели и месяца",
     "Итоги за {period}. Канал и автопостинг настраиваются в админ-панели.",
     "Можно вставить: {period} - неделя или месяц, {start} - начало, {end} - конец, {guild} - сервер."),
    ("weekly_champion_message_template", "Сообщение с упоминанием чемпионов недели",
     "🏆 Поздравляем чемпионов недели: {mentions}",
     "Можно вставить: {mentions} - упоминания чемпионов, {guild} - сервер, {period} - период."),
    ("game_spotlight_title_template", "Заголовок выбранной игры", "{label}: {game}",
     "Можно вставить: {label} - твоё название, {game} - выбранная игра, {period}, {start}, {end}, {guild}."),
    ("game_spotlight_empty_template", "Если никто не играл в выбранную игру",
     "За этот период никто не отметился в {game}.",
     "Можно вставить: {game} - выбранная игра, {label} - твоё название, {period}, {start}, {end}, {guild}."),
)

SUMMARY_DAILY_BLOCKS = (
    ("daily_block_stats", "За день", "Общая сумма сообщений, войса и времени в играх."),
    ("daily_block_tracked", "Что трекалось", "Пояснение, какие данные бот учитывал."),
    ("daily_block_voice_games", "Играли", "Голосовые игровые каналы, где была активность."),
    ("daily_block_top_chatters", "Самые активные", "Топ участников по сообщениям."),
    ("daily_block_top_voice", "Топ войса", "Топ участников по времени в голосе."),
    ("daily_block_top_words", "Слова дня", "Топ слов за день."),
    ("daily_block_top_emojis", "Эмодзи дня", "Топ эмодзи за день."),
    ("daily_block_top_games", "Игры дня", "Топ игр по времени."),
    ("daily_block_user_games", "Кто во что играл", "Участники и игры, в которых они отметились."),
    ("daily_block_game_users", "Топ игроков дня", "Топ участников по игровому времени."),
    ("daily_block_game_winner", "Игровой победитель дня", "Первое место среди игроков дня."),
    ("daily_block_winners", "Победители дня", "Сводка победителей по чату, войсу и играм."),
    ("daily_block_misc", "Прочее", "Токсичность и дополнительные события дня."),
)

SUMMARY_PERIOD_BLOCKS = (
    ("period_block_main_people", "Главные люди", "Топ участников по сообщениям за неделю или месяц."),
    ("period_block_voice", "Войс", "Топ участников по времени в голосе."),
    ("period_block_rep", "Размер", "Топ по репутации/Размеру."),
    ("period_block_words", "О чём шумели", "Слова и эмодзи периода."),
    ("period_block_game_overview", "Игровой блок", "Heroes, топ игр и топ игроков."),
    ("period_block_game_spotlight", "Выбранная игра", "Отдельный блок по игре из списка, например Where Winds Meet."),
    ("period_block_user_games", "Кто во что играл", "Участники и игры периода."),
    ("period_block_other_activities", "Другие активности", "Стримы, слушает, смотрит и другие Discord-активности."),
    ("period_block_balance", "Баланс", "Топ по валюте."),
    ("period_block_streaks", "Серии", "Топ серий активности."),
    ("period_block_toxic", "Токсичность", "Топ токсичности и цитата."),
    ("period_block_champion_congrats", "Поздравления чемпионам", "Текстовый блок поздравлений победителей."),
)

SUMMARY_THEME_OPTIONS = (
    ("neon", "Неон", "Контрастный игровой стиль: фиолетовый, синий, яркие акценты."),
    ("royal", "Премиум", "Золотой акцент для недельных и месячных итогов."),
    ("forest", "Спокойный", "Зеленый и бирюзовый, меньше визуального шума."),
    ("fire", "Жаркий", "Красный/оранжевый акцент для соревновательных итогов."),
)

SUMMARY_FILTER_OPTIONS = (
    ("all", "Показывать все игры"),
    ("spotlight", "Все игры + отдельный блок выбранной игры"),
    ("only_selected", "Только выбранная игра в игровых блоках"),
)

SUMMARY_RENDER_OPTIONS = (
    ("embed", "Embed + кнопки"),
    ("components_v2", "Components v2 beta"),
)

SUMMARY_TEMPLATE_HELP = (
    ("{date}", "Дата итога дня"),
    ("{haiku}", "Автоматическое хокку дня"),
    ("{guild}", "Название сервера"),
    ("{start}", "Начало периода недели или месяца"),
    ("{end}", "Конец периода недели или месяца"),
    ("{period}", "Тип периода: неделю или месяц"),
    ("{mentions}", "Упоминания чемпионов недели"),
    ("{label}", "Твоё название группы, например “Задроты недели”"),
    ("{game}", "Выбранная игра из активности сервера"),
)


def feature_requires_restart(feature: Mapping[str, Any]) -> bool:
    return bool(feature.get("restart_on_change"))


def block_enabled(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "да", "вкл"}


def build_daily_summary_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, _, default, _ in SUMMARY_TEXT_FIELDS:
        value = str(data.get(key) or "").strip()
        payload[key] = value or default
    payload.update({
        "game_spotlight_enabled": _checked(data.get("game_spotlight_enabled")),
        "game_spotlight_label": str(data.get("game_spotlight_label") or "").strip() or "Задроты",
        "game_spotlight_game": str(data.get("game_spotlight_game") or "").strip(),
        "summary_theme": str(data.get("summary_theme") or "neon").strip() or "neon",
        "summary_render_mode": str(data.get("summary_render_mode") or "embed").strip() or "embed",
        "summary_accent_color": str(data.get("summary_accent_color") or "").strip(),
        "summary_thumbnail_url": str(data.get("summary_thumbnail_url") or "").strip(),
        "summary_buttons_enabled": _checked(data.get("summary_buttons_enabled")),
        "summary_compact_mode": _checked(data.get("summary_compact_mode")),
        "game_filter_mode": str(data.get("game_filter_mode") or "all").strip() or "all",
        "daily_top_limit": str(data.get("daily_top_limit") or "3").strip() or "3",
        "period_top_limit": str(data.get("period_top_limit") or "5").strip() or "5",
    })
    for key, _, _ in (*SUMMARY_DAILY_BLOCKS, *SUMMARY_PERIOD_BLOCKS):
        payload[key] = _checked(data.get(key))
        payload[f"{key}_title"] = str(data.get(f"{key}_title") or "").strip()
        payload[f"{key}_limit"] = str(data.get(f"{key}_limit") or "").strip()
    return payload


def parse_social_chat_form(data: Mapping[str, Any]) -> tuple[bool, int]:
    ambient_opt_in = _checked(data.get("ambient_opt_in"))
    try:
        chance_percent = int(str(data.get("chance_percent") or "0").strip())
    except ValueError as exc:
        raise ValueError("chance_percent must be an integer") from exc
    if chance_percent < 0 or chance_percent > 100:
        raise ValueError("chance_percent must be between 0 and 100")
    return ambient_opt_in, chance_percent


def mode_title(mode: str) -> str:
    return {
        "output": "канал публикаций",
        "allow": "разрешенный канал",
        "exclude": "запрещенный канал",
    }.get(mode, mode)


def _checked(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "on", "yes"}
