from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable


DEFAULT_SUMMARY_TEXTS: dict[str, Any] = {
    "daily_title_template": "🌙 Итог дня — {date}",
    "daily_description_template": "*{haiku}*",
    "daily_footer_template": "Увидимся завтра 👋",
    "weekly_title_template": "🏆 Итоги недели — {start}–{end}",
    "monthly_title_template": "📅 Итоги месяца — {start}–{end}",
    "period_footer_template": "Итоги за {period}. Канал и автопостинг настраиваются в админ-панели.",
    "weekly_champion_message_template": "🏆 Поздравляем чемпионов недели: {mentions}",
    "game_spotlight_title_template": "{label}: {game}",
    "game_spotlight_empty_template": "За этот период никто не отметился в {game}.",
    "summary_theme": "neon",
    "summary_render_mode": "embed",
    "summary_accent_color": "",
    "summary_thumbnail_url": "",
    "summary_buttons_enabled": True,
    "summary_compact_mode": False,
    "game_filter_mode": "all",
    "daily_top_limit": "3",
    "period_top_limit": "5",
}

SUMMARY_THEME_COLORS = {
    "neon": 0x8B5CF6,
    "royal": 0xF59E0B,
    "forest": 0x10B981,
    "fire": 0xF97316,
}


class SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def merge_summary_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(DEFAULT_SUMMARY_TEXTS)
    for key, value in (payload or {}).items():
        if key not in result:
            result[key] = value
        elif isinstance(result[key], str):
            if isinstance(value, str) and value.strip():
                result[key] = value.strip()
        elif value is not None:
            result[key] = value
    return result


def render_summary_template(template: str, **values: Any) -> str:
    try:
        return str(template).format_map(SafeFormatDict(values))
    except (ValueError, KeyError, IndexError):
        return str(template)


def truthy_setting(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "да", "вкл"}


def block_enabled(payload: dict[str, Any], key: str) -> bool:
    return True if key not in payload else truthy_setting(payload.get(key))


