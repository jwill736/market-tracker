"""SQLite persistence for transactions, the watchlist and the strategy plan's log."""

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
CREATE TABLE IF NOT EXISTS plan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    price REAL NOT NULL,
    shares REAL NOT NULL,
    value REAL NOT NULL,
    reason TEXT,
    UNIQUE (day, symbol, action)
);
"""


@contextmanager
def connect(path: str | None = None):
    conn = sqlite3.connect(path or settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn) -> None:
    """Columns added after the first release, for databases created before them."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
    if "import_key" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN import_key TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS transactions_import_key ON transactions(import_key)")


def add_transaction(conn, symbol: str, side: str, quantity: float, price: float, date: str,
                    fees: float = 0.0, note: str | None = None, import_key: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO transactions (symbol, side, quantity, price, fees, date, note, import_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol.upper(), side, quantity, price, fees, date, note, import_key))
    return cur.lastrowid


def import_keys(conn) -> set[str]:
    return {r["import_key"] for r in conn.execute("SELECT import_key FROM transactions WHERE import_key IS NOT NULL")}


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


def log_plan(conn, day: str, actions: list[dict]) -> int:
    """Record the day's first recommendation per symbol and action, so the plan can be scored
    later against what the prices did. Holds are not logged."""
    n = 0
    for a in actions:
        if a["action"] == "Hold" or not a.get("price"):
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO plan_log (day, symbol, action, price, shares, value, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (day, a["symbol"], a["action"], a["price"], a["shares"], a["value"], (a.get("reasons") or [""])[0]))
        n += cur.rowcount
    return n


def plan_history(conn, limit: int = 200) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM plan_log ORDER BY day DESC, id DESC LIMIT ?", (limit,))]
