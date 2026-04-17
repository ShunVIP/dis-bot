# -*- coding: utf-8 -*-
# fun_slesh/parody_persona.py
"""
Persona-профиль пользователя — "паспорт стиля речи".

Хранит в persona.db:
  - топ слов и биграмм (словарный запас)
  - средняя длина сообщения
  - любимые темы (кластеры слов)
  - эмоциональный профиль (мат, юмор, вопросы, восклицания)
  - синтаксические паттерны (длина предложений, структура)
  - характерные фразы-зацепки (начала и концы предложений)

Используется как system prompt для ruGPT и как шаблонная база для TF-IDF.
"""

import os
import re
import json
import math
from fun_slesh.parody_filters import get_blocked_words, get_downranked_words, apply_word_filters
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from fun_slesh.parody_collector import get_user_messages, get_user_stats, DB_PATH

UTC = timezone.utc
PERSONA_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "persona.db"))

# ─── БД ───────────────────────────────────────────────────────────────────────
def _ensure_persona_db():
    os.makedirs(os.path.dirname(PERSONA_DB), exist_ok=True)
    with sqlite3.connect(PERSONA_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS personas (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                profile     TEXT NOT NULL,   -- JSON
                built_at    TEXT NOT NULL,
                msg_count   INTEGER NOT NULL
            )
        """)
        conn.commit()

def save_persona(user_id: int, username: str, profile: dict, msg_count: int):
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(PERSONA_DB) as conn:
        conn.execute("""
            INSERT INTO personas (user_id, username, profile, built_at, msg_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username, profile=excluded.profile,
                built_at=excluded.built_at, msg_count=excluded.msg_count
        """, (user_id, username, json.dumps(profile, ensure_ascii=False), now, msg_count))
        conn.commit()

def load_persona(user_id: int) -> Optional[dict]:
    with sqlite3.connect(PERSONA_DB) as conn:
        cur = conn.execute("SELECT profile FROM personas WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

def persona_exists(user_id: int) -> bool:
    with sqlite3.connect(PERSONA_DB) as conn:
        cur = conn.execute("SELECT 1 FROM personas WHERE user_id=?", (user_id,))
        return cur.fetchone() is not None

# ─── Стоп-слова ───────────────────────────────────────────────────────────────
STOPWORDS = {
    "и","в","на","с","по","к","у","за","из","от","до","не","а","но","что","это",
    "как","так","то","же","ли","бы","да","нет","все","ещё","уже","тут","там",
    "вот","ну","мне","ты","я","он","она","мы","вы","они","его","её","их","им",
    "был","была","было","были","есть","быть","будет","будут","при","об","или",
    "со","во","без","для","под","над","про","через","между","если","когда","чем",
    "где","кто","чего","того","этого","этот","эта","эти","один","два","три",
}

# ─── Анализ ───────────────────────────────────────────────────────────────────
def _tokenize(text: str) -> list[str]:
    return re.findall(r'[а-яёa-z]+', text.lower())

def _extract_sentences(messages: list[str]) -> list[str]:
    """Разбиваем сообщения на предложения."""
    sentences = []
    for msg in messages:
        parts = re.split(r'[.!?]+', msg)
        for p in parts:
            p = p.strip()
            if len(p.split()) >= 3:
                sentences.append(p)
    return sentences

def build_persona(user_id: int, messages: list[str]) -> dict:
    """
    Строит полный профиль стиля пользователя.
    """
    if not messages:
        return {}

    stats = get_user_stats(user_id)
    username = stats.get("username", str(user_id))

    # ── Базовая статистика ────────────────────────────────────────────────────
    total_msgs = len(messages)
    msg_lengths = [len(m.split()) for m in messages]
    avg_len   = sum(msg_lengths) / max(total_msgs, 1)
    med_len   = sorted(msg_lengths)[len(msg_lengths) // 2]
    short_pct = sum(1 for l in msg_lengths if l <= 3)  / total_msgs  # доля коротких
    long_pct  = sum(1 for l in msg_lengths if l >= 15) / total_msgs  # доля длинных

    # ── Словарный запас ───────────────────────────────────────────────────────
    all_tokens = []
    for msg in messages:
        all_tokens.extend(_tokenize(msg))

    word_freq = Counter(all_tokens)
    vocab_size = len(word_freq)

    # TF-IDF: характерные слова (часто у него, редко у всех)
    # Используем простой вариант — топ слов без стоп-слов
    # URL_NOISE — технические паразиты ссылок (не редактируется вручную)
    # Для пользовательских блоков используй /фильтр_слово_блок
    URL_NOISE = {
        'https', 'http', 'www', 'com', 'youtube', 'youtu', 'watch',
        'view', 'tenor', 'discordapp', 'attachments', 'userapi',
        'media', 'videos', 'video', 'channel', 'imgur', 'tiktok',
        'pikabu', 'coub', 'drive', 'google', 'docs', 'spreadsheets',
        'pinimg', 'twitch', 'clips', 'joxi', 'width', 'height',
        'shop', 'product', 'finalize', 'status', 'result', 'option',
        'value', 'condition', 'show', 'name', 'unknown', 'emoji',
        'jpeg', 'ping', 'roll', 'here', 'everyone',
        'link', 'href', 'html', 'giphy', 'cdn',
    }

    # Загружаем пользовательские фильтры из БД
    _db_blocked    = get_blocked_words()     # полный блок
    _db_downranked = get_downranked_words()  # словарь {слово: strength}
    # Объединяем: URL_NOISE + блок из БД = полный стоп-сет
    FULL_BLOCK = URL_NOISE | _db_blocked

    # ── Подавление сверхчастотных «дежурных» слов через логарифмическую шкалу ──
    # Логика: слово ценно если оно встречается часто У ЭТОГО пользователя, но не
    # монополизирует всё общение. Используем log-dampening:
    # score = log(1 + count) / log(1 + max_count) * нормировка
    # Дополнительно: слова с долей > DOMINANCE_THRESHOLD от всех слов — жёстко режем.

    total_words = sum(word_freq.values()) or 1
    DOMINANCE_THRESHOLD = 0.04  # авто-порог: слово > 4% всего текста → дежурное

    def _word_score(word: str, count: int) -> float:
        w = word.lower()
        if w in FULL_BLOCK:
            return 0.0
        # Базовый score — логарифм с подавлением доминирующих слов
        share = count / total_words
        if share > DOMINANCE_THRESHOLD:
            base = math.log(1 + count) * (DOMINANCE_THRESHOLD / share) ** 2
        else:
            base = math.log(1 + count)
        # Применяем explicit понижение из БД (перекрывает авто-подавление если сильнее)
        return apply_word_filters(w, base, _db_blocked, _db_downranked)

    scored = [(w, _word_score(w, c)) for w, c in word_freq.most_common(400)
              if w not in STOPWORDS and w not in FULL_BLOCK and len(w) > 3 and not w.isdigit()]
    scored.sort(key=lambda x: -x[1])
    char_words = [w for w, s in scored if s > 0][:50]

    # Доминирующие слова для биграмм = авто-порог + явный блок из БД
    dominant_words = {
        w for w, c in word_freq.items()
        if c / total_words > DOMINANCE_THRESHOLD
    } | _db_blocked

    # Биграммы — фильтруем FULL_BLOCK и доминирующие из обоих позиций
    bigrams = Counter()
    for msg in messages:
        tokens = _tokenize(msg)
        for i in range(len(tokens) - 1):
            w1, w2 = tokens[i], tokens[i+1]
            if w1 in FULL_BLOCK or w2 in FULL_BLOCK:
                continue
            if w1 in dominant_words and w2 in dominant_words:
                continue  # обе части дежурные — пропускаем
            if w1 not in STOPWORDS or w2 not in STOPWORDS:
                bigrams[(w1, w2)] += 1
    top_bigrams = [list(bg) for bg, _ in bigrams.most_common(30)]

    # ── Эмоциональный профиль ─────────────────────────────────────────────────
    mat_pattern = re.compile(r'\b(бля|блять|хуй|хуе|пизд|ебан|ебать|нахуй|сука|блин|чёрт|млять)\w*', re.I)
    lol_pattern = re.compile(r'\b(лол|лмао|хах|хех|кек|ахах|ахха|хаха|xd|😂|🤣)\w*', re.I)

    mat_count  = sum(len(mat_pattern.findall(m)) for m in messages)
    lol_count  = sum(len(lol_pattern.findall(m)) for m in messages)
    q_count    = sum(1 for m in messages if '?' in m)
    exc_count  = sum(1 for m in messages if '!' in m)
    caps_count = sum(1 for m in messages if m == m.upper() and len(m) > 3)

    emotional = {
        "мат_на_100":     round(mat_count  / total_msgs * 100, 1),
        "юмор_на_100":    round(lol_count  / total_msgs * 100, 1),
        "вопросы_пct":    round(q_count    / total_msgs * 100, 1),
        "восклиц_pct":    round(exc_count  / total_msgs * 100, 1),
        "капс_pct":       round(caps_count / total_msgs * 100, 1),
    }

    # ── Синтаксические паттерны ───────────────────────────────────────────────
    sentences = _extract_sentences(messages)
    sent_lengths = [len(s.split()) for s in sentences]
    avg_sent_len = sum(sent_lengths) / max(len(sent_lengths), 1)

    # Начала предложений (первое слово)
    starts = Counter()
    for s in sentences:
        words = s.split()
        if words:
            starts[words[0].lower()] += 1
    top_starts = [w for w, _ in starts.most_common(20) if w not in STOPWORDS][:10]

    # Концы предложений (последнее слово)
    endings = Counter()
    for s in sentences:
        words = s.split()
        if words:
            clean = re.sub(r'[^\w]', '', words[-1].lower())
            if clean:
                endings[clean] += 1
    top_endings = [w for w, _ in endings.most_common(20) if w not in STOPWORDS][:10]

    # ── Любимые темы (топ тематических кластеров) ─────────────────────────────
    # Простая версия — слова которые встречаются вместе
    topic_words = char_words[:20]

    # ── Характерные реальные фразы (для TF-IDF шаблонов) ─────────────────────
    # Берём предложения средней длины которые наиболее типичны
    good_sentences = [s for s in sentences if 4 <= len(s.split()) <= 20]
    # Сортируем по тому насколько слова в предложении характерны для пользователя
    char_word_set = set(char_words)
    def sentence_score(s):
        tokens = set(_tokenize(s))
        return len(tokens & char_word_set) / max(len(tokens), 1)
    good_sentences.sort(key=sentence_score, reverse=True)
    template_phrases = good_sentences[:100]  # топ-100 для TF-IDF

    # ── Сборка профиля ────────────────────────────────────────────────────────
    profile = {
        "username":        username,
        "msg_count":       total_msgs,
        "vocab_size":      vocab_size,
        "avg_msg_len":     round(avg_len, 1),
        "med_msg_len":     med_len,
        "short_pct":       round(short_pct * 100, 1),
        "long_pct":        round(long_pct * 100, 1),
        "avg_sent_len":    round(avg_sent_len, 1),
        "char_words":      char_words,
        "top_bigrams":     top_bigrams,
        "topic_words":     topic_words,
        "emotional":       emotional,
        "top_starts":      top_starts,
        "top_endings":     top_endings,
        "template_phrases": template_phrases,
    }

    return profile

def build_gpt_system_prompt(profile: dict) -> str:
    """
    Строит system prompt для ruGPT на основе persona.
    Описывает стиль речи пользователя словами — модель понимает
    и может использовать НОВЫЕ слова которые подходят под этот стиль.
    """
    if not profile:
        return "Ты имитируешь стиль речи пользователя."

    name = profile.get("username", "пользователь")
    avg_len = profile.get("avg_msg_len", 10)
    emotional = profile.get("emotional", {})
    char_words = profile.get("char_words", [])[:20]
    bigrams = profile.get("top_bigrams", [])[:10]
    starts = profile.get("top_starts", [])
    endings = profile.get("top_endings", [])

    # Определяем характер стиля
    style_traits = []
    if avg_len <= 4:
        style_traits.append("пишет очень коротко, часто одним словом или фразой")
    elif avg_len <= 8:
        style_traits.append("пишет короткими фразами")
    elif avg_len >= 15:
        style_traits.append("пишет длинными развёрнутыми сообщениями")

    if emotional.get("мат_на_100", 0) > 10:
        style_traits.append("часто использует мат и грубые выражения")
    if emotional.get("юмор_на_100", 0) > 15:
        style_traits.append("любит шутить, часто смеётся")
    if emotional.get("вопросы_пct", 0) > 30:
        style_traits.append("часто задаёт вопросы")
    if emotional.get("восклиц_pct", 0) > 30:
        style_traits.append("эмоционально, много восклицаний")
    if emotional.get("капс_pct", 0) > 5:
        style_traits.append("иногда пишет КАПСОМ для акцента")

    traits_str = "; ".join(style_traits) if style_traits else "нейтральный стиль"
    words_str  = ", ".join(char_words) if char_words else ""
    starts_str = ", ".join(starts) if starts else ""

    bigram_examples = []
    for bg in bigrams[:5]:
        if len(bg) == 2:
            bigram_examples.append(f"{bg[0]} {bg[1]}")
    bigrams_str = ", ".join(bigram_examples)

    prompt = f"""Ты имитируешь стиль речи пользователя по имени {name}.

Характер стиля: {traits_str}.
Средняя длина сообщения: {avg_len:.0f} слов.

Характерные слова этого человека: {words_str}.
Характерные словосочетания: {bigrams_str}.
Часто начинает фразы со слов: {starts_str}.

Важно:
- Пиши В ХАРАКТЕРЕ этого человека, используй похожий словарный запас и интонацию
- Можешь использовать новые слова — главное чтобы они подходили под этот стиль
- НЕ копируй дословно существующие фразы — создавай новые в том же духе
- Длина ответа должна быть около {avg_len:.0f} слов
- Отвечай ТОЛЬКО одной фразой, без пояснений"""

    return prompt


def build_all_personas(min_messages: int = 50) -> dict:
    from fun_slesh.parody_collector import get_all_user_ids
    results = {}
    for uid in get_all_user_ids():
        msgs = get_user_messages(uid)
        if len(msgs) >= min_messages:
            stats = get_user_stats(uid)
            profile = build_persona(uid, msgs)
            save_persona(uid, stats.get("username", str(uid)), profile, len(msgs))
            results[uid] = True
    return results


async def setup(bot):
    _ensure_persona_db()
    print("[persona] ✅ Persona DB готова")
