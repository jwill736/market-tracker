"""What professionals are reading, and early warnings on the areas you care about.

- Curators: Abnormal Returns (a daily link list for investment professionals, by section) and
  Barry Ritholtz's daily reads. The links they pick are the closest public signal of what people
  in the business are reading; an article picked by both ranks first.
- Desks: the market feeds of the FT (including Alphaville), Bloomberg, the WSJ, CNBC, MarketWatch,
  Seeking Alpha, the Economist, Yahoo Finance and Calculated Risk; the Fed and the SEC; CoinDesk,
  Decrypt and The Block for crypto.
- Every item is checked for your holdings (ticker, company or coin name) and your topics. A topic
  "heats up" when its mentions in the last 24 hours reach twice its daily pace of the three days
  before (and at least five).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urlsplit

from . import http

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126 Safari/537.36")


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    url: str
    kind: str          # curated / desk / policy / crypto


SOURCES = [
    Source("abnormalreturns", "Abnormal Returns", "https://abnormalreturns.com/feed/", "curated"),
    Source("ritholtz", "The Big Picture (Ritholtz)", "https://ritholtz.com/feed/", "curated"),
    Source("ft-markets", "FT Markets", "https://www.ft.com/markets?format=rss", "desk"),
    Source("ft-alphaville", "FT Alphaville", "https://www.ft.com/alphaville?format=rss", "desk"),
    Source("bloomberg", "Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss", "desk"),
    Source("wsj", "WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "desk"),
    Source("cnbc", "CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html", "desk"),
    Source("marketwatch", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "desk"),
    Source("seekingalpha", "Seeking Alpha", "https://seekingalpha.com/market_currents.xml", "desk"),
    Source("economist", "The Economist: Finance", "https://www.economist.com/finance-and-economics/rss.xml", "desk"),
    Source("yahoo", "Yahoo Finance", "https://finance.yahoo.com/news/rssindex", "desk"),
    Source("calculatedrisk", "Calculated Risk", "https://www.calculatedriskblog.com/feeds/posts/default?alt=rss", "desk"),
    Source("fed", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "policy"),
    Source("sec", "SEC press releases", "https://www.sec.gov/news/pressreleases.rss", "policy"),
    Source("coindesk", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "crypto"),
    Source("decrypt", "Decrypt", "https://decrypt.co/feed", "crypto"),
    Source("theblock", "The Block", "https://www.theblock.co/rss.xml", "crypto"),
]
MAX_AGE = timedelta(hours=72)
HEAT_MIN = 5
HEAT_RATIO = 2.0

DEFAULT_TOPICS = {
    "Rates & the Fed": "fed, fomc, powell, warsh, rate cut, rate hike, treasury yields, bond market, inflation, cpi",
    "AI trade": "artificial intelligence, ai, nvidia, data center, hyperscaler, openai, semiconductor, chips",
    "Crypto policy": "stablecoin, genius act, crypto regulation, bitcoin etf, crypto etf, sec crypto",
    "Tariffs & trade": "tariff, tariffs, trade war, export controls",
    "Recession watch": "recession, layoffs, jobless claims, unemployment rate",
}
CRYPTO_NAMES = {
    "BTC": ["Bitcoin"], "ETH": ["Ethereum", "Ether"], "SOL": ["Solana"], "XRP": ["XRP", "Ripple"],
    "DOGE": ["Dogecoin"], "ADA": ["Cardano"], "AVAX": ["Avalanche"], "LINK": ["Chainlink"], "LTC": ["Litecoin"],
    "DOT": ["Polkadot"], "SHIB": ["Shiba Inu"], "USDC": ["USDC"], "MATIC": ["Polygon"], "POL": ["Polygon"],
}
# Tickers that are also everyday words are matched only as $TICKER.
COMMON_WORDS = {"ALL", "NOW", "ARE", "IT", "ON", "ONE", "SO", "BIG", "FUN", "CAR", "LOW", "KEY", "RUN", "EAT",
                "HAS", "CAN", "OUT", "NEW", "BE", "GO", "AI", "EV", "USA", "CEO", "SEC", "FED", "IPO", "ETF"}
_LEGAL = r"\b(inc|incorporated|corp|corporation|company|co|ltd|limited|plc|sa|nv|ag|se|lp|llc|class [a-c]|the)\b\.?"
_GENERIC_TAIL = {"global", "markets", "platforms", "holdings", "group", "technologies", "therapeutics", "entertainment",
                 "brands", "systems", "international", "communications", "enterprises", "inc"}


@dataclass
class Item:
    title: str
    url: str
    source: str
    source_name: str
    kind: str
    published: str               # ISO UTC
    summary: str = ""
    mentions: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)


@dataclass
class Pick:
    title: str
    url: str
    domain: str
    curator: str
    section: str
    published: str
    picked_by: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ feed parsing

def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _date(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse_feed(text: str) -> list[dict]:
    """RSS 2.0 or Atom -> [{title, url, published, summary, html}] (html: full content if given)."""
    try:
        root = ET.fromstring(text.encode() if isinstance(text, str) else text)
    except ET.ParseError:
        return _parse_loose(text)
    out = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {c.tag.rsplit("}", 1)[-1]: c for c in el}
        link = fields.get("link")
        url = _text(link) or (link.get("href", "") if link is not None else "")
        if tag == "entry":
            for c in el:
                if c.tag.rsplit("}", 1)[-1] == "link" and c.get("rel", "alternate") == "alternate":
                    url = c.get("href", url)
                    break
        when = _date(_text(fields.get("pubDate")) or _text(fields.get("published")) or _text(fields.get("updated"))
                     or _text(fields.get("date")))
        html = _text(fields.get("encoded")) or _text(fields.get("content"))
        out.append({"title": _strip_html(_text(fields.get("title"))), "url": url.strip(),
                    "published": when.isoformat() if when else "",
                    "summary": _strip_html(_text(fields.get("description")) or _text(fields.get("summary")))[:400],
                    "html": html})
    return out


def _parse_loose(text: str) -> list[dict]:
    """Regex fallback for feeds that aren't well-formed XML."""
    out = []
    for block in re.findall(r"<item[\s>].*?</item>", text, re.S):
        def grab(tag):
            m = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", block, re.S)
            return m.group(1).strip() if m else ""
        when = _date(grab("pubDate"))
        out.append({"title": _strip_html(grab("title")), "url": grab("link"), "published": when.isoformat() if when else "",
                    "summary": _strip_html(grab("description"))[:400], "html": grab("content:encoded")})
    return out


