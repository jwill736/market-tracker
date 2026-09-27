"""The idea log: every buy idea the app produces, written down once and scored against VOO.

Each screen (quality-value-momentum, backlog, insider buying, sleepers, chatter) logs its top
names with that day's price, the reason, and what would prove it wrong. Nothing in the log can
be edited afterwards. At 3, 6 and 12 months each idea is compared with putting the same money in
VOO on the same day; the leaderboard shows, per kind of idea, how many beat VOO, by how much on
average, and the worst result.

Why: published stock-picking signals lose about half their edge once they're known (McLean &
Pontiff 2016), and a screen that looks clever can simply be a bet on one kind of stock. This is
how the app finds out, with your money on the line only after a signal has earned it. Until 20
ideas of a kind have a 6-month result, it says too early to tell.

You can mark an idea "bought" or "passed" once, so the log also shows how the ideas you acted on
did against the ones you skipped.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

BENCH = "VOO"
HORIZONS = {"3m": 91, "6m": 182, "12m": 365}
MIN_RESOLVED = 20
DEDUPE_DAYS = 30                    # the same idea from the same screen isn't logged twice within a month
SOURCES = {
    "qvm": "Quality, value and momentum screen",
    "backlog": "Growing order backlog",
    "insider": "Opportunistic insider buying",
    "sleeper": "Sleepers (small and mid caps)",
    "chatter": "Water-cooler chatter",
    "supplier": "Supplier hasn't caught up",
    "contract": "Large government contracts",
    "manual": "Your own ideas",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    price REAL NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    wrong_if TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    decision TEXT NOT NULL DEFAULT '',
    decided TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ideas_by_symbol ON ideas (symbol, source, day);
CREATE TRIGGER IF NOT EXISTS ideas_immutable BEFORE UPDATE OF day, symbol, source, price, reason, wrong_if, data ON ideas
BEGIN SELECT RAISE(ABORT, 'ideas are write-once'); END;
CREATE TRIGGER IF NOT EXISTS ideas_no_delete BEFORE DELETE ON ideas
BEGIN SELECT RAISE(ABORT, 'ideas are write-once'); END;
"""


def log(conn, day: str, items: list[dict], price_fn=None) -> int:
    """items: [{symbol, source, reason, wrong_if?, price?, data?}]. Returns how many were new."""
    conn.executescript(SCHEMA)
    since = (date.fromisoformat(day) - timedelta(days=DEDUPE_DAYS)).isoformat()
    n = 0
    for it in items:
        if it["source"] not in SOURCES:
            raise ValueError(f"unknown idea source {it['source']}")
        if conn.execute("SELECT 1 FROM ideas WHERE symbol = ? AND source = ? AND day >= ?", (it["symbol"], it["source"], since)).fetchone():
            continue
        price = it.get("price")
        if not price and price_fn:
            try:
                price = price_fn(it["symbol"])
            except Exception:  # noqa: BLE001 - no price, no record; the next run tries again
                price = None
        if not price:
            continue
        conn.execute("INSERT INTO ideas (day, symbol, source, price, reason, wrong_if, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (day, it["symbol"], it["source"], float(price), (it.get("reason") or "")[:300], (it.get("wrong_if") or "")[:200],
                      json.dumps(it.get("data") or {})[:4000]))
        n += 1
    return n


def decide(conn, idea_id: int, decision: str, day: str) -> bool:
    """Mark an idea bought or passed, once."""
    if decision not in ("bought", "passed"):
        raise ValueError("decision: bought or passed")
    conn.executescript(SCHEMA)
    cur = conn.execute("UPDATE ideas SET decision = ?, decided = ? WHERE id = ? AND decision = ''", (decision, day, idea_id))
    return cur.rowcount == 1


def logged(conn) -> list[dict]:
    conn.executescript(SCHEMA)
    out = []
    for r in conn.execute("SELECT * FROM ideas ORDER BY day DESC, id DESC"):
        d = dict(r)
        d["data"] = json.loads(d["data"] or "{}")
        out.append(d)
    return out


