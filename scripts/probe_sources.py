"""Temporary: status and a sample of each candidate source (removed before merge)."""
import json, os, re
import httpx

UA = os.environ.get("SEC_USER_AGENT") or "plumbline research probe@example.com"
BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}
TODAY = "2026-09-25"
SOURCES = [
    ("wire-globenewswire", "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies", 1200),
    ("wire-prnewswire", "https://www.prnewswire.com/rss/news-releases-list.rss", 1200),
    ("wire-businesswire", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeGVtRWA==", 1200),
    ("wire-accesswire", "https://www.accessnewswire.com/newsroom/api/rss", 600),
    ("wire-newsfile", "https://www.newsfilecorp.com/feed/news", 600),
    ("apewisdom-stocks", "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1", 800),
    ("apewisdom-crypto", "https://apewisdom.io/api/v1.0/filter/all-crypto/page/1", 500),
    ("stocktwits-trending", "https://api.stocktwits.com/api/2/trending/symbols.json", 800),
    ("stocktwits-stream", "https://api.stocktwits.com/api/2/streams/symbol/TSLA.json", 800),
    ("reddit-wsb", "https://www.reddit.com/r/wallstreetbets/new.json?limit=3", 600),
    ("reddit-old", "https://old.reddit.com/r/wallstreetbets/new.json?limit=3", 600),
    ("hn-algolia", "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=nvidia&hitsPerPage=3", 800),
    ("bluesky-search", "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=%24TSLA&limit=3", 800),
    ("coingecko-trending", "https://api.coingecko.com/api/v3/search/trending", 800),
    ("binance-announce", "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId=48&pageNo=1&pageSize=5", 800),
    ("coinbase-products", "https://api.exchange.coinbase.com/products", 300),
    ("house-watcher", "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json", 600),
    ("senate-watcher", "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json", 600),
    ("capitoltrades-bff", "https://bff.capitoltrades.com/trades?per_page=3&page=1", 1500),
    ("house-clerk-index", "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2026FD.zip", 0),
    ("ark-arkk-assets", "https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv", 800),
    ("ark-arkk-wp", "https://ark-funds.com/wp-content/uploads/funds-etf-csv/ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv", 800),
    ("nasdaq-earnings", f"https://api.nasdaq.com/api/calendar/earnings?date={TODAY}", 1200),
    ("yahoo-options", "https://query1.finance.yahoo.com/v7/finance/options/AAPL", 400),
    ("yahoo-options2", "https://query2.finance.yahoo.com/v7/finance/options/AAPL", 400),
    ("yahoo-calendar", "https://query1.finance.yahoo.com/v10/finance/quoteSummary/AAPL?modules=calendarEvents", 400),
    ("yahoo-chart-ohlc", "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=5d&interval=15m&includePrePost=true", 600),
    ("polymarket", "https://gamma-api.polymarket.com/markets?limit=3&order=volume24hr&ascending=false&active=true&closed=false", 900),
    ("kalshi", "https://api.elections.kalshi.com/trade-api/v2/markets?limit=3&status=open", 900),
    ("sec-fts-8k", "https://efts.sec.gov/LATEST/search-index?q=%22definitive%20agreement%22&forms=8-K&dateRange=custom&startdt=2026-09-22&enddt=2026-09-25", 500),
    ("finviz-news", "https://finviz.com/news.ashx", 300),
    ("google-news-rss", "https://news.google.com/rss/search?q=%22FDA%20approval%22%20when:1d&hl=en-US&gl=US&ceid=US:en", 600),
    ("fda-press", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml", 600),
    ("usaspending", "https://api.usaspending.gov/api/v2/search/spending_by_award/", 0),
    ("whalealert", "https://api.whale-alert.io/v1/status", 300),
    ("mempool", "https://mempool.space/api/v1/fees/recommended", 300),
]
with httpx.Client(timeout=20, follow_redirects=True) as c:
    for name, url, n in SOURCES:
        try:
            if name == "usaspending":
                body = {"filters": {"time_period": [{"start_date": "2026-09-20", "end_date": TODAY}], "award_type_codes": ["A", "B", "C", "D"]},
                        "fields": ["Award ID", "Recipient Name", "Award Amount"], "limit": 3, "sort": "Award Amount", "order": "desc"}
                r = c.post(url, json=body, headers=BROWSER)
            else:
                h = dict(BROWSER)
                if "sec.gov" in url:
                    h = {"User-Agent": UA}
                r = c.get(url, headers=h)
            ctype = r.headers.get("content-type", "")
            print(f"=== {name} {r.status_code} {ctype[:40]} len={len(r.content)}")
            if n and r.status_code == 200:
                text = r.text
                if "json" in ctype:
                    try:
                        j = r.json()
                        text = json.dumps(j)[:n]
                    except Exception:
                        text = text[:n]
                print(text[:n].replace("\n", " "))
            elif r.status_code != 200:
                print(r.text[:200].replace("\n", " "))
        except Exception as e:
            print(f"=== {name} ERROR {type(e).__name__} {str(e)[:150]}")
