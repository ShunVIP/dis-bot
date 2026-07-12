from __future__ import annotations

import os
import random
import re
import threading
from pathlib import Path
from typing import Any

from core.ml_artifacts import register_artifact, remove_artifacts
from core.parody_feedback_store import get_bad_phrases, get_good_phrases
from core.parody_message_store import get_user_messages, get_user_messages_by_year
from core.paths import MODELS_DIR
from core.runtime_policy import IS_SERVER_RUNTIME


try:
    import markovify

    MARKOV_OK = True
except ImportError:
    markovify = None
    MARKOV_OK = False


QUALITY_LEVELS = {
    "мем": {"state_size": 2, "emoji": "🎲", "desc": "абсурдный рандом", "candidates": 5, "min_words": 2},
    "разум": {"state_size": 3, "emoji": "🧠", "desc": "максимум осознанности", "candidates": 30, "min_words": 5},
}
DEFAULT_MODEL = "мем"
_MARKOV_CMD_RE = re.compile(r"^/\S")
_ROLE_RE = re.compile(r"<@&\d+>")


def model_path(user_id: int, quality: str = DEFAULT_MODEL) -> str:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return str(MODELS_DIR / f"{int(user_id)}_{quality}.json")


def save_model(user_id: int, quality: str, model: Any, source_rows: int = 0) -> None:
    path = model_path(user_id, quality)
    temporary = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    Path(temporary).write_text(model.to_json(), encoding="utf-8")
    os.replace(temporary, path)
    register_artifact(
        pipeline="parody_markov",
        user_id=user_id,
        kind=quality,
        path=path,
        source_rows=source_rows,
        execution_location="vps" if IS_SERVER_RUNTIME else "local_pc",
        metadata={"state_size": QUALITY_LEVELS[quality]["state_size"]},
    )


def load_model(user_id: int, quality: str = DEFAULT_MODEL):
    path = Path(model_path(user_id, quality))
    if not path.exists() or not MARKOV_OK:
        return None
    try:
        return markovify.Text.from_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def markov_model_exists(user_id: int, quality: str = DEFAULT_MODEL) -> bool:
    return Path(model_path(user_id, quality)).is_file()


def remove_user_models(user_id: int) -> int:
    removed = 0
    for quality in QUALITY_LEVELS:
        path = Path(model_path(user_id, quality))
        if path.is_file():
            path.unlink()
            removed += 1
    remove_artifacts(pipeline="parody_markov", user_id=user_id, kinds=set(QUALITY_LEVELS))
    return removed


def preprocess_for_markov(messages: list[str]) -> list[str]:
    clean = []
    for message in messages:
        value = str(message).strip()
        if not value:
            continue
        if _MARKOV_CMD_RE.match(value) and " " not in value and len(value) < 40:
            continue
        clean.append(value)
    return clean


def build_model(messages: list[str], state_size: int):
    if not messages or not MARKOV_OK:
        return None
    try:
        filtered = preprocess_for_markov(messages)
        return markovify.NewlineText("\n".join(filtered), state_size=state_size) if filtered else None
    except Exception:
        return None


def train_user(user_id: int, messages: list[str], quality: str = DEFAULT_MODEL):
    if quality not in QUALITY_LEVELS or not MARKOV_OK or not messages:
        return None
    training_messages = list(messages)
    if quality == "разум":
        good = get_good_phrases(user_id, quality)
        if good:
            training_messages.extend(good * 3)
    new_model = build_model(training_messages, QUALITY_LEVELS[quality]["state_size"])
    if not new_model:
        return None
    old_model = load_model(user_id, quality)
    if old_model:
        try:
            new_model = markovify.combine([new_model, old_model], [0.7, 0.3])
        except Exception:
            pass
    save_model(user_id, quality, new_model, len(messages))
    return new_model


def train_user_all_qualities(user_id: int, messages: list[str]) -> dict[str, bool]:
    return {quality: train_user(user_id, messages, quality) is not None for quality in QUALITY_LEVELS}


def train_all_users(user_ids: list[int], minimum_messages: int = 50) -> dict[int, dict[str, bool]]:
    result = {}
    for user_id in user_ids:
        messages = get_user_messages(user_id)
        if len(messages) >= minimum_messages:
            result[int(user_id)] = train_user_all_qualities(user_id, messages)
    return result


