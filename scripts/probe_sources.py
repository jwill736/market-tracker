"""Temporary probe (removed before merge): data sources for the idea engine."""
import json
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, ".")
from market_tracker import http  # noqa: E402
from market_tracker.providers import sec  # noqa: E402


def show(title, fn):
    t = time.time()
    try:
        out = fn()
        print(f"OK   {title} ({time.time() - t:.1f}s): {out}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {title}: {type(exc).__name__}: {str(exc)[:300]}")


FR = "https://data.sec.gov/api/xbrl/frames/{tax}/{tag}/{unit}/{period}.json"


def frame(tax, tag, unit, period):
    d = sec._sec_get(FR.format(tax=tax, tag=tag, unit=unit, period=period), ttl=0)
    rows = d.get("data", [])
    s = rows[0] if rows else {}
    return f"{len(rows)} rows, keys {sorted(s)}, e.g. {json.dumps(s)[:200]}"


for tax, tag, unit, per in [("us-gaap", "GrossProfit", "USD", "CY2025Q2"), ("us-gaap", "Assets", "USD", "CY2025Q2I"),
                            ("us-gaap", "RevenueRemainingPerformanceObligation", "USD", "CY2025Q2I"),
                            ("us-gaap", "NetIncomeLoss", "USD", "CY2025Q2"), ("us-gaap", "StockholdersEquity", "USD", "CY2025Q2I"),
                            ("dei", "EntityCommonStockSharesOutstanding", "shares", "CY2025Q2I"),
                            ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD", "CY2025Q2"),
                            ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment", "USD", "CY2025Q2"),
                            ("us-gaap", "GrossProfit", "USD", "CY2026Q2")]:
    show(f"frames {tag} {per}", lambda: frame(tax, tag, unit, per))


def fred():
    txt = http.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": "DGS10,BAMLH0A0HYM2,SAHMREALTIME,NFCI,T10Y3M,ICSA,DCOILBRENTEU,NEWORDER"},
                   ttl=0, as_json=False, headers={"User-Agent": "Plumbline/1.0 (https://github.com/jwill736/market-tracker)"})
    lines = txt.strip().splitlines()
    return f"{len(lines)} lines; header {lines[0]}; last {lines[-1]}; {lines[-40]}"


show("FRED multi-series CSV", fred)


def usasp():
    import httpx
    today = date.today()
    body = {"filters": {"recipient_search_text": ["LOCKHEED MARTIN"], "award_type_codes": ["A", "B", "C", "D"],
                        "time_period": [{"start_date": (today - timedelta(days=365)).isoformat(), "end_date": today.isoformat()}]},
            "fields": ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Start Date", "Description"],
            "sort": "Award Amount", "order": "desc", "limit": 5, "page": 1}
    r = httpx.post("https://api.usaspending.gov/api/v2/search/spending_by_award/", json=body, timeout=60)
    d = r.json()
    return f"{r.status_code}; {len(d.get('results', []))} results; {json.dumps(d.get('results', [])[:2])[:400]}; meta {json.dumps(d.get('page_metadata'))[:200]}"


show("USAspending awards (Lockheed, 12 months)", usasp)


def usasp_total():
    import httpx
    today = date.today()
    body = {"filters": {"recipient_search_text": ["LOCKHEED MARTIN"], "award_type_codes": ["A", "B", "C", "D"],
                        "time_period": [{"start_date": (today - timedelta(days=365)).isoformat(), "end_date": today.isoformat()}]},
            "category": "recipient", "limit": 5, "page": 1}
    r = httpx.post("https://api.usaspending.gov/api/v2/search/spending_by_category/recipient/", json=body, timeout=60)
    return f"{r.status_code}; {json.dumps(r.json().get('results', [])[:3])[:500]}"


show("USAspending by recipient total", usasp_total)

FTS = "https://efts.sec.gov/LATEST/search-index"


def fts(q, forms, days=180):
    d = sec._sec_get(f"{FTS}?q={q}&forms={forms}&dateRange=custom&startdt={(date.today() - timedelta(days=days)).isoformat()}&enddt={date.today().isoformat()}", ttl=0)
    hits = d.get("hits", {})
    items = hits.get("hits", [])
    return f"total {hits.get('total')}; e.g. {[ (h['_source'].get('display_names'), h['_source'].get('form'), h['_source'].get('file_date')) for h in items[:3]]}"


show("FTS new funds 'artificial intelligence'", lambda: fts('%22artificial%20intelligence%22', "N-1A,485APOS"))
show("FTS new funds 'nuclear'", lambda: fts('%22nuclear%22', "N-1A,485APOS"))
show("FTS customer mention 'Microsoft' of our revenue", lambda: fts('%22Microsoft%22%20%22of%20our%20revenue%22', "10-K", 400))
show("FTS customer 'accounted for' 'Apple'", lambda: fts('%22Apple%22%20%22accounted%20for%22', "10-K", 400))


def apewisdom():
    d = http.get("https://apewisdom.io/api/v1.0/filter/all-stocks/page/1", ttl=0)
    r = d.get("results", [])
    return f"{len(r)} rows; keys {sorted(r[0]) if r else None}; top {[ (x.get('ticker'), x.get('mentions'), x.get('mentions_24h_ago')) for x in r[:5]]}"


show("ApeWisdom Reddit mentions", apewisdom)
show("SEC submissions SIC (NVDA)", lambda: {k: sec._sec_get(sec.SUBMISSIONS.format(cik="0001045810"), ttl=0).get(k) for k in ("sic", "sicDescription", "tickers")})
