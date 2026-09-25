"""Scorecards for public stock pickers (StockTwits).

A "call" is a post the author tagged Bullish or Bearish, naming one or two tickers. StockTwits
stores the price of each ticker at the moment of posting, so every call has an honest entry
price. Each call is scored 5 trading days later against SPY over the same days: a bullish call
is right when the stock beat SPY, a bearish one when it lagged.

Calls are saved here the first time they're seen. People delete posts that aged badly; a
scorecard built only from what's still online flatters them, so ours keeps the deleted ones.
Posts without a Bullish/Bearish tag aren't calls and aren't scored, which is why some big
accounts show few or none.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import early, http
from .providers import market
from .reading import BROWSER_UA

USER_STREAM = "https://api.stocktwits.com/api/2/streams/user/{user}.json"
SUGGESTED = "https://api.stocktwits.com/api/2/streams/suggested.json"
HEADERS = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
PAGES = 4
HORIZON = 5
MAX_SYMBOLS = 2


def calls_from_stream(data: dict) -> list[dict]:
    """Tagged calls in one page of a user's stream: [{id, user, symbol, side, created, day, price, body}]."""
    out = []
    for m in (data or {}).get("messages") or []:
        sentiment = ((m.get("entities") or {}).get("sentiment") or {}) or {}
        side = sentiment.get("basic")
        syms = [s.get("symbol") for s in m.get("symbols") or [] if s.get("symbol")]
        if side not in ("Bullish", "Bearish") or not syms or len(syms) > MAX_SYMBOLS:
            continue
        prices = {p.get("symbol"): p.get("price") for p in m.get("prices") or []}
        for sym in syms:
            try:
                price = float(prices.get(sym) or 0)
            except (TypeError, ValueError):
                price = 0.0
            if not price:
                continue
            out.append({"id": f"{m['id']}:{sym}", "user": ((m.get("user") or {}).get("username") or "").lower(),
                        "symbol": market.normalize_symbol(sym.replace(".X", "-USD") if sym.endswith(".X") else sym),
                        "side": side.lower(), "created": m.get("created_at", ""), "day": (m.get("created_at") or "")[:10],
                        "price": price, "body": (m.get("body") or "")[:280]})
    return out


def fetch_calls(user: str, get=None, pages: int = PAGES) -> tuple[dict, list[dict]]:
    """(profile, calls) from the user's recent stream."""
    get = get or http.get
    calls: list[dict] = []
    profile: dict = {}
    cursor = None
    for _ in range(pages):
        params = {"max": cursor} if cursor else {}
        data = get(USER_STREAM.format(user=user), params=params, headers=HEADERS, ttl=900)
        profile = profile or (data or {}).get("user") or {}
        calls += calls_from_stream(data)
        cur = (data or {}).get("cursor") or {}
        if not cur.get("more") or not cur.get("max"):
            break
        cursor = cur["max"]
    return profile, calls


def suggested(get=None) -> list[dict]:
    """Accounts StockTwits suggests, most followed first, for finding people to grade."""
    get = get or http.get
    data = get(SUGGESTED, headers=HEADERS, ttl=3600)
    users = {}
    for m in (data or {}).get("messages") or []:
        u = m.get("user") or {}
        if u.get("username"):
            users[u["username"].lower()] = {"username": u["username"], "name": u.get("name", ""),
                                            "followers": u.get("followers", 0), "official": bool(u.get("official"))}
    return sorted(users.values(), key=lambda u: -u["followers"])[:20]


def score(calls: list[dict], history_fn=None, horizon: int = HORIZON, now: datetime | None = None,
          quote_fn=None) -> dict:
    """Score calls: first call per ticker per day, `horizon` closes later vs SPY (bearish calls
    count as right when the stock lagged). Also the move since the call for open ones."""
    now = now or datetime.now(timezone.utc)
    firsts: dict[tuple, dict] = {}
    for c in sorted(calls, key=lambda c: c["created"]):
        firsts.setdefault((c["symbol"], c["day"], c["side"]), c)
    logged = [{"day": c["day"], "symbol": c["symbol"], "kinds": c["side"], "early": 0, "price": c["price"]}
              for c in firsts.values()]
    sc = early.scorecard(logged, horizon=horizon, history_fn=history_fn) if logged else {"rows": [], "pending": 0, "errors": []}
    by = {(r["day"], r["symbol"], r["kinds"]): r for r in sc["rows"]}
    rows = []
    for c in sorted(firsts.values(), key=lambda c: c["created"], reverse=True):
        r = by.get((c["day"], c["symbol"], c["side"]))
        sign = 1 if c["side"] == "bullish" else -1
        excess = sign * r["excess_pct"] if r else None
        since = None
        if quote_fn:
            try:
                q = quote_fn(c["symbol"])
                since = round(sign * (q.price / c["price"] - 1) * 100, 2) if q.price else None
            except (http.DataUnavailable, KeyError, ValueError):
                since = None
        rows.append({**c, "excess_pct": round(excess, 2) if excess is not None else None,
                     "right": (excess > 0) if excess is not None else None, "since_pct": since})
    done = [r for r in rows if r["excess_pct"] is not None]
    ex = sorted(r["excess_pct"] for r in done)
    med = (ex[len(ex) // 2] if len(ex) % 2 else (ex[len(ex) // 2 - 1] + ex[len(ex) // 2]) / 2) if ex else None
    return {"calls": rows, "scored": len(done), "pending": len(rows) - len(done),
            "hit_rate": (sum(1 for r in done if r["right"]) / len(done)) if done else None,
            "median_excess_pct": med, "bullish": sum(1 for r in rows if r["side"] == "bullish"),
            "bearish": sum(1 for r in rows if r["side"] == "bearish"), "horizon": horizon}
