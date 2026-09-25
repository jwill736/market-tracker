"""SQLite persistence: transactions, the watchlist, the strategy plan's log, heads-ups and topics."""

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
CREATE TABLE IF NOT EXISTS headsup (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    level INTEGER NOT NULL DEFAULT 1,
    symbol TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS topics (
    name TEXT PRIMARY KEY,
    terms TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS early_log (
    day TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kinds TEXT NOT NULL,
    strength REAL NOT NULL,
    early INTEGER NOT NULL,
    price REAL,
    headline TEXT,
    PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS snapshots (
    source TEXT NOT NULL,
    day TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (source, day)
);
CREATE TABLE IF NOT EXISTS picker_calls (
    id TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    created TEXT NOT NULL,
    price REAL NOT NULL,
    body TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS theses (
    symbol TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated TEXT NOT NULL DEFAULT (date('now'))
);
CREATE TABLE IF NOT EXISTS follows (
    who TEXT PRIMARY KEY,
    grp TEXT NOT NULL,
    added TEXT NOT NULL DEFAULT (date('now'))
);
CREATE TABLE IF NOT EXISTS income (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL DEFAULT '',
    day TEXT NOT NULL,
    amount REAL NOT NULL,
    kind TEXT NOT NULL,               -- dividend / reinvested / interest / tax_withheld
    account TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    import_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
    if "account" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN account TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS transactions_import_key ON transactions(import_key)")


def add_transaction(conn, symbol: str, side: str, quantity: float, price: float, date: str,
                    fees: float = 0.0, note: str | None = None, import_key: str | None = None,
                    account: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO transactions (symbol, side, quantity, price, fees, date, note, import_key, account) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol.upper(), side, quantity, price, fees, date, note, import_key, account or ""))
    return cur.lastrowid


def import_keys(conn) -> set[str]:
    return {r["import_key"] for r in conn.execute("SELECT import_key FROM transactions WHERE import_key IS NOT NULL")}


def add_income(conn, rows: list[dict]) -> int:
    """Insert income rows, skipping ones already imported (same import_key). Returns how many were new."""
    n = 0
    for r in rows:
        cur = conn.execute("INSERT OR IGNORE INTO income (symbol, day, amount, kind, account, note, import_key) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (r.get("symbol") or "", r["day"], r["amount"], r["kind"], r.get("account") or "",
                            r.get("note") or "", r.get("import_key")))
        n += cur.rowcount
    return n


def income(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM income ORDER BY day DESC, id DESC")]


def income_keys(conn) -> set[str]:
    return {r["import_key"] for r in conn.execute("SELECT import_key FROM income WHERE import_key IS NOT NULL")}


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


def add_headsup(conn, key: str, kind: str, level: int, title: str, body: str = "", url: str = "",
                symbol: str = "", at: str = "") -> bool:
    """Record a heads-up once per key. Returns True when it is new (and worth notifying)."""
    cur = conn.execute("INSERT OR IGNORE INTO headsup (key, kind, level, symbol, title, body, url, at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (key, kind, level, symbol, title, body, url, at))
    return cur.rowcount > 0


def list_headsup(conn, limit: int = 60) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM headsup ORDER BY at DESC LIMIT ?", (limit,))]


def mark_headsup_read(conn) -> None:
    conn.execute("UPDATE headsup SET read = 1 WHERE read = 0")


def topics(conn, defaults: dict[str, str] | None = None) -> dict[str, str]:
    """Your topics; the defaults are added once, the first time (deleting them sticks)."""
    if defaults and not conn.execute("SELECT 1 FROM meta WHERE key = 'topics_seeded'").fetchone():
        for name, terms in defaults.items():
            conn.execute("INSERT OR IGNORE INTO topics (name, terms) VALUES (?, ?)", (name, terms))
        conn.execute("INSERT INTO meta (key, value) VALUES ('topics_seeded', '1')")
    return {r["name"]: r["terms"] for r in conn.execute("SELECT name, terms FROM topics ORDER BY name")}


def set_topic(conn, name: str, terms: str) -> None:
    conn.execute("INSERT INTO topics (name, terms) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET terms = excluded.terms",
                 (name, terms))


def delete_topic(conn, name: str) -> None:
    conn.execute("DELETE FROM topics WHERE name = ?", (name,))


def save_picker_calls(conn, calls: list[dict]) -> int:
    n = 0
    for c in calls:
        cur = conn.execute("INSERT OR IGNORE INTO picker_calls (id, user, symbol, side, created, price, body) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?)", (c["id"], c["user"], c["symbol"], c["side"], c["created"],
                                                            c["price"], c.get("body", "")))
        n += cur.rowcount
    return n


def picker_calls(conn, user: str) -> list[dict]:
    return [dict(r, day=r["created"][:10]) for r in
            conn.execute("SELECT * FROM picker_calls WHERE user = ? ORDER BY created DESC", (user.lower(),))]


def theses(conn) -> dict[str, dict]:
    import json
    return {r["symbol"]: dict(json.loads(r["data"]), symbol=r["symbol"], updated=r["updated"])
            for r in conn.execute("SELECT symbol, data, updated FROM theses")}


def save_thesis(conn, symbol: str, data: dict) -> None:
    import json
    conn.execute("INSERT INTO theses (symbol, data, updated) VALUES (?, ?, date('now')) ON CONFLICT(symbol) DO UPDATE "
                 "SET data = excluded.data, updated = excluded.updated", (symbol.upper(), json.dumps(data)))


def delete_thesis(conn, symbol: str) -> None:
    conn.execute("DELETE FROM theses WHERE symbol = ?", (symbol.upper(),))


def get_meta(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


def log_early(conn, day: str, symbol: str, kinds: str, strength: float, early: bool, price: float | None,
              headline: str) -> bool:
    cur = conn.execute("INSERT OR IGNORE INTO early_log (day, symbol, kinds, strength, early, price, headline) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)", (day, symbol, kinds, strength, int(early), price, headline))
    return cur.rowcount > 0


def early_logged(conn, day: str) -> set[str]:
    return {r["symbol"] for r in conn.execute("SELECT symbol FROM early_log WHERE day = ?", (day,))}


def early_history(conn, limit: int = 150) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM early_log WHERE price IS NOT NULL ORDER BY day DESC, strength DESC "
                                          "LIMIT ?", (limit,))]


def save_snapshot(conn, source: str, day: str, data: str, keep: int = 15) -> None:
    conn.execute("INSERT OR REPLACE INTO snapshots (source, day, data) VALUES (?, ?, ?)", (source, day, data))
    conn.execute("DELETE FROM snapshots WHERE source = ? AND day NOT IN "
                 "(SELECT day FROM snapshots WHERE source = ? ORDER BY day DESC LIMIT ?)", (source, source, keep))


def snapshots(conn, source: str, limit: int = 2) -> list[tuple[str, str]]:
    return [(r["day"], r["data"]) for r in
            conn.execute("SELECT day, data FROM snapshots WHERE source = ? ORDER BY day DESC LIMIT ?", (source, limit))]


def follows(conn, include_pickers: bool = False) -> dict[str, str]:
    """People followed on the People tab (Congress, ARK, insiders, activists); stock pickers
    followed for scorecards are kept in the same table under the "picker" group."""
    return {r["who"]: r["grp"] for r in conn.execute("SELECT who, grp FROM follows ORDER BY who")
            if include_pickers or r["grp"] != "picker"}


def follow(conn, who: str, grp: str) -> None:
    conn.execute("INSERT OR IGNORE INTO follows (who, grp) VALUES (?, ?)", (who, grp))


def unfollow(conn, who: str) -> None:
    conn.execute("DELETE FROM follows WHERE who = ?", (who,))
