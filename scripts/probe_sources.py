"""Temporary probe (removed before merge): Nasdaq's stock list, Yahoo history speed, FRED single series."""
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, ".")
from market_tracker import http, macro  # noqa: E402
from market_tracker.providers import market  # noqa: E402
from market_tracker.reading import BROWSER_UA  # noqa: E402

t = time.time()
try:
    d = http.get("https://api.nasdaq.com/api/screener/stocks", params={"tableonly": "true", "limit": "10000", "download": "true"},
                 ttl=0, headers={"User-Agent": BROWSER_UA, "Accept": "application/json, text/plain, */*"})
    rows = (d.get("data") or {}).get("rows") or []
    print(f"NASDAQ screener: {len(rows)} rows in {time.time() - t:.1f}s; keys {sorted(rows[0]) if rows else None}; e.g. {rows[:2]}")
    big = [r for r in rows if r.get("marketCap") and float(r["marketCap"] or 0) >= 3e8]
    print("  >= $300M market cap:", len(big), "sectors:", sorted({r.get('sector') for r in big})[:20])
except Exception as exc:  # noqa: BLE001
    print("NASDAQ screener FAIL", type(exc).__name__, str(exc)[:300])

syms = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "V", "KO", "PEP", "XOM", "CVX", "WMT", "HD", "UNH", "LLY", "AVGO", "ORCL", "COST",
        "MU", "AMD", "INTC", "QCOM", "TXN", "CAT", "DE", "GE", "HON", "LMT", "RTX", "NOC", "GD", "BA", "UPS", "FDX", "NKE", "SBUX", "MCD", "DIS",
        "NFLX", "CRM", "ADBE", "NOW", "SNOW", "PLTR", "UBER", "ABNB", "SHOP", "SQ"]
t = time.time()
ok = 0


def one(s):
    try:
        return len(market.get_history(s, 260))
    except Exception as exc:  # noqa: BLE001
        return str(exc)[:80]


with ThreadPoolExecutor(max_workers=6) as pool:
    res = list(pool.map(one, syms))
print(f"Yahoo 1y histories: {sum(isinstance(r, int) for r in res)}/{len(syms)} in {time.time() - t:.1f}s; failures {[r for r in res if not isinstance(r, int)][:3]}")
t = time.time()
try:
    data = macro.load()
    print(f"FRED per series: {len(data)} series in {time.time() - t:.1f}s; " + "; ".join(f"{k} {v[-1]}" for k, v in data.items()))
except Exception as exc:  # noqa: BLE001
    print("FRED FAIL", exc)
