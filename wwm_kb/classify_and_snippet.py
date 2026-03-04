import os
import re
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "datebase", "wwm.db")

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

STOPWORDS = {
    "the","and","or","to","of","in","a","an","for","with","on","at","by","from","is","are","be","as",
    "how","what","when","where","why","who","all","list","guide","tips",
}

TYPE_RULES = [
    ("skill",   [r"\bmystic skill\b", r"\bskill\b"]),
    ("weapon",  [r"\bweapon\b", r"\bblade\b", r"\bsword\b", r"\bstaff\b", r"\bspear\b", r"\bbow\b", r"\bdagger\b"]),
    ("boss",    [r"\bboss\b"]),
    ("npc",     [r"\bnpc\b"]),
    ("quest",   [r"\bquest\b", r"\bside quest\b"]),
    ("walkthrough", [r"\bwalkthrough\b", r"\bstory walkthrough\b"]),
    ("build",   [r"\bbuild\b"]),
    ("system",  [r"\bdailies\b", r"\bweeklies\b", r"\bai chat\b", r"\bhow to unlock\b"]),
    ("location",[r"\blocation\b", r"\bmap\b", r"\boutpost\b"]),
]

def normalize_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def tokenize(s: str):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    parts = [p for p in s.split() if p and p not in STOPWORDS and len(p) >= 3]
    return parts

def extract_text_from_payload(payload_json: str) -> str:
    try:
        obj = json.loads(payload_json)
    except Exception:
        return ""

    # ожидаем "text", но оставляем fallback
    for k in ("text", "extract", "content", "body"):
        if isinstance(obj, dict) and k in obj and isinstance(obj[k], str):
            return obj[k]
    return ""
def cut_game8_membership_block(text: str) -> str:
    """
    У Game8 в начале текста часто идёт огромный блок про membership/премиум.
    Мы пытаемся отрезать его, чтобы добраться до контента статьи.
    """
    if not text:
        return ""

    lines = [normalize_text(x) for x in text.split("\n") if normalize_text(x)]
    if not lines:
        return text

    # Если видим типичное начало membership-блока — пробуем отрезать до первого "содержательного" маркера
    joined = "\n".join(lines)

    membership_markers = [
        "what can you do as a free member",
        "create your free account",
        "site interface",
        "want more information",
        "learn more",
    ]

    if any(m in joined.lower() for m in membership_markers):
        # Ищем строку, после которой вероятно начинается статья
        start_markers = [
            "where winds meet",
            "mystic skill",
            "how to unlock",
            "effects",
            "overview",
            "list of",
            "walkthrough",
            "guide",
        ]

        for i, ln in enumerate(lines):
            low = ln.lower()
            if any(sm in low for sm in start_markers) and i > 5:
                return "\n".join(lines[i:]).strip()

        # если маркеры не нашли — просто отрежем первые N строк membership-блока
        return "\n".join(lines[60:]).strip()

    return text


def preprocess_text_for_snippet(source: str, text: str) -> str:
    if source == "game8":
        return cut_game8_membership_block(text)
    return text

def make_keywords(text: str, topk: int = 12):
    tokens = tokenize(text)
    if not tokens:
        return []
    counts = Counter(tokens)
    return [w for w, _ in counts.most_common(topk)]

