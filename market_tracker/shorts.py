"""Short interest: how much of a company's stock is sold short, from FINRA's twice-monthly reports.

Heavily shorted stocks have tended to do worse afterwards: short sellers are mostly informed
professionals (Asquith, Pathak & Ritter 2005; Boehmer, Jones & Zhang 2008). The same stocks can
also jump briefly when shorts rush to buy back (a squeeze), which is what the chatter crowd is
often chasing. So a high reading is a warning on a sleeper and a caution on a chatter name.

Share of stock sold short is measured against shares outstanding (price and market value from
the weekly screen), not the smaller "float", so it reads a little lower than sites that quote
short interest as a share of float.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

from . import http

FINRA = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
HEAVY_PCT, HEAVY_DAYS = 0.15, 10.0
ELEVATED_PCT, ELEVATED_DAYS = 0.08, 6.0
BATCH = 50
LOOKBACK_DAYS = 45
CACHE_SECONDS = 12 * 3600
_cache: dict[str, tuple[float, dict | None]] = {}


def _post(url: str, body: dict) -> list[dict]:
    import httpx
    try:
        r = httpx.post(url, json=body, headers={"Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        return r.json() if r.content else []
    except (httpx.HTTPError, ValueError) as exc:
        raise http.DataUnavailable(f"FINRA short interest: {exc}") from exc


def latest(symbols: list[str], post=None, now_date: date | None = None) -> dict[str, dict]:
    """{symbol: {settlement, short, days_to_cover, avg_volume}} for the latest report of each."""
    post = post or _post
    want = sorted({s.upper() for s in symbols if s})
    out: dict[str, dict] = {}
    now = time.time()
    todo = []
    for s in want:
        hit = _cache.get(s)
        if hit and now - hit[0] < CACHE_SECONDS:
            if hit[1]:
                out[s] = hit[1]
        else:
            todo.append(s)
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        # FINRA refuses sorting on this dataset and returns the oldest reports first, so ask for the last
        # 45 days (three or four twice-monthly reports) and keep the latest per symbol.
        today = (now_date or date.today())
        rows = post(FINRA, {"limit": len(chunk) * 5,
                            "domainFilters": [{"fieldName": "symbolCode", "values": [s.replace("-", ".") for s in chunk]}],
                            "dateRangeFilters": [{"fieldName": "settlementDate", "startDate": (today - timedelta(days=LOOKBACK_DAYS)).isoformat(),
                                                  "endDate": today.isoformat()}]})
        got: dict[str, dict] = {}
        for r in sorted(rows, key=lambda r: r.get("settlementDate") or "", reverse=True):
            sym = (r.get("symbolCode") or "").upper().replace(".", "-")
            if sym in got or not r.get("settlementDate"):
                continue
            got[sym] = {"settlement": r["settlementDate"], "short": float(r.get("currentShortPositionQuantity") or 0),
                        "days_to_cover": float(r["daysToCoverQuantity"]) if r.get("daysToCoverQuantity") is not None else None,
                        "avg_volume": float(r.get("averageDailyVolumeQuantity") or 0)}
        for s in chunk:
            _cache[s] = (now, got.get(s))
            if s in got:
                out[s] = got[s]
    return out


def assess(row: dict | None, cap: float | None, price: float | None) -> dict | None:
    """The reading as a level (heavy / elevated / normal) with a sentence, or None without data."""
    if not row:
        return None
    shares = cap / price if cap and price else None
    pct = row["short"] / shares if shares else None
    dtc = row.get("days_to_cover")
    heavy = (pct is not None and pct >= HEAVY_PCT) or (dtc is not None and dtc >= HEAVY_DAYS)
    elevated = (pct is not None and pct >= ELEVATED_PCT) or (dtc is not None and dtc >= ELEVATED_DAYS)
    level = "heavy" if heavy else "elevated" if elevated else "normal"
    what = (f"{pct:.0%} of shares sold short" if pct is not None else f"{row['short']:,.0f} shares sold short")
    what += f", {dtc:.1f} days of trading to cover" if dtc is not None else ""
    text = {"heavy": f"Heavily shorted: {what} ({row['settlement']}). Heavily shorted stocks have tended to lag; they can also squeeze up briefly.",
            "elevated": f"Short sellers are active: {what} ({row['settlement']}).",
            "normal": f"Short interest is ordinary: {what} ({row['settlement']})."}[level]
    return {"level": level, "pct": round(pct, 4) if pct is not None else None, "days_to_cover": dtc, "settlement": row["settlement"], "text": text}


def for_symbols(symbols: list[str], lookup_fn, post=None) -> dict[str, dict]:
    """{symbol: assess(...)} using the weekly screen's market value and price for shares outstanding."""
    try:
        rows = latest(symbols, post)
    except http.DataUnavailable:
        return {}
    out = {}
    for s in symbols:
        lk = lookup_fn(s) or {}
        a = assess(rows.get(s.upper()), lk.get("cap"), lk.get("price"))
        if a:
            out[s] = a
    return out