# ------------------------------------------------------------------ curated links

def domain_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def abnormal_returns_links(html: str) -> list[tuple[str, str, str]]:
    """(section, title, url) from an Abnormal Returns link post."""
    out = []
    for section, body in re.findall(r'<h4[^>]*link-group-title[^>]*>(.*?)</h4>\s*<ul[^>]*>(.*?)</ul>', html, re.S):
        for url, inner in re.findall(r'<a[^>]*class="link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S):
            title = _strip_html(re.sub(r'<span class="source">.*?</span>', "", inner, flags=re.S))
            out.append((_strip_html(section), title, unescape(url)))
    return out


def ritholtz_links(html: str) -> list[tuple[str, str, str]]:
    """(section, title, url) from a Ritholtz daily reads post: outside links with real anchor text."""
    out = []
    for url, inner in re.findall(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, re.S):
        title = _strip_html(inner)
        d = domain_of(url)
        if len(title) < 15 or d.endswith("ritholtz.com") or "amzn" in d or "amazon." in d:
            continue
        out.append(("Reads", title, unescape(url)))
    return out


def _norm_url(url: str) -> str:
    p = urlsplit(url)
    return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()


def curated_picks(feeds: dict[str, list[dict]], now: datetime) -> list[Pick]:
    picks: dict[str, Pick] = {}
    for key, curator, is_list_post, extract in (
            ("abnormalreturns", "Abnormal Returns", lambda t: "links" in t.lower(), abnormal_returns_links),
            ("ritholtz", "Ritholtz", lambda t: re.search(r"\breads\b", t, re.I), ritholtz_links)):
        for post in feeds.get(key, []):
            when = _date(post["published"])
            if not when or now - when > timedelta(days=4) or not is_list_post(post["title"]):
                continue
            for section, title, url in extract(post["html"]):
                k = _norm_url(url)
                if k in picks:
                    if curator not in picks[k].picked_by:
                        picks[k].picked_by.append(curator)
                    continue
                picks[k] = Pick(title, url, domain_of(url), curator, section, post["published"], [curator])
    return sorted(picks.values(), key=lambda p: (-len(p.picked_by), p.published), reverse=False)


# ------------------------------------------------------------------ matching holdings and topics

def short_company_name(title: str) -> str:
    name = re.sub(_LEGAL, "", title, flags=re.I)
    name = re.sub(r"[,.]", " ", name)
    words = name.split()
    while len(words) > 1 and words[-1].lower() in _GENERIC_TAIL:
        words.pop()
    name = " ".join(words)
    return name.title() if name.isupper() else name


@dataclass
class Matcher:
    patterns: list[tuple[str, list[re.Pattern]]]

    def find(self, text: str) -> list[str]:
        return sorted({sym for sym, pats in self.patterns if any(p.search(text) for p in pats)})


def build_matcher(symbols: list[str], names: dict[str, str] | None = None) -> Matcher:
    """Tickers match case-sensitively ("NOW" the stock, not "now"), as $TICKER always and bare
    only when 3+ letters and not an everyday word; company and coin names match in any case.
    names: symbol -> company title from the SEC list."""
    out: list[tuple[str, list[re.Pattern]]] = []
    for sym in symbols:
        base = sym.removesuffix("-USD")
        tick = [rf"\${re.escape(base)}\b"]
        if len(base) >= 3 and base not in COMMON_WORDS:
            tick.append(rf"(?<![\w$]){re.escape(base)}(?![\w-])")
        words = list(CRYPTO_NAMES.get(base, [])) if sym.endswith("-USD") else []
        if not sym.endswith("-USD") and names and names.get(sym):
            short = short_company_name(names[sym])
            if len(short) >= 4:
                words.append(short)
        pats = [re.compile("|".join(tick))]
        if words:
            pats.append(re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")(?:'s)?\b", re.I))
        out.append((sym, pats))
    return Matcher(out)