def bounded_int(
    payload: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int = 25,
) -> int:
    try:
        value = int(str(payload.get(key) or default).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def block_title(payload: dict[str, Any], key: str, default: str) -> str:
    value = str(payload.get(f"{key}_title") or "").strip()
    return value or default


def format_seconds(seconds: int) -> str:
    clean = max(0, int(seconds))
    hours = clean // 3600
    minutes = (clean % 3600) // 60
    return f"{hours}ч {minutes}м" if hours else f"{minutes}м"


def week_bounds(today: date) -> tuple[date, date]:
    start_current_week = today - timedelta(days=today.weekday())
    if today.weekday() == 6:
        return start_current_week, today + timedelta(days=1)
    return start_current_week - timedelta(days=7), start_current_week


def month_bounds(today: date) -> tuple[date, date]:
    start_this_month = today.replace(day=1)
    if today.month == 12:
        start_next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        start_next_month = today.replace(month=today.month + 1, day=1)
    return start_this_month, start_next_month


def selected_game(payload: dict[str, Any]) -> str:
    return str(payload.get("game_spotlight_game") or "").strip()


def filter_game_rows(payload: dict[str, Any], rows: list[tuple]) -> list[tuple]:
    if str(payload.get("game_filter_mode") or "all").strip() != "only_selected":
        return rows
    selected = selected_game(payload).casefold()
    if not selected:
        return rows
    return [
        row for row in rows
        if (
            (len(row) >= 2 and str(row[1]).strip().casefold() == selected)
            or (row and str(row[0]).strip().casefold() == selected)
        )
    ]


def selected_game_user_rows(
    payload: dict[str, Any],
    stats: dict[str, Any],
) -> list[tuple[int, int]]:
    wanted = selected_game(payload).casefold()
    if str(payload.get("game_filter_mode") or "all").strip() != "only_selected" or not wanted:
        return list(stats.get("top_game_users") or [])
    rows = [
        (int(user_id), int(seconds))
        for user_id, activity_name, seconds in stats.get("top_user_games", [])
        if str(activity_name).strip().casefold() == wanted
    ]
    rows.sort(key=lambda row: row[1], reverse=True)
    return rows


def format_rank_lines(
    rows: list[tuple],
    suffix: str,
    member_name: Callable[[int], str],
    *,
    cast_int: bool = True,
    value_formatter: Callable[[Any], str] | None = None,
    limit: int = 5,
) -> str:
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for index, (user_id, raw_value) in enumerate(rows[:limit], start=1):
        prefix = medals[index - 1] if index <= 3 else f"**{index}.**"
        value = int(raw_value) if cast_int else raw_value
        shown = value_formatter(value) if value_formatter else f"{value} {suffix}"
        lines.append(f"{prefix} {member_name(int(user_id))} — **{shown}**")
    return "\n".join(lines) if lines else "Пока пусто."


def format_term_lines(rows: list[tuple]) -> str:
    if not rows:
        return "Пока пусто."
    return "\n".join(
        f"**{index}.** {term} — **{int(count)}**"
        for index, (term, count) in enumerate(rows[:3], start=1)
    )


def format_named_duration_lines(rows: list[tuple], limit: int = 5) -> str:
    if not rows:
        return "Пока пусто."
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for index, (name, seconds) in enumerate(rows[:limit], start=1):
        prefix = medals[index - 1] if index <= 3 else f"**{index}.**"
        lines.append(f"{prefix} {name} — **{format_seconds(int(seconds))}**")
    return "\n".join(lines)


def summary_metrics(stats: dict[str, Any]) -> str:
    parts = [
        f"💬 **{int(stats.get('total_msgs') or 0)}** сообщ.",
        f"🎙️ **{format_seconds(int(stats.get('total_voice_s') or 0))}** войс",
        f"🎮 **{format_seconds(int(stats.get('total_game_s') or 0))}** игры",
    ]
    return "  •  ".join(parts)


def period_focus_line(stats: dict[str, Any], member_name: Callable[[int], str]) -> str:
    focus = []
    if stats.get("top_msgs"):
        user_id, total = stats["top_msgs"][0]
        focus.append(f"чат держал {member_name(int(user_id))} ({int(total)} сообщ.)")
    if stats.get("top_game_users"):
        user_id, seconds = stats["top_game_users"][0]
        focus.append(f"в играх лидировал {member_name(int(user_id))} ({format_seconds(int(seconds))})")
    if stats.get("top_games"):
        name, seconds = stats["top_games"][0]
        focus.append(f"главная игра: {name} ({format_seconds(int(seconds))})")
    return " · ".join(focus[:3]) if focus else "Период прошёл тихо: статистика ещё копится."


def fit_field(value: str, limit: int = 1024) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def format_user_game_lines(
    rows: list[tuple],
    member_name: Callable[[int], str],
    limit: int = 10,
) -> str:
    if not rows:
        return "Пока пусто."
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for index, (user_id, game_name, seconds) in enumerate(rows[:limit], start=1):
        prefix = medals[index - 1] if index <= 3 else f"**{index}.**"
        lines.append(
            f"{prefix} {member_name(int(user_id))} — **{game_name}**, {format_seconds(int(seconds))}"
        )
    return "\n".join(lines)


def format_game_spotlight_lines(
    stats: dict[str, Any],
    game_name: str,
    member_name: Callable[[int], str],
    limit: int = 10,
) -> str:
    wanted = game_name.strip().casefold()
    rows = [
        (int(user_id), str(activity_name), int(seconds))
        for user_id, activity_name, seconds in stats.get("top_user_games", [])
        if str(activity_name).strip().casefold() == wanted
    ]
    if not rows:
        return ""
    rows.sort(key=lambda row: row[2], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for index, (user_id, _activity_name, seconds) in enumerate(rows[:limit], start=1):
        prefix = medals[index - 1] if index <= 3 else f"**{index}.**"
        lines.append(f"{prefix} {member_name(user_id)} — **{format_seconds(seconds)}**")
    return "\n".join(lines)


def tracked_daily_lines(stats: dict[str, Any]) -> str:
    lines = ["💬 сообщения, слова и эмодзи: топ-3 слов и эмодзи длиннее 2 букв"]
    if stats.get("total_voice_s") or stats.get("top_voice"):
        lines.append("🎙️ голосовые сессии")
    if stats.get("total_game_s") or stats.get("top_games") or stats.get("top_game_users"):
        lines.append("🎮 Discord-игры и игровое время")
    if stats.get("rep_events"):
        lines.append("⭐ Размер")
    if stats.get("toxic_count"):
        lines.append("☢️ токсичность")
    return "\n".join(lines)


def tracked_weekly_lines(stats: dict[str, Any]) -> str:
    lines = ["💬 сообщения, слова и эмодзи: топ-3 слов и эмодзи длиннее 2 букв"]
    if stats.get("total_voice_s") or stats.get("top_voice"):
        lines.append("🎙️ голосовые сессии")
    if stats.get("top_games") or stats.get("top_game_users") or stats.get("top_heroes"):
        lines.append("🎮 Discord-игры и игровое время")
    if stats.get("top_other_activities") or stats.get("top_activity_users"):
        lines.append("📡 прочие Discord-активности")
    if stats.get("top_balance") or stats.get("top_streaks"):
        lines.append("💰 экономика и дэйлики")
    if stats.get("top_rep"):
        lines.append("⭐ Размер")
    if stats.get("top_toxic"):
        lines.append("☢️ токсичность")
    return "\n".join(lines)


def winner_haiku(display_name: str, categories: list[str]) -> str:
    joined = ", ".join(categories[:3])
    return f"Корона недели.\n{display_name} забрал: {joined}.\nСервер шлёт салют."


def daily_winners(stats: dict[str, Any], member_name: Callable[[int], str]) -> str:
    lines = []
    if stats.get("top_chatters"):
        user_id, total = stats["top_chatters"][0]
        lines.append(f"💬 Чат: {member_name(int(user_id))} — **{int(total)} сообщ.**")
    if stats.get("top_voice"):
        user_id, seconds = stats["top_voice"][0]
        lines.append(f"🎙️ Войс: {member_name(int(user_id))} — **{format_seconds(int(seconds))}**")
    if stats.get("top_game_users"):
        user_id, seconds = stats["top_game_users"][0]
        lines.append(f"🎮 Игры: {member_name(int(user_id))} — **{format_seconds(int(seconds))}**")
    return "\n".join(lines) if lines else "Сегодня победители спрятались в тумане."


def weekly_champion_ids(stats: dict[str, Any]) -> list[int]:
    sources = (
        stats.get("top_msgs", []),
        stats.get("top_voice", []),
        stats.get("top_game_users", []),
        stats.get("top_rep", []),
    )
    result = []
    for rows in sources:
        if rows:
            user_id = int(rows[0][0])
            if user_id not in result:
                result.append(user_id)
    return result[:5]
