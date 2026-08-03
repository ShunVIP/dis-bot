from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.db import connection as db_connection
from core.economy_profile import can_receive_currency
from core.paths import SOCIAL_DB


DB_PATH = SOCIAL_DB


def _ensure_tables() -> None:
    with db_connection(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS coins_wallet (
                user_id    INTEGER PRIMARY KEY,
                balance    INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS coin_ledger (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                delta      INTEGER NOT NULL,
                reason     TEXT NOT NULL,
                meta       TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_coin_ledger_user_created
                ON coin_ledger(user_id, created_at DESC);
            """
        )


def _balance_in_connection(conn, user_id: int) -> int:
    row = conn.execute(
        "SELECT balance FROM coins_wallet WHERE user_id=?",
        (int(user_id),),
    ).fetchone()
    return int(row[0]) if row else 0


def _write_wallet_delta(
    conn,
    user_id: int,
    delta: int,
    reason: str,
    meta: dict[str, Any] | None,
    created_at: str,
) -> int:
    current = _balance_in_connection(conn, user_id)
    new_balance = current + int(delta)
    conn.execute(
        """
        INSERT INTO coin_ledger(user_id, delta, reason, meta, created_at)
        VALUES(?,?,?,?,?)
        """,
        (
            int(user_id),
            int(delta),
            str(reason)[:80],
            json.dumps(meta or {}, ensure_ascii=False),
            created_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO coins_wallet(user_id, balance, updated_at) VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            balance=excluded.balance,
            updated_at=excluded.updated_at
        """,
        (int(user_id), new_balance, created_at),
    )
    return new_balance


def get_balance(user_id: int) -> int:
    _ensure_tables()
    with db_connection(DB_PATH) as conn:
        return _balance_in_connection(conn, int(user_id))


def add_coins(user_id: int, delta: int, reason: str, meta: dict | None = None) -> int:
    """Record a wallet delta and return the resulting balance."""
    _ensure_tables()
    clean_user_id = int(user_id)
    clean_delta = int(delta)
    if clean_delta > 0 and not can_receive_currency(clean_user_id):
        return get_balance(clean_user_id)
    now_utc = datetime.now(timezone.utc).isoformat()
    with db_connection(DB_PATH) as conn:
        conn.execute("BEGIN IMMEDIATE")
        return _write_wallet_delta(
            conn, clean_user_id, clean_delta, reason, meta, now_utc
        )


def debit_coins(
    user_id: int,
    amount: int,
    reason: str,
    meta: dict | None = None,
    *,
    allow_partial: bool = False,
) -> dict[str, int | str]:
    """Atomically debit a wallet without ever crossing below zero."""
    _ensure_tables()
    clean_user_id = int(user_id)
    requested = int(amount)
    if requested <= 0:
        return {"status": "invalid_amount", "requested": requested}
    now_utc = datetime.now(timezone.utc).isoformat()
    with db_connection(DB_PATH) as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = _balance_in_connection(conn, clean_user_id)
        if before < requested and not allow_partial:
            return {
                "status": "insufficient",
                "requested": requested,
                "actual": 0,
                "before": before,
                "balance": before,
            }
        actual = min(before, requested)
        balance = before
        if actual:
            balance = _write_wallet_delta(
                conn,
                clean_user_id,
                -actual,
                reason,
                meta,
                now_utc,
            )
    return {
        "status": "debited" if actual == requested else "partial",
        "requested": requested,
        "actual": actual,
        "before": before,
        "balance": balance,
    }


def transfer_coins(
    sender_id: int,
    recipient_id: int,
    amount: int,
) -> dict[str, int | str]:
    """Move currency atomically so concurrent transfers cannot overspend."""
    _ensure_tables()
    sender = int(sender_id)
    recipient = int(recipient_id)
    clean_amount = int(amount)
    if sender == recipient:
        return {"status": "same_user"}
    if clean_amount <= 0:
        return {"status": "invalid_amount"}
    if not can_receive_currency(recipient):
        return {"status": "recipient_profile_required"}

    now_utc = datetime.now(timezone.utc).isoformat()
    with db_connection(DB_PATH) as conn:
        conn.execute("BEGIN IMMEDIATE")
        sender_balance = _balance_in_connection(conn, sender)
        if sender_balance < clean_amount:
            return {"status": "insufficient", "sender_balance": sender_balance}
        sender_balance = _write_wallet_delta(
            conn,
            sender,
            -clean_amount,
            "transfer_out",
            {"to": recipient},
            now_utc,
        )
        recipient_balance = _write_wallet_delta(
            conn,
            recipient,
            clean_amount,
            "transfer_in",
            {"from": sender},
            now_utc,
        )
    return {
        "status": "transferred",
        "sender_balance": sender_balance,
        "recipient_balance": recipient_balance,
    }


def settle_wager(
    winner_id: int,
    loser_id: int,
    amount: int,
    *,
    game: str,
) -> dict[str, int | str]:
    """Atomically move an already-agreed wager between two players."""
    _ensure_tables()
    winner = int(winner_id)
    loser = int(loser_id)
    clean_amount = int(amount)
    if winner == loser or clean_amount <= 0:
        return {"status": "invalid_wager"}
    now_utc = datetime.now(timezone.utc).isoformat()
    with db_connection(DB_PATH) as conn:
        conn.execute("BEGIN IMMEDIATE")
        loser_balance = _balance_in_connection(conn, loser)
        if loser_balance < clean_amount:
            return {
                "status": "insufficient",
                "loser_balance": loser_balance,
                "required": clean_amount,
            }
        loser_balance = _write_wallet_delta(
            conn,
            loser,
            -clean_amount,
            "game_lose",
            {"game": str(game), "to": winner},
            now_utc,
        )
        winner_balance = _write_wallet_delta(
            conn,
            winner,
            clean_amount,
            "game_win",
            {"game": str(game), "from": loser},
            now_utc,
        )
    return {
        "status": "settled",
        "winner_balance": winner_balance,
        "loser_balance": loser_balance,
    }


def list_ledger_entries(user_id: int, limit: int = 5) -> list[dict[str, Any]]:
    _ensure_tables()
    with db_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT delta, reason, meta, created_at
            FROM coin_ledger
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(user_id), max(1, min(100, int(limit)))),
        ).fetchall()
    result = []
    for delta, reason, meta, created_at in rows:
        try:
            parsed_meta = json.loads(str(meta or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed_meta = {}
        result.append(
            {
                "delta": int(delta),
                "reason": str(reason),
                "meta": parsed_meta if isinstance(parsed_meta, dict) else {},
                "created_at": str(created_at),
            }
        )
    return result


def list_wallets(limit: int = 100) -> list[tuple[int, int]]:
    _ensure_tables()
    with db_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT user_id, balance
            FROM coins_wallet
            ORDER BY balance DESC, user_id ASC
            LIMIT ?
            """,
            (max(1, min(10_000, int(limit))),),
        ).fetchall()
    return [(int(user_id), int(balance)) for user_id, balance in rows]


def list_positive_wallets() -> list[tuple[int, int]]:
    _ensure_tables()
    with db_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT user_id, balance
            FROM coins_wallet
            WHERE balance > 0
            ORDER BY user_id ASC
            """
        ).fetchall()
    return [(int(user_id), int(balance)) for user_id, balance in rows]