def _close_on_or_after(bars: list[tuple[str, float]], day: str) -> float | None:
    for d, c in bars:
        if d >= day:
            return c
    return None


def score(rows: list[dict], history_fn, today: date) -> dict:
    """history_fn(symbol) -> [(date, close)] oldest first. Each idea's return against VOO so far and
    at each horizon, and a leaderboard per source."""
    cache: dict[str, list] = {}

    def bars(sym):
        if sym not in cache:
            try:
                cache[sym] = history_fn(sym)
            except Exception:  # noqa: BLE001 - no history: the idea stays unresolved
                cache[sym] = []
        return cache[sym]
    bench = bars(BENCH)
    items = []
    by_source: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        item = dict(r, results={}, so_far=None)
        b0 = _close_on_or_after(bench, r["day"])
        sb, bb = bars(r["symbol"]), bench
        if sb and bb and b0:
            item["so_far"] = {"stock": round((sb[-1][1] / r["price"] - 1) * 100, 2), "voo": round((bb[-1][1] / b0 - 1) * 100, 2)}
            item["so_far"]["edge"] = round(item["so_far"]["stock"] - item["so_far"]["voo"], 2)
        for name, days in HORIZONS.items():
            end = (date.fromisoformat(r["day"]) + timedelta(days=days)).isoformat()
            if end > today.isoformat() or not b0:
                continue
            s1, b1 = _close_on_or_after(sb, end), _close_on_or_after(bb, end)
            if not (s1 and b1):
                continue
            edge = (s1 / r["price"] - 1) - (b1 / b0 - 1)
            item["results"][name] = {"stock": round((s1 / r["price"] - 1) * 100, 2), "voo": round((b1 / b0 - 1) * 100, 2),
                                     "edge": round(edge * 100, 2)}
            by_source.setdefault(r["source"], {h: [] for h in HORIZONS}).setdefault(name, []).append(edge)
        items.append(item)
    board = []
    for src in sorted({r["source"] for r in rows}):
        counts = {h: by_source.get(src, {}).get(h, []) for h in HORIZONS}
        row = {"source": src, "label": SOURCES.get(src, src), "ideas": sum(1 for r in rows if r["source"] == src)}
        for h, edges in counts.items():
            n = len(edges)
            row[h] = {"resolved": n, "beat_voo": round(sum(e > 0 for e in edges) / n * 100) if n else None,
                      "avg_edge": round(sum(edges) / n * 100, 2) if n else None,
                      "worst": round(min(edges) * 100, 1) if n else None, "enough": n >= MIN_RESOLVED}
        board.append(row)
    acted = {"bought": [], "passed": []}
    for it in items:
        if it.get("decision") in acted and it["so_far"]:
            acted[it["decision"]].append(it["so_far"]["edge"])
    return {"items": items, "leaderboard": board, "benchmark": BENCH, "min_resolved": MIN_RESOLVED,
            "decisions": {k: {"n": len(v), "avg_edge": round(sum(v) / len(v), 2) if v else None} for k, v in acted.items()},
            "verdict": verdict(board)}


def verdict(board: list[dict]) -> str:
    ready = [b for b in board if b["6m"]["enough"]]
    if not ready:
        n = sum(b["6m"]["resolved"] for b in board)
        return (f"Too early to tell: {n} idea{'s' if n != 1 else ''} {'has' if n == 1 else 'have'} a 6-month result, and a kind of idea "
                f"needs {MIN_RESOLVED} before its record means anything. Everything is logged automatically; check back.")
    best = max(ready, key=lambda b: b["6m"]["avg_edge"])
    worst = min(ready, key=lambda b: b["6m"]["avg_edge"])
    line = f"Best so far: {best['label']}, {best['6m']['avg_edge']:+.1f} points against VOO over 6 months ({best['6m']['beat_voo']}% beat it)."
    if worst is not best:
        line += f" Worst: {worst['label']}, {worst['6m']['avg_edge']:+.1f}."
    return line
