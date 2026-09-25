"""Temporary: status and a sample of each candidate source (removed before merge)."""
import json
import httpx

BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}


def show(name, r, n=700):
    print(f"=== {name} {r.status_code} {r.headers.get('content-type', '')[:40]} len={len(r.content)}")
    print(" " + r.text[:n].replace("\n", " "))


with httpx.Client(timeout=25, follow_redirects=True, headers=BROWSER) as c:
    # Yahoo with cookie + crumb
    try:
        c.get("https://fc.yahoo.com")
        crumb = c.get("https://query1.finance.yahoo.com/v1/test/getcrumb").text
        print("=== yahoo-crumb", repr(crumb[:20]))
        for name, url in [
            ("yahoo-options", f"https://query1.finance.yahoo.com/v7/finance/options/AAPL?crumb={crumb}"),
            ("yahoo-options-date", f"https://query2.finance.yahoo.com/v7/finance/options/NVDA?crumb={crumb}"),
            ("yahoo-calendar", f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/AAPL?modules=calendarEvents,earnings&crumb={crumb}"),
        ]:
            show(name, c.get(url), 1500)
    except Exception as exc:
        print("=== yahoo ERROR", type(exc).__name__, exc)
    for name, url, n in [
        ("nasdaq-optionchain", "https://api.nasdaq.com/api/quote/AAPL/option-chain?assetclass=stocks&limit=40&fromdate=all&todate=undefined&excode=oprac&callput=callput&money=at&type=all", 1500),
        ("nasdaq-earnings-date", "https://api.nasdaq.com/api/analyst/AAPL/earnings-date", 900),
        ("nasdaq-earnings-cal-oct", "https://api.nasdaq.com/api/calendar/earnings?date=2026-10-29", 600),
        ("nasdaq-econ-cal", "https://api.nasdaq.com/api/calendar/economicevents?date=2026-10-02", 900),
        ("fed-fomc", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", 300),
        ("bls-ics", "https://www.bls.gov/schedule/news_release/bls.ics", 600),
        ("stocktwits-user", "https://api.stocktwits.com/api/2/streams/user/howardlindzon.json", 1500),
        ("stocktwits-user-2", "https://api.stocktwits.com/api/2/streams/user/zerohedge.json", 600),
        ("stocktwits-sugg", "https://api.stocktwits.com/api/2/streams/suggested.json", 600),
        ("llama-hacks", "https://api.llama.fi/hacks", 1200),
        ("llama-emissions", "https://api.llama.fi/emissions", 900),
        ("llama-unlocks-ds", "https://defillama-datasets.llama.fi/emissionsBreakdown", 600),
        ("farside-btc", "https://farside.co.uk/btc/", 400),
        ("coinbase-status", "https://status.coinbase.com/api/v2/incidents.json", 600),
        ("coingecko-coin", "https://api.coingecko.com/api/v3/coins/solana?localization=false&tickers=false&market_data=false&community_data=false&developer_data=false", 400),
        ("rekt-news", "https://rekt.news/", 300),
    ]:
        try:
            show(name, c.get(url), n)
        except Exception as exc:
            print(f"=== {name} ERROR", type(exc).__name__, exc)
