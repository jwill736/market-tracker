"""Market pulse: what is moving, what the news is covering, what is being bought quietly,
and which of your holdings deserve a second look.

Every list says *why* a ticker is on it. None of it is a buy or sell instruction: the tags are
rules over public data, and the backtest has not shown the composite score to predict returns.

- Movers: Yahoo's day gainers / losers / most-active screens (the whole US market), with a
  fallback to a fixed list of large caps if the screens are unavailable; crypto from Coinbase.
- Attention: each mover's headlines over 7 days, how many landed in the last 48 hours, how
  many publishers, and whether any came from outlets known for in-depth reporting.
- Sleepers: companies whose officers and directors bought recently (from the filing
  watcher's data), where the news hasn't noticed and the price hasn't run yet.
- Sell watch: rule-based flags on your holdings (trend, momentum, insiders, overextension,
  position size, dilution), each with its reason.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import alerts, dilution, http
from .providers import market, news, sec

SCREENER = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
SCREENS = {"gainers": "day_gainers", "losers": "day_losers", "active": "most_actives"}
DATA_URL = os.environ.get("MT_DATA_URL", "https://raw.githubusercontent.com/jwill736/market-tracker/journal-data")
FALLBACK_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "AVGO", "JPM", "LLY", "V", "UNH", "XOM",
    "MA", "JNJ", "PG", "HD", "COST", "ABBV", "MRK", "CVX", "CRM", "BAC", "NFLX", "AMD", "PEP", "KO", "WMT",
    "ADBE", "TMO", "ORCL", "CSCO", "ACN", "MCD", "ABT", "LIN", "INTC", "QCOM", "DIS", "WFC", "TXN", "PM",
    "INTU", "AMGN", "IBM", "CAT", "GE", "NOW", "UBER", "PLTR", "COIN", "MU", "SHOP", "PYPL", "SBUX", "BA",
]
CRYPTO = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "ADA-USD", "AVAX-USD", "LINK-USD", "LTC-USD", "DOT-USD"]
# Outlets whose coverage is usually reported rather than rewritten from a press release.
DEEP_SOURCES = ("reuters", "bloomberg", "wall street journal", "wsj", "financial times", "barron", "new york times",
                "the economist", "the information", "morningstar", "fortune", "axios", "associated press")
NEWS_HOT_48H = 5
DEEP_MIN_SOURCES = 6
UNUSUAL_VOLUME = 2.0
SLEEPER_MAX_NEWS_7D = 3
SLEEPER_MAX_RUN_1M = 0.15


@dataclass
class Mover:
    symbol: str
    name: str
    price: float | None
    change_pct: float | None
    volume: float | None = None
    rel_volume: float | None = None
    market_cap: float | None = None
    lists: list[str] = field(default_factory=list)
    attention: dict | None = None
    tags: list[str] = field(default_factory=list)


def parse_screen(data: dict, list_name: str) -> list[Mover]:
    try:
        quotes = data["finance"]["result"][0]["quotes"]
    except (KeyError, IndexError, TypeError):
        return []
    out = []
    for q in quotes:
        vol, avg = q.get("regularMarketVolume"), q.get("averageDailyVolume3Month")
        out.append(Mover(symbol=q["symbol"], name=q.get("shortName") or q.get("longName") or q["symbol"],
                         price=q.get("regularMarketPrice"), change_pct=q.get("regularMarketChangePercent"),
                         volume=vol, rel_volume=(vol / avg) if vol and avg else None,
                         market_cap=q.get("marketCap"), lists=[list_name]))
    return out


def fetch_screen(list_name: str, count: int = 25) -> list[Mover]:
    data = http.get(SCREENER, params={"scrIds": SCREENS[list_name], "count": count}, ttl=60)
    return parse_screen(data, list_name)


def fallback_movers(quote_fn=market.get_quote, universe: list[str] = FALLBACK_UNIVERSE) -> dict[str, list[Mover]]:
    """When the screens are unavailable: rank a fixed list of large caps by today's change."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        quotes = list(pool.map(lambda s: _safe(quote_fn, s), universe))
    movers = [Mover(q.symbol, q.symbol, q.price, q.change_pct) for q in quotes if q and q.change_pct is not None]
    up = sorted([m for m in movers if m.change_pct > 0], key=lambda m: -m.change_pct)[:15]
    down = sorted([m for m in movers if m.change_pct < 0], key=lambda m: m.change_pct)[:15]
    for m in up:
        m.lists = ["gainers"]
    for m in down:
        m.lists = ["losers"]
    return {"gainers": up, "losers": down, "active": []}


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (http.DataUnavailable, KeyError, ValueError, TypeError):
        return None


