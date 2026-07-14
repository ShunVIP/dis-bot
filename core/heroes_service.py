from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable


UTC = timezone.utc
HEROES_PATTERNS = (
    "heroes of might and magic", "heroes of might & magic", "might and magic heroes",
    "might & magic heroes", "homm", "olden era", "heroes iii", "heroes iv",
    "heroes v", "heroes vi", "heroes vii", "heroes 3", "heroes 4", "heroes 5",
    "heroes 6", "heroes 7",
)
GENERIC_START_HAIKUS = (
    "{display_name} запустил Heroes. Где-то в аду один архидьявол устало сел и сказал: опять этот пошаговый цирк.",
    "Heroes снова открыты. {display_name}, поздравляю: вечер официально списан в бухгалтерию сомнительных решений.",
    "{display_name} ушел в Heroes. Если через час начнутся разговоры про руду, серу и цепочки героев — мы делаем вид, что не знакомы.",
    "На карте появился {display_name}. Замки напряглись, нейтралы приготовились страдать, здравый смысл вышел из комнаты.",
)
OLDEN_START_HAIKUS = (
    "{display_name} запустил Olden Era. Ностальгия проснулась, потянулась и снова попросила денег.",
    "Olden Era стартовала. {display_name} добровольно зашел туда, где надежды умирают медленнее, чем ход ИИ.",
    "{display_name} проверяет Olden Era. Где-то Heroes III смотрит на это с выражением: ну давай, удиви меня.",
)
GENERIC_END_HAIKUS = (
    "{display_name} вышел из Heroes спустя {duration}. Потери: время, достоинство, возможно один союзный стек по тупости.",
    "Heroes закрыты. {display_name} продержался {duration}; психика сервера просит короткий перерыв.",
    "{display_name} вернулся из Heroes. {duration} ушли туда, где караваны не ходят, а мораль падает сама.",
)
OLDEN_END_HAIKUS = (
    "{display_name} вышел из Olden Era спустя {duration}. Эксперимент признан смелым, последствия — мутными.",
    "Olden Era закрыта. {display_name} вернулся, и это уже лучший патч за сегодня.",
)


def normalize_game_name(name: str) -> str:
    return " ".join((name or "").lower().replace(":", " ").replace("-", " ").split())


def is_heroes_name(name: str) -> bool:
    normalized = normalize_game_name(name)
    return any(pattern in normalized for pattern in HEROES_PATTERNS)


def is_olden_era(name: str) -> bool:
    return "olden era" in normalize_game_name(name)


def extract_game_names(activities: Iterable[object]) -> set[str]:
    names = set()
    for activity in activities or ():
        name = getattr(activity, "name", None)
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def find_started_heroes(before_names: set[str], after_names: set[str]) -> str | None:
    return next((name for name in sorted(after_names - before_names) if is_heroes_name(name)), None)


def find_active_heroes(names: set[str]) -> str | None:
    return next((name for name in sorted(names) if is_heroes_name(name)), None)


def format_duration(seconds: int) -> str:
    hours, minutes = int(seconds) // 3600, (int(seconds) % 3600) // 60
    return f"{hours}ч {minutes}м" if hours else f"{minutes}м"


def build_troll_message(
    user_id: int, game_name: str, display_name: str, *, duration: str | None = None, ended: bool = False
) -> str:
    if ended:
        pool = OLDEN_END_HAIKUS if is_olden_era(game_name) else GENERIC_END_HAIKUS
    else:
        pool = OLDEN_START_HAIKUS if is_olden_era(game_name) else GENERIC_START_HAIKUS
    base = pool[hash((int(user_id), game_name, datetime.now(UTC).hour, duration or "")) % len(pool)]
    return base.format(display_name=display_name, duration=duration or "недолго")
