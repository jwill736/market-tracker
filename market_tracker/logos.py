"""Company logos and names for tickers, fetched once and kept on disk.

The browser asks this app for /api/logo/AAPL, never a third party, so the logo services only
ever see this computer fetching each symbol once, not which phone looks at what. Sources:

- Stocks and funds: Financial Modeling Prep's public logo images, then Parqet's.
- Crypto: the cryptocurrency-icons set for the majors, then CoinGecko's image for the coin.
- Nothing found: a letter on a colored circle, drawn here, so every row still lines up.

Names: the SEC's company list (the name on its filings), Coinbase's currency names for coins,
then Yahoo's quote data (funds).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from html import escape

from . import config, http
from .providers import market
from .reading import BROWSER_UA

STOCK_SOURCES = ["https://financialmodelingprep.com/image-stock/{sym}.png",
                 "https://assets.parqet.com/logos/symbol/{sym}?format=png&size=128"]
CRYPTO_ICON = "https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/128/color/{coin}.png"
COINGECKO_SEARCH = "https://api.coingecko.com/api/v3/search"
COINBASE_CURRENCY = "https://api.exchange.coinbase.com/currencies/{coin}"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
MISS_RETRY = 7 * 86400          # try again for a missing logo after a week
MAX_BYTES = 300_000
_SAFE = re.compile(r"^[A-Z0-9.\-^=]{1,20}$")
_lock = threading.Lock()
_names: dict[str, str] = {}
KEEP_UPPER = {"ETF", "USA", "US", "AI", "II", "III", "LP", "N.V.", "S.A.", "PLC", "REIT"}


def cache_dir() -> str:
    d = os.path.join(os.path.dirname(os.path.abspath(config.settings.db_path)) or ".", "logo_cache")
    os.makedirs(d, exist_ok=True)
    return d


def safe(sym: str) -> str | None:
    s = market.normalize_symbol(sym or "")
    return s if _SAFE.match(s) else None


def _raw_get(url: str, params: dict | None = None):
    import httpx
    return httpx.get(url, params=params, headers={"User-Agent": BROWSER_UA}, timeout=10, follow_redirects=True)


def _image(resp) -> bytes | None:
    ctype = resp.headers.get("content-type", "")
    if resp.status_code == 200 and ctype.startswith("image/") and 0 < len(resp.content) <= MAX_BYTES:
        return resp.content
    return None


def fetch_logo(sym: str, get=_raw_get) -> tuple[bytes, str] | None:
    """(image bytes, content type) from the first source that has one, or None."""
    if market.asset_class(sym) == "crypto":
        coin = sym.split("-")[0]
        urls = [CRYPTO_ICON.format(coin=coin.lower())]
        try:
            found = get(COINGECKO_SEARCH, {"query": coin}).json().get("coins") or []
            hit = next((c for c in found if (c.get("symbol") or "").upper() == coin), None)
            if hit and hit.get("large"):
                urls.append(hit["large"])
        except Exception:  # noqa: BLE001 - a missing logo is never an error worth surfacing
            pass
    else:
        urls = [u.format(sym=sym) for u in STOCK_SOURCES]
    for url in urls:
        try:
            resp = get(url)
        except Exception:  # noqa: BLE001
            continue
        img = _image(resp)
        if img:
            return img, resp.headers.get("content-type", "image/png").split(";")[0]
    return None


def monogram(sym: str) -> bytes:
    """A letter on a circle whose color comes from the symbol, as SVG."""
    label = sym.split("-")[0][:1] or "?"
    hue = int(hashlib.sha1(sym.encode()).hexdigest()[:4], 16) % 360
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="32" '
            f'fill="hsl({hue},45%,42%)"/><text x="32" y="42" font-family="Helvetica,Arial,sans-serif" font-size="30" '
            f'font-weight="600" text-anchor="middle" fill="#fff">{escape(label)}</text></svg>').encode()


def logo(sym: str, get=_raw_get, now: float | None = None) -> tuple[bytes, str]:
    """The logo for a symbol: from the disk cache, fetched once, or a monogram."""
    s = safe(sym)
    if not s:
        return monogram("?"), "image/svg+xml"
    now = now or time.time()
    base = os.path.join(cache_dir(), s.replace("^", "_"))
    for ext, ctype in ((".png", "image/png"), (".jpg", "image/jpeg"), (".webp", "image/webp"), (".svg", "image/svg+xml")):
        if os.path.exists(base + ext):
            with open(base + ext, "rb") as fh:
                return fh.read(), ctype
    miss = base + ".miss"
    if os.path.exists(miss) and now - os.path.getmtime(miss) < MISS_RETRY:
        return monogram(s), "image/svg+xml"
    got = fetch_logo(s, get)
    if not got:
        with open(miss, "w") as fh:
            fh.write(str(int(now)))
        return monogram(s), "image/svg+xml"
    data, ctype = got
    ext = {"image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg"}.get(ctype, ".png")
    with _lock, open(base + ext, "wb") as fh:
        fh.write(data)
    return data, ctype


# ------------------------------------------------------------------ names

def _names_file() -> str:
    return os.path.join(cache_dir(), "names.json")


def _load_names() -> None:
    if _names:
        return
    try:
        with open(_names_file()) as fh:
            _names.update(json.load(fh))
    except (OSError, ValueError):
        pass


def _save_names() -> None:
    try:
        with _lock, open(_names_file(), "w") as fh:
            json.dump(_names, fh)
    except OSError:
        pass


def tidy(name: str) -> str:
    """'APPLE INC.' -> 'Apple Inc.'; names already in mixed case are kept."""
    name = (name or "").strip()
    if name.isupper() and len(name) > 4:
        name = " ".join(w if w in KEEP_UPPER else w.capitalize() for w in name.split())
    return name


def lookup_name(sym: str) -> str:
    if market.asset_class(sym) == "crypto":
        coin = sym.split("-")[0]
        try:
            data = http.get(COINBASE_CURRENCY.format(coin=coin), ttl=7 * 86400)
            return (data or {}).get("name") or coin
        except (http.DataUnavailable, KeyError, TypeError, ValueError):
            return coin
    try:
        from .providers import sec
        row = sec.ticker_map().by_ticker.get(sym.replace("-", "."), None) or sec.ticker_map().by_ticker.get(sym)
        if row and row.get("title"):
            return tidy(row["title"])
    except (http.DataUnavailable, KeyError, TypeError, ValueError):
        pass
    try:
        meta = http.get(YAHOO_CHART.format(sym=sym), params={"range": "1d", "interval": "1d"}, ttl=7 * 86400)["chart"]["result"][0]["meta"]
        return tidy(meta.get("longName") or meta.get("shortName") or "")
    except (http.DataUnavailable, KeyError, IndexError, TypeError, ValueError):
        return ""


def names(symbols: list[str]) -> dict[str, str]:
    """{symbol: company or coin name}, remembered on disk once found."""
    _load_names()
    out, changed = {}, False
    for raw in symbols[:200]:
        s = safe(raw)
        if not s:
            continue
        if s not in _names:
            n = lookup_name(s)
            if n:
                _names[s] = n
                changed = True
        out[s] = _names.get(s, "")
    if changed:
        _save_names()
    return out
