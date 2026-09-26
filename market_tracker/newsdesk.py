"""The news desk: many sources, weighed, before anything touches a decision.

A headline is not a fact, and twenty copies of one press release are not twenty sources. For
each company or coin you own, this reads several independent kinds of source:

- aggregators: Google News (hundreds of publishers, including Reuters and AP), Yahoo Finance,
  Finnhub (with a free key), Nasdaq's per-symbol feed;
- newsrooms read directly: CNBC, the Wall Street Journal, MarketWatch, Bloomberg (headlines);
- press-release wires: PR Newswire, Business Wire, GlobeNewswire;
- regulators: SEC press releases (enforcement actions are announced there), the FDA, the FTC;
  plus the SEC filing radar (8-Ks, going-concern language);
- crypto: CoinDesk, Cointelegraph, Decrypt, The Block;
- GDELT, a free index of world news, to count how many distinct outlets carry a story.

Then, following what the research says (see the README's news section for citations):
1. Stories are clustered: headlines about one event within 72 hours are one story.
2. Sources are counted by outlet, weighted by kind: a regulator or filing 1.0, a top newsroom
   0.8, another newsroom 0.6, a company press release 0.5 (self-serving), blogs and
   aggregator content 0.4, social 0.2. Confidence = 1 - product of (1 - weight).
3. Each story gets an event type. Tier A threatens the reason you own it: accounting
   restatement, auditor leaving, SEC or DOJ action, going concern, dividend cut, guidance cut,
   sudden CEO/CFO exit, bankruptcy or delisting, an FDA rejection, an exchange hack. Tier B
   deserves a look (deals, big contracts, analyst moves, earnings). Tier C is noise.
4. "Reportedly", "in talks", "people familiar" stories are unconfirmed until a non-rumor source
   or a filing backs them.
5. A story that repeats one from the last 30 days is marked stale: recycled news tends to
   reverse, not continue.

What it may do: a Tier A story with confidence of at least 0.8 (or any regulator/filing
source) puts the holding under "Review" in the hold plan and pauses new buys of it until you
look. That's all. It never recommends selling on news alone, never trades on headline mood, and
never counts buzz as confirmation. By the time a large company's news is a headline, the price
has usually moved already; news is for checking the reason you own it, not for timing.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse

from . import http

MARKET_FEEDS = {
    "CNBC": ("newsroom1", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    "CNBC Earnings": ("newsroom1", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000115"),
    "Wall Street Journal": ("newsroom1", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"),
    "WSJ Business": ("newsroom1", "https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness"),
    "MarketWatch": ("newsroom", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    "Bloomberg": ("newsroom1", "https://feeds.bloomberg.com/markets/news.rss"),
    "PR Newswire": ("wire", "https://www.prnewswire.com/rss/news-releases-list.rss"),
    "Business Wire": ("wire", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJcFVJYWQ=="),
    "GlobeNewswire": ("wire", "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies"),
    "SEC press releases": ("regulator", "https://www.sec.gov/news/pressreleases.rss"),
    "FDA": ("regulator", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"),
    "FTC": ("regulator", "https://www.ftc.gov/feeds/press-release.xml"),
}
CRYPTO_FEEDS = {
    "CoinDesk": ("newsroom", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    "Cointelegraph": ("newsroom", "https://cointelegraph.com/rss"),
    "Decrypt": ("newsroom", "https://decrypt.co/feed"),
    "The Block": ("newsroom", "https://www.theblock.co/rss.xml"),
}
WEIGHT = {"regulator": 1.0, "filing": 1.0, "newsroom1": 0.8, "newsroom": 0.6, "wire": 0.5, "blog": 0.4, "social": 0.2}
KIND_LABEL = {"regulator": "regulator", "filing": "SEC filing", "newsroom1": "major newsroom", "newsroom": "newsroom",
              "wire": "press release", "blog": "blog / aggregator", "social": "social"}

# Publisher names (as Google/Yahoo report them) -> (outlet group, kind). Syndicated copies map
# to the outlet that wrote them, so Yahoo reposting Reuters counts once.
OUTLETS = [
    (r"reuters", "Reuters", "newsroom1"), (r"bloomberg", "Bloomberg", "newsroom1"),
    (r"wall street journal|wsj|dow jones", "Wall Street Journal", "newsroom1"), (r"financial times|\bft\b", "Financial Times", "newsroom1"),
    (r"cnbc", "CNBC", "newsroom1"), (r"associated press|\bap news\b|apnews", "AP", "newsroom1"), (r"barron", "Barron's", "newsroom1"),
    (r"new york times|nytimes", "New York Times", "newsroom1"), (r"the economist", "The Economist", "newsroom1"),
    (r"marketwatch", "MarketWatch", "newsroom"), (r"fortune", "Fortune", "newsroom"), (r"business insider|insider\.com", "Business Insider", "newsroom"),
    (r"axios", "Axios", "newsroom"), (r"techcrunch", "TechCrunch", "newsroom"), (r"the verge", "The Verge", "newsroom"),
    (r"cnn", "CNN", "newsroom"), (r"fox business", "Fox Business", "newsroom"), (r"forbes", "Forbes", "newsroom"),
    (r"the information", "The Information", "newsroom"), (r"investor'?s business daily|\bibd\b", "IBD", "newsroom"),
    (r"yahoo finance|yahoo", "Yahoo Finance", "newsroom"), (r"coindesk", "CoinDesk", "newsroom"), (r"cointelegraph", "Cointelegraph", "newsroom"),
    (r"decrypt", "Decrypt", "newsroom"), (r"the block", "The Block", "newsroom"), (r"fierce", "Fierce", "newsroom"),
    (r"pr newswire|prnewswire", "PR Newswire", "wire"), (r"business wire|businesswire", "Business Wire", "wire"),
    (r"globe ?newswire", "GlobeNewswire", "wire"), (r"accesswire|access newswire", "Accesswire", "wire"),
    (r"motley fool|fool\.com", "Motley Fool", "blog"), (r"zacks", "Zacks", "blog"), (r"seeking alpha", "Seeking Alpha", "blog"),
    (r"benzinga", "Benzinga", "blog"), (r"investorplace", "InvestorPlace", "blog"), (r"simply wall", "Simply Wall St", "blog"),
    (r"24/7 wall|247wall", "24/7 Wall St", "blog"), (r"tipranks", "TipRanks", "blog"), (r"marketbeat", "MarketBeat", "blog"),
    (r"insider monkey", "Insider Monkey", "blog"), (r"gurufocus", "GuruFocus", "blog"), (r"nasdaq", "Nasdaq.com", "blog"),
    (r"investing\.com", "Investing.com", "blog"), (r"thestreet", "TheStreet", "blog"), (r"stocktwits|reddit|x\.com|twitter", "Social", "social"),
    (r"sec\.gov|securities and exchange", "SEC", "regulator"), (r"fda", "FDA", "regulator"), (r"justice\.gov|department of justice", "DOJ", "regulator"),
]

EVENTS = [   # (type, tier, pattern) — the first match wins, so the specific come first
    ("restatement", "A", r"restat(e|es|ed|ement)|non-?reliance|accounting (error|irregularit)|material weakness"),
    ("auditor", "A", r"auditor (resign|quit|dismiss|leav)|(resigns|dismisses|drops) (as )?(its )?auditor"),
    ("enforcement", "A", r"\bsec (charges|sues|probe|investigat|subpoena)|wells notice|subpoena|\bdoj\b|justice department|indict|"
                         r"criminal (probe|charges)|fraud (charges|probe|investigation)|antitrust (suit|lawsuit)|ftc sues"),
    ("going_concern", "A", r"going[- ]concern|substantial doubt"),
    ("bankruptcy", "A", r"bankruptcy|chapter 11|chapter 7|insolven|delist|trading (halt|suspend)"),
    ("dividend_cut", "A", r"(cuts?|slashes|suspends?|eliminates?|omits?) (its |the |quarterly )?dividend|dividend (cut|suspension)"),
    ("guidance_cut", "A", r"(cuts?|lowers?|slashes|withdraws?|pulls?) (its |full-year |annual )?(guidance|outlook|forecast)|profit warning|"
                          r"(guidance|outlook|forecast) (cut|lowered|slashed|withdrawn)|warns (on|of) (profit|revenue|sales)"),
    ("exec_exit", "A", r"\b(ceo|cfo|chief executive|chief financial)\b.{0,40}\b(resign|steps? down|ousted|fired|depart|exit|leav)"),
    ("fda_setback", "A", r"complete response letter|\bcrl\b|clinical hold|fda (rejects|declines)|fails? (phase|trial)"),
    ("crypto_hack", "A", r"\b(hack(ed)?|exploit(ed)?|drained|stolen)\b"),
    ("deal", "B", r"(agrees?|plans?|deal|offers?|bids?|moves?|set|nears? deal) to (acquire|buy|merge with) \w|to acquire|acquisition of|acquires|"
                  r"merger|takeover|buyout|in talks to (acquire|buy|merge)|bid for"),
    ("guidance_raise", "B", r"(raises|lifts|boosts) (its )?(guidance|outlook|forecast)"),
    ("earnings", "B", r"earnings|quarterly results|\bq[1-4]\b|beats|misses|revenue (rises|falls|jumps|drops)"),
    ("analyst", "B", r"downgrade|upgrade|price target|initiat(es|ed) coverage"),
    ("capital", "B", r"buyback|repurchase|raises? (its )?dividend|stock split|offering|dilut"),
    ("layoffs", "B", r"layoffs?|job cuts|cuts? .{0,20} jobs|restructur"),
    ("legal", "B", r"lawsuit|sued|settle(s|ment)|class action|recall"),
    ("contract", "B", r"wins? .{0,30}contract|awarded|partnership with"),
]
RUMOR = re.compile(r"\b(reportedly|report says|sources say|people familiar|in talks|considering|exploring|weighs|could|rumou?r|said to)\b", re.I)
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "as", "at", "by", "is", "are", "its", "it", "from", "after",
        "stock", "stocks", "shares", "says", "said", "why", "how", "what", "this", "that", "be", "will", "new", "today", "inc", "corp",
        "co", "ltd", "plc", "company", "report", "reports", "update", "news", "amid", "over", "into", "than", "more", "just"}
CLUSTER_HOURS = 72
JACCARD = 0.35
DAYS = 7


@dataclass
class Item:
    title: str
    outlet: str
    kind: str
    url: str
    published: str
    via: str


@dataclass
class Story:
    symbol: str
    title: str
    event: str
    tier: str
    first: str
    items: list[Item] = field(default_factory=list)
    outlets: list[str] = field(default_factory=list)
    confidence: float = 0.0
    rumor: bool = False
    confirmed: bool = False
    stale: bool = False
    action: str = ""


def outlet_of(source: str, url: str = "") -> tuple[str, str]:
    s = f"{source} {urlparse(url).netloc}".lower()
    for pat, name, kind in OUTLETS:
        if re.search(pat, s):
            return name, kind
    return (source or urlparse(url).netloc or "Unknown").strip(), "newsroom"


def classify(title: str) -> tuple[str, str]:
    t = title.lower()
    for name, tier, pat in EVENTS:
        if re.search(pat, t):
            return name, tier
    return "other", "C"


def tokens(title: str, drop: set[str]) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9'\-]+", title.lower()) if w not in STOP and w not in drop and len(w) > 2}


def _hours(a: str, b: str) -> float:
    try:
        return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()) / 3600
    except ValueError:
        return 1e9


def cluster(symbol: str, items: list[Item], name_words: set[str]) -> list[Story]:
    stories: list[tuple[Story, set[str]]] = []
    for it in sorted(items, key=lambda i: i.published):
        ev, tier = classify(it.title)
        tk = tokens(it.title, name_words)
        best = None
        for st, stk in stories:
            if _hours(st.first, it.published) > CLUSTER_HOURS:
                continue
            j = len(tk & stk) / len(tk | stk) if tk and stk else 0.0
            if j >= JACCARD or (ev != "other" and ev == st.event and _hours(st.first, it.published) <= 48):
                best = (st, stk)
                break
        if best:
            best[0].items.append(it)
            best[1].update(tk)
            if tier < best[0].tier:          # "A" < "B" < "C": keep the most serious reading
                best[0].event, best[0].tier = ev, tier
        else:
            stories.append((Story(symbol, it.title, ev, tier, it.published, [it]), set(tk)))
    out = []
    for st, _ in stories:
        by_outlet: dict[str, str] = {}
        for it in st.items:
            if it.outlet not in by_outlet or WEIGHT[it.kind] > WEIGHT[by_outlet[it.outlet]]:
                by_outlet[it.outlet] = it.kind
        miss = 1.0
        for kind in by_outlet.values():
            miss *= 1 - WEIGHT[kind]
        st.confidence = round(1 - miss, 3)
        st.outlets = sorted(by_outlet, key=lambda o: -WEIGHT[by_outlet[o]])
        st.rumor = all(RUMOR.search(it.title) for it in st.items if it.kind not in ("regulator", "filing"))
        st.confirmed = any(it.kind in ("regulator", "filing") for it in st.items) or (not st.rumor and st.confidence >= 0.8)
        best_item = max(st.items, key=lambda i: WEIGHT[i.kind])
        st.title = best_item.title
        out.append(st)
    return out


def decide(st: Story) -> str:
    if st.tier == "A" and st.confirmed and not st.stale:
        return "review"
    if st.tier == "A":
        return "watch"
    if st.tier == "B":
        return "note"
    return "ignore"


ACTION_TEXT = {
    "review": "Could change why you own it: review your thesis. New buys of it are paused until you do. Not a reason to sell on its own.",
    "watch": "Serious if true, but not confirmed yet (one source, or rumor wording). Wait for a filing or a second major outlet.",
    "note": "Worth a look; no change to the plan.",
    "ignore": "Noise for a long-term holder.",
}


# ------------------------------------------------------------------ fetching

FEED_STATUS: dict[str, dict] = {}     # source name -> {ok, items, at}: shown on the page so a dead feed is visible


def _rss(url: str, via: str, kind_default: str, get=None) -> list[Item]:
    from .providers import news
    try:
        text = (get or http.get)(url, ttl=600, as_json=False)
        arts = news.parse_rss(text, via)
    except (http.DataUnavailable, ET.ParseError, ValueError) as exc:
        FEED_STATUS[via] = {"ok": False, "items": 0, "error": str(exc)[:120], "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        return []
    FEED_STATUS[via] = {"ok": True, "items": len(arts), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    out = []
    for a in arts:
        name, kind = outlet_of(a.source if a.source != via else "", a.url)
        if a.source == via:
            name, kind = via, kind_default
        out.append(Item(a.title, name, kind, a.url, a.published, via))
    return out


def market_items(crypto: bool = False, get=None) -> list[Item]:
    feeds = CRYPTO_FEEDS if crypto else MARKET_FEEDS
    with ThreadPoolExecutor(max_workers=8) as pool:
        parts = pool.map(lambda kv: _rss(kv[1][1], kv[0], kv[1][0], get), feeds.items())
    return [i for p in parts for i in p]


def symbol_items(sym: str, company: str | None, crypto: bool, get=None) -> list[Item]:
    from .providers import news
    items: list[Item] = []
    base = sym.split("-")[0]
    q = f"{company or base} {'crypto' if crypto else 'stock'} when:{DAYS}d"
    sources = [("Google News", f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en")]
    if not crypto:
        sources += [("Yahoo Finance", f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote_plus(sym)}&region=US&lang=en-US"),
                    ("Nasdaq", f"https://www.nasdaq.com/feed/rssoutbound?symbol={quote_plus(sym)}")]
    for via, url in sources:
        items += _rss(url, via, "newsroom", get)
    if not crypto:
        try:
            for a in news._finnhub(sym, DAYS):
                name, kind = outlet_of(a.source, a.url)
                items.append(Item(a.title, name, kind, a.url, a.published, "Finnhub"))
        except (http.DataUnavailable, ValueError, TypeError):
            pass
    return items


def gdelt_outlets(names: dict[str, str], get=None) -> dict[str, int]:
    """Distinct outlets per company over three days, from one GDELT query (no key)."""
    terms = [f'"{n}"' for n in names.values() if n and len(n) > 3][:20]
    if not terms:
        return {}
    q = f"({' OR '.join(terms)}) sourcelang:english" if len(terms) > 1 else f"{terms[0]} sourcelang:english"
    try:
        data = (get or http.get)("https://api.gdeltproject.org/api/v2/doc/doc",
                                 params={"query": q, "mode": "artlist", "format": "json", "maxrecords": 250, "timespan": "3d"}, ttl=1800)
    except (http.DataUnavailable, ValueError):
        return {}
    out: dict[str, set[str]] = {}
    for a in (data or {}).get("articles") or []:
        t = (a.get("title") or "").lower()
        for sym, n in names.items():
            if n and n.lower() in t:
                out.setdefault(sym, set()).add(a.get("domain") or "")
    return {s: len(d) for s, d in out.items()}


def mentions(item: Item, sym: str, company: str | None) -> bool:
    t = item.title
    base = sym.split("-")[0]
    if company and re.search(rf"\b{re.escape(company)}\b", t, re.I):
        return True
    return bool(re.search(rf"(\(|\$|\b(NYSE|NASDAQ|Nasdaq):\s?){re.escape(base)}\b|\b{re.escape(base)}\)", t))


def build(symbols: list[str], names: dict[str, str], radar_by: dict[str, list[dict]] | None = None, history: list[dict] | None = None,
          now: datetime | None = None, get=None, crypto_names: dict[str, str] | None = None) -> dict:
    """{symbol: {"stories": [...], "outlets_3d": n}} for your holdings, most serious first."""
    from .providers import market
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=DAYS)).isoformat()
    crypto = {s for s in symbols if market.asset_class(s) == "crypto"}
    with ThreadPoolExecutor(max_workers=6) as pool:
        per = dict(zip(symbols, pool.map(lambda s: symbol_items(s, names.get(s) or (crypto_names or {}).get(s), s in crypto, get), symbols)))
    wide = market_items(False, get) if set(symbols) - crypto else []
    cwide = market_items(True, get) if crypto else []
    gd = gdelt_outlets({s: names.get(s, "") for s in symbols if s not in crypto}, get)
    old = {(h["symbol"], h["event"]) for h in history or [] if h.get("first", "") < (now - timedelta(days=2)).isoformat()}
    out = {}
    for sym in symbols:
        company = names.get(sym) or (crypto_names or {}).get(sym)
        items = [i for i in per[sym] if i.published >= since]
        items += [i for i in (cwide if sym in crypto else wide) if i.published >= since and mentions(i, sym, company)]
        for a in (radar_by or {}).get(sym, []):
            when = (a.get("filed") or a.get("when") or "")[:10]
            if when and when >= since[:10]:
                items.append(Item(a.get("headline") or a.get("label") or "SEC filing", "SEC EDGAR", "filing", a.get("url", ""),
                                  when + "T12:00:00+00:00", "SEC filing radar"))
        seen, uniq = set(), []
        for i in items:
            k = (re.sub(r"[^a-z0-9]", "", i.title.lower())[:70], i.outlet)
            if k not in seen:
                seen.add(k)
                uniq.append(i)
        words = tokens(company or "", set()) | {sym.split("-")[0].lower()}
        stories = cluster(sym, uniq, words)
        for st in stories:
            st.stale = (sym, st.event) in old and st.event != "other" and not any(i.kind in ("regulator", "filing") for i in st.items)
            st.action = decide(st)
        stories.sort(key=lambda s: ({"A": 0, "B": 1, "C": 2}[s.tier], -s.confidence, s.first), reverse=False)
        out[sym] = {"stories": [dict(asdict(s), action_text=ACTION_TEXT[s.action], sources=len(s.outlets)) for s in stories[:12]],
                    "outlets_3d": gd.get(sym), "items": len(uniq)}
    return out


def remember(history: list[dict], desk: dict, now: datetime, keep_days: int = 30) -> list[dict]:
    """The stories seen, kept 30 days, so a repeat is recognised as stale."""
    cut = (now - timedelta(days=keep_days)).isoformat()
    seen = {(h["symbol"], h["event"], h["first"][:10]) for h in history}
    out = [h for h in history if h.get("first", "") >= cut]
    for sym, d in desk.items():
        for st in d["stories"]:
            if st["event"] != "other" and (sym, st["event"], st["first"][:10]) not in seen:
                out.append({"symbol": sym, "event": st["event"], "first": st["first"], "title": st["title"][:140]})
    return out[-2000:]


def new_reviews(desk: dict) -> list[dict]:
    """Stories that should reach your phone: confirmed Tier A, not stale."""
    return [dict(st, symbol=sym) for sym, d in desk.items() for st in d["stories"] if st["action"] == "review"]


def reviews(desk: dict, now: datetime | None = None) -> dict[str, list[dict]]:
    """Radar-style alerts (level 2: review) for confirmed Tier A stories, for the hold plan."""
    out: dict[str, list[dict]] = {}
    for sym, d in desk.items():
        for st in d["stories"]:
            if st["action"] == "review":
                out.setdefault(sym, []).append({"level": 2, "kind": "news", "filed": st["first"][:10],
                                                "headline": f"News ({st['event'].replace('_', ' ')}, {st['sources']} sources): {st['title']}",
                                                "url": st["items"][0]["url"] if st["items"] else ""})
    return out
