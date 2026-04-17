# -*- coding: utf-8 -*-
# fun_slesh/parody_engine.py  v4.0
"""
ML-движок пародии.

/пародия  — все модели в одной команде (параметр "модель"):
    🎲 Мем      — markovify state_size=2
    🧠 Разум    — markovify state_size=3 + фильтр осознанности
    📊 Автор    — TF-IDF шаблоны
    🤖 Нейро    — ruGPT-3 fine-tune

/батл, /коллаж, /эпоха, /тема, /мем_фраза — спецрежимы
/дообучить   — markovify + persona + GPT (админ)
/профилактика — сброс → сбор → дообучить (всё сразу, админ)
/список_пользователей, /модели_статус
"""

import os
import re
import sys
import sqlite3
import asyncio
import random
import ctypes
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

# ─── Предотвращение сна Windows ───────────────────────────────────────────────
# ES_CONTINUOUS      = 0x80000000 — применять постоянно
# ES_SYSTEM_REQUIRED = 0x00000001 — система не спит
# ES_DISPLAY_REQUIRED = 0x00000002 — монитор не гасить (опционально)
_ES_CONTINUOUS       = 0x80000000
_ES_SYSTEM_REQUIRED  = 0x00000001

def _prevent_sleep():
    """Запрещает Windows засыпать. Вызывать перед долгой операцией."""
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
        )

def _allow_sleep():
    """Разрешает Windows засыпать снова."""
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)

import discord
from discord import app_commands
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

try:
    from fun_slesh.parody_engine_wakelock import prevent_sleep, allow_sleep
    WAKELOCK_OK = True
except Exception:
    WAKELOCK_OK = False
    def prevent_sleep(keep_display=False): pass
    def allow_sleep(): pass

from fun_slesh.parody_collector import (
    get_user_messages, get_user_stats, get_all_user_ids,
    collect_channel, _ensure_db as ensure_collector_db, DB_PATH,
)

try:
    import markovify
    MARKOV_OK = True
except ImportError:
    MARKOV_OK = False

# Persona — без GPU
try:
    from fun_slesh.parody_persona import (
        _ensure_persona_db, build_persona, save_persona,
        load_persona, persona_exists, build_all_personas,
    )
    PERSONA_OK = True
except Exception as e:
    PERSONA_OK = False
    print(f"[parody] ⚠️  Persona: {e}")

# GPT — требует torch
try:
    from fun_slesh.parody_gpt import (
        fine_tune_user, gpt_model_exists, GPT_MODELS_DIR,
        TRANSFORMERS_OK, DEVICE,
        generate_author_phrase, generate_neuro_phrase,
    )
    GPT_OK = TRANSFORMERS_OK
except Exception as e:
    GPT_OK = False
    TRANSFORMERS_OK = False
    print(f"[parody] ⚠️  GPT: {e}")

MSK = ZoneInfo("Europe/Moscow")
UTC = timezone.utc
MODELS_DIR  = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))
RATINGS_DB  = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "parody_ratings.db"))

_MK_EXECUTOR = ThreadPoolExecutor(max_workers=1)

# ─── Уровни markovify ─────────────────────────────────────────────────────────
QUALITY_LEVELS = {
    "мем":   {"state_size": 2, "emoji": "🎲", "desc": "абсурдный рандом",      "candidates": 5,  "min_words": 2},
    "разум": {"state_size": 3, "emoji": "🧠", "desc": "максимум осознанности", "candidates": 30, "min_words": 5},
}
DEFAULT_MODEL = "мем"

# Дубли аккаунтов — объединяются при старте
KNOWN_DUPLICATES: dict[int, list[int]] = {
    379371451079327748: [311460575152439299],
    362869980414345218: [230653962670309376],
    245175948314542080: [294095352053628929],
    399304144944496651: [366540917261467648],
    225821802432167937: [302166549102592000],
    226003307338924032: [347707345998053377],
}

# ─── Рейтинги ─────────────────────────────────────────────────────────────────
def _ensure_ratings_db():
    os.makedirs(os.path.dirname(RATINGS_DB), exist_ok=True)
    with sqlite3.connect(RATINGS_DB) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS phrase_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, quality TEXT NOT NULL,
            phrase TEXT NOT NULL, rating INTEGER NOT NULL,
            rated_by INTEGER NOT NULL, rated_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pr_user ON phrase_ratings(user_id, quality, rating)")
        conn.commit()

def save_rating(user_id: int, quality: str, phrase: str, rating: int, rated_by: int):
    _ensure_ratings_db()
    with sqlite3.connect(RATINGS_DB) as conn:
        conn.execute(
            "INSERT INTO phrase_ratings (user_id,quality,phrase,rating,rated_by,rated_at) VALUES (?,?,?,?,?,?)",
            (user_id, quality, phrase, rating, rated_by, datetime.now(UTC).isoformat())
        )
        conn.commit()

def get_bad_phrases(user_id: int, quality: str) -> set:
    _ensure_ratings_db()
    with sqlite3.connect(RATINGS_DB) as conn:
        cur = conn.execute("SELECT phrase FROM phrase_ratings WHERE user_id=? AND quality=? AND rating=-1", (user_id, quality))
        return {r[0] for r in cur.fetchall()}

def get_good_phrases(user_id: int, quality: str) -> list:
    _ensure_ratings_db()
    with sqlite3.connect(RATINGS_DB) as conn:
        cur = conn.execute("SELECT phrase FROM phrase_ratings WHERE user_id=? AND quality=? AND rating=1", (user_id, quality))
        return [r[0] for r in cur.fetchall()]

# ─── Пути к moделям ───────────────────────────────────────────────────────────
def _model_path(user_id: int, quality: str = DEFAULT_MODEL) -> str:
    os.makedirs(MODELS_DIR, exist_ok=True)
    return os.path.join(MODELS_DIR, f"{user_id}_{quality}.json")

def _save_model(user_id: int, quality: str, model):
    with open(_model_path(user_id, quality), "w", encoding="utf-8") as f:
        f.write(model.to_json())

def _load_model(user_id: int, quality: str = DEFAULT_MODEL):
    path = _model_path(user_id, quality)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return markovify.Text.from_json(f.read())
    except Exception:
        try:
            os.remove(path)
            print(f"[parody] ⚠️ Удалён битый файл: {path}")
        except Exception:
            pass
        return None

def model_exists(user_id: int, quality: str = DEFAULT_MODEL) -> bool:
    return os.path.exists(_model_path(user_id, quality))

# ─── Объединение дублей ───────────────────────────────────────────────────────
def _merge_accounts(primary_id: int, secondary_id: int) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM user_messages WHERE user_id=?", (secondary_id,))
        if cur.fetchone()[0] == 0:
            return 0
        cur.execute("UPDATE OR IGNORE user_messages SET user_id=? WHERE user_id=?", (primary_id, secondary_id))
        moved = cur.rowcount
        cur.execute("DELETE FROM user_messages WHERE user_id=?", (secondary_id,))
        cur.execute("DELETE FROM known_users WHERE user_id=?", (secondary_id,))
        conn.commit()
    for q in QUALITY_LEVELS:
        p = _model_path(secondary_id, q)
        if os.path.exists(p):
            os.remove(p)
    return moved