# ------------------------------------------------------------------ news attention

def _hours_ago(iso: str, now: datetime) -> float | None:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).total_seconds() / 3600


def is_deep_source(source: str) -> bool:
    s = (source or "").lower()
    return any(d in s for d in DEEP_SOURCES)


def attention(news_data: dict, now: datetime) -> dict:
    arts = news_data.get("articles") or []
    recent = [a for a in arts if (h := _hours_ago(a.get("published", ""), now)) is not None and h <= 48]
    deep = [a for a in arts if is_deep_source(a.get("source", ""))]
    sources = {a.get("source") for a in arts if a.get("source")}
    return {
        "count_7d": len(arts), "count_48h": len(recent), "sources": len(sources),
        "sentiment": news_data.get("avg_sentiment", 0.0),
        "headline": (recent or arts)[0] if arts else None,
        "deep": deep[:3],
    }


def tag(m: Mover) -> None:
    a = m.attention or {}
    if a.get("count_48h", 0) >= NEWS_HOT_48H:
        m.tags.append("In the news")
    if a.get("deep") or a.get("sources", 0) >= DEEP_MIN_SOURCES:
        m.tags.append("Deep coverage")
    if m.rel_volume and m.rel_volume >= UNUSUAL_VOLUME:
        m.tags.append("Unusual volume")
    if m.change_pct is not None and abs(m.change_pct) >= 5 and a.get("count_48h", 0) == 0:
        m.tags.append("Moving without news")


def add_attention(movers: list[Mover], news_fn=news.get_news, now: datetime | None = None, limit: int = 45) -> None:
    now = now or datetime.now(timezone.utc)
    todo = movers[:limit]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda m: _safe(news_fn, m.symbol, m.name if m.name != m.symbol else None, 7), todo))
    for m, nd in zip(todo, results):
        m.attention = attention(nd, now) if nd else None
        tag(m)


# ------------------------------------------------------------------ sleepers

@dataclass
class Sleeper:
    symbol: str
    company: str
    reasons: list[str]
    insider_value: float
    news_7d: int | None
    return_1m: float | None
    price: float | None
    change_pct: float | None
    dilution: str = ""


def insider_candidates(buys: list[alerts.Buy], today: date, days: int = 30) -> dict[str, dict]:
    """Companies with a recent cluster of officer/director buying or a $1M+ purchase by one of
    them. One line per company for the cluster and one for its largest buyer."""
    out: dict[str, dict] = {}
    for c in alerts.find_clusters(buys, today, window_days=days):
        if c.symbol.strip().upper() in alerts.NO_TICKER:
            continue
        out[c.symbol] = {"cik": c.issuer_cik, "company": c.issuer_name, "value": c.total_value,
                         "reasons": [f"{len(c.insiders)} insiders bought {alerts._money(c.total_value)} "
                                     f"({c.first_trade} to {c.last_trade})"]}
    since = (today - timedelta(days=days)).isoformat()
    from . import realtime
    per_insider: dict[tuple, dict] = {}
    for b in realtime.big_buys([x for x in buys if x.filed >= since]):
        if b.symbol.strip().upper() in alerts.NO_TICKER:
            continue
        agg = per_insider.setdefault((b.symbol, b.insider), {"buy": b, "value": 0.0})
        agg["value"] += b.value
    largest: dict[str, dict] = {}
    for (sym, _), agg in per_insider.items():
        if sym not in largest or agg["value"] > largest[sym]["value"]:
            largest[sym] = agg
    for sym, agg in largest.items():
        b = agg["buy"]
        entry = out.setdefault(sym, {"cik": b.issuer_cik, "company": b.issuer_name, "value": 0.0, "reasons": []})
        entry["value"] = max(entry["value"], agg["value"])
        who = "largest: " if entry["reasons"] else ""
        entry["reasons"].append(f"{who}{b.insider} ({b.role.split(',')[0]}) bought {alerts._money(agg['value'])}")
    return out