def strip_roles(text: str) -> str:
    return re.sub(r"  +", " ", _ROLE_RE.sub("", text)).strip()


def _is_coherent(phrase: str, minimum_words: int) -> bool:
    if not phrase:
        return False
    words = phrase.split()
    if len(words) < minimum_words:
        return False
    bad_endings = {"и", "или", "но", "а", "в", "на", "с", "к", "по", "за", "из", "от", "до"}
    return words[-1].lower().rstrip(".,!?") not in bad_endings


def _score_phrase(phrase: str) -> float:
    words = phrase.split()
    score = float(len(words))
    if phrase.rstrip()[-1] in ".!?":
        score += 3.0
    if 8 <= len(words) <= 20:
        score += 2.0
    return score


def generate_phrase(
    user_id: int,
    quality: str = DEFAULT_MODEL,
    tries: int = 200,
    context_word: str | None = None,
) -> str | None:
    if quality not in QUALITY_LEVELS:
        return None
    model = load_model(user_id, quality)
    if not model:
        return None
    config = QUALITY_LEVELS[quality]
    bad = get_bad_phrases(user_id, quality)
    candidates = []
    for _ in range(config["candidates"]):
        try:
            phrase = (
                model.make_sentence_with_start(context_word, tries=20, strict=False)
                if context_word
                else model.make_sentence(tries=max(1, tries // config["candidates"]))
            )
            if phrase and phrase not in bad and _is_coherent(phrase, config["min_words"]):
                candidates.append(phrase)
        except Exception:
            pass
    if candidates:
        return max(candidates, key=_score_phrase) if quality == "разум" else random.choice(candidates)
    for _ in range(20):
        phrase = model.make_sentence(tries=10)
        if phrase and phrase not in bad:
            return phrase
    return model.make_short_sentence(max_chars=200, tries=50)


def generate_collage(first_id: int, second_id: int, quality: str = DEFAULT_MODEL) -> str | None:
    if quality not in QUALITY_LEVELS:
        return None
    first, second = load_model(first_id, quality), load_model(second_id, quality)
    if not first or not second:
        return None
    try:
        model = markovify.combine([first, second], [0.5, 0.5])
        config = QUALITY_LEVELS[quality]
        bad = get_bad_phrases(first_id, quality) | get_bad_phrases(second_id, quality)
        candidates = [
            phrase
            for _ in range(config["candidates"])
            if (phrase := model.make_sentence(tries=20))
            and phrase not in bad
            and _is_coherent(phrase, config["min_words"])
        ]
        if quality == "разум" and candidates:
            return max(candidates, key=_score_phrase)
        return random.choice(candidates) if candidates else model.make_sentence(tries=100)
    except Exception:
        return None


def generate_epoch(user_id: int, year: int, quality: str = DEFAULT_MODEL) -> str | None:
    messages = get_user_messages_by_year(user_id, year)
    if len(messages) < 20 or quality not in QUALITY_LEVELS:
        return None
    model = build_model(messages, QUALITY_LEVELS[quality]["state_size"])
    return _generate_from_temporary_model(model, user_id, quality) if model else None


def generate_topic(user_id: int, keyword: str, quality: str = DEFAULT_MODEL) -> str | None:
    messages = [message for message in get_user_messages(user_id) if keyword.lower() in message.lower()]
    if len(messages) < 15 or quality not in QUALITY_LEVELS:
        return None
    model = build_model(messages, QUALITY_LEVELS[quality]["state_size"])
    return _generate_from_temporary_model(model, user_id, quality) if model else None


def _generate_from_temporary_model(model: Any, user_id: int, quality: str) -> str | None:
    config = QUALITY_LEVELS[quality]
    bad = get_bad_phrases(user_id, quality)
    candidates = [
        phrase
        for _ in range(config["candidates"])
        if (phrase := model.make_sentence(tries=20))
        and phrase not in bad
        and _is_coherent(phrase, config["min_words"])
    ]
    if quality == "разум" and candidates:
        return max(candidates, key=_score_phrase)
    return random.choice(candidates) if candidates else model.make_sentence(tries=50)
