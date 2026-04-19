import os
import random
import re
import sqlite3
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from wwm_kb.classify_and_snippet import (
    extract_text_from_payload,
    pick_best_snippet,
    predict_type,
    preprocess_text_for_snippet,
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "datebase", "wwm.db")

GAME8_SUFFIX_RE = re.compile(r"\s*\|\s*Where\s+Winds\s+Meet\s*[|｜]\s*Game8\s*$", re.IGNORECASE)
GAME8_SUFFIX_RE2 = re.compile(r"\s*\|\s*Game8\s*$", re.IGNORECASE)

TYPE_LABELS = {
    "any": "anything",
    "walkthrough": "walkthroughs",
    "skill": "mystic skills",
    "location": "locations",
    "system": "systems",
    "weapon": "weapons",
    "boss": "bosses",
    "quest": "quests",
    "build": "builds",
}


def normalize_key(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9а-яё\s]+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_title(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return "unknown"
    t = GAME8_SUFFIX_RE.sub("", t).strip()
    t = GAME8_SUFFIX_RE2.sub("", t).strip()
    if "game8" in t.lower() and "|" in t:
        t = t.split("|", 1)[0].strip()
    return t or "unknown"


def clamp_text(s: str, max_chars: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + "…"


def fetch_sources(conn: sqlite3.Connection, entity_id: int) -> List[Tuple[str, str]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT source, url, MAX(fetched_at) AS mf
        FROM entity_sources
        WHERE entity_id = ?
        GROUP BY source
        ORDER BY
          CASE source WHEN 'game8' THEN 0 WHEN 'fandom' THEN 1 ELSE 2 END,
          source
    """,
        (entity_id,),
    )
    out = []
    for source, url, _ in cur.fetchall():
        if url:
            out.append((source, url))
    return out


def fetch_features(conn: sqlite3.Connection, entity_id: int) -> Tuple[str, float, str]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT predicted_type, confidence, snippet_en
        FROM entity_features
        WHERE entity_id = ?
    """,
        (entity_id,),
    )
    row = cur.fetchone()
    if not row:
        return ("unknown", 0.0, "")
    return (row[0] or "unknown", float(row[1] or 0.0), row[2] or "")


def fetch_latest_entity_source(conn: sqlite3.Connection, entity_id: int) -> Optional[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT source, title, url, payload_json, fetched_at
        FROM entity_sources
        WHERE entity_id = ?
        ORDER BY
          CASE source WHEN 'game8' THEN 0 WHEN 'fandom' THEN 1 ELSE 2 END,
          fetched_at DESC
        LIMIT 1
    """,
        (entity_id,),
    )
    return cur.fetchone()


def has_entity_index(conn: sqlite3.Connection) -> bool:
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM entities LIMIT 1")
        return cur.fetchone() is not None
    except sqlite3.Error:
        return False


def entity_search(conn: sqlite3.Connection, query: str, limit: int = 5) -> List[Dict]:
    qk = normalize_key(query)
    if not qk or not has_entity_index(conn):
        return []

    cur = conn.cursor()
    cur.execute(
        """
        SELECT e.entity_id, e.canonical_title, MIN(LENGTH(a.alias_key)) AS best_len
        FROM aliases a
        JOIN entities e ON e.entity_id = a.entity_id
        WHERE a.alias_key LIKE ?
        GROUP BY e.entity_id, e.canonical_title
        ORDER BY best_len ASC, e.entity_id DESC
        LIMIT ?
    """,
        (f"%{qk}%", limit),
    )

    results = []
    for entity_id, title, _ in cur.fetchall():
        ptype, conf, snippet = fetch_features(conn, entity_id)
        if not snippet or ptype == "unknown":
            latest_source = fetch_latest_entity_source(conn, entity_id)
            if latest_source:
                fallback = build_record_from_raw(latest_source)
                if not snippet:
                    snippet = fallback["snippet"]
                if ptype == "unknown" and fallback["ptype"] != "unknown":
                    ptype = fallback["ptype"]
                    conf = fallback["conf"]
        results.append(
            {
                "entity_id": entity_id,
                "title": clean_title(title),
                "ptype": ptype,
                "conf": conf,
                "snippet": snippet,
                "sources": fetch_sources(conn, entity_id),
            }
        )
    return results


def build_record_from_raw(row: sqlite3.Row) -> Dict:
    raw_title = row["title"] or ""
    title = clean_title(raw_title)
    payload_json = row["payload_json"] or "{}"
    text = extract_text_from_payload(payload_json)
    text = preprocess_text_for_snippet(row["source"], text)
    snippet = pick_best_snippet(text, title)
    ptype, conf = predict_type(title, text)

    return {
        "entity_id": None,
        "title": title,
        "ptype": ptype,
        "conf": conf,
        "snippet": snippet,
        "sources": [(row["source"], row["url"])] if row["url"] else [],
    }


def raw_search(conn: sqlite3.Connection, query: str, limit: int = 5) -> List[Dict]:
    qk = normalize_key(query)
    if not qk:
        return []

    terms = [t for t in qk.split() if len(t) >= 2]
    if not terms:
        terms = [qk]

    cur = conn.cursor()
    cur.execute(
        """
        SELECT source, title, url, payload_json, fetched_at
        FROM raw_records
        WHERE lower(COALESCE(title, '')) LIKE ?
           OR lower(COALESCE(url, '')) LIKE ?
        ORDER BY fetched_at DESC
        LIMIT 300
    """,
        (f"%{query.lower()}%", f"%{query.lower()}%"),
    )
    rows = cur.fetchall()

    results = []
    seen = set()
    for row in rows:
        title_key = normalize_key(clean_title(row["title"] or ""))
        if not title_key or title_key in seen:
            continue
        if not all(term in title_key for term in terms) and qk not in title_key:
            continue
        seen.add(title_key)
        results.append(build_record_from_raw(row))
        if len(results) >= limit:
            break

    if results:
        return results

    cur.execute(
        """
        SELECT source, title, url, payload_json, fetched_at
        FROM raw_records
        ORDER BY fetched_at DESC
        LIMIT 500
    """
    )
    rows = cur.fetchall()
    for row in rows:
        title_key = normalize_key(clean_title(row["title"] or ""))
        if not title_key or title_key in seen:
            continue
        payload_key = normalize_key(extract_text_from_payload(row["payload_json"] or "{}"))
        if qk not in title_key and qk not in payload_key:
            continue
        seen.add(title_key)
        results.append(build_record_from_raw(row))
        if len(results) >= limit:
            break
    return results


def browse_random(conn: sqlite3.Connection, requested_type: str) -> Optional[Dict]:
    cur = conn.cursor()

    if requested_type != "any" and has_entity_index(conn):
        cur.execute(
            """
            SELECT e.entity_id, e.canonical_title, f.predicted_type, f.confidence, f.snippet_en
            FROM entity_features f
            JOIN entities e ON e.entity_id = f.entity_id
            WHERE f.predicted_type = ?
            ORDER BY RANDOM()
            LIMIT 1
        """,
            (requested_type,),
        )
        row = cur.fetchone()
        if row:
            entity_id, title, ptype, conf, snippet = row
            if not snippet or ptype == "unknown":
                latest_source = fetch_latest_entity_source(conn, entity_id)
                if latest_source:
                    fallback = build_record_from_raw(latest_source)
                    if not snippet:
                        snippet = fallback["snippet"]
                    if ptype == "unknown" and fallback["ptype"] != "unknown":
                        ptype = fallback["ptype"]
                        conf = fallback["conf"]
            return {
                "entity_id": entity_id,
                "title": clean_title(title),
                "ptype": ptype,
                "conf": float(conf or 0.0),
                "snippet": snippet or "",
                "sources": fetch_sources(conn, entity_id),
            }

    cur.execute(
        """
        SELECT source, title, url, payload_json, fetched_at
        FROM raw_records
        ORDER BY RANDOM()
        LIMIT 250
    """
    )
    rows = cur.fetchall()
    seen = set()
    candidates = []
    for row in rows:
        title_key = normalize_key(clean_title(row["title"] or ""))
        if not title_key or title_key in seen:
            continue
        seen.add(title_key)
        item = build_record_from_raw(row)
        if requested_type != "any" and item["ptype"] != requested_type:
            continue
        candidates.append(item)

    if not candidates and requested_type != "any":
        return None
    if not candidates:
        return None
    return random.choice(candidates)


def format_sources(sources: List[Tuple[str, str]]) -> str:
    if not sources:
        return "—"
    return " | ".join([f"**{source}**: <{url}>" for source, url in sources])


def build_result_embed(title: str, description: str, results: List[Dict]) -> discord.Embed:
    embed = discord.Embed(title=title, description=description)
    for result in results:
        tag = result["ptype"]
        if result["conf"] > 0:
            tag += f" ({int(result['conf'] * 100)}%)"

        snippet = result["snippet"] or "No snippet yet."
        snippet = clamp_text(snippet, 650)
        sources_str = format_sources(result["sources"])
        value = clamp_text(f"{snippet}\n\nSources: {sources_str}", 950)

        id_prefix = f"`{result['entity_id']}` " if result["entity_id"] else ""
        embed.add_field(
            name=f"{id_prefix}[{tag}] {clamp_text(result['title'], 180)}",
            value=value,
            inline=False,
        )
    return embed


class WWMSearchCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="wwm_search",
        description="Search in Where Winds Meet KB (supports fallback search)",
    )
    @app_commands.describe(query="Search text; English in-game terms work best")
    async def wwm_search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)

        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                results = entity_search(conn, query, limit=5)
                fallback_used = False
                if not results:
                    results = raw_search(conn, query, limit=5)
                    fallback_used = True
        except Exception as e:
            await interaction.followup.send(f"KB error: {e}")
            return

        if not results:
            await interaction.followup.send("Nothing found. Try using English in-game names or quest titles.")
            return

        suffix = "Fallback mode: raw article search." if fallback_used else "Indexed KB results."
        embed = build_result_embed(
            title="Where Winds Meet — KB Search",
            description=f"Query: **{clamp_text(query, 120)}**\n{suffix}",
            results=results,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="wwm_random",
        description="Get a random Where Winds Meet article or guide",
    )
    @app_commands.describe(category="Optional category to browse")
    @app_commands.choices(
        category=[
            app_commands.Choice(name="Anything", value="any"),
            app_commands.Choice(name="Walkthrough", value="walkthrough"),
            app_commands.Choice(name="Mystic Skill", value="skill"),
            app_commands.Choice(name="Location", value="location"),
            app_commands.Choice(name="System", value="system"),
            app_commands.Choice(name="Weapon", value="weapon"),
            app_commands.Choice(name="Boss", value="boss"),
            app_commands.Choice(name="Quest", value="quest"),
            app_commands.Choice(name="Build", value="build"),
        ]
    )
    async def wwm_random(
        self,
        interaction: discord.Interaction,
        category: Optional[app_commands.Choice[str]] = None,
    ):
        await interaction.response.defer(thinking=True)

        requested_type = category.value if category else "any"
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                item = browse_random(conn, requested_type)
        except Exception as e:
            await interaction.followup.send(f"KB error: {e}")
            return

        if not item:
            label = TYPE_LABELS.get(requested_type, requested_type)
            await interaction.followup.send(f"I couldn't find random {label} in the current WWM database.")
            return

        embed = build_result_embed(
            title="Where Winds Meet — Random Pick",
            description=f"Category: **{TYPE_LABELS.get(requested_type, requested_type)}**",
            results=[item],
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(WWMSearchCog(bot))
