"""Temporary probe (removed before merge): which news-desk feeds and broker endpoints answer from a
GitHub runner, and what their items look like."""
import json
import os
import re
import sys

import httpx

sys.path.insert(0, ".")
from market_tracker import newsdesk  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
SEC = {"User-Agent": os.environ.get("SEC_USER_AGENT") or "Plumbline research contact@example.com"}


def show(name, url, headers=UA, **kw):
    try:
        r = httpx.get(url, headers=headers, timeout=25, follow_redirects=True, **kw)
        body = r.text
        titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", body)[1:4]
        items = body.count("<item")
        print(f"{name:28s} {r.status_code} items={items} ctype={r.headers.get('content-type','')[:30]} {titles}")
    except Exception as exc:  # noqa: BLE001
        print(f"{name:28s} ERR {exc!r}"[:200])


for name, (kind, url) in {**newsdesk.MARKET_FEEDS, **newsdesk.CRYPTO_FEEDS}.items():
    show(name, url, SEC if "sec.gov" in url else UA)
show("Nasdaq per-symbol", "https://www.nasdaq.com/feed/rssoutbound?symbol=AAPL")
show("Yahoo per-symbol", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US")
show("Google News", "https://news.google.com/rss/search?q=Apple+stock+when:7d&hl=en-US&gl=US&ceid=US:en")
# CNBC alternative ids
for i in ("100003114", "10000115", "15839069", "10001147", "19854910"):
    show(f"CNBC id {i}", f"https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id={i}")
show("WSJ old host", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml")
show("BusinessWire alt", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJcFVJYWA==")
show("GlobeNewswire alt", "https://www.globenewswire.com/RssFeed/subjectcode/13-Earnings%20Releases%20and%20Operating%20Results/feedTitle/GlobeNewswire%20-%20Earnings%20Releases%20and%20Operating%20Results")
# GDELT
try:
    r = httpx.get("https://api.gdeltproject.org/api/v2/doc/doc", params={"query": '("Apple" OR "Nvidia") sourcelang:english', "mode": "artlist",
                  "format": "json", "maxrecords": 50, "timespan": "3d"}, timeout=30)
    j = r.json()
    arts = j.get("articles") or []
    print("GDELT", r.status_code, len(arts), [(a.get("domain"), a.get("title", "")[:50]) for a in arts[:3]])
except Exception as exc:  # noqa: BLE001
    print("GDELT ERR", repr(exc)[:200])
# Brokers: refuse unsigned requests (reachable)
for name, method, url, kw in [
        ("Alpaca paper account", "GET", "https://paper-api.alpaca.markets/v2/account", {}),
        ("Alpaca data latest", "GET", "https://data.alpaca.markets/v2/stocks/trades/latest?symbols=AAPL&feed=iex", {}),
        ("Public token", "POST", "https://api.public.com/userapiauthservice/personal/access-tokens", {"json": {"secret": "x", "validityInMinutes": 5}}),
        ("Public accounts", "GET", "https://api.public.com/userapigateway/trading/account", {}),
        ("Finnhub quote bad key", "GET", "https://finnhub.io/api/v1/quote?symbol=AAPL&token=bad", {}),
        ("Yahoo ^IRX", "GET", "https://query1.finance.yahoo.com/v8/finance/chart/%5EIRX?range=1d&interval=1d", {}),
        ("GitHub contents API", "GET", "https://api.github.com/repos/octocat/Hello-World", {})]:
    try:
        r = httpx.request(method, url, headers=UA, timeout=20, **kw)
        print(f"{name:28s} {r.status_code} {r.text[:160]!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"{name:28s} ERR {exc!r}"[:200])
# End-to-end: the desk on two real holdings
print(json.dumps({k: {"stories": [(s["tier"], s["event"], s["sources"], s["action"], s["title"][:70]) for s in v["stories"][:5]],
                      "items": v["items"], "outlets_3d": v["outlets_3d"]}
                  for k, v in newsdesk.build(["AAPL", "BTC-USD"], {"AAPL": "Apple"}, {}, [], crypto_names={"BTC-USD": "Bitcoin"}).items()},
                 indent=1)[:6000])
print({k: v for k, v in newsdesk.FEED_STATUS.items()})
