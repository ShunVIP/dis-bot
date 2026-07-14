from __future__ import annotations

import time


MOOD_EMOJI = {
    1: "😭", 2: "😢", 3: "😕", 4: "😐", 5: "🙂",
    6: "😊", 7: "😄", 8: "😁", 9: "🤩", 10: "🔥",
}


class GameReputationCooldown:
    def __init__(self, seconds: int = 1800):
        self.seconds = max(0, int(seconds))
        self._seen: dict[tuple[int, int, str], float] = {}

    def allow(self, user_id: int, guild_id: int, game: str, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else float(now)
        key = (int(user_id), int(guild_id), str(game))
        previous = self._seen.get(key)
        if previous is not None and timestamp - previous < self.seconds:
            return False
        self._seen[key] = timestamp
        return True


def mood_emoji(value: int) -> str:
    return MOOD_EMOJI.get(int(value), "😶")


def mood_color(value: int) -> tuple[int, int, int]:
    score = max(1, min(int(value), 10))
    return max(0, 255 - score * 20), min(255, score * 25), 100


def average_mood(rows: list[tuple[object, int]]) -> float:
    return sum(int(value) for _, value in rows) / len(rows) if rows else 0.0