def find_sleepers(buys: list[alerts.Buy], today: date, *, quote_fn=market.get_quote,
                  history_fn=market.get_history, news_fn=news.get_news,
                  is_fund: Callable[[str], bool] = alerts.issuer_is_fund,
                  dilution_fn: Callable[[str], dilution.DilutionCheck] | None = None, limit: int = 25,
                  show: int = 12) -> list[Sleeper]:
    cands = sorted(insider_candidates(buys, today).items(), key=lambda kv: -kv[1]["value"])[:limit]
    check = dilution_fn or (lambda cik: dilution.check(cik, today))

    def evaluate(item):
        sym, c = item
        if is_fund(c["cik"]):
            return None
        q = _safe(quote_fn, sym)
        if q is None:
            return None               # no US quote (foreign listings, delisted): nothing to act on here
        hist = _safe(history_fn, sym, 60) or []
        nd = _safe(news_fn, sym, c["company"], 7)
        n7 = nd["count"] if nd else None
        r1m = (hist[-1].close / hist[-22].close - 1) if len(hist) >= 22 else None
        if (n7 is not None and n7 > SLEEPER_MAX_NEWS_7D) or (r1m is not None and r1m > SLEEPER_MAX_RUN_1M):
            return None
        d = check(c["cik"])
        reasons = list(c["reasons"])
        reasons.append(f"only {n7} headline{'' if n7 == 1 else 's'} this week" if n7 is not None else "news not checked")
        if r1m is not None:
            reasons.append(f"price {r1m:+.0%} over the past month")
        return Sleeper(symbol=sym, company=c["company"], reasons=reasons, insider_value=c["value"], news_7d=n7,
                       return_1m=r1m, price=q.price, change_pct=q.change_pct,
                       dilution=dilution.short_label(d))

    with ThreadPoolExecutor(max_workers=6) as pool:
        found = [s for s in pool.map(evaluate, cands) if s]
    # Active dilution goes to the bottom: insiders buying into an offering is a weaker story.
    found.sort(key=lambda s: (s.dilution == "Dilution: active", -s.insider_value))
    return found[:show]


def load_watcher_buys(get=http.get) -> list[alerts.Buy]:
    """The filing watcher's purchases, from the public journal-data branch."""
    return alerts.parse_buys(get(f"{DATA_URL}/insider_buys.csv", ttl=600, as_json=False))


# ------------------------------------------------------------------ sell watch

@dataclass
class Flag:
    severity: int          # 1 = worth knowing, 2 = worth acting on
    text: str


def sell_flags(analysis: dict, weight: float | None, dil: dilution.DilutionCheck | None,
               unrealized_pct: float | None) -> list[Flag]:
    ind = analysis.get("indicators") or {}
    sig = analysis.get("signal") or {}
    ins = analysis.get("insiders") or {}
    flags: list[Flag] = []
    if ind.get("above_sma200") is False:
        flags.append(Flag(2, "Below its 200-day average: the long-term trend has turned down"))
    mom = ind.get("momentum_12_1")
    if mom is not None and mom < 0:
        flags.append(Flag(1, f"12-month momentum is negative ({mom:+.0%})"))
    if sig.get("score") is not None and sig["score"] <= -15:
        flags.append(Flag(2, f"Composite signal is {sig.get('label', 'bearish').lower()} ({sig['score']:+.0f})"))
    if ins and not ins.get("open_market_buys") and (ins.get("discretionary_sell_value_usd") or 0) >= 1_000_000:
        flags.append(Flag(1, f"Insiders sold {alerts._money(ins['discretionary_sell_value_usd'])} outside planned "
                             f"(10b5-1) sales in {ins.get('window_days', 90)} days, with no buying"))
    r3, rsi = ind.get("return_3m"), ind.get("rsi14")
    if r3 is not None and rsi is not None and r3 > 0.40 and rsi > 75:
        flags.append(Flag(1, f"Up {r3:.0%} in 3 months with RSI {rsi:.0f}: extended; trimming locks in part of the gain"))
    max_w = sig.get("suggested_max_weight")
    if weight is not None and max_w and weight / 100 > max_w * 1.5:
        flags.append(Flag(2, f"{weight:.0f}% of your portfolio; its volatility suggests at most {max_w:.0%}"))
    elif weight is not None and weight > 25:
        flags.append(Flag(2, f"{weight:.0f}% of your portfolio in one position"))
    if dil is not None and dil.level == "active":
        flags.append(Flag(1, "The company is selling shares (recent offering filings)"))
    if unrealized_pct is not None and unrealized_pct < -20 and ind.get("above_sma200") is False:
        flags.append(Flag(1, f"Down {abs(unrealized_pct):.0f}% from your cost: a possible tax-loss sale"))
    return flags