def pick_best_snippet(text: str, title: str, max_lines: int = 4, max_chars: int = 650) -> str:
    """
    Выжимка без LLM:
    - дробим по строкам
    - выкидываем мусор/CTA/меню
    - берём первые осмысленные строки
    """
    if not text:
        return ""

    lines = [normalize_text(x) for x in text.split("\n")]
    # небольшой фильтр шума по длине
    lines = [x for x in lines if x and len(x) >= 25]

    bad = (
        "create your free account",
        "premium features",
        "watchlist",
        "favorite games",
        "sign up",
        "log in",
        "cookie",
        "privacy",
        "subscribe",
        "twitter",
        "recommended guides",
        "latest news",
        "game8",
        "terms of service",
    )

    cleaned = []
    seen = set()

    for ln in lines:
        low = ln.lower()

        # 1) выкидываем явный мусор по ключевым фразам
        if any(b in low for b in bad):
            continue

        # 2) выкидываем повторы
        if ln in seen:
            continue

        # 3) Фильтр: короткие строки без пунктуации чаще всего меню/обвязка
        #    Но длинные строки оставляем, даже если без "." и ":"
        if ln.count(".") < 1 and ln.count(":") < 1 and len(ln.split()) < 10:
            continue

        # 4) режем совсем “служебные” штуки
        if low.startswith("home") and len(ln.split()) <= 3:
            continue

        seen.add(ln)
        cleaned.append(ln)

        # достаточно набрать немного кандидатов
        if len(cleaned) >= 40:
            break

    if not cleaned:
        return ""

    out = []
    total = 0
    t_low = (title or "").strip().lower()

    for ln in cleaned:
        if len(out) >= max_lines:
            break

        # не повторяем заголовок как первую строку
        if t_low and ln.strip().lower().startswith(t_low):
            continue

        # ограничение по длине сообщения
        if total + len(ln) + 1 > max_chars:
            break

        out.append(ln)
        total += len(ln) + 1

    # если вдруг всё выкинулось — fallback: возьмём 1-2 лучших строки из cleaned
    if not out:
        for ln in cleaned[:2]:
            if len(ln) > max_chars:
                ln = ln[: max_chars - 1].rstrip() + "…"
            out.append(ln)
        return "\n".join(out).strip()

    return "\n".join(out).strip()

def predict_type(title: str, text: str):
    hay = f"{title}\n{text}".lower()
    for t, patterns in TYPE_RULES:
        for pat in patterns:
            if re.search(pat, hay, flags=re.IGNORECASE):
                conf = 0.85 if "mystic skill" in pat else 0.70
                return t, conf
    return "unknown", 0.40

def ensure_features_schema(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS entity_features (
        entity_id INTEGER PRIMARY KEY,
        predicted_type TEXT NOT NULL,
        confidence REAL NOT NULL,
        snippet_en TEXT,
        keywords_json TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(entity_id) REFERENCES entities(entity_id)
    );

    CREATE INDEX IF NOT EXISTS ix_entity_features_type ON entity_features(predicted_type);
    """)
    conn.commit()

def main(limit: int = 0):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        ensure_features_schema(conn)

        cur = conn.cursor()
        cur.execute("""
            SELECT e.entity_id, e.canonical_title
            FROM entities e
            ORDER BY e.entity_id ASC
        """)
        entities = cur.fetchall()

        processed = 0
        for e in entities:
            entity_id = e["entity_id"]
            title = e["canonical_title"] or ""

            src_cur = conn.cursor()
            src_cur.execute("""
                SELECT source, payload_json, fetched_at
                FROM entity_sources
                WHERE entity_id=?
                ORDER BY
                  CASE source WHEN 'game8' THEN 0 WHEN 'fandom' THEN 1 ELSE 2 END,
                  fetched_at DESC
                LIMIT 1
            """, (entity_id,))
            row = src_cur.fetchone()
            if not row:
                continue

            text = extract_text_from_payload(row["payload_json"])
            text = preprocess_text_for_snippet(row["source"], text)
            snippet = pick_best_snippet(text, title)
            
            # лёгкая защита от гигантских строк
            if snippet and len(snippet) > 1200:
                snippet = snippet[:1199].rstrip() + "…"

            kw = make_keywords((title + "\n" + text)[:20000])
            ptype, conf = predict_type(title, text)

            conn.execute("""
                INSERT INTO entity_features(entity_id, predicted_type, confidence, snippet_en, keywords_json, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                  predicted_type=excluded.predicted_type,
                  confidence=excluded.confidence,
                  snippet_en=excluded.snippet_en,
                  keywords_json=excluded.keywords_json,
                  updated_at=excluded.updated_at
            """, (
                entity_id, ptype, conf, snippet, json.dumps(kw, ensure_ascii=False), now_iso()
            ))

            processed += 1
            if processed % 200 == 0:
                conn.commit()
            if limit and processed >= limit:
                break

        conn.commit()
        print(f"classify_and_snippet done. processed entities: {processed}")

    finally:
        conn.close()

if __name__ == "__main__":
    main()
