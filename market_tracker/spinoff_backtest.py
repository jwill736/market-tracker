"""Have spin-offs beaten the market? A replay of every spin-off registration since 2005.

The spin-off list rests on research from the 1990s and 2000s (Cusatis, Miles & Woolridge 1993;
McConnell & Ovtchinnikov 2004). This checks whether it held up afterwards:

- every company that filed a Form 10-12B mentioning a spin-off, found with SEC full-text search
  year by year;
- first trading day: the first price after the registration (a company that traded before it
  registered was moving exchanges, not being spun off, and is dropped);
- returns from the first close, and from the 20th trading day (after most of the forced index
  selling), over 6, 12 and 24 months, against SPY over the same days.

Spin-offs that were later bought out or went bust have no ticker today and can't be priced, so
they're missing. Buyouts usually come at a premium, so unlike the screen this gap cuts both
ways; the count of missing ones is reported next to the result.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

from . import http

FILE = "spinoff_backtest.json"
FIRST_YEAR = 2005
HOLD = {"6m": 126, "12m": 252, "24m": 504}
LATE_START = 20


def registrations_by_year(year: int, get=None) -> list[dict]:
    from .spinoffs import registrations
    end = date(year, 12, 31)
    rows = registrations(end, get, days=366, pages=10)
    return [r for r in rows if r["first_filed"][:4] == str(year)]


def returns(bars: list[tuple[str, float]], spy: list[tuple[str, float]], start_i: int) -> dict:
    """Returns from bars[start_i] over each horizon, and SPY's from the same date."""
    import bisect
    sd = [d for d, _ in spy]
    out = {}
    if start_i >= len(bars):
        return out
    d0, p0 = bars[start_i]
    j0 = bisect.bisect_left(sd, d0)
    for h, n in HOLD.items():
        if start_i + n < len(bars) and j0 + n < len(spy) and p0 > 0:
            out[h] = bars[start_i + n][1] / p0 - 1
            out[f"{h}_spy"] = spy[j0 + n][1] / spy[j0][1] - 1
    return out


def stats(rows: list[dict], key: str, h: str) -> dict | None:
    edges = [r[key][h] - r[key][f"{h}_spy"] for r in rows if h in (r.get(key) or {})]
    n = len(edges)
    if n < 10:
        return None
    mean = sum(edges) / n
    sd = math.sqrt(sum((e - mean) ** 2 for e in edges) / (n - 1))
    med = sorted(edges)[n // 2]
    return {"spinoffs": n, "avg_edge": round(mean * 100, 1), "median_edge": round(med * 100, 1),
            "beat_pct": round(sum(e > 0 for e in edges) / n * 100), "t": round(mean / (sd / math.sqrt(n)), 2) if sd else None}


def summarize(rows: list[dict]) -> dict:
    out = {}
    for key, label in (("from_first", "Bought at the first close"), ("from_day20", "Bought after 20 trading days")):
        out[key] = {"label": label, **{h: stats(rows, key, h) for h in HOLD}}
    recent = [r for r in rows if r["trading_since"] >= "2015"]
    out["since_2015"] = {"label": "Since 2015, bought after 20 days", **{h: stats(recent, "from_day20", h) for h in HOLD}}
    return out


def verdict(summary: dict, missing: int, found: int) -> str:
    s = summary["from_day20"].get("12m")
    if not s:
        return "Not enough spin-offs with a year of prices to judge."
    line = (f"Spin-offs bought after their first 20 trading days: {s['avg_edge']:+.1f} points against SPY over 12 months on average "
            f"(median {s['median_edge']:+.1f}), ahead in {s['beat_pct']}% of {s['spinoffs']} cases"
            + (f", t = {s['t']:.1f}" if s["t"] is not None else "") + ".")
    r = summary["since_2015"].get("12m")
    if r:
        line += f" Since 2015: {r['avg_edge']:+.1f} (median {r['median_edge']:+.1f})."
    line += (f" {missing} of {found} registrants have no ticker today (bought out, merged or failed) and are missing; "
             "buyouts usually pay a premium, so this gap cuts both ways.")
    return line


def run(get=None, history_fn=None, ticker_fn=None, first_year: int = FIRST_YEAR, log=print) -> dict:
    from .providers import market, sec
    if ticker_fn is None:
        tm = sec.ticker_map()
        by_cik: dict[str, str] = {}
        for t, row in tm.by_ticker.items():
            by_cik.setdefault(str(row["cik_str"]).zfill(10), t)
        ticker_fn = lambda cik: by_cik.get(str(cik).zfill(10))  # noqa: E731
    history_fn = history_fn or (lambda s: [(b.date, b.close) for b in market.parse_yahoo_history(
        market._yahoo_chart(s, "", "1d", ttl=0, period_days=(date.today() - date(first_year - 1, 1, 1)).days))])
    regs: dict[str, dict] = {}
    for y in range(first_year, date.today().year + 1):
        try:
            got = registrations_by_year(y, get)
        except http.DataUnavailable as exc:
            log(f"  {y}: {exc}")
            continue
        for r in got:
            regs.setdefault(r["cik"], r)
        log(f"  {y}: {len(got)} spin-off registrants")
    spy = history_fn("SPY")
    rows, missing, moved = [], 0, 0
    for r in regs.values():
        t = r.get("ticker") or ticker_fn(r["cik"])
        if not t:
            missing += 1
            continue
        try:
            bars = history_fn(t)
        except (http.DataUnavailable, KeyError, ValueError, TypeError):
            missing += 1
            continue
        if len(bars) < 30:
            missing += 1
            continue
        if bars[0][0] <= r["first_filed"]:
            moved += 1                  # traded before registering: an exchange move
            continue
        rows.append({"ticker": t, "name": r["name"], "first_filed": r["first_filed"], "trading_since": bars[0][0],
                     "from_first": returns(bars, spy, 0), "from_day20": returns(bars, spy, LATE_START)})
    summary = summarize(rows)
    log(f"{len(regs)} registrants: {len(rows)} priced, {missing} without a ticker or prices, {moved} exchange moves dropped")
    return {"as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"), "first_year": first_year, "registrants": len(regs),
            "priced": len(rows), "missing": missing, "exchange_moves": moved, "summary": summary,
            "verdict": verdict(summary, missing, len(regs)),
            "cases": sorted(({"ticker": r["ticker"], "name": r["name"][:60], "since": r["trading_since"],
                              "12m_edge": round((r["from_day20"]["12m"] - r["from_day20"]["12m_spy"]) * 100, 1) if "12m" in r["from_day20"] else None}
                             for r in rows), key=lambda x: x["since"], reverse=True)[:80]}


def load(get=None) -> dict | None:
    from .pulse import DATA_URL
    try:
        return (get or (lambda u: http.get(u, ttl=6 * 3600)))(f"{DATA_URL}/{FILE}")
    except http.DataUnavailable:
        return None
