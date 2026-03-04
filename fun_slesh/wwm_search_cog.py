import os
import re
import sqlite3
from typing import List, Tuple, Optional, Dict

import discord
from discord import app_commands
from discord.ext import commands

# Корень проекта: D:\dis-bot
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "datebase", "wwm.db")

def normalize_key(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9а-яё\s]+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def fetch_sources(conn: sqlite3.Connection, entity_id: int) -> List[Tuple[str, str]]:
    """
    Возвращает список (source, url) — по одному актуальному url на источник.
    Берём самый свежий fetched_at в рамках источника.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT source, url, MAX(fetched_at) AS mf
        FROM entity_sources
        WHERE entity_id = ?
        GROUP BY source
        ORDER BY
          CASE source WHEN 'game8' THEN 0 WHEN 'fandom' THEN 1 ELSE 2 END,
          source
    """, (entity_id,))
    out = []
    for source, url, _ in cur.fetchall():
        if url:
            out.append((source, url))
    return out

def fetch_features(conn: sqlite3.Connection, entity_id: int) -> Tuple[str, float, str]:
    """
    Возвращает (predicted_type, confidence, snippet_en)
    Если entity_features ещё не заполнена — вернёт значения по умолчанию.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT predicted_type, confidence, snippet_en
        FROM entity_features
        WHERE entity_id = ?
    """, (entity_id,))
    row = cur.fetchone()
    if not row:
        return ("unknown", 0.0, "")
    return (row[0] or "unknown", float(row[1] or 0.0), row[2] or "")

def search_db(query: str, limit: int = 5) -> List[Dict]:
    qk = normalize_key(query)
    if not qk:
        return []

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.entity_id, e.canonical_title, MIN(LENGTH(a.alias_key)) AS best_len
            FROM aliases a
            JOIN entities e ON e.entity_id = a.entity_id
            WHERE a.alias_key LIKE ?
            GROUP BY e.entity_id, e.canonical_title
            ORDER BY best_len ASC, e.entity_id DESC
            LIMIT ?
        """, (f"%{qk}%", limit))

        rows = cur.fetchall()
        results = []
        for entity_id, title, _ in rows:
            ptype, conf, snippet = fetch_features(conn, entity_id)
            sources = fetch_sources(conn, entity_id)
            results.append({
                "entity_id": entity_id,
                "title": title or "unknown",
                "ptype": ptype,
                "conf": conf,
                "snippet": snippet,
                "sources": sources,
            })
        return results
    finally:
        conn.close()

def format_sources(sources: List[Tuple[str, str]]) -> str:
    if not sources:
        return "—"
    # Превращаем в кликабельные ссылки
    parts = []
    for s, u in sources:
        parts.append(f"**{s}**: <{u}>")
    return " | ".join(parts)

def clamp_text(s: str, max_chars: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + "…"

class WWMSearchCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="wwm_search", description="Search in Where Winds Meet KB (shows snippet + sources)")
    @app_commands.describe(query="Search text (EN works best for now)")
    async def wwm_search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)

        try:
            results = search_db(query, limit=5)
        except Exception as e:
            await interaction.followup.send(f"KB error: {e}")
            return

        if not results:
            await interaction.followup.send("Nothing found. Try using in-game English terms.")
            return

        # Один embed, несколько результатов (до 5)
        embed = discord.Embed(
            title="Where Winds Meet — KB Search",
            description=f"Query: **{clamp_text(query, 120)}**",
        )

        for r in results:
            tag = f"{r['ptype']}"
            if r["conf"] > 0:
                tag += f" ({int(r['conf']*100)}%)"

            snippet = r["snippet"] or "No snippet yet. Run: `python wwm_kb\\classify_and_snippet.py`"
            snippet = clamp_text(snippet, 650)

            sources_str = format_sources(r["sources"])
            value = f"{snippet}\n\nSources: {sources_str}"

            embed.add_field(
                name=f"`{r['entity_id']}` [{tag}] {clamp_text(r['title'], 140)}",
                value=clamp_text(value, 950),
                inline=False
            )

        await interaction.followup.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(WWMSearchCog(bot))
