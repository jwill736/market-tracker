"""What moved your portfolio today, and why.

Today's change in dollars, split by holding, biggest first, each with the news desk's most
serious story from the last day and a half, if there is one.

A move is "big" when it's more than twice the holding's usual daily move (the standard
deviation of its last 60 daily changes) and at least 3%. Big moves push to your phone once a
day each, saying whether news explains it. Research finds large moves that come with real news
tend to continue, while moves with no news tend to reverse (Chan 2003, "Stock price reaction to
news and no-news"): a reason not to act on an unexplained drop the same day.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

SIGMAS = 2.0
MIN_MOVE = 3.0          # percent
NEWS_HOURS = 36


def typical_move(closes: list[float], n: int = 60) -> float | None:
    """Standard deviation of daily % changes over the last n days."""
    tail = closes[-n - 1:]
    rets = [(b / a - 1) * 100 for a, b in zip(tail, tail[1:]) if a > 0]
    if len(rets) < 20:
        return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))


def reason(desk: dict | None, symbol: str, now: datetime) -> dict | None:
    """The most serious recent story for the symbol from the news desk (tier A, then B, then any)."""
    if not desk:
        return None
    since = (now - timedelta(hours=NEWS_HOURS)).isoformat()
    stories = [s for s in (desk.get(symbol) or {}).get("stories", []) if s.get("first", "") >= since]
    if not stories:
        return None
    s = min(stories, key=lambda s: ({"A": 0, "B": 1, "C": 2}[s["tier"]], -s["confidence"]))
    return {"title": s["title"], "event": s["event"], "tier": s["tier"], "sources": s["sources"], "action": s["action"],
            "url": (s.get("items") or [{}])[0].get("url", "")}


def today(positions: list[dict], typical: dict[str, float | None], desk: dict | None, now: datetime | None = None) -> dict:
    """positions: valued [{symbol, market_value, day_change_pct}]."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for p in positions:
        pct, v = p.get("day_change_pct"), p.get("market_value") or 0
        if pct is None or not v:
            continue
        change = v - v / (1 + pct / 100)
        sd = typical.get(p["symbol"])
        big = abs(pct) >= MIN_MOVE and (sd is None or abs(pct) >= SIGMAS * sd)
        why = reason(desk, p["symbol"], now)
        rows.append({"symbol": p["symbol"], "change": round(change, 2), "change_pct": round(pct, 2), "typical": round(sd, 2) if sd else None,
                     "big": big, "why": why,
                     "note": ("" if not big else "News explains it: large moves with real news tend to continue." if why and why["tier"] != "C"
                              else "No news found: moves without news tend to reverse. Don't act on it today.")})
    rows.sort(key=lambda r: -abs(r["change"]))
    total = sum(r["change"] for r in rows)
    ups = [r for r in rows if r["change"] > 0]
    downs = [r for r in rows if r["change"] < 0]
    headline = (f"{'Up' if total >= 0 else 'Down'} ${abs(total):,.0f} today" +
                (f": {rows[0]['symbol']} {'added' if rows[0]['change'] >= 0 else 'took off'} ${abs(rows[0]['change']):,.0f}" if rows else ""))
    return {"total": round(total, 2), "headline": headline, "rows": rows, "up": len(ups), "down": len(downs),
            "big": [r for r in rows if r["big"]]}


def push_text(r: dict) -> tuple[str, str]:
    arrow = "up" if r["change_pct"] > 0 else "down"
    title = f"{r['symbol']} {arrow} {abs(r['change_pct']):.1f}% ({'+' if r['change'] >= 0 else '-'}${abs(r['change']):,.0f})"
    if r.get("typical"):
        title += f", {abs(r['change_pct']) / r['typical']:.1f}× its usual move"
    body = (f"Likely why: {r['why']['title']} ({r['why']['sources']} source{'s' if r['why']['sources'] != 1 else ''}). " if r.get("why") else "") + r["note"]
    return title, body