def apply_known_duplicates():
    for primary, secondaries in KNOWN_DUPLICATES.items():
        for sec in secondaries:
            moved = _merge_accounts(primary, sec)
            if moved > 0:
                print(f"[parody] 🔀 {primary} ← {sec} (+{moved} сообщ.)")

# ─── Обучение markovify ───────────────────────────────────────────────────────
_MARKOV_CMD_RE = re.compile(r'^/\S')

def _preprocess_for_markov(messages: list) -> list:
    """Убираем slash-команды из обучающего корпуса markovify."""
    clean = []
    for m in messages:
        s = m.strip()
        if not s:
            continue
        if _MARKOV_CMD_RE.match(s) and ' ' not in s and len(s) < 40:
            continue
        clean.append(s)
    return clean

def _build_model(messages: list, state_size: int):
    if not messages:
        return None
    try:
        filtered = _preprocess_for_markov(messages)
        if not filtered:
            return None
        return markovify.NewlineText("\n".join(filtered), state_size=state_size)
    except Exception:
        return None

def train_user(user_id: int, messages: list, quality: str = DEFAULT_MODEL):
    if not MARKOV_OK or not messages:
        return None
    cfg = QUALITY_LEVELS[quality]
    if quality == "разум":
        good = get_good_phrases(user_id, quality)
        if good:
            messages = messages + good * 3
    new_model = _build_model(messages, cfg["state_size"])
    if not new_model:
        return None
    old_model = _load_model(user_id, quality)
    if old_model:
        try:
            combined = markovify.combine([new_model, old_model], [0.7, 0.3])
            _save_model(user_id, quality, combined)
            return combined
        except Exception:
            pass
    _save_model(user_id, quality, new_model)
    return new_model

def train_user_all_qualities(user_id: int, messages: list) -> dict:
    return {q: (train_user(user_id, messages, q) is not None) for q in QUALITY_LEVELS}

def _train_all_users_sync(min_messages: int = 50) -> dict:
    results = {}
    for uid in get_all_user_ids():
        msgs = get_user_messages(uid)
        if len(msgs) >= min_messages:
            results[uid] = train_user_all_qualities(uid, msgs)
    return results

async def train_all_users_async(min_messages: int = 50) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_MK_EXECUTOR, _train_all_users_sync, min_messages)

def train_all_users(min_messages: int = 50) -> dict:
    return _train_all_users_sync(min_messages)

# ─── Генерация markovify ──────────────────────────────────────────────────────
import re as _re
_ROLE_RE = _re.compile(r'<@&\d+>')

def _strip_roles(text: str) -> str:
    text = _ROLE_RE.sub('', text)
    return _re.sub(r'  +', ' ', text).strip()

def _is_coherent(phrase: str, min_words: int) -> bool:
    if not phrase:
        return False
    words = phrase.split()
    if len(words) < min_words:
        return False
    bad_endings = {"и","или","но","а","в","на","с","к","по","за","из","от","до"}
    if words[-1].lower().rstrip(".,!?") in bad_endings:
        return False
    return True

def _score_phrase(phrase: str) -> float:
    words = phrase.split()
    score = len(words) * 1.0
    if phrase.rstrip()[-1] in ".!?":
        score += 3.0
    if 8 <= len(words) <= 20:
        score += 2.0
    return score

def generate_phrase(user_id: int, quality: str = DEFAULT_MODEL,
                    tries: int = 200, context_word: str = None) -> Optional[str]:
    if not MARKOV_OK:
        return None
    model = _load_model(user_id, quality)
    if not model:
        return None
    cfg = QUALITY_LEVELS[quality]
    bad = get_bad_phrases(user_id, quality)
    candidates = []
    for _ in range(cfg["candidates"]):
        try:
            if context_word:
                p = model.make_sentence_with_start(context_word, tries=20, strict=False)
            else:
                p = model.make_sentence(tries=tries // cfg["candidates"])
            if p and p not in bad and _is_coherent(p, cfg["min_words"]):
                candidates.append(p)
        except Exception:
            pass
    if not candidates:
        for _ in range(20):
            p = model.make_sentence(tries=10)
            if p and p not in bad:
                return p
        return model.make_short_sentence(max_chars=200, tries=50)
    return max(candidates, key=_score_phrase) if quality == "разум" else random.choice(candidates)

def generate_collage(id1: int, id2: int, quality: str = DEFAULT_MODEL) -> Optional[str]:
    if not MARKOV_OK:
        return None
    m1, m2 = _load_model(id1, quality), _load_model(id2, quality)
    if not m1 or not m2:
        return None
    try:
        combined = markovify.combine([m1, m2], [0.5, 0.5])
        cfg = QUALITY_LEVELS[quality]
        bad = get_bad_phrases(id1, quality) | get_bad_phrases(id2, quality)
        candidates = [p for _ in range(cfg["candidates"])
                      if (p := combined.make_sentence(tries=20))
                      and p not in bad and _is_coherent(p, cfg["min_words"])]
        return max(candidates, key=_score_phrase) if quality == "разум" and candidates \
            else (random.choice(candidates) if candidates else combined.make_sentence(tries=100))
    except Exception:
        return None

def get_user_messages_by_year(user_id: int, year: int) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT content FROM user_messages WHERE user_id=? AND strftime('%Y', created_at)=? ORDER BY created_at ASC",
                    (user_id, str(year)))
        return [r[0] for r in cur.fetchall()]

def get_available_years(user_id: int) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT strftime('%Y', created_at) yr, COUNT(*) cnt FROM user_messages WHERE user_id=? GROUP BY yr HAVING cnt >= 20 ORDER BY yr DESC",
                    (user_id,))
        return [int(r[0]) for r in cur.fetchall()]

def generate_epoch(user_id: int, year: int, quality: str = DEFAULT_MODEL) -> Optional[str]:
    msgs = get_user_messages_by_year(user_id, year)
    if len(msgs) < 20:
        return None
    model = _build_model(msgs, QUALITY_LEVELS[quality]["state_size"])
    if not model:
        return None
    cfg = QUALITY_LEVELS[quality]
    bad = get_bad_phrases(user_id, quality)
    candidates = [p for _ in range(cfg["candidates"])
                  if (p := model.make_sentence(tries=20))
                  and p not in bad and _is_coherent(p, cfg["min_words"])]
    return max(candidates, key=_score_phrase) if quality == "разум" and candidates \
        else (random.choice(candidates) if candidates else model.make_sentence(tries=50))

def generate_topic(user_id: int, keyword: str, quality: str = DEFAULT_MODEL) -> Optional[str]:
    filtered = [m for m in get_user_messages(user_id) if keyword.lower() in m.lower()]
    if len(filtered) < 15:
        return None
    model = _build_model(filtered, QUALITY_LEVELS[quality]["state_size"])
    if not model:
        return None
    cfg = QUALITY_LEVELS[quality]
    bad = get_bad_phrases(user_id, quality)
    candidates = [p for _ in range(cfg["candidates"])
                  if (p := model.make_sentence(tries=20))
                  and p not in bad and _is_coherent(p, cfg["min_words"])]
    return max(candidates, key=_score_phrase) if quality == "разум" and candidates \
        else (random.choice(candidates) if candidates else model.make_sentence(tries=50))

# ─── Резолюция пользователя ───────────────────────────────────────────────────
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

