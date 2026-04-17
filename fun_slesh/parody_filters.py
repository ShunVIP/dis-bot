# -*- coding: utf-8 -*-
# fun_slesh/parody_filters.py
"""
Управление фильтрами пародии через БД — без правки кода.

БД: datebase/parody_filters.db
Таблицы:
  - channel_ignore   : каналы, которые не собираются вообще
  - word_block       : слова, которые убираются из char_words/bigrams полностью
  - word_downrank    : слова, которым снижается приоритет (но не убираются)

Команды (все только для администраторов):
  /фильтр_канал_добавить    — добавить канал в стоп-лист
  /фильтр_канал_убрать      — убрать канал из стоп-листа
  /фильтр_слово_блок        — добавить слово в полный блок
  /фильтр_слово_понизить    — добавить слово в список понижения
  /фильтр_слово_убрать      — убрать слово из любого списка
  /фильтр_список            — показать все активные фильтры
"""

import os
import sqlite3
import math
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

FILTERS_DB = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "datebase", "parody_filters.db")
)
UTC = timezone.utc


# ─── Schema + helpers ─────────────────────────────────────────────────────────

def _ensure_db():
    os.makedirs(os.path.dirname(FILTERS_DB), exist_ok=True)
    with sqlite3.connect(FILTERS_DB) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS channel_ignore (
                channel_name TEXT PRIMARY KEY COLLATE NOCASE,
                added_by     INTEGER,
                added_at     TEXT
            );
            CREATE TABLE IF NOT EXISTS word_block (
                word         TEXT PRIMARY KEY COLLATE NOCASE,
                added_by     INTEGER,
                added_at     TEXT
            );
            CREATE TABLE IF NOT EXISTS word_downrank (
                word         TEXT PRIMARY KEY COLLATE NOCASE,
                strength     REAL NOT NULL DEFAULT 0.1,
                added_by     INTEGER,
                added_at     TEXT
            );
        """)
        conn.commit()


# ─── Public API — используется в collector и persona ──────────────────────────

def get_ignored_channels() -> set[str]:
    """Возвращает set имён каналов (lowercase) которые не надо собирать."""
    _ensure_db()
    with sqlite3.connect(FILTERS_DB) as conn:
        rows = conn.execute("SELECT channel_name FROM channel_ignore").fetchall()
    return {r[0].lower() for r in rows}


def get_blocked_words() -> set[str]:
    """Слова для полного исключения из char_words/bigrams."""
    _ensure_db()
    with sqlite3.connect(FILTERS_DB) as conn:
        rows = conn.execute("SELECT word FROM word_block").fetchall()
    return {r[0].lower() for r in rows}


def get_downranked_words() -> dict[str, float]:
    """Слова со сниженным приоритетом. {слово: strength}, strength ∈ (0, 1].
    strength=0.1 → слово получает 10% от исходного score.
    strength=0.5 → 50% (умеренное понижение).
    """
    _ensure_db()
    with sqlite3.connect(FILTERS_DB) as conn:
        rows = conn.execute("SELECT word, strength FROM word_downrank").fetchall()
    return {r[0].lower(): float(r[1]) for r in rows}


def apply_word_filters(
    word: str,
    base_score: float,
    blocked: set[str],
    downranked: dict[str, float],
) -> float:
    """Применяет фильтры к слову. Возвращает итоговый score (0 = убрать)."""
    w = word.lower()
    if w in blocked:
        return 0.0
    if w in downranked:
        return base_score * downranked[w]
    return base_score


# ─── Cog ──────────────────────────────────────────────────────────────────────

class ParodyFilters(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_db()

    # ── /фильтр_канал_добавить ────────────────────────────────────────────────
    @app_commands.command(
        name="фильтр_канал_добавить",
        description="(Админ) Исключить канал из сбора сообщений для пародии"
    )
    @app_commands.describe(
        канал="Канал который не нужно собирать",
        название="Или введи название вручную (для несуществующих каналов)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def фильтр_канал_добавить(
        self,
        interaction: discord.Interaction,
        канал: Optional[discord.TextChannel] = None,
        название: Optional[str] = None,
    ):
        name = (канал.name if канал else название or "").strip().lower()
        if not name:
            await interaction.response.send_message(
                "❌ Укажи канал или введи название.", ephemeral=True)
            return

        with sqlite3.connect(FILTERS_DB) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO channel_ignore(channel_name, added_by, added_at) VALUES (?,?,?)",
                (name, interaction.user.id, datetime.now(UTC).isoformat())
            )
            conn.commit()

        await interaction.response.send_message(
            f"✅ Канал `#{name}` добавлен в стоп-лист.\n"
            f"Новые сборы будут его пропускать. Уже собранные сообщения остаются в БД.",
            ephemeral=True
        )

    # ── /фильтр_канал_убрать ──────────────────────────────────────────────────
    @app_commands.command(
        name="фильтр_канал_убрать",
        description="(Админ) Убрать канал из стоп-листа — начать собирать из него снова"
    )
    @app_commands.describe(название="Название канала (без #)")
    @app_commands.checks.has_permissions(administrator=True)
    async def фильтр_канал_убрать(
        self,
        interaction: discord.Interaction,
        название: str,
    ):
        name = название.strip().lower()
        with sqlite3.connect(FILTERS_DB) as conn:
            cur = conn.execute("DELETE FROM channel_ignore WHERE channel_name = ?", (name,))
            conn.commit()
            deleted = cur.rowcount

        if deleted:
            await interaction.response.send_message(
                f"✅ Канал `#{name}` убран из стоп-листа. Следующий сбор его включит.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Канал `#{name}` не найден в стоп-листе.", ephemeral=True)

    # ── /фильтр_слово_блок ────────────────────────────────────────────────────
    @app_commands.command(
        name="фильтр_слово_блок",
        description="(Админ) Полностью убрать слово из профиля стиля и биграмм"
    )
    @app_commands.describe(слово="Слово для блокировки (регистр не важен)")
    @app_commands.checks.has_permissions(administrator=True)
    async def фильтр_слово_блок(
        self,
        interaction: discord.Interaction,
        слово: str,
    ):
        word = слово.strip().lower()
        if not word:
            await interaction.response.send_message("❌ Введи слово.", ephemeral=True)
            return

        with sqlite3.connect(FILTERS_DB) as conn:
            # Убираем из downrank если было там
            conn.execute("DELETE FROM word_downrank WHERE word = ?", (word,))
            conn.execute(
                "INSERT OR REPLACE INTO word_block(word, added_by, added_at) VALUES (?,?,?)",
                (word, interaction.user.id, datetime.now(UTC).isoformat())
            )
            conn.commit()

        await interaction.response.send_message(
            f"🚫 Слово `{word}` заблокировано — не будет появляться в характерных словах и биграммах.\n"
            f"Запусти `/дообучить модели:Только Persona` чтобы пересчитать профили.",
            ephemeral=True
        )

    # ── /фильтр_слово_понизить ────────────────────────────────────────────────
    @app_commands.command(
        name="фильтр_слово_понизить",
        description="(Админ) Снизить приоритет слова в профиле стиля (но не убирать)"
    )
    @app_commands.describe(
        слово="Слово для понижения",
        убрать_процентов="На сколько процентов снизить вес слова (50% = вдвое реже, 90% = почти убрать)"
    )
    @app_commands.choices(убрать_процентов=[
        app_commands.Choice(name="30% — слегка (слово останется, чуть реже)",        value=30),
        app_commands.Choice(name="50% — умеренно (вдвое реже)",                      value=50),
        app_commands.Choice(name="70% — заметно (в 3× реже)",                        value=70),
        app_commands.Choice(name="85% — сильно (почти не видно)",                    value=85),
        app_commands.Choice(name="95% — почти полностью (только след остаётся)",      value=95),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def фильтр_слово_понизить(
        self,
        interaction: discord.Interaction,
        слово: str,
        убрать_процентов: int = 70,
    ):
        word = слово.strip().lower()
        if not word:
            await interaction.response.send_message("❌ Введи слово.", ephemeral=True)
            return

        # Конвертируем: пользователь говорит "убрать 70%" → strength = 0.30
        strength = round(1.0 - убрать_процентов / 100, 2)

        with sqlite3.connect(FILTERS_DB) as conn:
            # Убираем из block если было там
            conn.execute("DELETE FROM word_block WHERE word = ?", (word,))
            conn.execute(
                "INSERT OR REPLACE INTO word_downrank(word, strength, added_by, added_at) VALUES (?,?,?,?)",
                (word, strength, interaction.user.id, datetime.now(UTC).isoformat())
            )
            conn.commit()

        remain = 100 - убрать_процентов
        await interaction.response.send_message(
            f"📉 Слово `{word}`: убрано **{убрать_процентов}%** веса, осталось **{remain}%**\n"
            f"Запусти `/дообучить модели:Только Persona` чтобы пересчитать профили.",
            ephemeral=True
        )

    # ── /фильтр_слово_убрать ──────────────────────────────────────────────────
    @app_commands.command(
        name="фильтр_слово_убрать",
        description="(Админ) Убрать слово из любого фильтра (блок или понижение)"
    )
    @app_commands.describe(слово="Слово для удаления из фильтров")
    @app_commands.checks.has_permissions(administrator=True)
    async def фильтр_слово_убрать(
        self,
        interaction: discord.Interaction,
        слово: str,
    ):
        word = слово.strip().lower()
        with sqlite3.connect(FILTERS_DB) as conn:
            d1 = conn.execute("DELETE FROM word_block WHERE word = ?", (word,)).rowcount
            d2 = conn.execute("DELETE FROM word_downrank WHERE word = ?", (word,)).rowcount
            conn.commit()

        if d1 or d2:
            where = []
            if d1: where.append("блок")
            if d2: where.append("понижение")
            await interaction.response.send_message(
                f"✅ Слово `{word}` удалено из: {', '.join(where)}.\n"
                f"Запусти `/дообучить модели:Только Persona` чтобы пересчитать профили.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Слово `{word}` не найдено ни в одном фильтре.", ephemeral=True)

    # ── /фильтр_список ────────────────────────────────────────────────────────
    @app_commands.command(
        name="фильтр_список",
        description="(Админ) Показать все активные фильтры пародии"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def фильтр_список(self, interaction: discord.Interaction):
        with sqlite3.connect(FILTERS_DB) as conn:
            channels  = conn.execute(
                "SELECT channel_name, added_at FROM channel_ignore ORDER BY added_at").fetchall()
            blocked   = conn.execute(
                "SELECT word, added_at FROM word_block ORDER BY word").fetchall()
            downranked = conn.execute(
                "SELECT word, strength, added_at FROM word_downrank ORDER BY strength").fetchall()

        emb = discord.Embed(
            title="⚙️ Фильтры пародии",
            color=discord.Color.dark_grey()
        )

        # Каналы
        if channels:
            ch_lines = [f"`#{name}` — {ts[:10]}" for name, ts in channels]
            emb.add_field(
                name=f"⛔ Каналы-исключения ({len(channels)})",
                value="\n".join(ch_lines) or "—",
                inline=False
            )
        else:
            emb.add_field(name="⛔ Каналы-исключения", value="Нет", inline=False)

        # Блок слов
        if blocked:
            bl_lines = [f"`{w}`" for w, _ in blocked]
            # Разбиваем на строки по 10 слов
            rows = [" · ".join(bl_lines[i:i+10]) for i in range(0, len(bl_lines), 10)]
            emb.add_field(
                name=f"🚫 Слова (полный блок) — {len(blocked)} шт.",
                value="\n".join(rows) or "—",
                inline=False
            )
        else:
            emb.add_field(name="🚫 Слова (полный блок)", value="Нет", inline=False)

        # Понижение
        if downranked:
            dn_lines = [f"`{w}` −{100-int(s*100)}%" for w, s, _ in downranked]
            rows = [" · ".join(dn_lines[i:i+6]) for i in range(0, len(dn_lines), 6)]
            emb.add_field(
                name=f"📉 Слова (понижение) — {len(downranked)} шт.",
                value="\n".join(rows) or "—",
                inline=False
            )
        else:
            emb.add_field(name="📉 Слова (понижение)", value="Нет", inline=False)

        emb.set_footer(text=f"БД: parody_filters.db · Всего фильтров: {len(channels)+len(blocked)+len(downranked)}")
        await interaction.response.send_message(embed=emb, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ParodyFilters(bot))
