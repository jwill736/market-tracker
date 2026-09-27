"""Temporary probe for the next idea-engine round (removed before merge)."""
import json
import subprocess
import time
from datetime import date, timedelta

from market_tracker import fundamentals, http
from market_tracker.providers import sec


def step(name, fn):
    t = time.monotonic()
    try:
        out = fn()
        print(f"== {name} ({time.monotonic() - t:.1f}s)\n{out}\n", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"== {name} FAILED: {type(exc).__name__}: {str(exc)[:400]}\n", flush=True)


def nee():
    out = []
    for s in ("NEE", "AEP"):
        cik = sec.ticker_map().cik_for(s)
        f = sec._sec_get(fundamentals.COMPANYFACTS.format(cik=str(cik).zfill(10)), ttl=1)
        for ns, tags in f["facts"].items():
            hits = [(t, len(v.get("units", {}).get("USD", []))) for t, v in tags.items()
                    if any(w in t for w in ("CapitalExpend", "Construction", "PaymentsToAcquire", "PropertyPlantAndEquipmentAdditions", "Capex"))]
            if hits:
                out.append(f"{s} {ns}: {hits[:12]}")
        from market_tracker import moneyflow
        out.append(f"{s} capex_ttm {moneyflow.capex_ttm(f)}")
    return "\n".join(out)


def finra():
    import httpx
    out = []
    for body in ({"limit": 3, "sortFields": ["-settlementDate"], "compareFilters": [{"compareType": "equal", "fieldName": "symbolCode", "fieldValue": "GME"}]},
                 {"limit": 10, "sortFields": ["-settlementDate"], "domainFilters": [{"fieldName": "symbolCode", "values": ["GME", "AAPL", "VAL", "AROC"]}]}):
        r = httpx.post("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest", json=body,
                       headers={"Accept": "application/json"}, timeout=30)
        rows = r.json() if r.status_code == 200 else r.text[:300]
        out.append(f"{r.status_code}: " + (json.dumps([{k: x.get(k) for k in ('symbolCode', 'settlementDate', 'currentShortPositionQuantity', 'daysToCoverQuantity')} for x in rows]) if isinstance(rows, list) else rows))
    return "\n".join(out)


def fts8k():
    from market_tracker.config import settings
    today = date.today()
    out = []
    for frm in (0, 100):
        p = {"forms": "8-K", "dateRange": "custom", "startdt": (today - timedelta(days=4)).isoformat(), "enddt": today.isoformat(), "from": frm}
        d = http.get("https://efts.sec.gov/LATEST/search-index", params=p, headers={"User-Agent": settings.sec_user_agent}, ttl=0)
        hits = (d.get("hits") or {}).get("hits") or []
        e202 = [h["_source"]["display_names"][0] for h in hits if "2.02" in (h["_source"].get("items") or [])]
        out.append(f"from {frm}: total {(d.get('hits') or {}).get('total')}; page {len(hits)}; with 2.02: {len(e202)} e.g. {e202[:5]}")
    p = {"q": '"guidance"', "forms": "8-K", "dateRange": "custom", "startdt": (today - timedelta(days=30)).isoformat(), "enddt": today.isoformat()}
    d = http.get("https://efts.sec.gov/LATEST/search-index", params=p, headers={"User-Agent": settings.sec_user_agent}, ttl=0)
    hits = (d.get("hits") or {}).get("hits") or []
    e202 = [(h["_source"]["display_names"][0], h["_source"].get("file_date")) for h in hits if "2.02" in (h["_source"].get("items") or [])]
    out.append(f"guidance 30d: total {(d.get('hits') or {}).get('total')}; page {len(hits)}; 2.02 {len(e202)}: {e202[:8]}")
    return "\n".join(out)


def recaps():
    from market_tracker import earnings
    out = []
    for s in ("NNBR", "VNCE", "FDX", "NKE", "MU"):
        r = earnings.recap(s)
        out.append(f"{s}: " + (f"filed {r['release']['filed']} outlook {r['outlook'].get('direction')} reaction {r.get('reaction')}" if r else "None"))
    return "\n".join(out)


def screen_run():
    subprocess.run(["mt", "screen", "--out", "/tmp/screen.json"], check=True)
    d = json.load(open("/tmp/screen.json"))
    from market_tracker import screen
    out = []
    for s in ("XOM", "NEM", "MTB", "JPM", "BRK-B", "AAPL"):
        out.append(f"{s}: {screen.lookup(d, s)}")
    return "\n".join(out)


step("NEE / AEP capex tags", nee)
step("FINRA short interest", finra)
step("FTS 8-K results releases", fts8k)
step("earnings recaps", recaps)
step("screen with the fixes", screen_run)
t = time.monotonic()
subprocess.run(["mt", "screen-backtest", "--out", "/tmp/bt.json"], check=False)
print(f"backtest took {time.monotonic() - t:.0f}s")
d = json.load(open("/tmp/bt.json"))
for p in d["periods"][-3:]:
    print(p["quarter"], p["day"], p["companies"], {g: {h: p["groups"][g].get(h) for h in ("3m", "12m")} for g in ("large", "bottom")}, p["spy"])
print(json.dumps(d["summary"], indent=1)[:6000])
