"""Follow the money: who is spending, who gets paid, and whether the price already knows.

Three parts, each labelled with how much evidence is behind it:

1. Spending waves (context, not a buy signal). Capital spending over the last four quarters for
   the big spenders (cloud and AI data centers, electric utilities, chip makers), from their own
   cash-flow statements, against a year earlier. Next to each wave, the listed companies that
   supply it, by category (a hand-picked list of well-known names), with how far their price has
   already run and the weekly screen's grade. No study shows suppliers to heavy spenders beat the
   market in general; the point is to see where money is going and what's already priced in.

2. Government contracts (weak to moderate). Federal contract money obligated to a company over
   the last 12 months (USAspending.gov) against its revenue. Most of the price reaction happens
   the day an award is announced; this is a check on how much of a business depends on it.

3. Suppliers that haven't caught up (weak). Companies whose 10-K names a big customer ("Apple
   accounted for 23% of our revenue"), when the customer's stock has moved 10%+ in a month and
   the supplier's hasn't followed. The effect was strong in the 1980s-2000s (Cohen & Frazzini
   2008) and has mostly faded since, surviving in small, thinly covered companies, so only
   suppliers under $10 billion are shown, and every one is logged and scored against VOO.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from . import http

WAVES: dict[str, dict] = {
    "Cloud and AI data centers": {
        "spenders": ["AMZN", "MSFT", "GOOGL", "META", "ORCL"],
        "suppliers": {"AI chips": ["NVDA", "AVGO", "AMD", "MRVL"], "Memory": ["MU"], "Networking and connectors": ["ANET", "APH", "CSCO"],
                      "Power and cooling": ["VRT", "ETN", "GEV"], "Construction and electrical": ["PWR", "EME", "FIX"],
                      "Chip equipment": ["AMAT", "LRCX", "KLAC", "TER"]}},
    "Electric utilities and the grid": {
        "spenders": ["NEE", "DUK", "SO", "AEP", "D", "EXC", "XEL"],
        "suppliers": {"Turbines and grid equipment": ["GEV", "ETN", "HUBB", "POWL"], "Grid construction": ["PWR", "MYRG", "MTZ"],
                      "Nuclear fuel and services": ["CCJ", "BWXT", "LEU"]}},
    "Chip factories": {
        "spenders": ["INTC", "MU", "TXN", "GFS"],
        "suppliers": {"Chip equipment": ["AMAT", "LRCX", "KLAC", "TER", "ONTO"], "Materials and gases": ["LIN", "APD", "ENTG"]}},
}
CUSTOMERS = {"AAPL": "Apple", "MSFT": "Microsoft", "AMZN": "Amazon", "GOOGL": "Google", "META": "Meta", "NVDA": "NVIDIA", "TSLA": "Tesla",
             "WMT": "Walmart", "BA": "Boeing", "LMT": "Lockheed Martin"}
FTS = "https://efts.sec.gov/LATEST/search-index"
USASPENDING = "https://api.usaspending.gov/api/v2/search/spending_by_category/recipient/"
LAG_MOVE = 0.10
SUPPLIER_MAX_CAP = 1e10
CACHE_SECONDS = 24 * 3600
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


# ------------------------------------------------------------------ 1. spending waves

def capex_ttm(facts: dict) -> tuple[float | None, float | None, str | None]:
    """(capital spending over the last four quarters, the four before, latest quarter end)."""
    from . import fundamentals
    q = fundamentals._best(facts, fundamentals.CAPEX)
    ends = sorted(q, reverse=True)
    if len(ends) < 8 or fundamentals._days(ends[7], ends[0]) > 700:
        return None, None, None
    return sum(q[e] for e in ends[:4]), sum(q[e] for e in ends[4:8]), ends[0]


def waves(facts_fn, lookup_fn, price_fn=None) -> list[dict]:
    """facts_fn(symbol) -> companyfacts; lookup_fn(symbol) -> weekly screen row or None."""
    syms = sorted({s for w in WAVES.values() for s in w["spenders"]})

    def one(s):
        try:
            return s, capex_ttm(facts_fn(s))
        except (http.DataUnavailable, KeyError, TypeError, ValueError):
            return s, (None, None, None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        cap = dict(pool.map(one, syms))
    out = []
    for name, w in WAVES.items():
        rows = [{"symbol": s, "capex": cap[s][0], "capex_prior": cap[s][1], "quarter": cap[s][2],
                 "growth": round(cap[s][0] / cap[s][1] - 1, 3) if cap[s][0] and cap[s][1] else None} for s in w["spenders"]]
        now = sum(r["capex"] for r in rows if r["capex"] and r["capex_prior"])
        before = sum(r["capex_prior"] for r in rows if r["capex"] and r["capex_prior"])
        cats = []
        for cat, tickers in w["suppliers"].items():
            cats.append({"category": cat, "companies": [dict(symbol=t, **_supplier_view(lookup_fn(t))) for t in tickers]})
        out.append({"wave": name, "spenders": rows, "total": now or None, "total_prior": before or None,
                    "growth": round(now / before - 1, 3) if now and before else None, "suppliers": cats})
    return out


def _supplier_view(row: dict | None) -> dict:
    if not row:
        return {"score": None, "return_12m_pct": None, "above_200d": None, "priced_in": None}
    hot = (row.get("return_12m_pct") or 0) >= 90 or (row.get("above_200d") or 0) >= 0.3
    return {"score": row.get("score"), "return_12m_pct": row.get("return_12m_pct"), "above_200d": row.get("above_200d"),
            "priced_in": "Already ran hard" if hot else "Not stretched"}


# ------------------------------------------------------------------ 2. government contracts

def _clean_name(name: str) -> str:
    n = re.sub(r"[.,]", " ", name.upper())
    n = re.sub(r"\b(INC|CORP|CORPORATION|CO|COMPANY|HOLDINGS?|LTD|PLC|LLC|GROUP|THE|CLASS [A-C]|/DE/|/MD/)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def contracts(company: str, revenue_ttm: float | None, today: date | None = None, post=None) -> dict:
    """Federal contract obligations to entities matching the company's name over the last year."""
    today = today or date.today()
    name = _clean_name(company)
    if len(name) < 3:
        return {"name": name, "amount": None}
    body = {"filters": {"recipient_search_text": [name], "award_type_codes": ["A", "B", "C", "D"],
                        "time_period": [{"start_date": (today - timedelta(days=365)).isoformat(), "end_date": today.isoformat()}]},
            "category": "recipient", "limit": 25, "page": 1}
    if post is None:
        import httpx

        def post(url, json):
            try:
                r = httpx.post(url, json=json, timeout=60)
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise http.DataUnavailable(f"USAspending: {exc}") from exc
    d = post(USASPENDING, body)
    first = name.split()[0]
    matched = [r for r in d.get("results", []) if _clean_name(r.get("name") or "").startswith(first)]
    amount = sum(float(r.get("amount") or 0) for r in matched)
    share = amount / revenue_ttm if revenue_ttm and amount else None
    return {"name": name, "amount": round(amount, 2), "entities": len(matched), "revenue_ttm": revenue_ttm,
            "share": round(share, 4) if share is not None else None,
            "material": share is not None and share >= 0.05,
            "text": (f"${amount / 1e9:,.1f}B of federal contracts obligated in 12 months" + (f", {share:.0%} of revenue" if share else ""))
            if amount else "No federal contract obligations found under this name."}


