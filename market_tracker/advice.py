"""The app's own track record: did following its advice beat doing nothing?

Each day the actionable advice is written down with that day's price, once per symbol and
action:
- the hold plan's "Sell?" and "Trim" (thesis tripwires, over-concentration),
- its reinvest queue's first two picks and buy-the-dip hits (where new money should go),
- the Strategy page's buys and sells (already logged in plan_log; read from there).

Later, each piece of advice is scored against the realistic alternative at 1, 3 and 6 months:
- a sell or trim: the money would have gone into the index fund (VOO), so the advice helped
  when the stock did worse than VOO after that day;
- a buy or add: the money would otherwise have gone into VOO too, so it helped when the stock
  did better than VOO.

That's the honest test for a buy-and-hold investor: not "was it right?" but "was it better
than the index I'd have bought anyway?". Until 20 pieces of advice have a result at a horizon,
the page says it's too early to tell, whatever the numbers look like.
"""

from __future__ import annotations

from datetime import date, timedelta

BENCH = "VOO"
HORIZONS = (30, 91, 182)          # calendar days: about 1, 3 and 6 months
MIN_RESOLVED = 20
SELLS = {"Sell?", "Trim", "Sell", "Trim some"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS advice_log (
    day TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    source TEXT NOT NULL,
    price REAL NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (day, symbol, action, source)
);
"""


def from_plan(plan: dict) -> list[dict]:
    """The actionable advice in a hold plan (hold plan rows, reinvest picks, dip hits)."""
    out = []
    for h in plan.get("holdings") or []:
        if h.get("verdict") in ("Sell?", "Trim") and h.get("price"):
            why = (h.get("triggers") or [{}])[0].get("text") if h.get("triggers") else ""
            out.append({"symbol": h["symbol"], "action": h["verdict"], "source": "hold", "price": h["price"], "reason": why or ""})
    prices = {h["symbol"]: h.get("price") for h in plan.get("holdings") or []}
    for q in (plan.get("reinvest") or [])[:2]:
        out.append({"symbol": q["symbol"], "action": "Buy", "source": "reinvest", "price": prices.get(q["symbol"]),
                    "reason": q.get("why", "")})
    for d in plan.get("dip_hits") or []:
        out.append({"symbol": d, "action": "Buy", "source": "dip", "price": prices.get(d), "reason": "Hit your buy-the-dip price"})
    return out


def record(conn, day: str, items: list[dict], price_fn=None) -> int:
    """Write the day's advice (first time only). Items without a price get one from price_fn."""
    conn.executescript(SCHEMA)
    n = 0
    for it in items:
        price = it.get("price")
        if not price and price_fn:
            try:
                price = price_fn(it["symbol"])
            except Exception:  # noqa: BLE001 - no price, no record; tomorrow tries again
                price = None
        if not price:
            continue
        cur = conn.execute("INSERT OR IGNORE INTO advice_log (day, symbol, action, source, price, reason) VALUES (?, ?, ?, ?, ?, ?)",
                           (day, it["symbol"], it["action"], it["source"], float(price), (it.get("reason") or "")[:200]))
        n += cur.rowcount
    return n


def logged(conn) -> list[dict]:
    conn.executescript(SCHEMA)
    rows = [dict(r) for r in conn.execute("SELECT * FROM advice_log ORDER BY day DESC")]
    rows += [{"day": r["day"], "symbol": r["symbol"], "action": r["action"], "source": "strategy", "price": r["price"],
              "reason": r["reason"] or ""} for r in conn.execute("SELECT * FROM plan_log ORDER BY day DESC")
             if r["action"] != "Hold"]
    return rows


def _close_on_or_after(bars: list[tuple[str, float]], day: str) -> float | None:
    for d, c in bars:
        if d >= day:
            return c
    return None


def score(rows: list[dict], history_fn, today: date) -> dict:
    """history_fn(symbol) -> [(date, close)] oldest first. Returns per-horizon results and each item."""
    cache: dict[str, list] = {}

    def bars(sym):
        if sym not in cache:
            try:
                cache[sym] = history_fn(sym)
            except Exception:  # noqa: BLE001 - a symbol with no history just stays unresolved
                cache[sym] = []
        return cache[sym]
    bench = bars(BENCH)
    items, by_h = [], {h: [] for h in HORIZONS}
    for r in rows:
        sell = r["action"] in SELLS
        item = dict(r, kind="sell" if sell else "buy", results={})
        for h in HORIZONS:
            end = (date.fromisoformat(r["day"]) + timedelta(days=h)).isoformat()
            if end > today.isoformat():
                continue
            s0, s1 = r["price"], _close_on_or_after(bars(r["symbol"]), end)
            b0, b1 = _close_on_or_after(bench, r["day"]), _close_on_or_after(bench, end)
            if not (s1 and b0 and b1):
                continue
            stock, idx = s1 / s0 - 1, b1 / b0 - 1
            edge = (idx - stock) if sell else (stock - idx)
            item["results"][h] = {"stock": round(stock * 100, 2), "voo": round(idx * 100, 2), "edge": round(edge * 100, 2),
                                  "helped": edge > 0}
            by_h[h].append(edge)
        items.append(item)
    summary = {}
    for h, edges in by_h.items():
        n = len(edges)
        summary[h] = {"resolved": n, "hit_rate": round(sum(e > 0 for e in edges) / n * 100, 1) if n else None,
                      "avg_edge": round(sum(edges) / n * 100, 2) if n else None, "enough": n >= MIN_RESOLVED}
    return {"horizons": summary, "items": items, "min_resolved": MIN_RESOLVED, "benchmark": BENCH,
            "verdict": verdict(summary)}


def verdict(summary: dict) -> str:
    h = summary.get(91) or {}
    if not h.get("enough"):
        n = h.get("resolved") or 0
        return (f"Too early to tell: {n} piece{'s' if n != 1 else ''} of advice {'has' if n == 1 else 'have'} a 3-month result "
                f"(needs {MIN_RESOLVED}). Keep going; this is logged automatically every day.")
    if h["avg_edge"] > 1 and h["hit_rate"] > 55:
        return f"So far following the advice beat VOO by {h['avg_edge']:.1f} points on average over 3 months ({h['hit_rate']:.0f}% of the time)."
    if h["avg_edge"] < -1:
        return (f"So far following the advice did {-h['avg_edge']:.1f} points WORSE than just buying VOO over 3 months. "
                "Treat its suggestions with suspicion and prefer the index.")
    return "So far following the advice has been about the same as buying VOO: no edge either way."
