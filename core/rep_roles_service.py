from __future__ import annotations

import random
import re
from collections import Counter


def generate_role_name(user_id: int, threshold: int, label: str) -> str:
    words: list[str] = []
    try:
        from core.parody_message_store import get_user_messages

        stop_words = {"что", "это", "как", "для", "его", "она", "они", "или", "так", "уже", "ещё", "только"}
        tokens = (
            word
            for message in get_user_messages(user_id)
            for word in re.findall(r"[a-zа-яё]{3,}", message.lower())
            if word not in stop_words
        )
        words = [word for word, _ in Counter(tokens).most_common(15)]
    except Exception:
        pass
    with_word = (
        "Легенда {w}", "Властелин {w}", "Гуру {w}", "Бог {w}", "Повелитель {w}",
        "Мастер {w}", "Адепт {w}", "Хранитель {w}", "Профессор {w}", "Академик {w}",
        "Маршал {w}", "Барон {w}",
    )
    plain = (
        "Уважаемый участник", "Почётный резидент", "Заслуженный ветеран",
        "Известная личность", "Звезда сервера", "Признанный эксперт",
        "Почтённый старожил", "Человек-легенда",
    )
    name = random.choice(with_word).format(w=random.choice(words).capitalize()) if words else random.choice(plain)
    if label:
        name = f"{name} · {label}"
    return name[:100]
