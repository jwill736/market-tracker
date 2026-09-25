"""Temporary: shapes of the sources for fundamentals, dividends and SnapTrade (removed before merge)."""
import json
import os
import httpx

UA = os.environ.get("SEC_USER_AGENT") or "plumbline research probe@example.com"
BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
           "Accept": "application/json, text/plain, */*"}
with httpx.Client(timeout=30, follow_redirects=True) as c:
    r = c.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json", headers={"User-Agent": UA})
    print("=== sec companyfacts AAPL", r.status_code, len(r.content))
    d = r.json()
    g = d["facts"].get("us-gaap", {})
    for k in ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss",
              "EarningsPerShareDiluted", "NetCashProvidedByUsedInOperatingActivities", "PaymentsToAcquirePropertyPlantAndEquipment",
              "LongTermDebt", "LongTermDebtNoncurrent", "CashAndCashEquivalentsAtCarryingValue"]:
        if k in g:
            units = g[k]["units"]
            u = next(iter(units))
            vals = units[u]
            print(" ", k, u, len(vals), json.dumps(vals[-3:])[:420])
        else:
            print(" ", k, "MISSING")
    dei = d["facts"].get("dei", {})
    print("  dei shares", json.dumps(dei.get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])[-2:])[:300])
    r = c.get("https://data.sec.gov/api/xbrl/companyconcept/CIK0000021344/us-gaap/Revenues.json", headers={"User-Agent": UA})
    print("=== sec concept KO Revenues", r.status_code, r.text[:200])
    r = c.get("https://query1.finance.yahoo.com/v8/finance/chart/KO?range=2y&interval=1d&events=div", headers=BROWSER)
    print("=== yahoo div KO", r.status_code, json.dumps((r.json()["chart"]["result"][0].get("events") or {}).get("dividends", {}))[:500] if r.status_code == 200 else r.text[:200])
    for sym in ["KO", "SCHD", "AAPL"]:
        r = c.get(f"https://api.nasdaq.com/api/quote/{sym}/dividends?assetclass={'etf' if sym == 'SCHD' else 'stocks'}", headers=BROWSER)
        print(f"=== nasdaq div {sym}", r.status_code, r.text[:700])
    for u in ["https://api.snaptrade.com/", "https://api.snaptrade.com/api/v1/", "https://api.snaptrade.com/accounts"]:
        r = c.get(u, headers={"Accept": "application/json"})
        print("=== snaptrade", u, r.status_code, r.text[:200])
