"""Temporary probe for the next idea-engine round (removed before merge)."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from market_tracker import fundamentals, http, screen
from market_tracker.providers import market, sec
from market_tracker.reading import BROWSER_UA

B = {"User-Agent": BROWSER_UA, "Accept": "application/json, text/plain, */*"}


def step(name, fn):
    t = time.monotonic()
    try:
        out = fn()
        print(f"== {name} ({time.monotonic() - t:.1f}s)\n{out}\n", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"== {name} FAILED: {type(exc).__name__}: {str(exc)[:400]}\n", flush=True)


def xom():
    lst = screen.listed()
    tm = sec.ticker_map()
    cik = tm.cik_for("XOM")
    out = [f"listed: {lst.get('XOM')}", f"cik {cik}"]
    same = [s for s, i in lst.items() if tm.cik_for(s) == cik]
    out.append(f"tickers sharing cik: {same} caps {[lst[s]['cap'] for s in same]}")
    c = int(cik)
    for tag, per in (("Assets", "CY2026Q2I"), ("Assets", "CY2025Q2I"), ("StockholdersEquity", "CY2026Q2I"), ("OperatingIncomeLoss", "CY2025")):
        out.append(f"{tag} {per}: {screen.frame(tag, per).get(c)}")
    for t in screen.NET_INCOME:
        out.append(f"{t} CY2025: {screen.frame(t, 'CY2025').get(c)}")
    out.append(f"shares {screen.frame(screen.SHARES_Q, 'CY2026Q2', 'shares').get(c)}")
    return "\n".join(out)


def capex():
    out = []
    for s in ("NEE", "AEP", "DUK", "SO", "XEL"):
        cik = sec.ticker_map().cik_for(s)
        f = sec._sec_get(fundamentals.COMPANYFACTS.format(cik=str(cik).zfill(10)), ttl=1)
        g = f["facts"].get("us-gaap", {})
        tags = [(t, len(v.get("units", {}).get("USD", []))) for t, v in g.items()
                if t.startswith("Payments") and any(w in t for w in ("Property", "Capital", "Construction", "Productive", "Utility", "Plant"))]
        q = fundamentals._best(f, fundamentals.CAPEX)
        ends = sorted(q, reverse=True)[:9]
        out.append(f"{s}: best CAPEX quarters {len(q)} latest {ends[:3]}; tags {tags}")
    return "\n".join(out)


def shorts():
    out = []
    for url in ("https://api.nasdaq.com/api/quote/AAPL/short-interest?assetClass=stocks",
                "https://api.nasdaq.com/api/quote/GME/short-interest?assetClass=stocks"):
        try:
            d = http.get(url, headers=B, ttl=0)
            rows = ((d.get("data") or {}).get("shortInterestTable") or {}).get("rows") or []
            out.append(f"nasdaq {url.split('/')[5]}: {len(rows)} rows, first {rows[:2]}")
        except Exception as exc:  # noqa: BLE001
            out.append(f"nasdaq failed {exc}")
    for url in ("https://query2.finance.yahoo.com/v10/finance/quoteSummary/GME?modules=defaultKeyStatistics",
                "https://query1.finance.yahoo.com/v7/finance/quote?symbols=GME,AAPL&fields=shortPercentOfFloat,sharesShort"):
        try:
            d = http.get(url, headers=B, ttl=0)
            out.append(f"yahoo {url[:60]}: {json.dumps(d)[:300]}")
        except Exception as exc:  # noqa: BLE001
            out.append(f"yahoo failed {str(exc)[:200]}")
    try:
        import httpx
        r = httpx.post("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest",
                       json={"limit": 3, "compareFilters": [{"compareType": "equal", "fieldName": "symbolCode", "fieldValue": "GME"}]},
                       headers={"Accept": "application/json"}, timeout=30)
        out.append(f"finra {r.status_code}: {r.text[:500]}")
    except Exception as exc:  # noqa: BLE001
        out.append(f"finra failed {exc}")
    return "\n".join(out)


def fts(q, forms, days):
    from market_tracker.config import settings
    today = date.today()
    p = {"forms": forms, "dateRange": "custom", "startdt": (today - timedelta(days=days)).isoformat(), "enddt": today.isoformat()}
    if q:
        p["q"] = q
    d = http.get("https://efts.sec.gov/LATEST/search-index", params=p, headers={"User-Agent": settings.sec_user_agent}, ttl=0)
    hits = (d.get("hits") or {}).get("hits") or []
    tot = (d.get("hits") or {}).get("total")
    return f"total {tot}; " + "; ".join(f"{h['_source'].get('file_date')} {h['_source'].get('form')} {h['_source'].get('display_names')} items={h['_source'].get('items')}" for h in hits[:12])


def history():
    h = market.get_history("AAPL", 4500)
    syms = ["MSFT", "KO", "CAT", "JPM", "XOM", "NEE", "LRCX", "ILMN", "CF", "APA", "EXPD", "JBHT", "REGN", "CTSH", "MTB", "NEM",
            "DELL", "AVGO", "BLZE", "SEG"]
    t = time.monotonic()
    with ThreadPoolExecutor(6) as ex:
        lens = list(ex.map(lambda s: len(market.get_history(s, 4500)), syms))
    return f"AAPL {len(h)} bars from {h[0].date}; 20 syms in {time.monotonic() - t:.1f}s: {lens}"


def frames_hist():
    tm = sec.ticker_map()
    known = {int(c) for c in tm.by_cik} if hasattr(tm, "by_cik") else None
    out = []
    for per in ("CY2010Q2I", "CY2012Q2I", "CY2015Q2I", "CY2019Q2I"):
        a = screen.frame("Assets", per)
        big = [c for c, v in a.items() if v >= 1e9]
        miss = len([c for c in big if known is not None and c not in known])
        out.append(f"Assets {per}: {len(a)} filers, {len(big)} with $1B+ assets, {miss} of those without a current ticker")
    for tag, per, unit, tax in ((screen.SHARES_Q, "CY2012Q2", "shares", "us-gaap"), ("EntityCommonStockSharesOutstanding", "CY2012Q2I", "shares", "dei"),
                                ("OperatingIncomeLoss", "CY2011", "USD", "us-gaap"), ("RevenueRemainingPerformanceObligation", "CY2019Q2I", "USD", "us-gaap")):
        out.append(f"{tag} {per}: {len(screen.frame(tag, per, unit, tax))}")
    out.append(f"ticker map attrs: {[a for a in dir(tm) if not a.startswith('_')]}")
    return "\n".join(out)


def ape():
    d = http.get("https://apewisdom.io/api/v1.0/filter/all-stocks/page/1", headers=B, ttl=0)
    return f"{len(d.get('results', []))} rows; top {[r.get('ticker') for r in d.get('results', [])[:8]]}"


def recap_one():
    from market_tracker import earnings
    out = []
    for s in ("NVDA", "ORCL", "FDX"):
        r = earnings.recap(s)
        out.append(f"{s}: filed {r['release']['filed']} outlook {r['outlook'].get('kind')} reaction {r.get('reaction')}")
    return "\n".join(out)


step("XOM", xom)
step("utility capex", capex)
step("short interest", shorts)
step("FTS 10-12B spin-off", lambda: fts('"spin-off"', "10-12B", 365))
step("FTS 10-12B no q", lambda: fts(None, "10-12B", 365))
step("FTS 8-K raises guidance", lambda: fts('"raises full-year" OR "raised its full-year" OR "raises guidance" OR "raising full-year" OR "raises its outlook"', "8-K", 10))
step("FTS 8-K raises (single phrase)", lambda: fts('"raises full-year"', "8-K", 20))
step("long history", history)
step("historical frames", frames_hist)
step("ApeWisdom", ape)
step("earnings recap", recap_one)
