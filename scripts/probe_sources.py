"""Temporary: field shapes for the new sources (removed before merge)."""
import json
import httpx

BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}
with httpx.Client(timeout=25, follow_redirects=True, headers=BROWSER) as c:
    for url in ["https://api.nasdaq.com/api/quote/NVDA/option-chain?assetclass=stocks&limit=400&fromdate=2026-11-20&todate=2026-11-20&excode=oprac&callput=callput&money=at&type=all",
                "https://api.nasdaq.com/api/quote/NVDA/option-chain?assetclass=stocks&limit=10&fromdate=all&todate=undefined&excode=oprac&callput=callput&money=all&type=all"]:
        d = c.get(url).json()["data"]
        rows = d["table"]["rows"]
        print("=== chain", d.get("lastTrade"), "rows", len(rows), "keys", list(d.keys()))
        for r in rows[:6]:
            print(" ", json.dumps(r)[:300])
        print("  filterlist:", json.dumps(d.get("filterlist"))[:900])
    d = c.get("https://api.nasdaq.com/api/analyst/NVDA/earnings-date").json()["data"]
    print("=== nvda earnings", d.get("announcement"), "|", json.dumps(d)[:300])
    d = c.get("https://api.nasdaq.com/api/calendar/economicevents?date=2026-10-28").json()["data"]
    print("=== econ", [(r["gmt"], r["country"], r["eventName"]) for r in d["rows"] if r["country"] == "United States"][:30])
    d = c.get("https://api.stocktwits.com/api/2/streams/user/howardlindzon.json").json()
    for m in d["messages"][:8]:
        print("=== st", m["created_at"], json.dumps(m.get("entities"))[:200], json.dumps(m.get("prices"))[:200], [s["symbol"] for s in m.get("symbols", [])])
    print("=== st keys", list(d["messages"][0].keys()))
    d = c.get("https://api.stocktwits.com/api/2/streams/suggested.json").json()
    print("=== suggested users", sorted({(m["user"]["username"], m["user"]["followers"]) for m in d["messages"]}, key=lambda x: -x[1])[:20])
    d = c.get("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=solana,sui,aptos,bitcoin&sparkline=false").json()
    for x in d:
        print("=== cg", x["id"], x.get("circulating_supply"), x.get("total_supply"), x.get("max_supply"), x.get("fully_diluted_valuation"), x.get("market_cap"))
    d = c.get("https://api.llama.fi/hacks").json()
    recent = sorted(d, key=lambda h: -h["date"])[:8]
    for h in recent:
        print("=== hack", h["date"], h["name"], h["amount"], h["chain"], h["targetType"], h.get("classification"))
    d = c.get("https://status.coinbase.com/api/v2/incidents/unresolved.json").json()
    print("=== cb unresolved", [(i["name"], i["impact"], [cmp.get("name") for cmp in i.get("components", [])][:5]) for i in d.get("incidents", [])][:10])
