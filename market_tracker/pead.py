"""Raised guidance, and the market agreed: the after-earnings drift.

Stocks tend to keep drifting in the direction of an earnings surprise for weeks after the
announcement (Bernard & Thomas 1989). The market's own first-day reaction is a good measure of
the surprise (Brandt, Kishore, Santa-Clara & Venkatachalam 2008). The effect has faded a lot in
large companies since the 2000s and survives mostly in smaller, less-followed ones (Martineau
2022), so treat this as a short-lived tilt, not a long-term reason to own something.

What counts here: a company whose results release (8-K item 2.02, exhibit 99.1) in the last ten
days raised its outlook, and whose stock then rose at least 3% on the first trading day and by
more than 1.5 times a normal day's move. Worth $300M or more on the weekly screen. Each one is
logged to the idea log and scored against VOO, which will say whether this still works.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from . import http

FTS = "https://efts.sec.gov/LATEST/search-index"
WINDOW_DAYS = 10
MIN_MOVE = 3.0
MIN_TIMES = 1.5
MIN_CAP = 3e8
PAGES = 4


def candidates(today: date, get=None) -> list[str]:
    """Tickers with a results release in the last WINDOW_DAYS that talks about guidance or outlook."""
    from .config import settings
    get = get or (lambda params: http.get(FTS, params=params, headers={"User-Agent": settings.sec_user_agent}, ttl=3600))
    out: list[str] = []
    for page in range(PAGES):
        d = get({"q": '"guidance" OR "outlook"', "forms": "8-K", "dateRange": "custom",
                 "startdt": (today - timedelta(days=WINDOW_DAYS)).isoformat(), "enddt": today.isoformat(), "from": page * 100})
        hits = (d.get("hits") or {}).get("hits") or []
        for h in hits:
            src = h.get("_source") or {}
            if "2.02" not in (src.get("items") or []):
                continue
            for dn in src.get("display_names") or []:
                m = re.search(r"\(([A-Z][A-Z.\-]{0,6})(?:,|\))", dn)
                if m and m.group(1) not in out:
                    out.append(m.group(1))
        if len(hits) < 100:
            break
    return out


def qualify(r: dict | None, today: date) -> dict | None:
    """The recap (earnings.recap) as a drift candidate, or None."""
    if not r or (r.get("outlook") or {}).get("direction") != "raised":
        return None
    re_ = r.get("reaction") or {}
    move, times = re_.get("move_pct"), re_.get("times_usual")
    if move is None or move < MIN_MOVE or (times is not None and times < MIN_TIMES):
        return None
    filed = r["release"]["filed"]
    if (today - date.fromisoformat(filed)).days > WINDOW_DAYS:
        return None
    line = (r["outlook"].get("lines") or [""])[0]
    return {"symbol": r["symbol"], "filed": filed, "company": r["release"].get("company"), "move_pct": move, "times_usual": times,
            "reaction_day": re_.get("day"), "outlook": line[:300], "url": r["release"].get("url"),
            "why": f"Raised its outlook on {filed}; the stock rose {move:+.1f}% the next session" + (f" ({times:.1f}x a normal day)" if times else "") + "."}


def build(today: date | None = None, lookup_fn=None, recap_fn=None, get=None, workers: int = 4) -> list[dict]:
    from . import earnings
    today = today or date.today()
    lookup_fn = lookup_fn or (lambda s: None)
    syms = [s for s in candidates(today, get) if ((lookup_fn(s) or {}).get("cap") or 0) >= MIN_CAP]
    recap_fn = recap_fn or earnings.recap

    def one(sym):
        try:
            return qualify(recap_fn(sym), today)
        except (http.DataUnavailable, KeyError, ValueError, TypeError):
            return None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        found = [x for x in ex.map(one, syms) if x]
    for x in found:
        lk = lookup_fn(x["symbol"]) or {}
        x.update(cap=lk.get("cap"), score=lk.get("score"), sector=lk.get("sector"))
    return sorted(found, key=lambda x: (-(x["times_usual"] or 0), -x["move_pct"]))
