from __future__ import annotations

import random
import re
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

LUCY_GUARD_TEMPLATES = {
    "definite": (
        "🛡️ {mention}, Люсю (lucykramer), любимую котю shunvip, не трогать. "
        "Ещё выпад — и я сломаю ноги твоему персонажу. В игре, модераторы, спокойно.",
        "🐈 {mention}, руки прочь от Люси — коти и любви shunvip. "
        "Продолжишь — сломаю ноги твоему игровому герою и отправлю ползти до костра.",
    ),
    "ambiguous": (
        "🛡️ {mention}, если ты про Люсю (lucykramer), любимую котю shunvip, — тормози. "
        "Ещё выпад — и я сломаю ноги твоему персонажу. Разумеется, только в игре.",
        "🐈 {mention}, если под «женщиной» ты имеешь в виду Люсю, то Люсю не трогать. "
        "Иначе сломаю ноги твоему игровому герою и отправлю его отдыхать у костра.",
    ),
}

_LUCY_ALIASES = re.compile(
    r"(?<![\w])(?:lucykramer|люся|люсю|люсе|люси|люка|люку|люке|люсь)(?![\w])",
    re.IGNORECASE | re.UNICODE,
)
_WOMAN_ALIAS = re.compile(
    r"(?<![\w])женщин(?:а|у|е|ой|ы)(?![\w])",
    re.IGNORECASE | re.UNICODE,
)
_REPLY_TARGET_WORDS = re.compile(
    r"(?<![\w])(?:ты|тебя|тебе|тобой|твоя|твой|твои|женщин(?:а|у|е|ой|ы))(?![\w])",
    re.IGNORECASE | re.UNICODE,
)


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


def detect_lucy_target(
    text: str,
    *,
    target_user_id: int = 0,
    mentioned_user_ids: tuple[int, ...] = (),
    reply_author_id: int | None = None,
) -> str:
    """Return definite/ambiguous/none without treating every 'woman' as Lucy."""
    value = str(text or "")
    if _LUCY_ALIASES.search(value):
        return "definite"
    target_id = int(target_user_id or 0)
    if target_id and target_id in {int(user_id) for user_id in mentioned_user_ids}:
        return "definite"
    if target_id and reply_author_id == target_id and _REPLY_TARGET_WORDS.search(value):
        return "definite"
    if _WOMAN_ALIAS.search(value):
        return "ambiguous"
    return "none"


def build_lucy_guard_response(
    mention: str,
    target_kind: str,
    *,
    rng: random.Random | None = None,
) -> str:
    picker = rng or random
    templates = LUCY_GUARD_TEMPLATES.get(str(target_kind), LUCY_GUARD_TEMPLATES["definite"])
    return picker.choice(templates).format(mention=mention)


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
