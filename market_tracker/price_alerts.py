"""Price lines that reach your phone right away: the sell-below and take-some-off lines from
each holding's "why you own it", and your buy-the-dip list (a price you'd pay for a stock or
coin you're watching). Checked every time the background loop runs (every 2 minutes), and at
most one push per line per day.
"""

from __future__ import annotations

from datetime import date

from . import db


def targets(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM buy_targets ORDER BY symbol")]


def set_target(conn, symbol: str, price: float, note: str = "") -> None:
    conn.execute("INSERT INTO buy_targets (symbol, price, note) VALUES (?, ?, ?) "
                 "ON CONFLICT(symbol) DO UPDATE SET price = excluded.price, note = excluded.note", (symbol, price, note))


def remove_target(conn, symbol: str) -> None:
    conn.execute("DELETE FROM buy_targets WHERE symbol = ?", (symbol,))


def lines(theses: dict[str, dict], buy_targets: list[dict]) -> list[dict]:
    out = []
    for sym, t in theses.items():
        if t.get("price_below"):
            out.append({"symbol": sym, "kind": "below", "price": float(t["price_below"]), "text": "your sell-below line"})
        if t.get("price_above"):
            out.append({"symbol": sym, "kind": "above", "price": float(t["price_above"]), "text": "your take-some-off price"})
    for b in buy_targets:
        out.append({"symbol": b["symbol"], "kind": "buy", "price": float(b["price"]), "text": "your buy price" +
                    (f" ({b['note']})" if b.get("note") else "")})
    return out


def crossed(line: dict, price: float) -> bool:
    return price <= line["price"] if line["kind"] in ("below", "buy") else price >= line["price"]


def check(quote_fn, today: date | None = None, raise_headsup=None) -> list[dict]:
    """Fire each crossed line once a day. quote_fn(symbol) -> price or None."""
    import json
    today = today or date.today()
    with db.connect() as conn:
        theses = db.theses(conn)
        bt = targets(conn)
    fired = []
    prices: dict[str, float | None] = {}
    for ln in lines(theses, bt):
        sym = ln["symbol"]
        if sym not in prices:
            try:
                prices[sym] = quote_fn(sym)
            except Exception:  # noqa: BLE001 - one bad quote shouldn't stop the others
                prices[sym] = None
        p = prices[sym]
        if p is None or not crossed(ln, p):
            continue
        key = f"pricealert:{sym}:{ln['kind']}:{today.isoformat()}"
        with db.connect() as conn:
            if db.get_meta(conn, key, ""):
                continue
            db.set_meta(conn, key, json.dumps({"price": p}))
        verb = {"below": "fell to", "above": "reached", "buy": "is at"}[ln["kind"]]
        title = f"{sym.replace('-USD', '')} {verb} ${p:,.2f}: {ln['text']} was ${ln['price']:,.2f}"
        body = {"below": "Time to re-read why you own it and decide.", "above": "Take some off, or raise the target.",
                "buy": "It's at the price you said you'd pay. The Hold plan's new-money list puts it first."}[ln["kind"]]
        if raise_headsup:
            raise_headsup(key, "price", 3 if ln["kind"] == "below" else 2, title, body, "", sym)
        fired.append({**ln, "now": p, "title": title})
    return fired