def topic_patterns(topics: dict[str, str]) -> dict[str, re.Pattern]:
    out = {}
    for name, terms in topics.items():
        words = [t.strip() for t in terms.split(",") if t.strip()]
        if words:
            alt = "|".join(re.escape(w) if w.lower() != "ai" else r"(?-i:AI)" for w in words)
            out[name] = re.compile(rf"\b(?:{alt})\b", re.I)
    return out


def topic_heat(items: list[Item], picks: list[Pick], topics: dict[str, str], now: datetime) -> list[dict]:
    """Per topic: mentions in the last 24 h vs the daily pace of the 72 h before."""
    out = []
    for name in topics:
        recent = [i for i in items if name in i.topics and _age_h(i.published, now) <= 24]
        before = [i for i in items if name in i.topics and 24 < _age_h(i.published, now) <= 96]
        pace = len(before) / 3
        hot = len(recent) >= HEAT_MIN and len(recent) >= HEAT_RATIO * max(pace, 1)
        out.append({"name": name, "terms": topics[name], "last_24h": len(recent), "daily_pace": round(pace, 1),
                    "hot": hot, "picks": sum(1 for p in picks if name in p.topics),
                    "latest": [asdict(i) for i in sorted(recent, key=lambda i: i.published, reverse=True)[:5]]})
    return sorted(out, key=lambda t: (not t["hot"], -t["last_24h"]))


def _age_h(iso: str, now: datetime) -> float:
    d = _date(iso)
    return (now - d).total_seconds() / 3600 if d else 1e9


# ------------------------------------------------------------------ assembled

def fetch_all(sources: list[Source] = SOURCES, get=http.get) -> tuple[dict[str, list[dict]], list[str]]:
    def one(src: Source):
        try:
            return src.key, parse_feed(get(src.url, headers={"User-Agent": BROWSER_UA}, ttl=600, as_json=False)), None
        except (http.DataUnavailable, ET.ParseError, ValueError) as exc:
            return src.key, [], f"{src.name}: {exc}"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, sources))
    return {k: v for k, v, _ in results}, [e for _, _, e in results if e]


def build(symbols: list[str], names: dict[str, str], topics: dict[str, str], *, fetch=fetch_all,
          now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    feeds, errors = fetch()
    matcher = build_matcher(symbols, names)
    tpats = topic_patterns(topics)
    by_key = {s.key: s for s in SOURCES}
    items: list[Item] = []
    seen: set[str] = set()
    for key, entries in feeds.items():
        src = by_key.get(key) or Source(key, key, "", "desk")
        if src.kind == "curated":
            continue
        for e in entries:
            d = _date(e["published"])
            if not d or now - d > MAX_AGE or not e["title"]:
                continue
            k = re.sub(r"\W+", "", e["title"].lower())[:80]
            if k in seen:
                continue
            seen.add(k)
            text = e["title"] + " " + e["summary"]
            items.append(Item(e["title"], e["url"], key, src.name, src.kind, d.isoformat(), e["summary"],
                              matcher.find(text), [t for t, p in tpats.items() if p.search(text)]))
    items.sort(key=lambda i: i.published, reverse=True)
    picks = curated_picks(feeds, now)
    for p in picks:
        p.mentions = matcher.find(p.title)
        p.topics = [t for t, pat in tpats.items() if pat.search(p.title)]
    picks.sort(key=lambda p: (len(p.picked_by), p.published), reverse=True)
    domains: dict[str, int] = {}
    for p in picks:
        domains[p.domain] = domains.get(p.domain, 0) + 1
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "picks": [asdict(p) for p in picks[:60]],
        "outlets": sorted(domains.items(), key=lambda kv: -kv[1])[:12],
        "items": [asdict(i) for i in items[:250]],
        "mentions": [asdict(i) for i in items if i.mentions][:40] + [asdict(p) | {"kind": "curated"} for p in picks
                                                                         if p.mentions][:20],
        "topics": topic_heat(items, picks, topics, now),
        "sources": [{"key": s.key, "name": s.name, "kind": s.kind, "count": len(feeds.get(s.key, []))} for s in SOURCES],
        "errors": errors,
    }
