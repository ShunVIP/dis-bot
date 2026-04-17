# -*- coding: utf-8 -*-
# fun_slesh/parody_gpt.py
"""
Продвинутые модели пародии v1.0

📊 АВТОР  — TF-IDF + шаблоны на основе persona-профиля
            Мгновенно, без GPU, без скачивания
            Берёт реальные фразы пользователя и собирает новые
            по характерным словосочетаниям

🤖 НЕЙРО  — ruGPT-3 small fine-tune на GPU
            Скачивается один раз ~500MB
            Fine-tune 2-5 мин на пользователя на RTX 5080
            Понимает стиль и генерирует НОВЫЕ фразы в том же духе
            Использует persona как system prompt
"""

import os
import re
import json
import math
import random
import sqlite3
import asyncio
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

# Глобальный executor для fine-tune (не блокирует asyncio loop)
_EXECUTOR = ThreadPoolExecutor(max_workers=1)

import discord
from discord import app_commands
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from fun_slesh.parody_collector import (
    get_user_messages, get_user_stats, get_all_user_ids,
    collect_channel, _ensure_db as ensure_collector_db, DB_PATH,
)
from fun_slesh.parody_persona import (
    _ensure_persona_db, build_persona, save_persona, load_persona,
    persona_exists, build_gpt_system_prompt, build_all_personas,
    STOPWORDS, _tokenize, PERSONA_DB,
)
from fun_slesh.parody_collector import get_all_user_ids, get_user_stats, DB_PATH as _DB_PATH

RATINGS_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "parody_ratings.db"))

# ─── Очистка текста для GPT ───────────────────────────────────────────────────
_URL_RE      = re.compile(r'https?://\S+|www\.\S+', re.I)  # полные ссылки — оставляем как есть
_MENTION_RE  = re.compile(r'<@!?\d+>|<#\d+>|<@&\d+>|@\d{10,}')  # raw ID упоминаний
_DISCORD_FMT = re.compile(r'[*_`~|>]+')
_EMOJI_RE    = re.compile(r'<a?:\w+:\d+>')
_MULTI_SPACE = re.compile(r'\s{2,}')
_BULLET_RE   = re.compile(r'^\s*[•\-\*]\s+', re.MULTILINE)

def _clean_for_gpt(text: str) -> str:
    """Убирает ссылки, упоминания, Discord-форматирование."""
    text = _URL_RE.sub('', text)          # убираем неполные ссылки (без протокола)
    text = _MENTION_RE.sub('', text)      # убираем @упоминания и raw ID
    text = _EMOJI_RE.sub('', text)        # убираем кастомные эмодзи
    text = _DISCORD_FMT.sub('', text)     # убираем **жирный**, _курсив_ и т.д.
    text = _BULLET_RE.sub('', text)       # убираем буллеты
    text = _MULTI_SPACE.sub(' ', text)    # схлопываем пробелы
    text = text.strip()
    return text

def _clean_generated(text: str) -> str:
    """Дополнительная очистка уже сгенерированной фразы."""
    text = _clean_for_gpt(text)
    # Убираем висящие знаки препинания в начале
    # Убираем артефакты в начале: //, :// и прочий мусор
    text = re.sub(r"^[^\w\u0400-\u04FF]+", "", text)
    text = text.strip()
    return text

def _clean_messages_for_gpt(messages: list[str]) -> list[str]:
    """Фильтрует и чистит список сообщений для GPT обучения."""
    result = []
    for msg in messages:
        cleaned = _clean_for_gpt(msg)
        if len(cleaned.split()) < 3:
            continue
        if cleaned.startswith('/') or cleaned.startswith('!'):
            continue
        # Пропускаем если после чистки остались артефакты ://
        if '://' in cleaned:
            continue
        result.append(cleaned)
    return result
MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))

def resolve_user(guild: discord.Guild, value: str):
    value = value.strip()
    if value.isdigit():
        s = get_user_stats(int(value))
        if s["count"] > 0:
            return int(value), s["username"]
    for uid in get_all_user_ids():
        s = get_user_stats(uid)
        if value.lower() in (s["username"] or "").lower():
            return uid, s["username"]
    return None, None

