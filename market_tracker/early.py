"""The early wire: tickers that are starting to move in places the mainstream press hasn't
caught up with yet.

Sources, each checked live on a server before use:
- Social: StockTwits trending symbols (with StockTwits' one-line reason) and ApeWisdom's count
  of Reddit mentions against 24 hours earlier (Reddit itself blocks servers).
- Press wires: companies' own releases, which usually land minutes before any article:
  GlobeNewswire directly, and Business Wire / PR Newswire / Accesswire through Google News.
  Releases are sorted into catalysts (deal, FDA, contract, guidance, buyback, offering...).
- SEC: 8-Ks that announce a material agreement, a completed acquisition or a change of control.
- Crypto: CoinGecko's trending searches, Binance's new-listing announcements, new Coinbase
  trading pairs, and stablecoins trading away from $1.

A signal is marked "early" when the ticker has at most two mainstream articles in the last 24
hours. Every signal is logged with the price when first seen (see early_log), so the wire gets
a track record like everything else here. None of this is a buy signal on its own: social
spikes in particular are where pump-and-dumps start.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from . import http, radar
from .providers import market, news
from .reading import BROWSER_UA, parse_feed

STOCKTWITS_TRENDING = "https://api.stocktwits.com/api/2/trending/symbols.json"
APEWISDOM = "https://apewisdom.io/api/v1.0/filter/{filter}/page/1"
GLOBENEWSWIRE = ("https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/"
                 "GlobeNewswire%20-%20News%20about%20Public%20Companies")
GOOGLE_NEWS = "https://news.google.com/rss/search"
WIRE_QUERY = "(site:businesswire.com OR site:prnewswire.com OR site:accessnewswire.com OR site:globenewswire.com) when:1d"
COINGECKO_TRENDING = "https://api.coingecko.com/api/v3/search/trending"
BINANCE_LISTINGS = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
                    "?type=1&catalogId=48&pageNo=1&pageSize=15")
COINBASE_PRODUCTS = "https://api.exchange.coinbase.com/products"
STABLECOINS = ["USDT-USD", "DAI-USD", "PYUSD-USD"]
DEPEG = 0.005               # 0.5% away from $1
MAINSTREAM_MAX = 2          # "early" while mainstream coverage in 24 h is at most this
MAINSTREAM = ("reuters", "bloomberg", "cnbc", "wall street journal", "wsj", "marketwatch", "barron", "financial times",
              "associated press", "ap news", "cnn", "fox business", "forbes", "business insider", "yahoo finance",
              "new york times", "the economist", "axios", "fortune")

CATALYSTS = [   # (pattern, label, strength, tone)
    (r"definitive (merger )?agreement|to acquire|to be acquired|merger agreement|agrees? to (buy|acquire)|tender offer",
     "Deal", 40, 1),
    (r"FDA (approval|approves|clears|clearance)|breakthrough (therapy|device) designation|accelerated approval|"
     r"(positive|met) (the )?primary endpoint|topline results", "FDA / trial", 40, 1),
    (r"strategic alternatives|exploring (a )?sale|going private|take[- ]private", "Strategic review", 35, 1),
    (r"(awarded|wins|secures|receives) .{0,40}(contract|order|award)|purchase order", "Contract", 25, 1),
    (r"raises? (its )?(full[- ]year |annual )?(guidance|outlook)|record (quarterly )?revenue", "Guidance up", 30, 1),
    (r"(share|stock) (repurchase|buyback)", "Buyback", 20, 1),
    (r"partnership|collaboration|strategic alliance", "Partnership", 15, 1),
    (r"uplist|approved for listing on (the )?(nasdaq|nyse)", "Uplisting", 20, 1),
    (r"(proposed |registered direct |underwritten |public )offering|at-the-market|pricing of", "Offering", 30, -1),
    (r"reverse (stock )?split", "Reverse split", 25, -1),
    (r"delist|deficiency notice|non-compliance", "Listing trouble", 30, -1),
    (r"guidance cut|lowers? (its )?(guidance|outlook)|withdraws guidance", "Guidance cut", 30, -1),
]
_CATALYSTS = [(re.compile(p, re.I), label, s, tone) for p, label, s, tone in CATALYSTS]
_TICKER_TAG = re.compile(r"\((?:NASDAQ|NYSE|NYSE American|NYSE MKT|NYSE Arca|OTCQX|OTCQB|OTC|Cboe|CBOE|TSX|TSXV)"
                         r"(?:\s*[:\-]\s*|\s+)([A-Z][A-Z.\-]{0,5})\)", re.I)
POSITIVE_8K = {"1.01": ("Material agreement signed", 25), "2.01": ("Acquisition or sale completed", 25),
               "5.01": ("Change in control", 35)}


@dataclass
class Signal:
    symbol: str
    kind: str                  # social / wire / filing / crypto / listing / depeg
    headline: str
    strength: float
    url: str = ""
    at: str = ""
    source: str = ""
    tone: int = 0              # +1 good news, -1 bad, 0 unknown
    name: str = ""


@dataclass
class Merged:
    symbol: str
    name: str
    strength: float
    kinds: list[str]
    signals: list[dict]
    tone: int
    mainstream_24h: int | None = None
    early: bool = False
    yours: bool = False
    asset: str = "stock"
    reasons: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ social

def stocktwits_signals(data: dict) -> list[Signal]:
    out = []
    for s in (data or {}).get("symbols") or []:
        sym = (s.get("symbol") or "").upper()
        if not sym:
            continue
        crypto = (s.get("instrument_class") or "").lower().startswith("crypto") or sym.endswith(".X")
        sym = sym.replace(".X", "") + "-USD" if crypto else sym
        score = float(s.get("trending_score") or 0)
        why = ((s.get("trends") or {}).get("summary") or "").strip()
        out.append(Signal(sym, "social", why or "Trending on StockTwits", min(35.0, 8 + score * 3),
                          f"https://stocktwits.com/symbol/{s.get('symbol')}", source="StockTwits",
                          name=s.get("title") or ""))
    return out


def apewisdom_signals(data: dict, crypto: bool = False, min_mentions: int = 15) -> list[Signal]:
    out = []
    for r in (data or {}).get("results") or []:
        m, before = int(r.get("mentions") or 0), int(r.get("mentions_24h_ago") or 0)
        if m < min_mentions:
            continue
        ratio = (m + 1) / (before + 5)
        jump = int(r.get("rank_24h_ago") or 999) - int(r.get("rank") or 999)
        if ratio < 1.5 and jump < 10:
            continue                              # busy but not rising
        sym = (r.get("ticker") or "").upper()
        sym = sym.replace(".X", "") + "-USD" if crypto else sym
        strength = min(40.0, 12 * math.log2(ratio + 1) + (8 if jump >= 10 else 0))
        out.append(Signal(sym, "social", f"Reddit mentions {m} in 24 h, up from {before}"
                          + (f"; rank {r.get('rank_24h_ago')} → {r.get('rank')}" if jump >= 10 else ""),
                          strength, f"https://apewisdom.io/stocks/{r.get('ticker')}/", source="Reddit (ApeWisdom)",
                          name=unescape_amp(r.get("name") or "")))
    return out


def unescape_amp(s: str) -> str:
    return s.replace("&amp;", "&")


# ------------------------------------------------------------------ press wires

def classify_release(text: str) -> tuple[str, float, int] | None:
    for pat, label, strength, tone in _CATALYSTS:
        if pat.search(text):
            return label, strength, tone
    return None


def wire_signals(items: list[dict], source: str, name_to_ticker=None) -> list[Signal]:
    out = []
    for it in items:
        text = f"{it.get('title', '')} {it.get('summary', '')} {it.get('html', '')[:2000]}"
        tickers = {m.group(1).upper().rstrip(".") for m in _TICKER_TAG.finditer(text)}
        if not tickers and name_to_ticker:
            t = name_to_ticker(it.get("title", ""))
            if t:
                tickers = {t}
        got = classify_release(it.get("title", "") + " " + it.get("summary", ""))
        if not tickers or not got:
            continue
        label, strength, tone = got
        for t in sorted(tickers)[:2]:
            out.append(Signal(t, "wire", f"{label}: {it.get('title', '')}", strength, it.get("url", ""),
                              it.get("published", ""), source, tone))
    return out


def google_wire_items(get=http.get) -> list[dict]:
    text = get(GOOGLE_NEWS, params={"q": WIRE_QUERY, "hl": "en-US", "gl": "US", "ceid": "US:en"},
               headers={"User-Agent": BROWSER_UA}, ttl=120, as_json=False)
    items = parse_feed(text)
    for it in items:            # Google appends " - Source" to titles
        it["title"] = re.sub(r"\s+-\s+(Business Wire|PR Newswire|GlobeNewswire|ACCESS Newswire|Accesswire)$", "",
                             it["title"])
    return items


# ------------------------------------------------------------------ SEC catalysts

def filing_signals(entries: list[radar.FeedEntry], tickers: dict[str, str]) -> list[Signal]:
    out, seen = [], set()
    for e in entries:
        if e.role.lower().startswith("filed by") or e.accession in seen:
            continue
        hits = [POSITIVE_8K[c] for c in e.items if c in POSITIVE_8K]
        sym = tickers.get(e.cik)
        if not hits or not sym:
            continue
        seen.add(e.accession)
        label, strength = max(hits, key=lambda h: h[1])
        out.append(Signal(sym, "filing", f"{label} (8-K): {e.name}", strength, e.link, e.updated, "SEC EDGAR", 0, e.name))
    return out


# ------------------------------------------------------------------ crypto

def coingecko_signals(data: dict) -> list[Signal]:
    out = []
    for c in ((data or {}).get("coins") or [])[:10]:
        it = c.get("item") or {}
        sym = (it.get("symbol") or "").upper()
        chg = ((it.get("data") or {}).get("price_change_percentage_24h") or {}).get("usd")
        why = f"Trending on CoinGecko searches (#{(it.get('score') or 0) + 1})" + (f", {chg:+.1f}% in 24 h" if chg else "")
        out.append(Signal(sym + "-USD", "crypto", why, 20 + min(15, abs(chg or 0) / 2), f"https://www.coingecko.com/en/coins/{it.get('id')}",
                          source="CoinGecko", name=it.get("name") or ""))
    return out


_LISTING = re.compile(r"Binance Will List ([^(]+)\(([A-Z0-9]+)\)", re.I)


def binance_signals(data: dict, now: datetime) -> list[Signal]:
    out = []
    for cat in ((data or {}).get("data") or {}).get("catalogs") or []:
        for a in cat.get("articles") or []:
            m = _LISTING.search(a.get("title", ""))
            when = datetime.fromtimestamp((a.get("releaseDate") or 0) / 1000, timezone.utc)
            if not m or now - when > timedelta(days=3):
                continue
            out.append(Signal(m.group(2).upper() + "-USD", "listing", a["title"], 40,
                              f"https://www.binance.com/en/support/announcement/{a.get('code')}", when.isoformat(),
                              "Binance", 1, m.group(1).strip()))
    return out


def coinbase_new_pairs(products: list[dict], known: set[str]) -> tuple[list[Signal], set[str]]:
    """New USD trading pairs on Coinbase since the last check (none on the first check)."""
    live = {p["id"] for p in products or [] if p.get("quote_currency") == "USD" and p.get("status") == "online"}
    new = sorted(live - known) if known else []
    return ([Signal(p, "listing", f"New on Coinbase: {p} started trading", 40,
                    f"https://www.coinbase.com/price/{p.split('-')[0].lower()}", source="Coinbase", tone=1) for p in new],
            live)


def depeg_signals(quote_fn=market.get_quote) -> list[Signal]:
    out = []
    for sym in STABLECOINS:
        try:
            q = quote_fn(sym)
        except http.DataUnavailable:
            continue
        if abs(q.price - 1) >= DEPEG:
            out.append(Signal(sym, "depeg", f"{sym.split('-')[0]} at ${q.price:.4f}: {abs(q.price - 1) * 100:.2f}% off its $1 peg",
                              70, f"https://www.coinbase.com/price/{sym.split('-')[0].lower()}", q.as_of, "Coinbase", -1))
    return out


# ------------------------------------------------------------------ merge and mainstream check

def mainstream_count(data: dict, now: datetime) -> int:
    n = 0
    for a in (data or {}).get("articles") or []:
        try:
            t = datetime.fromisoformat(a.get("published", "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if (now - (t if t.tzinfo else t.replace(tzinfo=timezone.utc))).total_seconds() <= 86400 and \
                any(m in (a.get("source") or "").lower() for m in MAINSTREAM):
            n += 1
    return n


def merge(signals: list[Signal], mine: set[str]) -> list[Merged]:
    by: dict[str, Merged] = {}
    for s in signals:
        if not s.symbol or len(s.symbol) > 12:
            continue
        m = by.get(s.symbol)
        if m is None:
            m = by[s.symbol] = Merged(s.symbol, s.name, 0.0, [], [], 0, asset=market.asset_class(s.symbol),
                                      yours=s.symbol in mine)
        m.signals.append(asdict(s))
        m.name = m.name or s.name
        if s.kind not in m.kinds:
            m.kinds.append(s.kind)
        m.tone += s.tone
    for m in by.values():
        best = sorted((x["strength"] for x in m.signals), reverse=True)
        # Independent kinds of evidence count more than repeats of one kind.
        m.strength = round(min(100.0, best[0] + 0.5 * sum(best[1:3]) + 10 * (len(m.kinds) - 1)), 1)
        m.reasons = [x["headline"] for x in sorted(m.signals, key=lambda x: -x["strength"])][:4]
        m.tone = (m.tone > 0) - (m.tone < 0)
    return sorted(by.values(), key=lambda m: -m.strength)


def check_mainstream(merged: list[Merged], now: datetime, news_fn=None, limit: int = 15) -> None:
    news_fn = news_fn or news.get_news
    todo = merged[:limit]

    def one(m: Merged):
        try:
            return m, mainstream_count(news_fn(m.symbol, m.name or None, 2), now)
        except (http.DataUnavailable, KeyError, ValueError):
            return m, None
    with ThreadPoolExecutor(max_workers=6) as pool:
        for m, n in pool.map(one, todo):
            m.mainstream_24h = n
            m.early = n is not None and n <= MAINSTREAM_MAX


# ------------------------------------------------------------------ assembled

def gather(*, get=http.get, now: datetime | None = None, known_pairs: set[str] | None = None,
           quote_fn=market.get_quote, tickers_fn=radar.cik_to_ticker) -> tuple[list[Signal], list[str], set[str]]:
    now = now or datetime.now(timezone.utc)
    errors: list[str] = []
    out: list[Signal] = []
    pairs: set[str] = set(known_pairs or ())

    def attempt(name, fn):
        try:
            return fn()
        except (http.DataUnavailable, ET.ParseError, KeyError, ValueError, TypeError) as exc:
            errors.append(f"{name}: {str(exc)[:120]}")
            return None

    browser = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
    tasks = {
        "StockTwits": lambda: stocktwits_signals(get(STOCKTWITS_TRENDING, headers=browser, ttl=120)),
        "Reddit stocks": lambda: apewisdom_signals(get(APEWISDOM.format(filter="all-stocks"), headers=browser, ttl=300)),
        "Reddit crypto": lambda: apewisdom_signals(get(APEWISDOM.format(filter="all-crypto"), headers=browser, ttl=300),
                                                   crypto=True, min_mentions=5),
        "GlobeNewswire": lambda: wire_signals(parse_feed(get(GLOBENEWSWIRE, headers={"User-Agent": BROWSER_UA}, ttl=120,
                                                             as_json=False, timeout=40)), "GlobeNewswire"),
        "Wires via Google": lambda: wire_signals(google_wire_items(get), "Press release"),
        "CoinGecko": lambda: coingecko_signals(get(COINGECKO_TRENDING, headers=browser, ttl=300)),
        "Binance": lambda: binance_signals(get(BINANCE_LISTINGS, headers=browser, ttl=300), now),
        "SEC 8-K": lambda: filing_signals(radar.fetch_feed("8-K", 100, get), tickers_fn()),
        "Stablecoins": lambda: depeg_signals(quote_fn),
    }
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {name: pool.submit(attempt, name, fn) for name, fn in tasks.items()}
        for f in futures.values():
            out += f.result() or []
    got = attempt("Coinbase listings", lambda: coinbase_new_pairs(get(COINBASE_PRODUCTS, ttl=300), pairs))
    if got:
        new, pairs = got
        out += new
    return out, errors, pairs


def build(mine: set[str], *, gather_fn=None, news_fn=None, now: datetime | None = None,
          known_pairs: set[str] | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    gather_fn = gather_fn or gather
    news_fn = news_fn or news.get_news
    signals, errors, pairs = gather_fn(now=now, known_pairs=known_pairs)
    merged = merge(signals, mine)
    # Check press coverage for yours first, then the strongest.
    todo = sorted((m for m in merged if m.kinds != ["depeg"]), key=lambda m: (not m.yours, -m.strength))
    check_mainstream(todo, now, news_fn)
    merged.sort(key=lambda m: (not m.yours, not m.early, -m.strength))
    return {"generated_at": now.isoformat(timespec="seconds"), "signals": [asdict(m) for m in merged[:80]],
            "errors": errors, "pairs": sorted(pairs), "mainstream_max": MAINSTREAM_MAX}