# ─── View: кнопки рейтинга ────────────────────────────────────────────────────
class RatingView(discord.ui.View):
    def __init__(self, user_id: int, quality: str, phrase: str):
        super().__init__(timeout=600)
        self.user_id  = user_id
        self.quality  = quality
        self.phrase   = phrase
        self.voted    = set()
        self.likes    = 0
        self.dislikes = 0

    async def _update_buttons(self, interaction: discord.Interaction):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label and child.label.startswith("👍"):
                    child.label = f"👍 {self.likes}" if self.likes else "👍"
                elif child.label and child.label.startswith("👎"):
                    child.label = f"👎 {self.dislikes}" if self.dislikes else "👎"
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="👍", style=discord.ButtonStyle.success)
    async def thumbs_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.voted:
            await interaction.response.send_message("Ты уже голосовал за эту фразу!", ephemeral=True)
            return
        self.voted.add(interaction.user.id)
        self.likes += 1
        save_rating(self.user_id, self.quality, self.phrase, +1, interaction.user.id)
        await interaction.response.send_message("👍 Хорошая фраза — попадёт в обучение!", ephemeral=True)
        await self._update_buttons(interaction)

    @discord.ui.button(label="👎", style=discord.ButtonStyle.danger)
    async def thumbs_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.voted:
            await interaction.response.send_message("Ты уже голосовал за эту фразу!", ephemeral=True)
            return
        self.voted.add(interaction.user.id)
        self.dislikes += 1
        save_rating(self.user_id, self.quality, self.phrase, -1, interaction.user.id)
        await interaction.response.send_message("👎 Отмечено — фраза исключится из модели.", ephemeral=True)
        await self._update_buttons(interaction)

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        self.stop()

# ─── Вспомогательная: полное дообучение всех моделей ─────────────────────────
async def _do_full_retrain(guild: discord.Guild, collect: bool = False) -> dict:
    """
    Запускает полный цикл: [сбор] → markovify → persona → GPT.
    Возвращает статистику.
    """
    stats = {"collected": 0, "markovify": 0, "persona": 0, "gpt": 0}

    # 1. Сбор (если нужно)
    if collect:
        channels = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).read_message_history]
        for ch in channels:
            stats["collected"] += await collect_channel(ch, guild.id)

    # 2. Markovify в executor
    mk_results = await train_all_users_async(min_messages=50)
    stats["markovify"] = len(mk_results)

    # 3. Persona
    if PERSONA_OK:
        def _build_personas():
            return build_all_personas(min_messages=50)
        loop = asyncio.get_event_loop()
        personas = await loop.run_in_executor(_MK_EXECUTOR, _build_personas)
        stats["persona"] = len(personas)

    # 4. GPT
    if GPT_OK:
        def _collect_gpt():
            return [(uid, get_user_messages(uid)) for uid in get_all_user_ids()
                    if len(get_user_messages(uid)) >= 200]
        loop = asyncio.get_event_loop()
        gpt_pairs = await loop.run_in_executor(_MK_EXECUTOR, _collect_gpt)
        for uid, msgs in gpt_pairs:
            if await fine_tune_user(uid, msgs, epochs=3):
                stats["gpt"] += 1

    return stats

# ─── Безопасная отправка (для долгих команд) ─────────────────────────────────
async def _safe_send(interaction: discord.Interaction,
                     status_msg: discord.Message | None,
                     embed: discord.Embed,
                     channel: discord.TextChannel | None = None):
    """
    Пытается обновить статусное сообщение.
    Если webhook протух (>15 мин) — отправляет в канал напрямую.
    """
    # 1. Пробуем отредактировать статус
    if status_msg:
        try:
            await status_msg.edit(content=None, embed=embed)
            return
        except Exception:
            pass

    # 2. Пробуем followup
    try:
        await interaction.followup.send(embed=embed)
        return
    except Exception:
        pass

    # 3. Fallback — напрямую в канал (всегда работает)
    try:
        ch = channel or interaction.channel
        if ch:
            mention = interaction.user.mention if interaction.user else ""
            await ch.send(content=mention, embed=embed)
    except Exception as e:
        print(f"[parody] ❌ Не удалось отправить финальное сообщение: {e}")


