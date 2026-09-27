"""Temporary probe (removed before merge): FINRA query shapes, raised-guidance diagnostics, spin-offs."""
import json
import time
from datetime import date, timedelta

import httpx

URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
today = date.today()
dom = [{"fieldName": "symbolCode", "values": ["AAPL", "GME", "XOM"]}]
rng = [{"fieldName": "settlementDate", "startDate": (today - timedelta(days=45)).isoformat(), "endDate": today.isoformat()}]
for name, body in [("domain only", {"limit": 10, "domainFilters": dom}),
                   ("domain + sort", {"limit": 10, "domainFilters": dom, "sortFields": ["-settlementDate"]}),
                   ("compare + sort", {"limit": 5, "compareFilters": [{"compareType": "equal", "fieldName": "symbolCode", "fieldValue": "GME"}], "sortFields": ["-settlementDate"]}),
                   ("domain + date range", {"limit": 20, "domainFilters": dom, "dateRangeFilters": rng}),
                   ("date range only", {"limit": 3, "dateRangeFilters": rng})]:
    t = time.monotonic()
    r = httpx.post(URL, json=body, headers={"Accept": "application/json"}, timeout=60)
    try:
        rows = r.json()
        txt = json.dumps([{k: x.get(k) for k in ("symbolCode", "settlementDate", "currentShortPositionQuantity", "daysToCoverQuantity")} for x in rows][:8]) if isinstance(rows, list) else str(rows)[:300]
    except ValueError:
        txt = r.text[:300]
    print(f"== {name}: {r.status_code} in {time.monotonic() - t:.1f}s, {len(r.content)} bytes: {txt}", flush=True)

from market_tracker import earnings, pead, screen, spinoffs  # noqa: E402
d = screen.load()
for s in pead.candidates(today):
    try:
        r = earnings.recap(s)
        print(f"{s}: filed {r['release']['filed']} outlook {r['outlook']['direction']} reaction {r.get('reaction')} | {(r['outlook']['lines'] or [''])[0][:140]}" if r else f"{s}: no recap", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"{s}: {type(exc).__name__} {exc}", flush=True)
for r in spinoffs.build(today, lookup_fn=lambda s: screen.lookup(d, s)):
    print(f"{r['stage']:10} {r['ticker']} {r['name'][:50]} since {r['trading_since']} ret {r['return_pct']} spy {r['spy_pct']}")
