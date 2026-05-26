# -*- coding: utf-8 -*-
"""
General Discord activity tracker.

Tracks rich presence activities, stores finished sessions, and can post short
game haiku without real user mentions.
"""

from __future__ import annotations

import asyncio
import html
import os
import random
import re
import sqlite3
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))
UTC = timezone.utc
MSK = ZoneInfo("Europe/Moscow")
WIKI_HEADERS = {"User-Agent": "ViPikBot/1.0 (Discord bot; private server)"}
SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ViPikBot/1.0; Discord private server)",
    "Accept-Language": "ru,en;q=0.8",
}

TRACKED_TYPES = {
    discord.ActivityType.playing: "game",
    discord.ActivityType.streaming: "streaming",
    discord.ActivityType.listening: "listening",
    discord.ActivityType.watching: "watching",
    discord.ActivityType.competing: "competing",
}

TYPE_LABELS = {
    "game": "игра",
    "streaming": "стрим",
    "listening": "слушает",
    "watching": "смотрит",
    "competing": "соревнование",
}

GENERIC_OPENINGS = [
    "Ночь у монитора.",
    "Свет дрожит на клавишах.",
    "Пиксели проснулись.",
    "Тихий старт клиента.",
    "Экран набирает дыхание.",
    "Вечер стал загрузкой.",
    "Курсор режет сумрак.",
    "Сервер ловит искру.",
    "Лаунчер щёлкнул тихо.",
    "В окне растёт другой мир.",
]

GENERIC_ACTIONS = [
    "{display_name} входит в {game}.",
    "{display_name} открывает {game}.",
    "{display_name} выбирает путь.",
    "{display_name} снова в игре.",
    "{game} зовёт {display_name}.",
    "{display_name} ловит первый кадр.",
    "{display_name} нажал продолжить.",
    "{display_name} уходит за экран.",
]

GENERIC_ENDINGS = [
    "Чат на миг притих.",
    "Время пошло по кругу.",
    "Discord всё записал.",
    "Карта ждёт следов.",
    "Сейв ещё впереди.",
    "Шум кулеров как дождь.",
    "Миникарта светится.",
    "Ночь получила квест.",
    "Пати ищет голос.",
    "Тень легла на HUD.",
]

GENRE_STYLES = {
    "moba": {
        "needles": ("moba", "моба", "league of legends", "dota", "нексус", "линия", "чемпион"),
        "openings": ["Линия встала туманом.", "Вард горит в кустах.", "Миньоны идут строем."],
        "actions": ["{display_name} ловит тайминг.", "{display_name} держит линию.", "{game} зовёт к командной драке."],
        "endings": ["Карта мигает тревогой.", "Объект ждёт ошибки.", "Пинг летит через реку."],
    },
    "racing": {
        "needles": ("гонк", "racing", "race", "forza", "horizon", "машин", "авто", "трасс"),
        "openings": ["Асфальт блестит жарой.", "Мотор будит рассвет.", "Пыль летит за спойлером."],
        "actions": ["{display_name} давит газ.", "{display_name} ловит апекс.", "{game} зовёт на трассу."],
        "endings": ["Шины пишут дугу.", "Финиш пахнет бензином.", "Радар мигает вдали."],
    },
    "strategy": {
        "needles": ("стратег", "strategy", "пошаг", "тактик", "heroes of might", "homm", "цивилизац"),
        "openings": ["Карта проснулась в тумане.", "Ход считает клетки.", "Ресурсы звенят под луной."],
        "actions": ["{display_name} строит план.", "{display_name} ведёт отряд.", "{game} зовёт к долгому ходу."],
        "endings": ["Флаг ждёт приказа.", "Очередь хода темнеет.", "Разведчик исчез за лесом."],
    },
    "rpg": {
        "needles": ("rpg", "ролевая", "jrpg", "action rpg", "персонаж", "сюжет", "квест"),
        "openings": ["Квест дрожит на карте.", "Инвентарь шуршит тихо.", "Город хранит побочный путь."],
        "actions": ["{display_name} выбирает судьбу.", "{display_name} входит в историю.", "{game} раскрывает журнал."],
        "endings": ["Диалог ждёт ответа.", "Сейв светится у двери.", "Лор ложится на ладонь."],
    },
    "shooter": {
        "needles": ("шутер", "shooter", "fps", "стрел", "оруж", "тактический"),
        "openings": ["Прицел режет дым.", "Шаги глохнут в коридоре.", "Раунд встаёт на паузу."],
        "actions": ["{display_name} проверяет угол.", "{display_name} держит прицел.", "{game} зовёт на точку."],
        "endings": ["Гильза стынет у стены.", "Радар молчит секунду.", "Пульс считает раунд."],
    },
    "anime": {
        "needles": ("аниме", "anime", "gacha", "гача", "оператор", "отряд", "персонажи"),
        "openings": ["Баннер мерцает в неоне.", "Отряд ждёт приказ.", "Арт сияет на экране."],
        "actions": ["{display_name} собирает команду.", "{display_name} листает судьбу.", "{game} зовёт новых героев."],
        "endings": ["Редкость шепчет из света.", "Скилл уходит в кулдаун.", "Меню пахнет надеждой."],
    },
    "horror": {
        "needles": ("хоррор", "horror", "ужас", "страх", "выживание"),
        "openings": ["Дверь скрипит без ветра.", "Фонарь дрожит в руке.", "Тень стоит за экраном."],
        "actions": ["{display_name} идёт на звук.", "{display_name} экономит патроны.", "{game} прячет дыхание."],
        "endings": ["Сейв далеко за спиной.", "Шорох гасит чат.", "Темнота считает шаги."],
    },
    "mmo": {
        "needles": ("mmo", "мморпг", "mmorpg", "онлайн", "рейд", "гильд", "массовая"),
        "openings": ["Город шумит никами.", "Рейд собирает голоса.", "Аукцион спит в углу."],
        "actions": ["{display_name} входит в мир.", "{display_name} ищет пати.", "{game} открывает сервер."],
        "endings": ["Чат торговли мерцает.", "Босс ждёт отката.", "Гильдия зовёт в ночь."],
    },
}