def save_rating(user_id: int, quality: str, phrase: str, rating: int, rated_by: int):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with __import__('sqlite3').connect(RATINGS_DB) as conn:
        conn.execute("""
            INSERT INTO phrase_ratings (user_id, quality, phrase, rating, rated_by, rated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, quality, phrase, rating, rated_by, now))
        conn.commit()

class RatingView(discord.ui.View):
    def __init__(self, user_id: int, quality: str, phrase: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.quality = quality
        self.phrase  = phrase
        self.voted   = set()

    @discord.ui.button(label="👍", style=discord.ButtonStyle.success)
    async def thumbs_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.voted:
            await interaction.response.send_message("Ты уже голосовал!", ephemeral=True)
            return
        self.voted.add(interaction.user.id)
        save_rating(self.user_id, self.quality, self.phrase, +1, interaction.user.id)
        await interaction.response.send_message("👍 Отмечено как хорошая фраза!", ephemeral=True)

    @discord.ui.button(label="👎", style=discord.ButtonStyle.danger)
    async def thumbs_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.voted:
            await interaction.response.send_message("Ты уже голосовал!", ephemeral=True)
            return
        self.voted.add(interaction.user.id)
        save_rating(self.user_id, self.quality, self.phrase, -1, interaction.user.id)
        await interaction.response.send_message("👎 Отмечено — фраза не попадёт в модель.", ephemeral=True)

MSK = ZoneInfo("Europe/Moscow")
UTC = timezone.utc

GPT_MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "gpt"))

try:
    from config import REMOTE_MODEL_API_URL, REMOTE_MODEL_API_TOKEN
except Exception:
    REMOTE_MODEL_API_URL = os.environ.get("REMOTE_MODEL_API_URL", "").strip()
    REMOTE_MODEL_API_TOKEN = os.environ.get("REMOTE_MODEL_API_TOKEN", "").strip()

_REMOTE_EXISTS_CACHE: dict[int, tuple[bool, float]] = {}
_REMOTE_EXISTS_TTL_SEC = 60.0

def _remote_enabled() -> bool:
    return bool(REMOTE_MODEL_API_URL and REMOTE_MODEL_API_TOKEN)

def _remote_call(path: str, payload: dict, timeout: float = 8.0) -> dict | None:
    if not _remote_enabled():
        return None
    try:
        base = REMOTE_MODEL_API_URL.rstrip("/")
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{base}{path}",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Model-Token": REMOTE_MODEL_API_TOKEN,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None

def remote_gpt_model_exists(user_id: int, use_cache: bool = True) -> bool:
    if not _remote_enabled():
        return False

    now_ts = datetime.now(UTC).timestamp()
    cached = _REMOTE_EXISTS_CACHE.get(user_id)
    if use_cache and cached and now_ts - cached[1] < _REMOTE_EXISTS_TTL_SEC:
        return cached[0]

    result = _remote_call("/model_exists", {"user_id": user_id}, timeout=3.0)
    ok = bool(result and result.get("ok") and result.get("exists"))
    _REMOTE_EXISTS_CACHE[user_id] = (ok, now_ts)
    return ok

def remote_generate_neuro_phrase(user_id: int, max_new_tokens: int = 80) -> Optional[str]:
    result = _remote_call(
        "/generate_neuro_phrase",
        {"user_id": user_id, "max_new_tokens": max_new_tokens},
        timeout=30.0,
    )
    if not result or not result.get("ok"):
        return None
    phrase = (result.get("phrase") or "").strip()
    return phrase or None

# ─── Проверка зависимостей ────────────────────────────────────────────────────
try:
    import torch
    from transformers import GPT2LMHeadModel, GPT2Tokenizer, TrainingArguments, Trainer
    from datasets import Dataset
    TRANSFORMERS_OK = True
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[parody_gpt] 🤖 transformers OK | device: {DEVICE}")
except ImportError:
    TRANSFORMERS_OK = False
    DEVICE = "cpu"
    print("[parody_gpt] ⚠️  transformers не установлен. pip install transformers torch datasets")

# Алиас для импорта из parody_engine
GPT_OK = TRANSFORMERS_OK

BASE_MODEL_NAME = "ai-forever/rugpt3small_based_on_gpt2"

# ─── Пути к GPT моделям ───────────────────────────────────────────────────────
def _gpt_model_path(user_id: int) -> str:
    os.makedirs(GPT_MODELS_DIR, exist_ok=True)
    return os.path.join(GPT_MODELS_DIR, str(user_id))

def gpt_model_exists(user_id: int) -> bool:
    path = _gpt_model_path(user_id)
    local_exists = os.path.exists(path) and os.path.exists(os.path.join(path, "config.json"))
    return local_exists or remote_gpt_model_exists(user_id)

# ─── TF-IDF: АВТОР ───────────────────────────────────────────────────────────
def _tfidf_scores(user_id: int, all_user_ids: list[int]) -> dict[str, float]:
    """
    Считает TF-IDF для слов пользователя относительно всего корпуса.
    Слова с высоким TF-IDF — самые характерные для этого человека.
    """
    # TF: частота слов у данного пользователя
    user_msgs = get_user_messages(user_id)
    user_tokens = _tokenize(" ".join(user_msgs))
    user_freq = Counter(user_tokens)
    total_user = max(sum(user_freq.values()), 1)
    tf = {w: c / total_user for w, c in user_freq.items()}

    # IDF: сколько пользователей употребляют это слово
    doc_count = len(all_user_ids)
    word_user_count: dict[str, int] = {}

    # Для скорости берём только топ-500 слов пользователя
    top_words = set(w for w, _ in user_freq.most_common(500))
    for uid in all_user_ids:
        if uid == user_id:
            continue
        msgs = get_user_messages(uid)
        tokens = set(_tokenize(" ".join(msgs[:500])))  # первые 500 для скорости
        for w in top_words:
            if w in tokens:
                word_user_count[w] = word_user_count.get(w, 0) + 1

    tfidf = {}
    for w, tf_val in tf.items():
        if w in STOPWORDS or len(w) < 3:
            continue
        df = word_user_count.get(w, 0) + 1
        idf = math.log(doc_count / df)
        tfidf[w] = tf_val * idf

    return tfidf

# Кэш последних выданных фраз по user_id чтобы не повторяться
_recent_phrases: dict[int, list[str]] = {}

def generate_author_phrase(user_id: int) -> Optional[str]:
    """
    📊 АВТОР — TF-IDF генерация.

    Алгоритм:
    1. Находим самые характерные слова пользователя (TF-IDF)
    2. Из template_phrases выбираем предложения с максимальным покрытием
    3. Из топ-40 берём случайные пары и склеиваем начало одного + конец другого
    4. Проверяем что фраза не совпадает с последними 10 выданными
    """
    persona = load_persona(user_id)
    if not persona:
        return None

    templates = persona.get("template_phrases", [])
    char_words = set(persona.get("char_words", []))
    if not templates or not char_words:
        return None

    def score(s: str) -> float:
        tokens = set(_tokenize(s))
        overlap = len(tokens & char_words)
        length_bonus = 1.0 if 5 <= len(s.split()) <= 15 else 0.5
        return overlap * length_bonus

    scored = sorted(templates, key=score, reverse=True)
    top = scored[:40]  # топ-40 для большего разнообразия

    recent = _recent_phrases.get(user_id, [])

    # Пробуем несколько раз сгенерировать не повторяющуюся фразу
    for attempt in range(15):
        if len(top) < 2:
            break

        # Каждый раз случайно выбираем пару из топ-40
        idx1, idx2 = random.sample(range(min(len(top), 40)), 2)
        s1 = top[idx1].split()
        s2 = top[idx2].split()

        phrase = None
        if len(s1) >= 4 and len(s2) >= 4:
            cut1 = random.randint(max(1, len(s1) // 3), len(s1) * 2 // 3)
            cut2 = random.randint(len(s2) // 3, min(len(s2) - 1, len(s2) * 2 // 3))
            candidate = " ".join(s1[:cut1] + s2[cut2:])
            if len(candidate.split()) >= 5:
                phrase = candidate[0].upper() + candidate[1:]

        if phrase and phrase not in recent:
            # Запоминаем последние 10
            recent.append(phrase)
            if len(recent) > 10:
                recent.pop(0)
            _recent_phrases[user_id] = recent
            return phrase

    # Фоллбэк — случайный из топ-20 которого нет в recent
    fallbacks = [t for t in top[:20] if t not in recent]
    if fallbacks:
        phrase = random.choice(fallbacks)
        recent.append(phrase)
        _recent_phrases[user_id] = recent[-10:]
        return phrase

    return random.choice(top[:5]) if top else None

# ─── ruGPT: НЕЙРО ─────────────────────────────────────────────────────────────
_base_tokenizer = None
_base_model = None

def _get_base_model():
    """Загружает базовую ruGPT модель (один раз в память)."""
    global _base_tokenizer, _base_model
    if _base_tokenizer is None:
        print(f"[parody_gpt] Загружаю базовую модель {BASE_MODEL_NAME}...")
        _base_tokenizer = GPT2Tokenizer.from_pretrained(BASE_MODEL_NAME)
        _base_tokenizer.pad_token = _base_tokenizer.eos_token
        _base_model = GPT2LMHeadModel.from_pretrained(BASE_MODEL_NAME)
        print(f"[parody_gpt] ✅ Базовая модель загружена")
    return _base_tokenizer, _base_model

def _fine_tune_sync(user_id: int, messages: list[str],
                    epochs: int = 3) -> bool:
    """Синхронная версия — запускается в ThreadPoolExecutor."""
    """
    Fine-tune ruGPT на сообщениях пользователя.
    На RTX 5080: ~2-5 мин на пользователя при 1000+ сообщений.

    Использует persona как prefix чтобы модель понимала СТИЛЬ,
    а не просто запоминала фразы.
    """
    if not TRANSFORMERS_OK:
        return False

    persona = load_persona(user_id)
    system_prompt = build_gpt_system_prompt(persona) if persona else ""
    model_path = _gpt_model_path(user_id)

    try:
        tokenizer, _ = _get_base_model()

        # Загружаем существующую fine-tuned или базовую
        if gpt_model_exists(user_id):
            model = GPT2LMHeadModel.from_pretrained(model_path)
            print(f"[parody_gpt] Дообучаю существующую модель {user_id}")
        else:
            model = GPT2LMHeadModel.from_pretrained(BASE_MODEL_NAME)
            print(f"[parody_gpt] Fine-tune с нуля для {user_id}")

        model = model.to(DEVICE)

        # Готовим датасет: каждое сообщение оборачиваем в стиль-контекст
        # Это ключевой момент — модель учится что ПОСЛЕ такого описания стиля
        # должны идти фразы в том же духе
        prefix = f"[СТИЛЬ: {system_prompt[:200]}]\n" if system_prompt else ""
        # Чистим сообщения перед обучением — убираем ссылки, форматирование
        clean_messages = _clean_messages_for_gpt(messages)
        texts = [prefix + msg for msg in clean_messages if len(msg.split()) >= 3]

        if len(texts) < 50:
            return False

        def tokenize_fn(examples):
            return tokenizer(
                examples["text"],
                truncation=True,
                max_length=128,
                padding="max_length",
            )

        dataset = Dataset.from_dict({"text": texts})
        tokenized = dataset.map(tokenize_fn, batched=True)
        tokenized = tokenized.map(lambda x: {"labels": x["input_ids"]})

        # Аргументы обучения — оптимизированы для RTX 5080
        # fp16=False — Blackwell (sm_120) не полностью поддерживается в текущем torch
        # bf16 тоже отключаем для совместимости
        training_args = TrainingArguments(
            output_dir=model_path,
            num_train_epochs=epochs,
            per_device_train_batch_size=8 if DEVICE == "cuda" else 2,
            gradient_accumulation_steps=2,  # эффективный batch=16 без fp16
            learning_rate=3e-5,
            warmup_steps=50,
            save_strategy="no",
            logging_strategy="no",
            fp16=False,   # отключено — NaN на Blackwell sm_120
            bf16=False,
            dataloader_num_workers=0,
            report_to="none",
            max_grad_norm=1.0,  # clipping против NaN
        )

        trainer = Trainer(
            model=model,
            args=training_args,
            train_dataset=tokenized,
        )

        trainer.train()
        model.save_pretrained(model_path)
        tokenizer.save_pretrained(model_path)
        print(f"[parody_gpt] ✅ Сохранена модель для {user_id} → {model_path}")
        return True

    except Exception as e:
        print(f"[parody_gpt] ❌ Ошибка fine-tune {user_id}: {e}")
        return False

def generate_neuro_phrase(user_id: int, max_new_tokens: int = 80) -> Optional[str]:
    """
    🤖 НЕЙРО — генерация через fine-tuned ruGPT.

    Использует persona system prompt как prefix — модель генерирует
    фразу которая СТИЛИСТИЧЕСКИ подходит пользователю,
    даже используя слова которых у него не было.
    """
    local_exists = os.path.exists(_gpt_model_path(user_id)) and os.path.exists(os.path.join(_gpt_model_path(user_id), "config.json"))
    if not local_exists and remote_gpt_model_exists(user_id):
        return remote_generate_neuro_phrase(user_id, max_new_tokens=max_new_tokens)

    if not TRANSFORMERS_OK:
        return None
    if not local_exists:
        return None

    persona = load_persona(user_id)
    system_prompt = build_gpt_system_prompt(persona) if persona else ""

    try:
        tokenizer = GPT2Tokenizer.from_pretrained(_gpt_model_path(user_id))
        tokenizer.pad_token = tokenizer.eos_token
        model = GPT2LMHeadModel.from_pretrained(_gpt_model_path(user_id))
        model = model.to(DEVICE)
        model.eval()

        # Берём случайное характерное слово как затравку
        char_words = persona.get("char_words", []) if persona else []
        seed_word = random.choice(char_words[:10]) if char_words else ""

        # Формируем prompt: стиль + затравка
        if system_prompt and seed_word:
            prompt = f"[СТИЛЬ: {system_prompt[:150]}]\n{seed_word}"
        elif seed_word:
            prompt = seed_word
        else:
            prompt = ""

        inputs = tokenizer(prompt, return_tensors="pt").to(DEVICE)

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                temperature=0.85,        # чуть творчески но не хаотично
                top_p=0.92,              # nucleus sampling
                top_k=50,
                repetition_penalty=1.3,  # не повторяем слова
                pad_token_id=tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )

        generated = tokenizer.decode(outputs[0], skip_special_tokens=True)

        # Убираем prompt из результата
        if prompt and generated.startswith(prompt):
            generated = generated[len(prompt):].strip()

        # Чистим сгенерированный текст
        generated = _clean_generated(generated)

        # Обрезаем до первого законченного предложения
        for punct in ['.', '!', '?']:
            idx = generated.find(punct)
            if 15 < idx < 300:
                generated = generated[:idx + 1]
                break

        generated = generated.strip()
        if len(generated.split()) < 3:
            return None

        # Последняя проверка — не должно содержать артефактов
        if re.search(r'<@\d+>', generated) or re.search(r'@\d{8,}', generated):
            return None
        if len(generated.split()) < 3:
            return None

        return generated

    except Exception as e:
        print(f"[parody_gpt] ❌ Ошибка генерации {user_id}: {e}")
        return None

async def fine_tune_user(user_id: int, messages: list[str], epochs: int = 3) -> bool:
    """Асинхронная обёртка — не блокирует Discord event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _EXECUTOR,
        _fine_tune_sync, user_id, messages, epochs
    )

# ─── Cog ──────────────────────────────────────────────────────────────────────


async def setup(bot):
    pass  # библиотека функций, не Cog
