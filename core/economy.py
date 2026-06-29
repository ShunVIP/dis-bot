# core/economy.py
import os, sqlite3, json
from datetime import datetime, timezone
from core.economy_profile import can_receive_currency

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "datebase", "social.db"))

def _ensure_tables():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS coins_wallet (
                user_id    INTEGER PRIMARY KEY,
                balance    INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS coin_ledger (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                delta      INTEGER NOT NULL,
                reason     TEXT NOT NULL,
                meta       TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()

def get_balance(user_id: int) -> int:
    _ensure_tables()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT balance FROM coins_wallet WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0

def add_coins(user_id: int, delta: int, reason: str, meta: dict | None = None) -> int:
    """¬сегда пишет в ledger, возвращает новый баланс."""
    _ensure_tables()
    if int(delta) > 0 and not can_receive_currency(user_id):
        return get_balance(user_id)
    now_utc = datetime.now(timezone.utc).isoformat()
    meta_text = json.dumps(meta or {}, ensure_ascii=False)

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        # ledger
        cur.execute(
            "INSERT INTO coin_ledger (user_id, delta, reason, meta, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, int(delta), reason, meta_text, now_utc)
        )
        # wallet upsert
        cur.execute("SELECT balance FROM coins_wallet WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row:
            new_balance = int(row[0]) + int(delta)
            cur.execute(
                "UPDATE coins_wallet SET balance = ?, updated_at = ? WHERE user_id = ?",
                (new_balance, now_utc, user_id)
            )
        else:
            new_balance = int(delta)
            cur.execute(
                "INSERT INTO coins_wallet (user_id, balance, updated_at) VALUES (?, ?, ?)",
                (user_id, new_balance, now_utc)
            )
        conn.commit()
        return new_balance