# ------------------------------------------------------------------ 3. suppliers that haven't caught up

def suppliers_of(customer: str, today: date | None = None, get=None) -> list[str]:
    """Tickers of companies whose latest 10-Ks say this customer accounted for part of their revenue."""
    from .config import settings
    today = today or date.today()
    get = get or (lambda url, params: http.get(url, params=params, headers={"User-Agent": settings.sec_user_agent}, ttl=0))
    out: list[str] = []
    for phrase in (f'"{customer} accounted for"', f'"sales to {customer}"'):
        d = get(FTS, {"q": phrase, "forms": "10-K", "dateRange": "custom", "startdt": (today - timedelta(days=400)).isoformat(),
                      "enddt": today.isoformat()})
        for h in ((d.get("hits") or {}).get("hits") or []):
            for dn in h.get("_source", {}).get("display_names") or []:
                m = re.search(r"\(([A-Z][A-Z.\-]{0,6})(?:,|\))", dn)
                if m and m.group(1) not in out:
                    out.append(m.group(1))
    return out


def month_return(bars: list[float]) -> float | None:
    return bars[-1] / bars[-22] - 1 if len(bars) >= 22 and bars[-22] > 0 else None


def lagging(customers: dict[str, str], suppliers_fn, closes_fn, lookup_fn) -> list[dict]:
    """Customers that moved 10%+ in a month, and their small suppliers that moved less than a third as much."""
    out = []
    for tkr, name in customers.items():
        try:
            cm = month_return(closes_fn(tkr))
        except Exception:  # noqa: BLE001
            continue
        if cm is None or abs(cm) < LAG_MOVE:
            continue
        for s in suppliers_fn(name):
            if s == tkr:
                continue
            row = lookup_fn(s) or {}
            if not row.get("cap") or row["cap"] >= SUPPLIER_MAX_CAP:
                continue
            try:
                sm = month_return(closes_fn(s))
            except Exception:  # noqa: BLE001
                continue
            if sm is None or (cm > 0 and sm >= cm / 3) or (cm < 0 and sm <= cm / 3):
                continue
            out.append({"symbol": s, "customer": tkr, "customer_name": name, "customer_move": round(cm, 3), "supplier_move": round(sm, 3),
                        "cap": row["cap"], "score": row.get("score"),
                        "why": f"{name} {cm:+.0%} in a month; {s}, which names {name} as a customer in its 10-K, {sm:+.0%}."})
    return sorted(out, key=lambda r: -abs(r["customer_move"] - r["supplier_move"]))
