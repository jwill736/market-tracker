"""Temporary: print status and a sample of each candidate source (removed before merge)."""
import os, re, sys
import httpx

UA = os.environ.get("SEC_USER_AGENT") or "plumbline research probe@example.com"
BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
SEC = [
    ("8k-feed", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&start=0&count=40&output=atom", 3000),
    ("nt-feed", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=NT%2010-K&count=10&output=atom", 1500),
    ("25-feed", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=25-NSE&count=10&output=atom", 1500),
    ("efts", "https://efts.sec.gov/LATEST/search-index?q=%22substantial%20doubt%22&forms=10-Q,10-K&dateRange=custom&startdt=2026-08-25&enddt=2026-09-25", 2500),
]
FEEDS = [
    ("abnormalreturns", "https://abnormalreturns.com/feed/"), ("ritholtz", "https://ritholtz.com/feed/"),
    ("calcrisk", "https://www.calculatedriskblog.com/feeds/posts/default?alt=rss"),
    ("ft-markets", "https://www.ft.com/markets?format=rss"), ("ft-alphaville", "https://www.ft.com/alphaville?format=rss"),
    ("bloomberg", "https://feeds.bloomberg.com/markets/news.rss"), ("wsj-markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("cnbc-markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("marketwatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("fed", "https://www.federalreserve.gov/feeds/press_all.xml"), ("sec-press", "https://www.sec.gov/news/pressreleases.rss"),
    ("seekingalpha", "https://seekingalpha.com/market_currents.xml"), ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("decrypt", "https://decrypt.co/feed"), ("theblock", "https://www.theblock.co/rss.xml"),
    ("axios-markets", "https://api.axios.com/feed/"), ("economist-finance", "https://www.economist.com/finance-and-economics/rss.xml"),
    ("barrons", "https://www.barrons.com/xml/rss/3_7510.xml"), ("yahoo-fin", "https://finance.yahoo.com/news/rssindex"),
]
with httpx.Client(timeout=20, follow_redirects=True) as c:
    for name, url, n in SEC:
        try:
            r = c.get(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
            print(f"=== {name} {r.status_code} {len(r.text)}\n{r.text[:n]}\n")
        except Exception as e:
            print(f"=== {name} ERROR {e}\n")
    r = c.get("https://data.sec.gov/submissions/CIK0000320193.json", headers={"User-Agent": UA})
    j = r.json()["filings"]["recent"]
    print("=== submissions keys", list(j.keys()))
    print([(f, d, i) for f, d, i in zip(j["form"], j["filingDate"], j["items"]) if f == "8-K"][:6])
    for name, url in FEEDS:
        try:
            r = c.get(url, headers={"User-Agent": BROWSER})
            items = len(re.findall(r"<item[ >]|<entry[ >]", r.text))
            titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", r.text)[1:4]
            links = len(re.findall(r"<a href", r.text))
            print(f"=== {name} {r.status_code} items={items} links_in_content={links} titles={titles}")
            if name in ("abnormalreturns", "ritholtz"):
                m = re.search(r"<item>.*?</item>", r.text, re.S)
                print(m.group(0)[:3000] if m else r.text[:1000])
        except Exception as e:
            print(f"=== {name} ERROR {type(e).__name__} {e}")
