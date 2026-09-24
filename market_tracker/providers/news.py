"""Headline collection (Google News RSS, Yahoo Finance RSS, optional Finnhub) and a
finance-tuned lexicon sentiment score.

Lexicon sentiment is crude: it reads headlines, not articles, and misses sarcasm and
context ("beats low expectations"). Treat it as a volume-weighted mood gauge; use the
Claude deep dive for actual reading comprehension.
"""

from __future__ import annotations

import email.utils
import html
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote_plus

from .. import http
from ..config import settings
from .market import asset_class, normalize_symbol

POSITIVE = {
    "beat", "beats", "surge", "surges", "soar", "soars", "jump", "jumps", "rally", "rallies", "record",
    "upgrade", "upgraded", "outperform", "bullish", "buy", "growth", "profit", "profitable", "strong",
    "gain", "gains", "rise", "rises", "raised", "raises", "boost", "boosts", "approval", "approved",
    "breakthrough", "expands", "expansion", "partnership", "wins", "win", "exceeds", "tops", "optimistic",
    "rebound", "rebounds", "accelerates", "buyback", "dividend", "upside", "inflows", "adoption",
    "all-time", "high", "highs", "momentum", "robust", "recovery", "beat-and-raise",
}
NEGATIVE = {
    "miss", "misses", "plunge", "plunges", "tumble", "tumbles", "crash", "crashes", "fall", "falls",
    "drop", "drops", "slump", "slumps", "downgrade", "downgraded", "underperform", "bearish", "sell",
    "loss", "losses", "weak", "cut", "cuts", "lawsuit", "sued", "probe", "investigation", "fraud",
    "recall", "layoffs", "layoff", "bankruptcy", "default", "warning", "warns", "decline", "declines",
    "slows", "slowdown", "risk", "risks", "hack", "hacked", "exploit", "outflows", "charges",
    "fine", "fined", "delay", "delays", "halt", "halts", "concern", "concerns", "fears", "low", "lows",
    "downside", "dilution", "resigns", "subpoena", "antitrust", "tariff", "tariffs",
}
NEGATORS = {"not", "no", "never", "without", "fails", "failed"}

_WORD = re.compile(r"[a-z][a-z\-']+")


def sentiment(text: str) -> float:
    """Score in [-1, 1]; 0 means neutral or no lexicon hits."""
    words = _WORD.findall(text.lower())
    score = 0
    hits = 0
    for i, w in enumerate(words):
        polarity = 1 if w in POSITIVE else -1 if w in NEGATIVE else 0
        if not polarity:
            continue
        if i > 0 and words[i - 1] in NEGATORS:
            polarity = -polarity
        score += polarity
        hits += 1
    return 0.0 if not hits else max(-1.0, min(1.0, score / (hits + 1)))


@dataclass
class Article:
    title: str
    source: str
    url: str
    published: str  # ISO timestamp
    sentiment: float
    feed: str


def _parse_date(txt: str | None) -> str:
    if not txt:
        return ""
    try:
        return email.utils.parsedate_to_datetime(txt).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return txt


def parse_rss(xml_text: str, feed: str) -> list[Article]:
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        if not title:
            continue
        source_el = item.find("source")
        source = source_el.text.strip() if source_el is not None and source_el.text else feed
        # Google News appends " - Publisher" to titles.
        if feed == "google" and source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        out.append(Article(title=title, source=source, url=(item.findtext("link") or "").strip(),
                           published=_parse_date(item.findtext("pubDate")), sentiment=sentiment(title),
                           feed=feed))
    return out


def _google(query: str) -> list[Article]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    return parse_rss(http.get(url, ttl=600, as_json=False), "google")


def _yahoo(symbol: str) -> list[Article]:
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote_plus(symbol)}&region=US&lang=en-US"
    return parse_rss(http.get(url, ttl=600, as_json=False), "yahoo")


def _finnhub(symbol: str, days: int) -> list[Article]:
    if not settings.finnhub_api_key:
        return []
    to = date.today()
    data = http.get("https://finnhub.io/api/v1/company-news", ttl=600, params={
        "symbol": symbol, "from": (to - timedelta(days=days)).isoformat(), "to": to.isoformat(),
        "token": settings.finnhub_api_key})
    return [Article(title=d.get("headline", ""), source=d.get("source", "finnhub"), url=d.get("url", ""),
                    published=datetime.fromtimestamp(d.get("datetime", 0), tz=timezone.utc).isoformat(),
                    sentiment=sentiment(d.get("headline", "") + " " + d.get("summary", "")), feed="finnhub")
            for d in data or [] if d.get("headline")]


def _dedupe(articles: list[Article]) -> list[Article]:
    seen: set[str] = set()
    out = []
    for a in sorted(articles, key=lambda a: a.published, reverse=True):
        key = re.sub(r"[^a-z0-9]", "", a.title.lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "as", "at", "by", "is",
             "are", "its", "it", "from", "after", "stock", "stocks", "shares", "price", "says", "why", "how",
             "what", "this", "that", "be", "will", "new", "today", "here", "s", "vs", "into", "over", "up",
             "down", "than", "more", "you", "your", "now", "could", "should", "may", "amid", "week", "year"}


def top_terms(articles: list[Article], n: int = 12) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for a in articles:
        counts.update({w for w in _WORD.findall(a.title.lower()) if w not in STOPWORDS and len(w) > 2})
    return counts.most_common(n)


def summarize(articles: list[Article]) -> dict:
    scored = [a.sentiment for a in articles if a.sentiment]
    return {
        "count": len(articles),
        "avg_sentiment": sum(scored) / len(scored) if scored else 0.0,
        "positive": sum(1 for a in articles if a.sentiment > 0.1),
        "negative": sum(1 for a in articles if a.sentiment < -0.1),
        "top_terms": top_terms(articles),
        "articles": [asdict(a) for a in articles],
    }


def get_news(symbol: str, company: str | None = None, days: int = 14, limit: int = 40) -> dict:
    sym = normalize_symbol(symbol)
    cls = asset_class(sym)
    base = sym.split("-")[0]
    articles: list[Article] = []
    errors: list[str] = []
    sources = [lambda: _google(f"{company or base} {'crypto' if cls == 'crypto' else 'stock'} when:{days}d")]
    if cls == "stock":
        sources += [lambda: _yahoo(sym), lambda: _finnhub(sym, days)]
    for fetch in sources:
        try:
            articles += fetch()
        except (http.DataUnavailable, ET.ParseError) as exc:
            errors.append(str(exc))
    articles = _dedupe(articles)[:limit]
    return dict(summarize(articles), symbol=sym, errors=errors)


def market_news(limit: int = 40) -> dict:
    articles: list[Article] = []
    errors: list[str] = []
    for q in ("stock market when:2d", "crypto market bitcoin when:2d", "federal reserve economy when:2d",
              "billionaire investor buys stake when:14d"):
        try:
            articles += _google(q)
        except (http.DataUnavailable, ET.ParseError) as exc:
            errors.append(str(exc))
    articles = _dedupe(articles)[:limit]
    return dict(summarize(articles), errors=errors)