# ─── Cog ──────────────────────────────────────────────────────────────────────
class ParodyEngine(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_collector_db()
        _ensure_ratings_db()
        if PERSONA_OK:
            _ensure_persona_db()
        os.makedirs(MODELS_DIR, exist_ok=True)
        if not MARKOV_OK:
            print("[parody] ⚠️  markovify не установлен!")
        apply_known_duplicates()
        _prevent_sleep()  # бот запущен — ПК не спит
        print("[parody] 💡 Режим 'не спать' активирован")
        self._scheduler = AsyncIOScheduler(timezone=MSK)
        self._scheduler.add_job(self._weekly_retrain, "cron", day_of_week="sun", hour=3, minute=0)
        self._scheduler.start()

    async def _weekly_retrain(self):
        print("[parody] 🔄 Еженедельное дообучение...")
        for guild in self.bot.guilds:
            stats = await _do_full_retrain(guild, collect=True)
            print(f"[parody] {guild.name}: +{stats['collected']} сообщ | "
                  f"mk:{stats['markovify']} persona:{stats['persona']} gpt:{stats['gpt']}")
        print("[parody] ✅ Готово")

    # ── /пародия ──────────────────────────────────────────────────────────────
    @app_commands.command(name="пародия", description="Сгенерировать фразу в стиле пользователя")
    @app_commands.describe(
        пользователь="Участник сервера",
        ник_или_id="Ник или ID (для ушедших с сервера)",
        модель="Модель генерации",
    )
    @app_commands.choices(модель=[
        app_commands.Choice(name="🎲 Мем — абсурдный рандом",       value="мем"),
        app_commands.Choice(name="🧠 Разум — максимум осознанности", value="разум"),
        app_commands.Choice(name="📊 Автор — TF-IDF стиль",          value="автор"),
        app_commands.Choice(name="🤖 Нейрослоп — ruGPT fine-tune",   value="нейро"),
    ])
    async def пародия(self, interaction: discord.Interaction,
                      пользователь: discord.Member | None = None,
                      ник_или_id: str | None = None,
                      модель: str = DEFAULT_MODEL):

        # Резолюция пользователя
        target_id, display_name, avatar_url = None, None, None
        if пользователь:
            target_id, display_name, avatar_url = пользователь.id, пользователь.display_name, пользователь.display_avatar.url
        elif ник_или_id:
            target_id, display_name = resolve_user(interaction.guild, ник_или_id)
            if not target_id:
                await interaction.response.send_message(
                    f"😕 **{ник_или_id}** не найден. Используй `/список_пользователей`.", ephemeral=True)
                return
        else:
            await interaction.response.send_message("❌ Укажи пользователя через @ или ник_или_id.", ephemeral=True)
            return

        # Для автор/нейро проверяем ДО defer
        if модель == "автор" and not PERSONA_OK:
            await interaction.response.send_message("❌ Persona недоступна.", ephemeral=True)
            return
        if модель == "нейро":
            if not GPT_OK:
                await interaction.response.send_message("❌ ruGPT не установлен.", ephemeral=True)
                return
            if not gpt_model_exists(target_id):
                await interaction.response.send_message(
                    f"😕 Нейро-модель для **{display_name}** не обучена.\n"
                    f"Запусти `/дообучить` сначала.", ephemeral=True)
                return

        # Для markovify проверяем ДО defer
        if модель in QUALITY_LEVELS and not model_exists(target_id, модель):
            msgs = get_user_messages(target_id)
            if len(msgs) < 50:
                await interaction.response.send_message(
                    f"😕 Мало данных для **{display_name}**: {len(msgs)} сообщ. (нужно ≥50).", ephemeral=True)
                return

        await interaction.response.defer(thinking=True)

        # Генерация
        phrase, color, icon, footer = None, discord.Color.purple(), "💬", ""

        if модель == "автор":
            if not persona_exists(target_id):
                msgs = get_user_messages(target_id)
                profile = build_persona(target_id, msgs)
                save_persona(target_id, get_user_stats(target_id).get("username", str(target_id)), profile, len(msgs))
            phrase = generate_author_phrase(target_id)
            color, icon, footer = discord.Color.teal(), "📊", "Автор · TF-IDF шаблоны"

        elif модель == "нейро":
            phrase = generate_neuro_phrase(target_id)
            color, icon, footer = discord.Color.brand_red(), "🤖", "НЕЙРОслоп · ruGPT fine-tune"

        else:
            if not model_exists(target_id, модель):
                msgs = get_user_messages(target_id)
                if not train_user(target_id, msgs, модель):
                    await interaction.followup.send("❌ Не удалось обучить модель.")
                    return
            phrase = generate_phrase(target_id, модель)
            q = QUALITY_LEVELS[модель]
            color, icon, footer = discord.Color.purple(), "💬", f"{q['emoji']} {модель.capitalize()} · {q['desc']}"

        if not phrase:
            await interaction.followup.send("🤔 Не удалось сгенерировать. Попробуй ещё раз.")
            return

        emb = discord.Embed(description=f'*"{phrase}"*', color=color)
        if avatar_url:
            emb.set_author(name=f"{display_name} (пародия)", icon_url=avatar_url)
        else:
            emb.set_author(name=f"{display_name} (пародия)")
        emb.set_footer(text=f"{footer} · Оцени фразу!")
        await interaction.followup.send(embed=emb, view=RatingView(target_id, модель, phrase))

    # ── /батл ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="батл", description="Батл фраз между двумя пользователями — 7 раундов")
    @app_commands.describe(
        пользователь1="Первый участник", пользователь2="Второй участник",
        ник_или_id1="Ник/ID первого (для ушедших)", ник_или_id2="Ник/ID второго (для ушедших)",
        модель="Модель генерации",
    )
    @app_commands.choices(модель=[
        app_commands.Choice(name="🎲 Мем",                    value="мем"),
        app_commands.Choice(name="🧠 Разум",                  value="разум"),
        app_commands.Choice(name="📊 Автор — TF-IDF стиль",   value="автор"),
        app_commands.Choice(name="🤖 Нейрослоп — ruGPT",      value="нейро"),
    ])
    async def батл(self, interaction: discord.Interaction,
                   пользователь1: discord.Member | None = None,
                   пользователь2: discord.Member | None = None,
                   ник_или_id1: str | None = None,
                   ник_или_id2: str | None = None,
                   модель: str = DEFAULT_MODEL):

        def rp(m, n):
            if m: return m.id, m.display_name
            if n: return resolve_user(interaction.guild, n)
            return None, None

        id1, name1 = rp(пользователь1, ник_или_id1)
        id2, name2 = rp(пользователь2, ник_или_id2)

        if not id1 or not id2:
            await interaction.response.send_message("❌ Укажи двух участников.", ephemeral=True)
            return
        if id1 == id2:
            await interaction.response.send_message("❌ Нельзя батл с самим собой.", ephemeral=True)
            return

        for uid, name in [(id1, name1), (id2, name2)]:
            if not model_exists(uid, модель):
                msgs = get_user_messages(uid)
                if len(msgs) < 50:
                    await interaction.response.send_message(f"😕 Мало данных для **{name}**: {len(msgs)} сообщ.", ephemeral=True)
                    return

        await interaction.response.defer(thinking=True)

        for uid, name in [(id1, name1), (id2, name2)]:
            if not model_exists(uid, модель):
                train_user(uid, get_user_messages(uid), модель)

        q = QUALITY_LEVELS[модель]
        rounds_text = ""
        context_word = None
        for i in range(7):
            uid, name = (id1, name1) if i % 2 == 0 else (id2, name2)
            if модель in ("автор", "нейро"):
                from fun_slesh.parody_gpt import generate_author_phrase, generate_neuro_phrase, GPT_OK, gpt_model_exists
                phrase = generate_author_phrase(uid) if модель == "автор" else                          ((generate_neuro_phrase(uid) if GPT_OK and gpt_model_exists(uid) else None) or generate_phrase(uid, "разум"))
            else:
                phrase = generate_phrase(uid, модель, context_word=context_word) or generate_phrase(uid, модель)
            phrase = _strip_roles(phrase or "...")
            words = [w for w in phrase.split() if len(w) > 3]
            context_word = random.choice(words) if words else None
            prefix = "⚔️" if i % 2 == 0 else "🛡️"
            rounds_text += f"{prefix} **{name}:** {phrase}\n\n"

        emb = discord.Embed(title=f"⚔️ БАТЛ: {name1} vs {name2}", description=rounds_text, color=discord.Color.gold())
        emb.set_footer(text=f"{q['emoji']} {модель.capitalize()} · 7 раундов · Оцени батл!")
        await interaction.followup.send(embed=emb, view=RatingView(id1, модель, rounds_text[:500]))

    # ── /коллаж ───────────────────────────────────────────────────────────────
    @app_commands.command(name="коллаж", description="Смешать стиль двух пользователей в одну фразу")
    @app_commands.describe(
        пользователь1="Первый", пользователь2="Второй",
        ник_или_id1="Ник/ID первого", ник_или_id2="Ник/ID второго",
        модель="Модель генерации",
    )
    @app_commands.choices(модель=[
        app_commands.Choice(name="🎲 Мем",                    value="мем"),
        app_commands.Choice(name="🧠 Разум",                  value="разум"),
        app_commands.Choice(name="📊 Автор — TF-IDF стиль",   value="автор"),
        app_commands.Choice(name="🤖 Нейрослоп — ruGPT",      value="нейро"),
    ])
    async def коллаж(self, interaction: discord.Interaction,
                     пользователь1: discord.Member | None = None,
                     пользователь2: discord.Member | None = None,
                     ник_или_id1: str | None = None,
                     ник_или_id2: str | None = None,
                     модель: str = DEFAULT_MODEL):

        def rp(m, n):
            if m: return m.id, m.display_name
            if n: return resolve_user(interaction.guild, n)
            return None, None

        id1, name1 = rp(пользователь1, ник_или_id1)
        id2, name2 = rp(пользователь2, ник_или_id2)

        if not id1 or not id2:
            await interaction.response.send_message("❌ Укажи двух пользователей.", ephemeral=True)
            return
        if id1 == id2:
            await interaction.response.send_message("❌ Нельзя смешать с самим собой.", ephemeral=True)
            return

        for uid, name in [(id1, name1), (id2, name2)]:
            if not model_exists(uid, модель):
                msgs = get_user_messages(uid)
                if len(msgs) < 50:
                    await interaction.response.send_message(f"😕 Мало данных для **{name}**: {len(msgs)} сообщ.", ephemeral=True)
                    return

        await interaction.response.defer(thinking=True)

        for uid, name in [(id1, name1), (id2, name2)]:
            if not model_exists(uid, модель):
                train_user(uid, get_user_messages(uid), модель)

        if модель in ("автор", "нейро"):
            from fun_slesh.parody_gpt import generate_author_phrase, generate_neuro_phrase, GPT_OK, gpt_model_exists
            p1 = generate_author_phrase(id1) if модель == "автор" else                  ((generate_neuro_phrase(id1) if GPT_OK and gpt_model_exists(id1) else None) or generate_phrase(id1, "разум"))
            p2 = generate_author_phrase(id2) if модель == "автор" else                  ((generate_neuro_phrase(id2) if GPT_OK and gpt_model_exists(id2) else None) or generate_phrase(id2, "разум"))
            w1 = (p1 or "").split(); w2 = (p2 or "").split()
            phrase = " ".join(w1[:len(w1)//2+1] + w2[len(w2)//2:]) if w1 and w2 else (p1 or p2)
        else:
            phrase = generate_collage(id1, id2, модель)
        if not phrase:
            await interaction.followup.send("🤔 Не удалось. Попробуй ещё раз.")
            return

        q = QUALITY_LEVELS[модель]
        emb = discord.Embed(description=f'🔀 *"{phrase}"*', color=discord.Color.blurple())
        emb.set_author(name=f"{name1} × {name2} (коллаж)")
        emb.set_footer(text=f"{q['emoji']} {модель.capitalize()} · смешано 50/50")
        await interaction.followup.send(embed=emb, view=RatingView(id1, модель, phrase))

    # ── /эпоха ────────────────────────────────────────────────────────────────
    @app_commands.command(name="эпоха", description="Фраза пользователя из конкретного года или промежутка")
    @app_commands.describe(
        год="Год начала (например 2020)",
        год_до="Год конца промежутка (например 2022, необязательно)",
        пользователь="Участник", ник_или_id="Ник/ID (для ушедших)",
        модель="Модель генерации",
    )
    @app_commands.choices(модель=[
        app_commands.Choice(name="🎲 Мем",                    value="мем"),
        app_commands.Choice(name="🧠 Разум",                  value="разум"),
        app_commands.Choice(name="📊 Автор — TF-IDF стиль",   value="автор"),
        app_commands.Choice(name="🤖 Нейрослоп — ruGPT",      value="нейро"),
    ])
    async def эпоха(self, interaction: discord.Interaction,
                    год: int,
                    год_до: int | None = None,
                    пользователь: discord.Member | None = None,
                    ник_или_id: str | None = None,
                    модель: str = DEFAULT_MODEL):
        target_id, display_name = None, None
        if пользователь:
            target_id, display_name = пользователь.id, пользователь.display_name
        elif ник_или_id:
            target_id, display_name = resolve_user(interaction.guild, ник_или_id)
        if not target_id:
            await interaction.response.send_message("😕 Пользователь не найден.", ephemeral=True)
            return

        available = get_available_years(target_id)
        if год not in available:
            years_str = ", ".join(str(y) for y in available) or "нет данных"
            await interaction.response.send_message(
                f"😕 Мало сообщений за **{год}** у **{display_name}**.\nДоступные года: {years_str}", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        # Промежуток дат
        if год_до and год_до > год:
            import sqlite3 as _sq3
            _DB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'datebase', 'messages.db'))
            with _sq3.connect(_DB) as _conn:
                _rows = _conn.execute(
                    "SELECT content FROM messages WHERE user_id=? AND CAST(strftime('%Y', timestamp) AS INT) BETWEEN ? AND ?",
                    (target_id, год, год_до)).fetchall()
            period_msgs = [r[0] for r in _rows]
            period_label = f"{год}–{год_до}"
        else:
            period_msgs = get_user_messages_by_year(target_id, год)
            period_label = str(год)

        if not period_msgs:
            await interaction.followup.send(f"😕 Нет сообщений за {period_label} год(а).")
            return

        if модель in ("автор", "нейро"):
            from fun_slesh.parody_gpt import generate_author_phrase, generate_neuro_phrase, GPT_OK, gpt_model_exists
            phrase = generate_author_phrase(target_id) if модель == "автор" else                      ((generate_neuro_phrase(target_id) if GPT_OK and gpt_model_exists(target_id) else None) or                       generate_epoch(target_id, год, "разум"))
        else:
            phrase = generate_epoch(target_id, год, модель)

        if not phrase:
            await interaction.followup.send("🤔 Не удалось. Попробуй ещё раз.")
            return

        phrase = _strip_roles(phrase)
        q = QUALITY_LEVELS[модель]
        emb = discord.Embed(description=f'📅 *"{phrase}"*', color=discord.Color.gold())
        emb.set_author(name=f"{display_name} · {period_label} год (эпоха)")
        emb.set_footer(text=f"{q['emoji']} {модель.capitalize()} · сообщения {period_label} года")
        await interaction.followup.send(embed=emb, view=RatingView(target_id, модель, phrase))

    # ── /тема ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="тема", description="Фраза пользователя на конкретную тему")
    @app_commands.describe(
        ключевое_слово="Слово для фильтра",
        пользователь="Участник", ник_или_id="Ник/ID (для ушедших)",
        модель="Модель генерации",
    )
    @app_commands.choices(модель=[
        app_commands.Choice(name="🎲 Мем",                    value="мем"),
        app_commands.Choice(name="🧠 Разум",                  value="разум"),
        app_commands.Choice(name="📊 Автор — TF-IDF стиль",   value="автор"),
        app_commands.Choice(name="🤖 Нейрослоп — ruGPT",      value="нейро"),
    ])
    async def тема(self, interaction: discord.Interaction,
                   ключевое_слово: str,
                   пользователь: discord.Member | None = None,
                   ник_или_id: str | None = None,
                   модель: str = DEFAULT_MODEL):
        target_id, display_name = None, None
        if пользователь:
            target_id, display_name = пользователь.id, пользователь.display_name
        elif ник_или_id:
            target_id, display_name = resolve_user(interaction.guild, ник_или_id)
        if not target_id:
            await interaction.response.send_message("😕 Пользователь не найден.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        filtered = [m for m in get_user_messages(target_id) if ключевое_слово.lower() in m.lower()]
        if len(filtered) < 15:
            await interaction.followup.send(
                f"😕 Слово **«{ключевое_слово}»** встречается у **{display_name}** только {len(filtered)} раз (нужно ≥15).")
            return

        if модель in ("автор", "нейро"):
            from fun_slesh.parody_gpt import generate_author_phrase, generate_neuro_phrase, GPT_OK, gpt_model_exists
            phrase = generate_author_phrase(target_id) if модель == "автор" else                      ((generate_neuro_phrase(target_id) if GPT_OK and gpt_model_exists(target_id) else None) or                       generate_topic(target_id, ключевое_слово, "разум"))
        else:
            phrase = generate_topic(target_id, ключевое_слово, модель)
        if not phrase:
            await interaction.followup.send("🤔 Не удалось. Попробуй ещё раз.")
            return

        phrase = _strip_roles(phrase)
        q = QUALITY_LEVELS[модель]
        emb = discord.Embed(description=f'🎯 *"{phrase}"*', color=discord.Color.green())
        emb.set_author(name=f"{display_name} · тема «{ключевое_слово}»")
        emb.set_footer(text=f"{q['emoji']} {модель.capitalize()} · {len(filtered)} сообщений по теме")
        await interaction.followup.send(embed=emb, view=RatingView(target_id, модель, phrase))

    # ── /мем_фраза ────────────────────────────────────────────────────────────
    @app_commands.command(name="мем_фраза", description="Короткая фраза ЗАГЛАВНЫМИ для мема — выбирает самую смешную")
    @app_commands.describe(
        пользователь="Участник", ник_или_id="Ник/ID (для ушедших)",
        модель="Источник фраз",
    )
    @app_commands.choices(модель=[
        app_commands.Choice(name="🎲 Мем — абсурдный рандом",       value="мем"),
        app_commands.Choice(name="🧠 Разум — осознанная фраза",      value="разум"),
        app_commands.Choice(name="📊 Автор — TF-IDF стиль",          value="автор"),
        app_commands.Choice(name="🤖 Нейрослоп — ruGPT fine-tune",   value="нейро"),
    ])
    async def мем_фраза(self, interaction: discord.Interaction,
                        пользователь: discord.Member | None = None,
                        ник_или_id: str | None = None,
                        модель: str = "мем"):
        target_id, display_name, avatar_url = None, None, None
        if пользователь:
            target_id, display_name, avatar_url = пользователь.id, пользователь.display_name, пользователь.display_avatar.url
        elif ник_или_id:
            target_id, display_name = resolve_user(interaction.guild, ник_или_id)
        if not target_id:
            await interaction.response.send_message("😕 Пользователь не найден.", ephemeral=True)
            return

        msgs = get_user_messages(target_id)
        if len(msgs) < 50:
            await interaction.response.send_message(f"😕 Мало данных: {len(msgs)} сообщ.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        _LOL_RE = _re.compile(r'(хах|ахах|лол|кек|лмао|хех|ору|ахаха|lmao|lol|xd|😂|🤣|💀)', _re.I)

        def _meme_score(text: str) -> float:
            words = text.split()
            length_score = max(0.0, 1.0 - len(words) / 15)
            lol_score    = len(_LOL_RE.findall(text)) * 0.3
            caps_score   = 0.2 if any(w.isupper() and len(w) > 2 for w in words) else 0
            punct_score  = 0.1 * text.count('!')
            return length_score + lol_score + caps_score + punct_score

        candidates = []
        if модель in ("автор", "нейро"):
            from fun_slesh.parody_gpt import generate_author_phrase, generate_neuro_phrase, GPT_OK, gpt_model_exists
            for _ in range(8):
                p = generate_author_phrase(target_id) if модель == "автор" else                     (generate_neuro_phrase(target_id) if GPT_OK and gpt_model_exists(target_id) else None)
                if p: candidates.append(p)
        else:
            if not model_exists(target_id, модель):
                train_user(target_id, msgs, модель)
            mk = _load_model(target_id, модель)
            if mk:
                for _ in range(12):
                    p = mk.make_short_sentence(max_chars=100, tries=30)
                    if p: candidates.append(p)
            if not candidates:
                p = generate_phrase(target_id, модель)
                if p: candidates.append(p)

        if not candidates:
            await interaction.followup.send("🤔 Не удалось. Попробуй ещё раз.")
            return

        phrase = _strip_roles(max(candidates, key=_meme_score))
        q = QUALITY_LEVELS.get(модель, QUALITY_LEVELS["мем"])
        emb = discord.Embed(description=f"🤣 **{phrase.upper()}**", color=discord.Color.yellow())
        if avatar_url:
            emb.set_author(name=f"Мем: {display_name}", icon_url=avatar_url)
        else:
            emb.set_author(name=f"Мем: {display_name}")
        emb.set_footer(text=f"{q['emoji']} {модель.capitalize()} · лучшая из {len(candidates)} фраз · Оцени!")
        await interaction.followup.send(embed=emb, view=RatingView(target_id, модель, phrase))

    # ── /профиль_стиля ────────────────────────────────────────────────────────
    @app_commands.command(name="профиль_стиля", description="Паспорт стиля речи пользователя")
    @app_commands.describe(пользователь="Участник", ник_или_id="Ник/ID (для ушедших)")
    async def профиль_стиля(self, interaction: discord.Interaction,
                             пользователь: discord.Member | None = None,
                             ник_или_id: str | None = None):
        if not PERSONA_OK:
            await interaction.response.send_message("❌ Persona недоступна.", ephemeral=True)
            return

        target_id, display_name, avatar_url = None, None, None
        if пользователь:
            target_id, display_name, avatar_url = пользователь.id, пользователь.display_name, пользователь.display_avatar.url
        elif ник_или_id:
            target_id, display_name = resolve_user(interaction.guild, ник_или_id)
        if not target_id:
            await interaction.response.send_message("😕 Пользователь не найден.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        if not persona_exists(target_id):
            msgs = get_user_messages(target_id)
            if len(msgs) < 50:
                await interaction.followup.send(f"😕 Мало данных: {len(msgs)} сообщ.")
                return
            profile = build_persona(target_id, msgs)
            save_persona(target_id, get_user_stats(target_id).get("username", str(target_id)), profile, len(msgs))

        persona = load_persona(target_id)
        if not persona:
            await interaction.followup.send("❌ Не удалось построить профиль.")
            return

        em = persona.get("emotional", {})
        char_words = persona.get("char_words", [])[:15]
        top_bigrams = persona.get("top_bigrams", [])[:8]
        bigrams_str = " · ".join(f"{b[0]} {b[1]}" for b in top_bigrams if len(b) == 2)

        def bar(val, max_val=50):
            filled = min(int(val / max_val * 10), 10)
            return "█" * filled + "░" * (10 - filled)

        emb = discord.Embed(title=f"🎭 Паспорт стиля: {display_name}", color=discord.Color.og_blurple())
        if avatar_url:
            emb.set_thumbnail(url=avatar_url)
        emb.add_field(name="📏 Длина сообщений",
            value=f"Среднее: **{persona.get('avg_msg_len', 0):.1f}** слов\n"
                  f"Короткие (≤3): **{persona.get('short_pct', 0):.0f}%**\n"
                  f"Длинные (≥15): **{persona.get('long_pct', 0):.0f}%**", inline=True)
        emb.add_field(name="😤 Эмоции",
            value=f"Мат: {bar(em.get('мат_на_100', 0))} {em.get('мат_на_100', 0):.0f}/100\n"
                  f"Юмор: {bar(em.get('юмор_на_100', 0))} {em.get('юмор_на_100', 0):.0f}/100\n"
                  f"❓{em.get('вопросы_пct', 0):.0f}% · ❗{em.get('восклиц_pct', 0):.0f}%", inline=True)
        emb.add_field(name="🔤 Характерные слова",
            value=" · ".join(f"`{w}`" for w in char_words) or "—", inline=False)
        emb.add_field(name="🔗 Любимые словосочетания", value=bigrams_str or "—", inline=False)
        emb.add_field(name="📚 Словарный запас",
            value=f"**{persona.get('vocab_size', 0):,}** уникальных слов", inline=True)
        emb.add_field(name="🤖 Нейро-модель",
            value="✅ Обучена" if (GPT_OK and gpt_model_exists(target_id)) else "⬜ Не обучена", inline=True)

        # Сырые данные из БД (было в /стиль_статистика)
        stats = get_user_stats(target_id)
        msg_count = stats.get("count", 0)
        first_msg = (stats.get("first") or "")[:10]
        last_msg  = (stats.get("last")  or "")[:10]
        mk_ready  = all(_model_path(target_id, q) and __import__("os").path.exists(_model_path(target_id, q))
                        for q in ["мем", "разум"])
        status_parts = []
        if mk_ready:        status_parts.append("🎲 Markovify ✅")
        else:               status_parts.append("🎲 Markovify ⬜")
        if persona_exists(target_id): status_parts.append("📊 Persona ✅")
        else:               status_parts.append("📊 Persona ⬜")
        if GPT_OK and gpt_model_exists(target_id): status_parts.append("🤖 GPT ✅")
        else:               status_parts.append("🤖 GPT ⬜")

        emb.add_field(
            name="📦 База данных",
            value=f"Сообщений: **{msg_count:,}**"
                  + (f"\nПериод: {first_msg} → {last_msg}" if first_msg else "")
                  + (f"\nГотовность: {'✅ Достаточно' if msg_count >= 200 else f'⚠️ Мало ({msg_count}/200)'}"),
            inline=False
        )
        emb.add_field(name="⚙️ Модели", value="  ".join(status_parts), inline=False)
        emb.set_footer(text=f"Профиль по {persona.get('msg_count', 0):,} сообщениям · /пародия чтобы попробовать")
        await interaction.followup.send(embed=emb)

    # ── /дообучить ────────────────────────────────────────────────────────────
    @app_commands.command(name="дообучить", description="(Админ) Обучить модели: markovify + persona + GPT")
    @app_commands.describe(
        пользователь="Конкретный пользователь (или все если не указан)",
        модели="Какие модели обучить (по умолчанию — все)",
        только_markovify="Быстрый режим: только markovify, без Persona и GPT",
    )
    @app_commands.choices(модели=[
        app_commands.Choice(name="🔄 Все (Markovify + Persona + GPT)",  value="все"),
        app_commands.Choice(name="🎲🧠 Только Markovify",               value="markovify"),
        app_commands.Choice(name="📊 Только Persona",                   value="persona"),
        app_commands.Choice(name="🤖 Только GPT fine-tune",             value="gpt"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def дообучить(self, interaction: discord.Interaction,
                        пользователь: discord.Member | None = None,
                        модели: str = "все",
                        только_markovify: bool = False):
        # только_markovify — обратная совместимость
        if только_markovify:
            модели = "markovify"
        await interaction.response.defer(thinking=True)
        _prevent_sleep()  # ПК не спит пока идёт обучение

        if пользователь:
            msgs = get_user_messages(пользователь.id)
            if len(msgs) < 50:
                await interaction.followup.send(f"❌ Мало данных: {len(msgs)} сообщ.")
                return
            status = await interaction.followup.send(f"⏳ Обучаю **{пользователь.display_name}**...", wait=True)
            mk_results = train_user_all_qualities(пользователь.id, msgs) if модели in ("все", "markovify") else {}
            lines = [f"🎲🧠 Markovify: {', '.join(q for q,v in mk_results.items() if v) or 'пропущено'}"]
            if PERSONA_OK and модели in ("все", "persona"):
                profile = build_persona(пользователь.id, msgs)
                save_persona(пользователь.id, пользователь.display_name, profile, len(msgs))
                lines.append("📊 Persona: обновлена")
            if GPT_OK and модели in ("все", "gpt") and len(msgs) >= 200:
                await status.edit(content=f"⏳ GPT fine-tune для **{пользователь.display_name}**...")
                ok = await fine_tune_user(пользователь.id, msgs, epochs=3)
                lines.append(f"🤖 GPT: {'✅' if ok else '❌'}")
            emb = discord.Embed(title=f"✅ {пользователь.display_name} — готово",
                description="\n".join(lines) + f"\n\nСообщений: **{len(msgs)}**",
                color=discord.Color.green())
            _allow_sleep()
            await _safe_send(interaction, status, emb, interaction.channel)
        else:
            uids = get_all_user_ids()
            status = await interaction.followup.send(
                f"⏳ Обучаю **{len(uids)}** пользователей...\n"
                f"{'Только Markovify' if модели == 'markovify' else ('Только Persona' if модели == 'persona' else ('Только GPT' if модели == 'gpt' else 'Markovify → Persona → GPT'))}", wait=True)

            prevent_sleep()
            mk_results = await train_all_users_async(min_messages=50) if модели in ("все", "markovify") else {}
            persona_count, gpt_count = 0, 0

            if PERSONA_OK and модели in ("все", "persona"):
                try:
                    await status.edit(content=f"⏳ Persona-профили ({len(mk_results)} польз.)...")
                except Exception:
                    pass
                loop = asyncio.get_event_loop()
                personas = await loop.run_in_executor(_MK_EXECUTOR,
                    lambda: build_all_personas(min_messages=50))
                persona_count = len(personas)

            if GPT_OK and модели in ("все", "gpt"):
                loop = asyncio.get_event_loop()
                gpt_pairs = await loop.run_in_executor(_MK_EXECUTOR,
                    lambda: [(uid, get_user_messages(uid)) for uid in get_all_user_ids()
                             if len(get_user_messages(uid)) >= 200])
                for i, (uid, msgs_u) in enumerate(gpt_pairs, 1):
                    stats_u = get_user_stats(uid)
                    try:
                        await status.edit(content=f"⏳ GPT {i}/{len(gpt_pairs)}: {stats_u.get('username', uid)}...")
                    except Exception:
                        pass
                    if await fine_tune_user(uid, msgs_u, epochs=3):
                        gpt_count += 1

            emb = discord.Embed(
                title="🧠 Дообучение завершено",
                description=(
                    f"🎲🧠 Markovify: **{len(mk_results)}** пользователей\n"
                    f"📊 Persona: **{persona_count}** профилей\n"
                    f"🤖 GPT: **{gpt_count}** моделей\n\n"
                    f"Следующее авто-обучение: **воскресенье 03:00 МСК**"
                ),
                color=discord.Color.green()
            )
            allow_sleep()
            _allow_sleep()
            await _safe_send(interaction, status, emb, interaction.channel)

    # ── /профилактика ─────────────────────────────────────────────────────────
    @app_commands.command(name="профилактика",
        description="(Админ) Полный сброс и переобучение: чекпоинты → сбор → дообучить всё")
    @app_commands.checks.has_permissions(administrator=True)
    async def профилактика(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        # Блокируем сон Windows на время профилактики
        prevent_sleep()

        # Шаг 1: сброс чекпоинтов
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM collect_checkpoints")
            deleted = cur.rowcount
            conn.commit()

        status = await interaction.followup.send(
            embed=discord.Embed(
                title="🔧 Профилактика запущена",
                description=(
                    f"**Шаг 1/4:** ✅ Сброшено {deleted} чекпоинтов\n"
                    f"**Шаг 2/4:** ⏳ Сбор сообщений со всех каналов...\n"
                    f"**Шаг 3/4:** ⬜ Markovify + Persona\n"
                    f"**Шаг 4/4:** ⬜ GPT fine-tune\n\n"
                    f"*Можешь идти отдыхать — бот всё сделает сам*"
                ),
                color=discord.Color.orange()
            ),
            wait=True
        )

        # Шаг 2: сбор сообщений
        guild = interaction.guild
        channels = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).read_message_history]
        total_collected = 0
        for ch in channels:
            total_collected += await collect_channel(ch, guild.id)

        try:
            await status.edit(embed=discord.Embed(
                title="🔧 Профилактика — шаг 3/4",
                description=(
                    f"**Шаг 1/4:** ✅ Сброшено {deleted} чекпоинтов\n"
                    f"**Шаг 2/4:** ✅ Собрано +{total_collected} сообщений\n"
                    f"**Шаг 3/4:** ⏳ Обучение Markovify + Persona...\n"
                    f"**Шаг 4/4:** ⬜ GPT fine-tune\n"
                ),
                color=discord.Color.orange()
            ))
        except Exception:
            pass

        # Шаг 3: markovify + persona
        mk_results = await train_all_users_async(min_messages=50)
        persona_count = 0
        if PERSONA_OK:
            loop = asyncio.get_event_loop()
            personas = await loop.run_in_executor(_MK_EXECUTOR,
                lambda: build_all_personas(min_messages=50))
            persona_count = len(personas)

        try:
            await status.edit(embed=discord.Embed(
                title="🔧 Профилактика — шаг 4/4",
                description=(
                    f"**Шаг 1/4:** ✅ Сброшено {deleted} чекпоинтов\n"
                    f"**Шаг 2/4:** ✅ Собрано +{total_collected} сообщений\n"
                    f"**Шаг 3/4:** ✅ Markovify: {len(mk_results)} | Persona: {persona_count}\n"
                    f"**Шаг 4/4:** ⏳ GPT fine-tune...\n"
                ),
                color=discord.Color.orange()
            ))
        except Exception:
            pass

        # Шаг 4: GPT
        gpt_count = 0
        if GPT_OK:
            loop = asyncio.get_event_loop()
            gpt_pairs = await loop.run_in_executor(_MK_EXECUTOR,
                lambda: [(uid, get_user_messages(uid)) for uid in get_all_user_ids()
                         if len(get_user_messages(uid)) >= 200])
            for uid, msgs_u in gpt_pairs:
                if await fine_tune_user(uid, msgs_u, epochs=3):
                    gpt_count += 1

        # Финал
        uids = get_all_user_ids()
        final_emb = discord.Embed(
            title="✅ Профилактика завершена",
            description=(
                f"**Шаг 1/4:** ✅ Сброшено **{deleted}** чекпоинтов\n"
                f"**Шаг 2/4:** ✅ Собрано **+{total_collected}** сообщений\n"
                f"**Шаг 3/4:** ✅ Markovify: **{len(mk_results)}** | Persona: **{persona_count}**\n"
                f"**Шаг 4/4:** ✅ GPT: **{gpt_count}** моделей\n\n"
                f"Пользователей в базе: **{len(uids)}**\n"
                f"Следующая авто-профилактика: **воскресенье 03:00 МСК**"
            ),
            color=discord.Color.green()
        )
        # Снимаем блокировку сна
        allow_sleep()

        _allow_sleep()  # профилактика завершена — можно спать
        await _safe_send(interaction, status, final_emb, interaction.channel)

    # ── /список_пользователей ─────────────────────────────────────────────────
    @app_commands.command(name="список_пользователей", description="Все пользователи в базе пародии")
    async def список_пользователей(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uids = get_all_user_ids()
        if not uids:
            await interaction.followup.send("😶 База пуста.", ephemeral=True)
            return

        users_data = sorted(
            [(uid, (s := get_user_stats(uid))["username"] or str(uid), s["count"]) for uid in uids],
            key=lambda x: x[2], reverse=True
        )
        PAGE = 20
        total_pages = (len(users_data) + PAGE - 1) // PAGE
        current_page = 0

        def make_embed(page: int) -> discord.Embed:
            rows = []
            for uid, username, count in users_data[page * PAGE:(page + 1) * PAGE]:
                if GPT_OK and gpt_model_exists(uid):      badge = "🤖"
                elif model_exists(uid, "разум"):           badge = "🧠"
                elif model_exists(uid, "мем"):             badge = "🎲"
                else:                                      badge = "⬜"
                on_server = "" if interaction.guild.get_member(uid) else " *(ушёл)*"
                rows.append(f"{badge} **{username}**{on_server} — {count:,} сообщ.")
            emb = discord.Embed(
                title=f"👥 Пользователи в базе ({len(users_data)} чел.)",
                description="\n".join(rows), color=discord.Color.blurple()
            )
            emb.add_field(
                name="Команды",
                value="`/пародия` · `/батл` · `/коллаж` · `/эпоха` · `/тема`\nДля ушедших: `ник_или_id:ник`",
                inline=False
            )
            emb.set_footer(text=f"Стр. {page+1}/{total_pages} | 🤖нейро 🧠разум 🎲мем ⬜нет модели")
            return emb

        def make_view(page: int) -> discord.ui.View:
            view = discord.ui.View(timeout=120)
            prev = discord.ui.Button(label="◀ Назад",  style=discord.ButtonStyle.secondary, disabled=page == 0)
            nxt  = discord.ui.Button(label="Вперёд ▶", style=discord.ButtonStyle.secondary, disabled=page >= total_pages - 1)
            async def prev_cb(bi: discord.Interaction):
                nonlocal current_page
                current_page -= 1
                await bi.response.edit_message(embed=make_embed(current_page), view=make_view(current_page))
            async def next_cb(bi: discord.Interaction):
                nonlocal current_page
                current_page += 1
                await bi.response.edit_message(embed=make_embed(current_page), view=make_view(current_page))
            prev.callback = prev_cb
            nxt.callback  = next_cb
            view.add_item(prev)
            view.add_item(nxt)
            return view

        await interaction.followup.send(embed=make_embed(0), view=make_view(0), ephemeral=True)

    # ── /модели_статус ────────────────────────────────────────────────────────
    @app_commands.command(name="модели_статус", description="(Админ) Статус обученных моделей")
    @app_commands.checks.has_permissions(administrator=True)
    async def модели_статус(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uids = get_all_user_ids()
        if not uids:
            await interaction.followup.send("😶 База пуста.", ephemeral=True)
            return
        lines = []
        for uid in uids:
            stats = get_user_stats(uid)
            mk = "".join(QUALITY_LEVELS[q]["emoji"] if model_exists(uid, q) else "·" for q in ["мем","разум"])
            gpt = "🤖" if (GPT_OK and gpt_model_exists(uid)) else "·"
            good = len(get_good_phrases(uid, "разум"))
            bad  = len(get_bad_phrases(uid, "разум"))
            rating_str = f" 👍{good}/👎{bad}" if good or bad else ""
            lines.append(f"`{mk}{gpt}` **{stats['username']}** — {stats['count']:,} сообщ.{rating_str}")
        lines.sort()
        emb = discord.Embed(
            title="🤖 Статус моделей",
            description="\n".join(lines[:25]),
            color=discord.Color.blurple()
        )
        emb.set_footer(text=f"🎲мем 🧠разум 🤖нейро | · = не обучена | Всего: {len(uids)}")
        await interaction.followup.send(embed=emb, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ParodyEngine(bot))
