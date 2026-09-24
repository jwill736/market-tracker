"""SQLite persistence for transactions and the watchlist."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity REAL NOT NULL CHECK (quantity > 0),
    price REAL NOT NULL CHECK (price >= 0),
    fees REAL NOT NULL DEFAULT 0,
    date TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    added TEXT NOT NULL DEFAULT (date('now'))
);
"""


@contextmanager
def connect(path: str | None = None):
    conn = sqlite3.connect(path or settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_transaction(conn, symbol: str, side: str, quantity: float, price: float, date: str,
                    fees: float = 0.0, note: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO transactions (symbol, side, quantity, price, fees, date, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (symbol.upper(), side, quantity, price, fees, date, note))
    return cur.lastrowid


def list_transactions(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM transactions ORDER BY date, id")]


def delete_transaction(conn, tx_id: int) -> bool:
    return conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,)).rowcount > 0


def watchlist(conn) -> list[str]:
    return [r["symbol"] for r in conn.execute("SELECT symbol FROM watchlist ORDER BY symbol")]


def add_watch(conn, symbol: str) -> None:
    conn.execute("INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (symbol.upper(),))


def remove_watch(conn, symbol: str) -> None:
    conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
