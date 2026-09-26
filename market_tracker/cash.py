"""Cash waiting in your accounts: how much, for how long, and what it's not earning.

Uninvested cash is the quiet cost of a buy-and-hold plan: a deposit that never got invested, a
dividend that piled up, a sale's proceeds waiting for a decision. Each account's cash is kept
here (Coinbase's USD and USDC fill in from its sync, Robinhood crypto's buying power from its
API; the rest you type), with the date it last changed. Anything over $100 sitting two weeks
or more is flagged with what it would earn in a Treasury-bill money-market fund, at today's
13-week T-bill yield (Yahoo's ^IRX), and a pointer to the new-money planner for where to put it.
"""

from __future__ import annotations

import json
from datetime import date

IDLE_MIN = 100.0
IDLE_DAYS = 14
FALLBACK_YIELD = 0.04        # used, and said so, when the T-bill yield can't be fetched


def load(conn) -> dict[str, dict]:
    from . import db
    try:
        return json.loads(db.get_meta(conn, "cash_accounts", "{}") or "{}")
    except ValueError:
        return {}


def save(conn, account: str, amount: float, apy: float | None, today: date, source: str = "typed") -> dict:
    """Set one account's cash. The 'since' date moves only when the amount changes."""
    from . import db
    accts = load(conn)
    cur = accts.get(account) or {}
    since = cur.get("since") if cur and abs(cur.get("amount", 0.0) - amount) < 0.005 else today.isoformat()
    accts[account] = {"amount": round(amount, 2), "apy": cur.get("apy", 0.0) if apy is None else apy, "since": since,
                      "source": source}
    if amount <= 0.005:
        accts.pop(account)
    db.set_meta(conn, "cash_accounts", json.dumps(accts))
    db.set_meta(conn, "cash", str(round(sum(a["amount"] for a in accts.values()), 2)))
    return accts


def tbill_yield(quote_fn=None) -> tuple[float, bool]:
    """(annual yield as a fraction, live?) from the 13-week T-bill index."""
    from . import http
    from .providers import market
    try:
        q = (quote_fn or market.get_live_quote)("^IRX")
        y = float(q.price) / 100
        if 0 < y < 0.2:
            return y, True
    except (http.DataUnavailable, AttributeError, TypeError, ValueError):
        pass
    return FALLBACK_YIELD, False


def view(accts: dict[str, dict], today: date, yield_now: float, live: bool) -> dict:
    rows, lost = [], 0.0
    for name, a in sorted(accts.items(), key=lambda kv: -kv[1]["amount"]):
        days = (today - date.fromisoformat(a["since"])).days if a.get("since") else 0
        gap = max(0.0, yield_now - (a.get("apy") or 0) / 100)
        per_year = a["amount"] * gap
        idle = a["amount"] >= IDLE_MIN and days >= IDLE_DAYS and gap > 0.005
        if idle:
            lost += per_year
        rows.append({"account": name, "amount": a["amount"], "apy": a.get("apy") or 0.0, "since": a.get("since"), "days": days,
                     "source": a.get("source", "typed"), "missed_per_year": round(per_year, 2), "idle": idle})
    total = sum(r["amount"] for r in rows)
    return {"accounts": rows, "total": round(total, 2), "yield": round(yield_now * 100, 2), "yield_live": live,
            "missed_per_year": round(lost, 2),
            "note": (f"${sum(r['amount'] for r in rows if r['idle']):,.0f} has sat uninvested for two weeks or more: about "
                     f"${lost:,.0f} a year at {yield_now:.2%}. The hold plan's new-money planner says where it would go.")
            if lost >= 1 else ""}
