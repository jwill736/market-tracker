"""Spin-offs: new companies split out of a parent, from their SEC registrations (Form 10-12B).

Spin-offs have historically beaten the market in their first two or three years (Cusatis, Miles
& Woolridge 1993; McConnell & Ovtchinnikov 2004). The usual explanation: many of the parent's
shareholders, index funds included, never chose the new company and sell it in the first weeks,
pushing the price down, while its managers now own its stock and answer for its results. More
recent studies find a smaller edge, so every spin-off the app logs is scored against VOO.

Form 10-12B is also used by a few companies moving to a stock exchange for other reasons, so only
registrations that talk about a spin-off count. A registration comes months before trading
starts; the list shows both the ones coming and the ones already trading, with how they've done
since their first day against SPY.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from . import http

FTS = "https://efts.sec.gov/LATEST/search-index"
LOOKBACK_DAYS = 540
LOG_FROM_DAYS, LOG_TO_DAYS = 20, 250          # log a spin-off as an idea after its first month of trading, within its first year
PAGES = 3


def _clean(name: str) -> tuple[str, str | None, str]:
    """('Solstice Advanced Materials Inc.', 'SOLS', '0002064953') from an EDGAR display name."""
    cik = re.search(r"\(CIK (\d+)\)", name)
    tick = re.search(r"\(([A-Z][A-Z.\-]{0,6})\)", name)
    base = re.sub(r"\s*\((?:CIK \d+|[A-Z][A-Z.\-]{0,6})\)", "", name).strip()
    return base, tick.group(1) if tick else None, cik.group(1) if cik else ""


def registrations(today: date, get=None) -> list[dict]:
    """[{cik, name, ticker, first_filed, last_filed, filings}] for spin-off registrations in the lookback."""
    from .config import settings
    get = get or (lambda params: http.get(FTS, params=params, headers={"User-Agent": settings.sec_user_agent}, ttl=6 * 3600))
    by_cik: dict[str, dict] = {}
    for page in range(PAGES):
        d = get({"q": '"spin-off" OR "spinoff" OR "separation and distribution"', "forms": "10-12B,10-12B/A", "dateRange": "custom",
                 "startdt": (today - timedelta(days=LOOKBACK_DAYS)).isoformat(), "enddt": today.isoformat(), "from": page * 100})
        hits = (d.get("hits") or {}).get("hits") or []
        for h in hits:
            src = h.get("_source") or {}
            filed = src.get("file_date") or ""
            for dn in (src.get("display_names") or [])[:1]:
                name, tick, cik = _clean(dn)
                if not cik:
                    continue
                e = by_cik.setdefault(cik, {"cik": cik, "name": name, "ticker": None, "first_filed": filed, "last_filed": filed, "filings": 0})
                e["filings"] += 1
                e["first_filed"] = min(e["first_filed"], filed)
                if filed >= e["last_filed"]:
                    e["last_filed"], e["name"] = filed, name
                e["ticker"] = e["ticker"] or tick
        if len(hits) < 100:
            break
    return sorted(by_cik.values(), key=lambda e: e["last_filed"], reverse=True)


def status(reg: dict, today: date, ticker_fn=None, history_fn=None, bench_fn=None) -> dict:
    """Adds ticker (from the filing or SEC's ticker list), and, once it trades: first day, days trading,
    return since the first close and SPY's over the same days."""
    out = dict(reg)
    if not out.get("ticker") and ticker_fn:
        out["ticker"] = ticker_fn(reg["cik"])
    out.update(trading_since=None, days_trading=0, return_pct=None, spy_pct=None, stage="registered")
    if not out["ticker"] or not history_fn:
        return out
    try:
        bars = history_fn(out["ticker"])
    except (http.DataUnavailable, KeyError, ValueError):
        return out
    bars = [b for b in bars if b[0] >= reg["first_filed"]]
    if len(bars) < 2:
        return out
    first_day, first = bars[0]
    out.update(trading_since=first_day, days_trading=len(bars), return_pct=round((bars[-1][1] / first - 1) * 100, 1), stage="trading")
    if bench_fn:
        try:
            sp = [b for b in bench_fn() if b[0] >= first_day]
            if len(sp) >= 2:
                out["spy_pct"] = round((sp[-1][1] / sp[0][1] - 1) * 100, 1)
        except (http.DataUnavailable, KeyError, ValueError):
            pass
    return out


def build(today: date | None = None, get=None, ticker_fn=None, history_fn=None, bench_fn=None, lookup_fn=None) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor

    from .providers import market, sec
    today = today or date.today()
    if ticker_fn is None:
        tm = sec.ticker_map()
        by_cik: dict[str, str] = {}
        for t, row in tm.by_ticker.items():
            by_cik.setdefault(str(row["cik_str"]).zfill(10), t)
        ticker_fn = lambda cik: by_cik.get(str(cik).zfill(10))  # noqa: E731
    history_fn = history_fn or (lambda s: [(b.date, b.close) for b in market.get_history(s, 400)])
    bench_fn = bench_fn or (lambda: [(b.date, b.close) for b in market.get_history("SPY", 400)])
    regs = registrations(today, get)
    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(lambda r: status(r, today, ticker_fn, history_fn, bench_fn), regs))
    for r in rows:
        lk = (lookup_fn(r["ticker"]) if lookup_fn and r.get("ticker") else None) or {}
        r.update(score=lk.get("score"), cap=lk.get("cap"), sector=lk.get("sector"))
        r["loggable"] = r["stage"] == "trading" and LOG_FROM_DAYS <= r["days_trading"] <= LOG_TO_DAYS
    trading = sorted((r for r in rows if r["stage"] == "trading"), key=lambda r: r["days_trading"])        # newest listings first
    coming = sorted((r for r in rows if r["stage"] != "trading"), key=lambda r: r["last_filed"], reverse=True)
    return trading + coming