def _ensure_tables():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS activity_tracker_config (
                guild_id       INTEGER PRIMARY KEY,
                channel_id     INTEGER,
                enabled        INTEGER NOT NULL DEFAULT 1,
                notify_starts  INTEGER NOT NULL DEFAULT 1,
                notify_ends    INTEGER NOT NULL DEFAULT 0,
                article_lookup INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS activity_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                activity_name TEXT    NOT NULL,
                activity_type TEXT    NOT NULL,
                started_at    TEXT    NOT NULL,
                ended_at      TEXT    NOT NULL,
                seconds       INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activity_active_sessions (
                guild_id      INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                activity_name TEXT    NOT NULL,
                activity_type TEXT    NOT NULL,
                started_at    TEXT    NOT NULL,
                PRIMARY KEY (guild_id, user_id, activity_name, activity_type)
            );

            CREATE TABLE IF NOT EXISTS activity_article_cache (
                activity_name TEXT NOT NULL,
                lang          TEXT NOT NULL,
                title         TEXT,
                extract       TEXT,
                url           TEXT,
                fetched_at    TEXT NOT NULL,
                PRIMARY KEY (activity_name, lang)
            );

            CREATE TABLE IF NOT EXISTS activity_game_profiles (
                activity_name TEXT PRIMARY KEY,
                title         TEXT,
                genre         TEXT,
                keywords      TEXT,
                source_text   TEXT,
                source_url    TEXT,
                fetched_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activity_notice_log (
                guild_id      INTEGER NOT NULL,
                user_id       INTEGER NOT NULL,
                activity_name TEXT    NOT NULL,
                posted_at     TEXT    NOT NULL,
                PRIMARY KEY (guild_id, user_id, activity_name)
            );

            CREATE TABLE IF NOT EXISTS activity_haiku_history (
                guild_id      INTEGER NOT NULL,
                activity_name TEXT    NOT NULL,
                haiku_text    TEXT    NOT NULL,
                created_at    TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_activity_sessions_guild_started
                ON activity_sessions(guild_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_activity_sessions_guild_user
                ON activity_sessions(guild_id, user_id);
            CREATE INDEX IF NOT EXISTS idx_activity_haiku_history_game
                ON activity_haiku_history(guild_id, activity_name, created_at);
            """
        )
        conn.commit()


def _normalize_name(name: str) -> str:
    return " ".join((name or "").strip().split())


def _activity_key(activity: discord.BaseActivity) -> tuple[str, str] | None:
    name = _normalize_name(getattr(activity, "name", "") or "")
    activity_type = TRACKED_TYPES.get(getattr(activity, "type", None))
    if not name or not activity_type:
        return None
    return name, activity_type


def _extract_activities(member: discord.Member) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for activity in member.activities or []:
        key = _activity_key(activity)
        if key:
            found.add(key)
    return found


def _fmt_seconds(sec: int) -> str:
    sec = max(0, int(sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    if h:
        return f"{h}ч {m}м"
    return f"{m}м"


def _member_name(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(int(user_id))
    return member.display_name if member else f"участник {user_id}"


def _infer_genre(text: str) -> str:
    normalized = text.lower()
    scores: dict[str, int] = {}
    for genre, style in GENRE_STYLES.items():
        score = sum(1 for needle in style["needles"] if needle in normalized)
        if score:
            scores[genre] = score
    if not scores:
        return "generic"
    return max(scores.items(), key=lambda item: item[1])[0]


def _style_for_profile(profile: dict | None) -> dict:
    merged = {
        "openings": list(GENERIC_OPENINGS),
        "actions": list(GENERIC_ACTIONS),
        "endings": list(GENERIC_ENDINGS),
    }
    genre = (profile or {}).get("genre")
    style = GENRE_STYLES.get(genre or "")
    if style:
        merged["openings"] = style["openings"] + merged["openings"]
        merged["actions"] = style["actions"] + merged["actions"]
        merged["endings"] = style["endings"] + merged["endings"]
    return merged


def _profile_words(profile: dict | None) -> list[str]:
    if not profile:
        return []
    raw_keywords = profile.get("keywords") or []
    if isinstance(raw_keywords, str):
        raw_keywords = [w.strip() for w in raw_keywords.split(",") if w.strip()]
    if raw_keywords:
        return list(dict.fromkeys(str(w).lower() for w in raw_keywords))[:5]

    text = " ".join(str(profile.get(key) or "") for key in ("title", "source_text"))
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9-]{3,}", text)
    blocked = {
        "игра", "игры", "video", "game", "games", "часть", "серия", "который", "которая",
        "после", "была", "были", "это", "для", "with", "from", "обзор", "прохождение",
    }
    result = []
    for word in words:
        clean = word.strip(".,:;!?()[]{}").lower()
        if clean in blocked or clean in result:
            continue
        result.append(clean)
        if len(result) >= 5:
            break
    return result


def _compose_fallback_haiku(game_name: str, display_name: str, profile: dict | None) -> str:
    rng = random.SystemRandom()
    style = _style_for_profile(profile)
    openings = list(style["openings"])
    actions = list(style["actions"])
    endings = list(style["endings"])

    words = _profile_words(profile)
    if words:
        openings.extend([
            f"{words[0].capitalize()} в тумане.",
            f"Поиск шепчет: {words[0]}.",
        ])
    if len(words) > 1:
        endings.extend([
            f"{words[1].capitalize()} ждёт в углу.",
            "Лор меняет дыхание.",
        ])

    return "\n".join(
        [
            rng.choice(openings),
            rng.choice(actions).format(game=game_name, display_name=display_name),
            rng.choice(endings),
        ]
    )


async def _fetch_wiki_article(game_name: str) -> dict | None:
    cache_key = game_name.lower()
    fresh_after = datetime.now(UTC) - timedelta(days=14)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT title, extract, url, fetched_at
            FROM activity_article_cache
            WHERE activity_name=? AND lang='ru'
            """,
            (cache_key,),
        ).fetchone()
        if row:
            try:
                fetched_at = datetime.fromisoformat(row[3])
            except Exception:
                fetched_at = datetime.min.replace(tzinfo=UTC)
            if fetched_at >= fresh_after:
                return {"title": row[0], "extract": row[1], "url": row[2], "lang": "ru"}

    async def search(lang: str, query: str) -> dict | None:
        base = f"https://{lang}.wikipedia.org/w/rest.php/v1"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=WIKI_HEADERS) as session:
            async with session.get(f"{base}/search/page", params={"q": query, "limit": 3}) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json()
            pages = payload.get("pages") or []
            if not pages:
                return None
            best = pages[0]
            key = best.get("key") or (best.get("title") or "").replace(" ", "_")
            if not key:
                return None
            safe_key = urllib.parse.quote(key, safe="")
            async with session.get(f"{base}/page/{safe_key}/summary") as resp:
                if resp.status != 200:
                    return None
                summary = await resp.json()
        title = summary.get("title") or key.replace("_", " ")
        extract = summary.get("extract") or ""
        url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(key, safe='')}"
        return {"title": title, "extract": extract[:900], "url": url, "lang": lang}

    article = None
    for lang, query in (("ru", f"{game_name} игра"), ("en", f"{game_name} video game")):
        try:
            article = await search(lang, query)
        except Exception:
            article = None
        if article:
            break

    if article:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO activity_article_cache(activity_name, lang, title, extract, url, fetched_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(activity_name, lang) DO UPDATE SET
                    title=excluded.title,
                    extract=excluded.extract,
                    url=excluded.url,
                    fetched_at=excluded.fetched_at
                """,
                (cache_key, "ru", article["title"], article["extract"], article["url"], datetime.now(UTC).isoformat()),
            )
            conn.commit()
    return article


def _clean_search_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


async def _fetch_ru_search_snippets(game_name: str) -> list[dict]:
    query = f"{game_name} игра обзор геймплей"
    url = "https://duckduckgo.com/html/"
    params = {"q": query, "kl": "ru-ru"}
    timeout = aiohttp.ClientTimeout(total=12)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=SEARCH_HEADERS) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text()
    except Exception:
        return []

    results = []
    blocks = re.findall(r'<div class="result__body">(.*?)</div>\s*</div>', text, flags=re.S)
    if not blocks:
        blocks = re.findall(r'<div class="result results_links.*?">(.*?)</div>\s*</div>', text, flags=re.S)
    for block in blocks[:5]:
        title_match = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>|class="result__snippet"[^>]*>(.*?)</div>', block, flags=re.S)
        if not title_match:
            continue
        raw_url = html.unescape(title_match.group(1))
        title = _clean_search_text(title_match.group(2))
        snippet_raw = ""
        if snippet_match:
            snippet_raw = snippet_match.group(1) or snippet_match.group(2) or ""
        snippet = _clean_search_text(snippet_raw)
        if title or snippet:
            results.append({"title": title, "snippet": snippet, "url": raw_url})
    return results


def _extract_keywords(text: str, limit: int = 8) -> list[str]:
    blocked = {
        "игра", "игры", "игру", "игре", "игрой", "обзор", "геймплей", "прохождение",
        "дата", "релиз", "трейлер", "скачать", "официальный", "official", "video",
        "game", "games", "для", "или", "это", "как", "что", "with", "from", "about",
        "steam", "страница", "сайт", "новости",
    }
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9-]{3,}", (text or "").lower())
    counts: dict[str, int] = {}
    for word in words:
        word = word.strip("-_")
        if word in blocked or len(word) < 4:
            continue
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ranked[:limit]]


def _profile_from_cache(game_name: str) -> dict | None:
    cache_key = game_name.lower()
    fresh_after = datetime.now(UTC) - timedelta(days=14)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT title, genre, keywords, source_text, source_url, fetched_at
            FROM activity_game_profiles
            WHERE activity_name=?
            """,
            (cache_key,),
        ).fetchone()
    if not row:
        return None
    try:
        fetched_at = datetime.fromisoformat(row[5])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
    except Exception:
        return None
    if fetched_at < fresh_after:
        return None
    keywords = [w.strip() for w in (row[2] or "").split(",") if w.strip()]
    return {
        "title": row[0] or game_name,
        "genre": row[1] or "generic",
        "keywords": keywords,
        "source_text": row[3] or "",
        "source_url": row[4],
    }


def _save_profile(game_name: str, profile: dict):
    cache_key = game_name.lower()
    keywords = profile.get("keywords") or []
    if not isinstance(keywords, str):
        keywords = ", ".join(str(word) for word in keywords[:8])
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO activity_game_profiles(activity_name, title, genre, keywords, source_text, source_url, fetched_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(activity_name) DO UPDATE SET
                title=excluded.title,
                genre=excluded.genre,
                keywords=excluded.keywords,
                source_text=excluded.source_text,
                source_url=excluded.source_url,
                fetched_at=excluded.fetched_at
            """,
            (
                cache_key,
                profile.get("title") or game_name,
                profile.get("genre") or "generic",
                keywords,
                (profile.get("source_text") or "")[:1600],
                profile.get("source_url"),
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()


async def _fetch_game_profile(game_name: str) -> dict:
    cached = _profile_from_cache(game_name)
    if cached:
        return cached

    article = await _fetch_wiki_article(game_name)
    snippets = await _fetch_ru_search_snippets(game_name)
    snippet_text = " ".join(
        f"{item.get('title', '')}. {item.get('snippet', '')}" for item in snippets
    )
    source_text = " ".join(
        part for part in [
            article.get("title", "") if article else "",
            article.get("extract", "") if article else "",
            snippet_text,
        ] if part
    )
    title = (article or {}).get("title") or (snippets[0]["title"] if snippets else game_name)
    source_url = (article or {}).get("url") or (snippets[0]["url"] if snippets else None)
    profile = {
        "title": title,
        "genre": _infer_genre(f"{game_name} {source_text}"),
        "keywords": _extract_keywords(source_text),
        "source_text": source_text[:1600],
        "source_url": source_url,
    }
    _save_profile(game_name, profile)
    return profile


def _call_gpt_haiku(prompt: str) -> str | None:
    try:
        import fun_slesh.parody_gpt as pgpt

        model, tokenizer = pgpt._load_model()
        if model is None:
            return None
        result = pgpt._generate(model, tokenizer, prompt, max_new_tokens=90)
        lines = [line.strip() for line in result.strip().splitlines() if line.strip()][:3]
        if len(lines) == 3:
            return "\n".join(lines)
    except Exception:
        return None
    return None


def _recent_haikus(guild_id: int, game_name: str, limit: int = 20) -> set[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT haiku_text FROM activity_haiku_history
            WHERE guild_id=? AND activity_name=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (guild_id, game_name, limit),
        ).fetchall()
    return {str(row[0]) for row in rows}


def _remember_haiku(guild_id: int, game_name: str, haiku: str):
    cutoff = (datetime.now(UTC) - timedelta(days=14)).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO activity_haiku_history(guild_id, activity_name, haiku_text, created_at)
            VALUES(?,?,?,?)
            """,
            (guild_id, game_name, haiku, datetime.now(UTC).isoformat()),
        )
        conn.execute("DELETE FROM activity_haiku_history WHERE created_at<?", (cutoff,))
        conn.commit()


async def _generate_game_haiku(guild_id: int, game_name: str, display_name: str, profile: dict | None) -> str:
    context = ""
    if profile and profile.get("source_text"):
        keywords = ", ".join(profile.get("keywords") or [])
        context = (
            f" Русскоязычный контекст/сниппеты: {profile.get('title') or game_name}; "
            f"жанр/тип: {profile.get('genre') or 'неизвестно'}; "
            f"ключевые слова: {keywords or 'нет'}; "
            f"описание: {profile['source_text'][:700]}"
        )
    prompt = (
        "Напиши короткое русское хокку в три строки о том, что участник Discord "
        f"{display_name} запустил игру {game_name}.{context} "
        "Адаптируй образы под конкретную игру, её жанр, сеттинг и лексику. "
        "Не используй шаблонные строки про пиксели, курсор и монитор, если контекст позволяет точнее. "
        "Без тегов, без пояснений, только три строки."
    )
    result = await asyncio.get_event_loop().run_in_executor(None, _call_gpt_haiku, prompt)
    if result:
        _remember_haiku(guild_id, game_name, result)
        return result

    recent = _recent_haikus(guild_id, game_name)
    haiku = ""
    for _ in range(16):
        candidate = _compose_fallback_haiku(game_name, display_name, profile)
        if candidate not in recent:
            haiku = candidate
            break
    if not haiku:
        haiku = _compose_fallback_haiku(game_name, display_name, profile)
    _remember_haiku(guild_id, game_name, haiku)
    return haiku


class ActivityTracker(commands.Cog):
    activity_group = app_commands.Group(
        name="активности",
        description="Трекинг Discord-активностей и игр",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active: dict[tuple[int, int, str, str], datetime] = {}
        _ensure_tables()
        self._load_active_sessions()

    def _load_active_sessions(self):
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT guild_id, user_id, activity_name, activity_type, started_at FROM activity_active_sessions"
            ).fetchall()
        for guild_id, user_id, name, activity_type, started_at in rows:
            try:
                started_dt = datetime.fromisoformat(started_at)
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=UTC)
            except Exception:
                started_dt = datetime.now(UTC)
            self._active[(int(guild_id), int(user_id), str(name), str(activity_type))] = started_dt

    @commands.Cog.listener()
    async def on_ready(self):
        asyncio.create_task(self._reconcile_active_sessions())

    def _remember(self, guild_id: int, user_id: int, name: str, activity_type: str):
        now = datetime.now(UTC)
        key = (guild_id, user_id, name, activity_type)
        self._active[key] = now
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO activity_active_sessions(guild_id, user_id, activity_name, activity_type, started_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(guild_id, user_id, activity_name, activity_type) DO UPDATE SET
                    started_at=excluded.started_at
                """,
                (guild_id, user_id, name, activity_type, now.isoformat()),
            )
            conn.commit()

    def _finish(self, guild_id: int, user_id: int, name: str, activity_type: str) -> int:
        key = (guild_id, user_id, name, activity_type)
        started_at = self._active.pop(key, None)
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT started_at FROM activity_active_sessions
                WHERE guild_id=? AND user_id=? AND activity_name=? AND activity_type=?
                """,
                (guild_id, user_id, name, activity_type),
            ).fetchone()
            conn.execute(
                """
                DELETE FROM activity_active_sessions
                WHERE guild_id=? AND user_id=? AND activity_name=? AND activity_type=?
                """,
                (guild_id, user_id, name, activity_type),
            )
            if started_at is None and row:
                try:
                    started_at = datetime.fromisoformat(row[0])
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=UTC)
                except Exception:
                    started_at = None
            if started_at is None:
                conn.commit()
                return 0
            ended_at = datetime.now(UTC)
            seconds = int((ended_at - started_at).total_seconds())
            if seconds > 0:
                conn.execute(
                    """
                    INSERT INTO activity_sessions(guild_id, user_id, activity_name, activity_type, started_at, ended_at, seconds)
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (guild_id, user_id, name, activity_type, started_at.isoformat(), ended_at.isoformat(), seconds),
                )
            conn.commit()
        return max(0, seconds)

    def _config(self, guild_id: int) -> dict:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR IGNORE INTO activity_tracker_config(guild_id) VALUES(?)", (guild_id,))
            row = conn.execute(
                """
                SELECT channel_id, enabled, notify_starts, notify_ends, article_lookup
                FROM activity_tracker_config WHERE guild_id=?
                """,
                (guild_id,),
            ).fetchone()
            conn.commit()
        return {
            "channel_id": row[0],
            "enabled": bool(row[1]),
            "notify_starts": bool(row[2]),
            "notify_ends": bool(row[3]),
            "article_lookup": bool(row[4]),
        }

    def _pick_channel(self, guild: discord.Guild, cfg: dict) -> discord.TextChannel | None:
        channel_id = cfg.get("channel_id")
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            return channel

        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT channel_id FROM daily_summary_config WHERE guild_id=? AND enabled=1 AND channel_id IS NOT NULL",
                (guild.id,),
            ).fetchone()
        channel = self.bot.get_channel(row[0]) if row else None
        if isinstance(channel, discord.TextChannel):
            return channel
        return guild.system_channel

    def _notice_recently_posted(self, guild_id: int, user_id: int, name: str, cooldown_minutes: int = 90) -> bool:
        fresh_after = datetime.now(UTC) - timedelta(minutes=cooldown_minutes)
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT posted_at FROM activity_notice_log
                WHERE guild_id=? AND user_id=? AND activity_name=?
                """,
                (guild_id, user_id, name),
            ).fetchone()
            if not row:
                return False
            try:
                posted_at = datetime.fromisoformat(row[0])
                if posted_at.tzinfo is None:
                    posted_at = posted_at.replace(tzinfo=UTC)
            except Exception:
                return False
        return posted_at >= fresh_after

    def _remember_notice(self, guild_id: int, user_id: int, name: str):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO activity_notice_log(guild_id, user_id, activity_name, posted_at)
                VALUES(?,?,?,?)
                ON CONFLICT(guild_id, user_id, activity_name) DO UPDATE SET
                    posted_at=excluded.posted_at
                """,
                (guild_id, user_id, name, datetime.now(UTC).isoformat()),
            )
            conn.commit()

    async def _send_start_notice(self, member: discord.Member, name: str, activity_type: str):
        cfg = self._config(member.guild.id)
        if not cfg["enabled"] or not cfg["notify_starts"] or activity_type != "game":
            return
        if self._notice_recently_posted(member.guild.id, member.id, name):
            return
        channel = self._pick_channel(member.guild, cfg)
        if channel is None:
            return
        profile = await _fetch_game_profile(name) if cfg["article_lookup"] else {"title": name, "genre": "generic", "keywords": []}
        haiku = await _generate_game_haiku(member.guild.id, name, member.display_name, profile)
        embed = discord.Embed(
            title=f"{member.display_name} запустил {name}",
            description=f"*{haiku}*",
            color=discord.Color.dark_teal(),
        )
        if profile and profile.get("source_url"):
            embed.add_field(
                name="Контекст",
                value=f"[{profile.get('title') or name}]({profile['source_url']}) · {profile.get('genre') or 'игра'}",
                inline=False,
            )
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            self._remember_notice(member.guild.id, member.id, name)
        except Exception:
            pass

    async def _send_end_notice(self, member: discord.Member, name: str, activity_type: str, seconds: int):
        cfg = self._config(member.guild.id)
        if not cfg["enabled"] or not cfg["notify_ends"]:
            return
        channel = self._pick_channel(member.guild, cfg)
        if channel is None:
            return
        label = TYPE_LABELS.get(activity_type, activity_type)
        try:
            await channel.send(
                f"{member.display_name} завершил {label} **{name}**: {_fmt_seconds(seconds)}.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass

    async def _reconcile_active_sessions(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                for name, activity_type in _extract_activities(member):
                    key = (guild.id, member.id, name, activity_type)
                    if key not in self._active:
                        self._remember(guild.id, member.id, name, activity_type)

        for guild_id, user_id, name, activity_type in list(self._active):
            guild = self.bot.get_guild(guild_id)
            member = guild.get_member(user_id) if guild else None
            if member is None:
                continue
            if (name, activity_type) not in _extract_activities(member):
                self._finish(guild_id, user_id, name, activity_type)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.bot or not after.guild:
            return
        before_items = _extract_activities(before)
        after_items = _extract_activities(after)

        for name, activity_type in sorted(before_items - after_items):
            seconds = self._finish(after.guild.id, after.id, name, activity_type)
            if seconds:
                await self._send_end_notice(after, name, activity_type, seconds)

        for name, activity_type in sorted(after_items - before_items):
            self._remember(after.guild.id, after.id, name, activity_type)
            await self._send_start_notice(after, name, activity_type)

    @activity_group.command(name="канал", description="(Админ) Канал для постов об игровых активностях")
    @app_commands.checks.has_permissions(administrator=True)
    async def активности_канал(self, interaction: discord.Interaction, канал: discord.TextChannel):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO activity_tracker_config(guild_id, channel_id, enabled)
                VALUES(?,?,1)
                ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, enabled=1
                """,
                (interaction.guild.id, канал.id),
            )
            conn.commit()
        await interaction.response.send_message(
            f"✅ Игровые хокку и топы активностей будут идти в {канал.mention}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @activity_group.command(name="вкл", description="(Админ) Включить или выключить трекинг активностей")
    @app_commands.checks.has_permissions(administrator=True)
    async def активности_вкл(self, interaction: discord.Interaction, включить: bool):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO activity_tracker_config(guild_id, enabled)
                VALUES(?,?)
                ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled
                """,
                (interaction.guild.id, int(включить)),
            )
            conn.commit()
        status = "✅ Включён" if включить else "⛔ Выключен"
        await interaction.response.send_message(f"{status} трекинг активностей.", ephemeral=True)

    @activity_group.command(name="посты", description="(Админ) Настроить посты о старте/конце активностей")
    @app_commands.checks.has_permissions(administrator=True)
    async def активности_посты(
        self,
        interaction: discord.Interaction,
        старты_игр: bool = True,
        окончания: bool = False,
        статьи: bool = True,
    ):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO activity_tracker_config(guild_id, notify_starts, notify_ends, article_lookup)
                VALUES(?,?,?,?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    notify_starts=excluded.notify_starts,
                    notify_ends=excluded.notify_ends,
                    article_lookup=excluded.article_lookup
                """,
                (interaction.guild.id, int(старты_игр), int(окончания), int(статьи)),
            )
            conn.commit()
        await interaction.response.send_message("✅ Настройки постов активностей обновлены.", ephemeral=True)

    @activity_group.command(name="топ", description="Топ активностей за N дней")
    async def активности_топ(self, interaction: discord.Interaction, дней: app_commands.Range[int, 1, 365] = 7):
        await interaction.response.defer()
        since = (datetime.now(UTC) - timedelta(days=int(дней))).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            top_games = conn.execute(
                """
                SELECT activity_name, SUM(seconds) AS total
                FROM activity_sessions
                WHERE guild_id=? AND started_at>=? AND activity_type='game'
                GROUP BY activity_name
                ORDER BY total DESC LIMIT 10
                """,
                (interaction.guild.id, since),
            ).fetchall()
            top_game_users = conn.execute(
                """
                SELECT user_id, SUM(seconds) AS total
                FROM activity_sessions
                WHERE guild_id=? AND started_at>=? AND activity_type='game'
                GROUP BY user_id
                ORDER BY total DESC LIMIT 10
                """,
                (interaction.guild.id, since),
            ).fetchall()
            other_activities = conn.execute(
                """
                SELECT activity_name, activity_type, SUM(seconds) AS total
                FROM activity_sessions
                WHERE guild_id=? AND started_at>=? AND activity_type<>'game'
                GROUP BY activity_name, activity_type
                ORDER BY total DESC LIMIT 10
                """,
                (interaction.guild.id, since),
            ).fetchall()
            top_all_users = conn.execute(
                """
                SELECT user_id, SUM(seconds) AS total
                FROM activity_sessions
                WHERE guild_id=? AND started_at>=?
                GROUP BY user_id
                ORDER BY total DESC LIMIT 10
                """,
                (interaction.guild.id, since),
            ).fetchall()

        if not top_games and not top_game_users and not other_activities and not top_all_users:
            await interaction.followup.send("📭 Пока нет завершённых активностей за выбранный период.")
            return

        def other_activity_line(i: int, row: tuple) -> str:
            name, activity_type, total = row
            label = TYPE_LABELS.get(activity_type, activity_type)
            return f"**{i}.** {name} ({label}) — **{_fmt_seconds(int(total))}**"

        game_lines = [
            f"**{i}.** {name} — **{_fmt_seconds(int(total))}**"
            for i, (name, total) in enumerate(top_games, start=1)
        ]
        game_user_lines = [
            f"**{i}.** {_member_name(interaction.guild, int(user_id))} — **{_fmt_seconds(int(total))}**"
            for i, (user_id, total) in enumerate(top_game_users, start=1)
        ]
        other_lines = [other_activity_line(i, row) for i, row in enumerate(other_activities, start=1)]
        all_user_lines = [
            f"**{i}.** {_member_name(interaction.guild, int(user_id))} — **{_fmt_seconds(int(total))}**"
            for i, (user_id, total) in enumerate(top_all_users, start=1)
        ]
        embed = discord.Embed(title=f"🎮 Активности за {дней} дн.", color=discord.Color.teal())
        if game_lines:
            embed.add_field(name="Топ игр", value="\n".join(game_lines), inline=False)
        if game_user_lines:
            embed.add_field(name="Топ игроков по играм", value="\n".join(game_user_lines), inline=False)
        if other_lines:
            embed.add_field(name="Другие активности", value="\n".join(other_lines), inline=False)
        if all_user_lines:
            embed.add_field(name="Все активности по участникам", value="\n".join(all_user_lines), inline=False)
        await interaction.followup.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityTracker(bot))
