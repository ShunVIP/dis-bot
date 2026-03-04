import os
import re
import sqlite3
import sys
from typing import List, Tuple, Dict

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(PROJECT_ROOT, "datebase", "wwm.db")

GAME8_SUFFIX_RE = re.compile(r"\s*\|\s*Where\s+Winds\s+Meet\s*\|\s*Game8\s*$", re.IGNORECASE)
GAME8_SUFFIX_RE2 = re.compile(r"\s*\|\s*Game8\s*$", re.IGNORECASE)

def normalize_key(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9а-яё\s]+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def clean_title(t: str) -> str:
    t = (t or "").strip()
    if not t:
        return t
    t = GAME8_SUFFIX_RE.sub("", t).strip()
    t = GAME8_SUFFIX_RE2.sub("", t).strip()
    if "game8" in t.lower() and "|" in t:
        t = t.split("|", 1)[0].strip()
    return t

def fetch_sources(conn: sqlite3.Connection, entity_id: int) -> List[Tuple[str, str]]:
    """
    Возвращает список (source, url) — по одному актуальному url на источник.
    Берём самый свежий fetched_at.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT source, url, MAX(fetched_at) AS mf
        FROM entity_sources
        WHERE entity_id = ?
        GROUP BY source
        ORDER BY source
    """, (entity_id,))
    out = []
    for source, url, _ in cur.fetchall():
        if url:
            out.append((source, url))
    return out

def search(conn: sqlite3.Connection, query: str, limit: int = 10) -> List[Dict]:
    qk = normalize_key(query)
    if not qk:
        return []

    cur = conn.cursor()
    # 1) ищем по aliases.alias_key
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
        title = clean_title(title)
        sources = fetch_sources(conn, entity_id)
        results.append({
            "entity_id": entity_id,
            "title": title,
            "sources": sources,
        })
    return results

def main():
    if len(sys.argv) < 2:
        print("Usage: python wwm_search.py <query>")
        raise SystemExit(1)

    query = " ".join(sys.argv[1:]).strip()
    conn = sqlite3.connect(DB_PATH)
    try:
        results = search(conn, query, limit=10)
        if not results:
            print("No results.")
            return

        for r in results:
            line = f'{r["entity_id"]}: {r["title"]}'
            if r["sources"]:
                src_str = " | ".join([f"{s}:{u}" for s, u in r["sources"]])
                line += f"  ->  {src_str}"
            print(line)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
