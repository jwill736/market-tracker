"""Price data for stocks (Yahoo Finance chart API, optional Finnhub) and crypto (Coinbase Exchange).

Crypto quotes from Coinbase are true real-time spot prices. Yahoo stock quotes are
real-time for most US listings during market hours but can be delayed for some venues;
set FINNHUB_API_KEY for an explicitly real-time US quote source.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .. import http
from ..config import settings

KNOWN_CRYPTO = {
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK", "LTC", "BCH",
    "MATIC", "POL", "ATOM", "UNI", "XLM", "ALGO", "AAVE", "NEAR", "APT", "ARB", "OP",
    "SHIB", "PEPE", "SUI", "HBAR", "ETC", "FIL", "ICP", "INJ", "RNDR", "TIA", "SEI",
}
_CRYPTO_QUOTES = ("-USD", "-USDT", "-USDC", "-EUR", "-GBP")


@dataclass
class Quote:
    symbol: str
    asset_class: str  # "stock" | "crypto"
    price: float
    previous_close: float | None
    change_pct: float | None
    currency: str
    source: str
    as_of: str
    session: str = ""    # stocks: pre / regular / post / closed; crypto: "24h"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PriceBar:
    date: str  # ISO date
    close: float
    volume: float | None = None


def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if s in KNOWN_CRYPTO:
        return f"{s}-USD"
    return s


def asset_class(symbol: str) -> str:
    s = normalize_symbol(symbol)
    return "crypto" if s.endswith(_CRYPTO_QUOTES) else "stock"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------- stocks (Yahoo)

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def _yahoo_chart(symbol: str, range_: str, interval: str, ttl: float, *, period_days: int | None = None) -> dict:
    """`period_days` asks for an explicit window (period1/period2) instead of a named range:
    Yahoo can silently return weekly or monthly bars for range=max."""
    if period_days:
        now = int(datetime.now(timezone.utc).timestamp())
        params = {"period1": now - period_days * 86400, "period2": now, "interval": interval}
    else:
        params = {"range": range_, "interval": interval}
    data = http.get(YAHOO_CHART.format(symbol=symbol), params=params, ttl=ttl)
    try:
        result = data["chart"]["result"][0]
    except (KeyError, IndexError, TypeError) as exc:
        err = (data or {}).get("chart", {}).get("error") if isinstance(data, dict) else None
        raise http.DataUnavailable(f"Yahoo returned no data for {symbol}: {err}") from exc
    return result


def parse_yahoo_quote(symbol: str, result: dict) -> Quote:
    meta = result["meta"]
    price = float(meta["regularMarketPrice"])
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    prev = float(prev) if prev else None
    return Quote(
        symbol=symbol,
        asset_class="stock",
        price=price,
        previous_close=prev,
        change_pct=((price / prev - 1) * 100) if prev else None,
        currency=meta.get("currency", "USD"),
        source="yahoo",
        as_of=_iso(meta.get("regularMarketTime") or datetime.now(timezone.utc).timestamp()),
    )


def parse_yahoo_history(result: dict) -> list[PriceBar]:
    stamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    adj = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
    closes = adj or quote.get("close") or []
    volumes = quote.get("volume") or [None] * len(stamps)
    bars = []
    for ts, close, vol in zip(stamps, closes, volumes):
        if close is None:
            continue
        bars.append(PriceBar(date=datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(),
                             close=float(close), volume=vol))
    return bars


def _finnhub_quote(symbol: str) -> Quote | None:
    if not settings.finnhub_api_key:
        return None
    try:
        data = http.get("https://finnhub.io/api/v1/quote",
                        params={"symbol": symbol, "token": settings.finnhub_api_key}, ttl=5)
    except http.DataUnavailable:
        return None
    if not data or not data.get("c"):
        return None
    prev = data.get("pc") or None
    return Quote(symbol=symbol, asset_class="stock", price=float(data["c"]), previous_close=prev,
                 change_pct=data.get("dp"), currency="USD", source="finnhub",
                 as_of=_iso(data.get("t") or datetime.now(timezone.utc).timestamp()))


# ---------------------------------------------------------------- crypto (Coinbase)

COINBASE = "https://api.exchange.coinbase.com/products/{product}"


def _coinbase_quote(product: str) -> Quote:
    ticker = http.get(COINBASE.format(product=product) + "/ticker", ttl=5)
    stats = http.get(COINBASE.format(product=product) + "/stats", ttl=30)
    price = float(ticker["price"])
    prev = float(stats["open"]) if stats.get("open") else None  # 24h open
    return Quote(symbol=product, asset_class="crypto", price=price, previous_close=prev,
                 change_pct=((price / prev - 1) * 100) if prev else None,
                 currency=product.split("-")[1], source="coinbase",
                 as_of=ticker.get("time") or datetime.now(timezone.utc).isoformat())


def parse_coinbase_candles(candles: list) -> list[PriceBar]:
    # Coinbase candle: [time, low, high, open, close, volume], newest first.
    bars = [PriceBar(date=datetime.fromtimestamp(c[0], tz=timezone.utc).date().isoformat(),
                     close=float(c[4]), volume=float(c[5])) for c in candles]
    bars.sort(key=lambda b: b.date)
    return bars


def _coinbase_history(product: str, days: int) -> list[PriceBar]:
    # Coinbase returns at most 300 candles per request; page backwards for longer windows.
    now = int(datetime.now(timezone.utc).timestamp())
    bars: dict[str, PriceBar] = {}
    end = now
    remaining = days
    while remaining > 0:
        span = min(remaining, 300)
        start = end - span * 86400
        candles = http.get(COINBASE.format(product=product) + "/candles",
                           params={"granularity": 86400, "start": _iso(start), "end": _iso(end)}, ttl=900)
        for bar in parse_coinbase_candles(candles or []):
            bars[bar.date] = bar
        end = start
        remaining -= span
        if not candles:
            break
    return [bars[d] for d in sorted(bars)]


# ---------------------------------------------------------------- public API

def get_quote(symbol: str) -> Quote:
    sym = normalize_symbol(symbol)
    if asset_class(sym) == "crypto":
        return _coinbase_quote(sym)
    return _finnhub_quote(sym) or parse_yahoo_quote(sym, _yahoo_chart(sym, "5d", "1d", ttl=15))


def market_session(meta: dict, now: float | None = None) -> str:
    """'pre', 'regular', 'post' or 'closed', from the trading periods Yahoo reports."""
    periods = meta.get("currentTradingPeriod") or {}
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    for name in ("pre", "regular", "post"):
        w = periods.get(name) or {}
        if w.get("start", 0) <= now < w.get("end", 0):
            return name
    return "closed"


def parse_live_quote(symbol: str, result: dict, now: float | None = None) -> Quote:
    """Today's 1-minute bars including pre-market and after-hours trading. Outside regular hours
    the latest extended-hours trade is the price (as Robinhood shows it); the change is measured
    from the previous regular close either way."""
    q = parse_yahoo_quote(symbol, result)
    meta = result["meta"]
    stamps = result.get("timestamp") or []
    closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    last = next(((t, c) for t, c in zip(reversed(stamps), reversed(closes)) if c is not None), None)
    q.session = market_session(meta, now)
    regular_time = meta.get("regularMarketTime") or 0
    if last and last[0] > regular_time:
        q.price = float(last[1])
        q.as_of = _iso(last[0])
        q.change_pct = (q.price / q.previous_close - 1) * 100 if q.previous_close else None
    return q


def get_live_quote(symbol: str) -> Quote:
    """The freshest price available without an API key: extended hours included for stocks."""
    sym = normalize_symbol(symbol)
    if asset_class(sym) == "crypto":
        q = _coinbase_quote(sym)
        q.session = "24h"
        return q
    try:
        result = http.get(YAHOO_CHART.format(symbol=sym),
                          params={"range": "1d", "interval": "1m", "includePrePost": "true"}, ttl=3)
        return parse_live_quote(sym, result["chart"]["result"][0])
    except (http.DataUnavailable, KeyError, IndexError, TypeError):
        return get_quote(sym)


def get_history(symbol: str, days: int = 400) -> list[PriceBar]:
    """Daily closes, oldest first. Stocks use split/dividend-adjusted closes."""
    sym = normalize_symbol(symbol)
    if asset_class(sym) == "crypto":
        return _coinbase_history(sym, days)
    if days <= 1825:
        range_ = "1y" if days <= 365 else "2y" if days <= 730 else "5y"
        result = _yahoo_chart(sym, range_, "1d", ttl=900)
    else:
        # Explicit daily window; ~1.46 calendar days per trading day plus slack.
        result = _yahoo_chart(sym, "", "1d", ttl=900, period_days=int(days * 1.5) + 10)
    granularity = result.get("meta", {}).get("dataGranularity")
    if granularity and granularity != "1d":
        raise http.DataUnavailable(f"Yahoo returned {granularity} bars for {sym}, not daily")
    return parse_yahoo_history(result)[-days:]
