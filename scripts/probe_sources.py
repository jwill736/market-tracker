"""Temporary: which logo and name sources answer without a key."""
import hashlib
import httpx

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}
c = httpx.Client(timeout=20, headers=UA, follow_redirects=True)


def show(label, url):
    try:
        r = c.get(url)
        body = r.content
        print(f"{label:34} {r.status_code} {r.headers.get('content-type','')[:30]:30} {len(body):7} sha={hashlib.sha1(body).hexdigest()[:10]} {r.url if str(r.url) != url else ''}")
    except Exception as e:
        print(f"{label:34} ERR {e}")


for sym in ["AAPL", "VOO", "SCHD", "KO", "BRK-B", "BRK.B", "NKE", "ZZZZQ", "QQQZZ"]:
    show(f"fmp {sym}", f"https://financialmodelingprep.com/image-stock/{sym}.png")
for sym in ["AAPL", "VOO", "SCHD", "BRK-B", "ZZZZQ"]:
    show(f"parqet {sym}", f"https://assets.parqet.com/logos/symbol/{sym}?format=png&size=100")
for sym in ["AAPL", "VOO", "ZZZZQ"]:
    show(f"eodhd {sym}", f"https://eodhd.com/img/logos/US/{sym}.png")
for coin in ["btc", "eth", "sol", "pepe", "sui", "zzzq"]:
    show(f"spothq {coin}", f"https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/128/color/{coin}.png")
try:
    r = c.get("https://api.coingecko.com/api/v3/search", params={"query": "sui"})
    j = r.json()
    print("coingecko search sui", r.status_code, [(x["symbol"], x["name"], x.get("large", "")[:80]) for x in j.get("coins", [])[:3]])
except Exception as e:
    print("coingecko ERR", e)
for cur in ["BTC", "SUI", "PEPE"]:
    try:
        r = c.get(f"https://api.exchange.coinbase.com/currencies/{cur}")
        print("coinbase currency", cur, r.status_code, r.json().get("name") if r.status_code == 200 else r.text[:80])
    except Exception as e:
        print("coinbase ERR", e)
for sym in ["VOO", "SCHD", "AAPL"]:
    try:
        r = c.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"range": "1d", "interval": "1d"})
        m = r.json()["chart"]["result"][0]["meta"]
        print("yahoo meta", sym, r.status_code, m.get("longName"), "|", m.get("shortName"), "|", m.get("instrumentType"))
    except Exception as e:
        print("yahoo ERR", sym, e)
