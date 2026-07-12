from __future__ import annotations

from typing import Any


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