def verdict(flags: list[Flag]) -> str:
    score = sum(f.severity for f in flags)
    return "Review" if score >= 3 else "Watch" if score else "No red flags"


def sell_watch(positions: list[dict], analyze_fn, cik_fn=None, dilution_fn=None) -> list[dict]:
    """positions: [{symbol, weight, unrealized_pct}] -> flags per holding, most flagged first."""
    def one(p):
        a = _safe(analyze_fn, p["symbol"]) or {}
        dil = None
        if market.asset_class(p["symbol"]) == "stock" and dilution_fn:
            cik = cik_fn(p["symbol"]) if cik_fn else None
            dil = dilution_fn(cik) if cik else None
        flags = sell_flags(a, p.get("weight"), dil, p.get("unrealized_pct"))
        sig = a.get("signal") or {}
        # Without the analysis only the size rule could run; an empty list would read as "all clear".
        v = verdict(flags) if a else ("Couldn't check" if not flags else verdict(flags))
        return {"symbol": p["symbol"], "verdict": v, "flags": [asdict(f) for f in flags],
                "score": sig.get("score"), "label": sig.get("label"), "weight": p.get("weight"),
                "unrealized_pct": p.get("unrealized_pct"), "error": None if a else "analysis unavailable"}

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(one, positions))
    order = {"Review": 0, "Watch": 1, "Couldn't check": 2, "No red flags": 3}
    rows.sort(key=lambda r: (order[r["verdict"]], -sum(f["severity"] for f in r["flags"])))
    return rows


# ------------------------------------------------------------------ assembled

def build(*, screen_fn=fetch_screen, quote_fn=market.get_quote, news_fn=news.get_news,
          buys_fn=load_watcher_buys, sleepers_fn=find_sleepers, today: date | None = None,
          now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    errors: list[str] = []
    lists: dict[str, list[Mover]] = {}
    for name in SCREENS:
        try:
            lists[name] = screen_fn(name)[:15]
        except http.DataUnavailable as exc:
            errors.append(f"{name}: {exc}")
    if not any(lists.values()):
        errors.append("Market screens unavailable; showing large caps only")
        lists = fallback_movers(quote_fn)
    with ThreadPoolExecutor(max_workers=8) as pool:
        cq = list(pool.map(lambda s: _safe(quote_fn, s), CRYPTO))
    lists["crypto"] = sorted([Mover(q.symbol, q.symbol.replace("-USD", ""), q.price, q.change_pct, lists=["crypto"])
                              for q in cq if q], key=lambda m: -abs(m.change_pct or 0))
    unique: dict[str, Mover] = {}
    for name, ms in lists.items():
        for m in ms:
            if m.symbol in unique:
                unique[m.symbol].lists.append(name)
            else:
                unique[m.symbol] = m
    add_attention(list(unique.values()), news_fn, now)
    everyone = list(unique.values())
    hot = sorted([m for m in everyone if m.attention], key=lambda m: -(m.attention["count_48h"]))[:10]
    deep = [m for m in everyone if m.attention and m.attention["deep"]][:10]
    try:
        sleepers = sleepers_fn(buys_fn(), today or now.date())
    except http.DataUnavailable as exc:
        errors.append(f"insider data: {exc}")
        sleepers = []
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "movers": {name: [asdict(unique[m.symbol]) for m in ms] for name, ms in lists.items()},
        "in_the_news": [asdict(m) for m in hot],
        "deep_coverage": [asdict(m) for m in deep],
        "sleepers": [asdict(s) for s in sleepers],
        "rules": {"hot_48h": NEWS_HOT_48H, "unusual_volume": UNUSUAL_VOLUME,
                  "sleeper_max_news_7d": SLEEPER_MAX_NEWS_7D, "sleeper_max_run_1m": SLEEPER_MAX_RUN_1M},
        "errors": errors,
    }


class Cache:
    """Keep an expensive result for `ttl` seconds (one per key)."""

    def __init__(self, ttl: float):
        self.ttl, self.store = ttl, {}

    def get(self, key, compute):
        hit = self.store.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        value = compute()
        self.store[key] = (time.monotonic(), value)
        return value


pulse_cache = Cache(180)
sellwatch_cache = Cache(600)


def cik_for_symbol(symbol: str) -> str | None:
    return _safe(lambda: sec.ticker_map().cik_for(symbol))
