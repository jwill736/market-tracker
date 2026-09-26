"""Temporary: Robinhood crypto API fields, SEC N-PORT holdings, fee source."""
import re
import httpx

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}
SEC = {"User-Agent": "plumbline probe contact@example.com", "Accept-Encoding": "gzip, deflate"}
c = httpx.Client(timeout=30, headers=UA, follow_redirects=True)

page = c.get("https://docs.robinhood.com/crypto/trading/").text
for src in re.findall(r'src="(/_next/static/chunks/pages/crypto/[^"]+\.js)"', page):
    js = c.get("https://docs.robinhood.com" + src).text
    print("=== chunk", src, len(js))
    for p in sorted(set(re.findall(r"/api/v[12]/crypto/[A-Za-z_/{}.:-]+", js))):
        print("PATH", p)
    for kw in ["trading.robinhood.com", "message_to_sign", "api_key}{", "f\"{api_key", "base64", "timestamp", "SigningKey",
               "market_order_config", "limit_order_config", "stop_loss_order_config", "asset_quantity", "quote_amount",
               "client_order_id", "time_in_force", "estimated_price", "best_bid_ask", "holdings", "average_price",
               "filled_asset_quantity", "executions", "effective_price", "state", "buying_power", "cancel"]:
        for m in list(re.finditer(re.escape(kw), js))[:2]:
            s = js[max(0, m.start() - 160): m.start() + 260]
            print(f"--- {kw}: {s!r}")

tick = c.get("https://www.sec.gov/files/company_tickers_mf.json", headers=SEC).json()
print("mf fields", tick.get("fields"))
rows = {r[3]: r for r in tick["data"] if r[3] in ("VOO", "QQQ", "SCHD", "VTI", "SPY", "IVV")}
print(rows)
for sym, r in rows.items():
    cik, series = str(r[0]), r[1]
    q = c.get("https://efts.sec.gov/LATEST/search-index", params={"q": f'"{series}"', "forms": "NPORT-P"}, headers=SEC).json()
    hits = (q.get("hits") or {}).get("hits") or []
    print(sym, series, "hits", len(hits), [(h["_id"], h["_source"].get("period_ending"), h["_source"].get("file_date")) for h in hits[:3]])
    if hits and sym in ("VOO", "SCHD"):
        h = max(hits, key=lambda h: h["_source"].get("file_date", ""))
        acc, fname = h["_id"].split(":")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(h['_source']['ciks'][0])}/{acc.replace('-', '')}/{fname}"
        x = c.get(url, headers=SEC)
        t = x.text
        print("xml", x.status_code, len(t), "seriesId" in t, t.count("<invstOrSec>"))
        i = t.find("<invstOrSec>")
        print(t[i:i + 900])
sa = c.get("https://stockanalysis.com/etf/qqq/holdings/").text
print("stockanalysis qqq", len(sa), re.findall(r'/stocks/([a-z.]+)/', sa)[:15])
i = sa.find("% Weight")
print(repr(re.sub(r"<[^>]+>", " ", sa[i:i + 800])))
