from __future__ import annotations

import random
from datetime import datetime, timezone


UTC = timezone.utc
SHAME_TEMPLATES = {
    1: (
        "👀 {mention} опять начинает, это уже **{count}**-й раз на этой неделе",
        "📊 {mention} набирает статистику: **{count}** токсичных сообщений за неделю",
        "🤔 {mention} не может без этого, счётчик: **{count}**",
    ),
    2: (
        "🚨 {mention} жарит — уже **{count}** раз за неделю, серьёзно?",
        "📈 {mention} бьёт рекорды: **{count}** раз за неделю",
        "🏆 {mention} лидирует в номинации «токсик недели»: **{count}** очков",
    ),
    3: (
        "🔥 {mention} совсем поехал — **{count}** раз за неделю, ты в порядке?",
        "💀 {mention} финалит: **{count}** токсичных за неделю, может хватит?",
        "🎖️ {mention} получает медаль «Главный токсик»: **{count}** раз за неделю",
    ),
}


class ToxicityCooldowns:
    def __init__(self, seconds: int = 24 * 3600):
        self.seconds = max(0, int(seconds))
        self._last_reply: dict[tuple[int, int], datetime] = {}

    def allow(self, guild_id: int, user_id: int, now: datetime | None = None) -> bool:
        timestamp = now or datetime.now(UTC)
        key = (int(guild_id), int(user_id))
        previous = self._last_reply.get(key)
        if previous and (timestamp - previous).total_seconds() < self.seconds:
            return False
        self._last_reply[key] = timestamp
        return True


def generate_markov_troll(user_id: int) -> str | None:
    try:
        from fun_slesh.parody_engine import generate_phrase, model_exists

        for quality in ("разум", "мем"):
            if model_exists(user_id, quality):
                sentence = generate_phrase(user_id, quality)
                if sentence:
                    return sentence
    except Exception:
        pass
    return None


def build_troll_response(
    mention: str, count: int, level: int, parody: str | None = None,
    *, rng: random.Random | None = None,
) -> str:
    picker = rng or random
    template = picker.choice(SHAME_TEMPLATES.get(int(level), SHAME_TEMPLATES[1]))
    response = template.format(mention=mention, count=int(count))
    if parody:
        connector = picker.choice((
            "\n\nА вот как это звучит на твоём языке: *«{parody}»*",
            "\n\nПереводим на твой: *«{parody}»*",
            "\n\nТвоя же модель говорит: *«{parody}»*",
        ))
        response += connector.format(parody=parody)
    return response
