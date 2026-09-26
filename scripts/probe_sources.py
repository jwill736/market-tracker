"""Temporary: Robinhood crypto API paths, fund holdings and fee sources."""
import re
import httpx

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
      "Accept": "text/html,application/json,*/*"}
c = httpx.Client(timeout=25, headers=UA, follow_redirects=True)


def get(label, url, **kw):
    try:
        r = c.get(url, **kw)
        print(f"=== {label} {r.status_code} {r.headers.get('content-type','')[:40]} {len(r.content)}")
        return r
    except Exception as e:
        print(f"=== {label} ERR {e}")
        return None


r = get("rh docs", "https://docs.robinhood.com/crypto/trading/")
if r is not None:
    t = r.text
    for pat in [r"https://[a-z.]*robinhood\.com[^\s\"'<>]*", r"/api/v[12]/crypto/[a-z_/{}.-]+[^\s\"'<]*"]:
        print(sorted(set(re.findall(pat, t)))[:60])
    for kw in ["message", "base64", "timestamp", "signature", "asset_quantity", "quote_amount", "market_order_config",
               "limit_order_config", "client_order_id", "time_in_force", "estimated_price", "best_bid_ask"]:
        i = t.find(kw)
        if i >= 0:
            print(f"--- {kw}: {re.sub(r'<[^>]+>', ' ', t[max(0,i-200):i+300])!r}"[:600])
    js = sorted(set(re.findall(r'src="([^"]+\.js)"', t)))
    print("scripts", js[:10])
for path in ["/api/v1/crypto/trading/accounts/", "/api/v2/crypto/trading/accounts/", "/api/v1/crypto/marketdata/best_bid_ask/?symbol=BTC-USD"]:
    rr = get("rh " + path, "https://trading.robinhood.com" + path)
    if rr is not None:
        print(rr.text[:200])

# fund holdings
get_v = get("vanguard VOO holdings", "https://investor.vanguard.com/investment-products/etfs/profile/api/VOO/portfolio-holding/stock?start=1&count=5")
if get_v is not None:
    print(get_v.text[:400])
for label, url in [("ssga SPY xlsx", "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"),
                   ("ishares IVV csv", "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf/1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund"),
                   ("invesco QQQ", "https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/0?audienceType=Investor&action=download&ticker=QQQ"),
                   ("schwab SCHD", "https://www.schwabassetmanagement.com/allholdings/SCHD"),
                   ("stockanalysis VOO", "https://stockanalysis.com/etf/voo/holdings/"),
                   ("zacks VOO", "https://www.zacks.com/funds/etf/VOO/holding")]:
    rr = get(label, url)
    if rr is not None:
        print(repr(rr.content[:300]))
# expense ratio sources
for label, url in [("stockanalysis VOO overview", "https://stockanalysis.com/etf/voo/"),
                   ("vanguard VOO profile", "https://investor.vanguard.com/investment-products/etfs/profile/api/VOO/profile")]:
    rr = get(label, url)
    if rr is not None:
        t = rr.text
        i = t.lower().find("expense")
        print(repr(re.sub(r"<[^>]+>", " ", t[max(0, i-100):i+200])) if i >= 0 else t[:200])
# SEC N-PORT for VOO (Vanguard Index Funds, series)
rr = get("sec nport search", "https://efts.sec.gov/LATEST/search-index?q=%22VOO%22&forms=NPORT-P", headers={"User-Agent": "plumbline probe contact@example.com"})
if rr is not None:
    print(rr.text[:300])
