"""Is it already priced in? Warnings for any stock, and how crowded each popular theme is.

Per stock (from the weekly screen and the company's own filings):
- valuation against its own last five years: today's price to trailing earnings against every
  quarter-end since; the top tenth of its own range is flagged;
- a 12-month return in the top 5% of all listed companies;
- 30%+ above its 200-day average;
- assets growing 30%+ in a year (fast-growing companies tend to lag afterwards: Cooper, Gulen
  & Schill 2008), and 5%+ more shares in a year (dilution).

Per theme: how many new fund registrations mention it in the last six months against the six
before (SEC full-text search of N-1A and 485APOS filings). Funds are launched when a theme is
already popular, and specialized ETFs have trailed the market by about 6% a year over their first
five years, because what they hold is already expensive at launch (Ben-David, Franzoni, Kim &
Moussawi 2023). A crowded theme is a reason to check the price, not a reason to buy.

Theme membership is a short, hand-picked list of well-known names per theme (examples, not
recommendations), so it misses newer names.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

from . import http

THEMES: dict[str, dict] = {
    "AI chips and data centers": {"q": '"artificial intelligence"', "tickers": ["NVDA", "AVGO", "AMD", "MRVL", "ARM", "SMCI", "VRT", "ANET"]},
    "Nuclear power": {"q": '"nuclear"', "tickers": ["CEG", "VST", "OKLO", "SMR", "NNE", "BWXT", "LEU"]},
    "Uranium": {"q": '"uranium"', "tickers": ["CCJ", "UEC", "NXE", "UUUU", "LEU"]},
    "Quantum computing": {"q": '"quantum computing"', "tickers": ["IONQ", "RGTI", "QBTS", "QUBT"]},
    "Drones and defense tech": {"q": '"drone"', "tickers": ["AVAV", "KTOS", "RCAT", "PLTR"]},
    "Space": {"q": '"space economy" OR "space exploration"', "tickers": ["RKLB", "ASTS", "LUNR", "PL"]},
    "Robotics": {"q": '"robotics"', "tickers": ["ISRG", "SYM", "TER", "TSLA"]},
    "Obesity drugs (GLP-1)": {"q": '"GLP-1" OR "obesity"', "tickers": ["LLY", "NVO", "VKTX"]},
    "Cybersecurity": {"q": '"cybersecurity"', "tickers": ["CRWD", "PANW", "ZS", "FTNT", "S"]},
    "Bitcoin and crypto stocks": {"q": '"bitcoin"', "tickers": ["COIN", "MSTR", "MARA", "RIOT", "HOOD"]},
    "Copper and critical minerals": {"q": '"copper" OR "critical minerals"', "tickers": ["FCX", "SCCO", "MP"]},
    "Power grid equipment": {"q": '"electrification" OR "power grid"', "tickers": ["GEV", "ETN", "PWR", "HUBB", "POWL"]},
}
FTS = "https://efts.sec.gov/LATEST/search-index"
FUND_FORMS = "N-1A,485APOS"
CACHE_SECONDS = 24 * 3600
_cache: dict[str, tuple[float, dict]] = {}


def themes(today: date | None = None, count_fn=None) -> list[dict]:
    """Each theme's new fund registrations, last 6 months against the 6 before."""
    today = today or date.today()
    hit = _cache.get("themes")
    if hit and time.time() - hit[0] < CACHE_SECONDS and not count_fn:
        return hit[1]["rows"]
    cache_it = count_fn is None
    count_fn = count_fn or _fts_count
    rows = []
    for name, t in THEMES.items():
        try:
            now_n = count_fn(t["q"], today - timedelta(days=182), today)
            before = count_fn(t["q"], today - timedelta(days=365), today - timedelta(days=183))
        except http.DataUnavailable:
            continue
        ratio = now_n / before if before else None
        level = "crowded" if now_n >= 40 and (ratio is None or ratio >= 1.3) else "busy" if now_n >= 20 else "quiet"
        rows.append({"theme": name, "last_6m": now_n, "prior_6m": before, "ratio": round(ratio, 2) if ratio else None, "level": level,
                     "tickers": t["tickers"]})
    rows.sort(key=lambda r: -r["last_6m"])
    if cache_it:
        _cache["themes"] = (time.time(), {"rows": rows})
    return rows


def _fts_count(q: str, start: date, end: date) -> int:
    from .config import settings
    d = http.get(FTS, params={"q": q, "forms": FUND_FORMS, "dateRange": "custom", "startdt": start.isoformat(), "enddt": end.isoformat()},
                 headers={"User-Agent": settings.sec_user_agent}, ttl=0)
    return int(((d.get("hits") or {}).get("total") or {}).get("value") or 0)


def theme_of(symbol: str) -> list[str]:
    return [name for name, t in THEMES.items() if symbol in t["tickers"]]


def pe_history(eps_q: dict[str, float], bars: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """[(quarter end, price / trailing-4-quarter EPS)] where trailing EPS is positive."""
    ends = sorted(eps_q)
    out = []
    for i in range(3, len(ends)):
        ttm = sum(eps_q[e] for e in ends[i - 3: i + 1])
        if ttm <= 0:
            continue
        px = next((c for d, c in reversed(bars) if d <= ends[i]), None)
        if px:
            out.append((ends[i], px / ttm))
    return out


def valuation(eps_q: dict[str, float], bars: list[tuple[str, float]]) -> dict | None:
    """Today's P/E against its own quarter-end P/Es over about five years."""
    ends = sorted(eps_q)
    if len(ends) < 4 or not bars:
        return None
    ttm = sum(eps_q[e] for e in ends[-4:])
    if ttm <= 0:
        return {"pe": None, "note": "Losing money over the last year: no P/E to compare."}
    hist = pe_history(eps_q, bars)
    if len(hist) < 8:
        return None
    pe = bars[-1][1] / ttm
    past = sorted(v for _, v in hist[-20:])
    pct = round(sum(1 for v in past if v < pe) / len(past) * 100)
    return {"pe": round(pe, 1), "pct": pct, "low": round(past[0], 1), "high": round(past[-1], 1), "median": round(past[len(past) // 2], 1),
            "quarters": len(past)}


def flags(symbol: str, screen_row: dict | None, val: dict | None, crowded: dict[str, str] | None = None) -> list[dict]:
    out = []
    if val and val.get("pe") and val.get("pct") is not None and val["pct"] >= 90:
        out.append({"level": 2, "text": f"P/E {val['pe']:g}: higher than {val['pct']}% of its own last {val['quarters']} quarter-ends "
                                        f"(range {val['low']:g}-{val['high']:g})."})
    r = screen_row or {}
    if r.get("return_12m_pct") is not None and r["return_12m_pct"] >= 95:
        out.append({"level": 1, "text": "12-month return in the top 5% of all listed companies."})
    if r.get("above_200d") is not None and r["above_200d"] >= 0.3:
        out.append({"level": 1, "text": f"{r['above_200d']:.0%} above its 200-day average."})
    if r.get("asset_growth") is not None and r["asset_growth"] >= 0.3:
        out.append({"level": 1, "text": f"Assets grew {r['asset_growth']:.0%} in a year: fast-growing companies have tended to lag afterwards."})
    if r.get("issuance") is not None and r["issuance"] >= 0.05:
        out.append({"level": 1, "text": f"{r['issuance']:.0%} more shares than a year ago: your slice is being diluted."})
    for t in theme_of(symbol):
        if (crowded or {}).get(t) == "crowded":
            out.append({"level": 1, "text": f"In a crowded theme ({t}): new funds are piling in, which has tended to mark expensive prices."})
    return out


def for_symbol(symbol: str, screen_data: dict | None = None, get=None, history_fn=None, short_fn=None) -> dict:
    from . import fundamentals, screen
    from .providers import market
    row = screen.lookup(screen_data, symbol) if screen_data else None
    val = None
    if market.asset_class(symbol) == "stock":
        try:
            from .filings import cik_of
            from .providers import sec
            cik = cik_of(symbol)
            if cik:
                facts = (get or (lambda u: sec._sec_get(u, ttl=1)))(fundamentals.COMPANYFACTS.format(cik=str(cik).zfill(10)))
                eps = fundamentals._best(facts, fundamentals.EPS, "USD/shares")
                bars = history_fn(symbol) if history_fn else [(b.date, b.close) for b in market.get_history(symbol, 1900)]
                val = valuation(eps, bars)
        except (http.DataUnavailable, KeyError, ValueError, TypeError):
            val = None
    crowded = {r["theme"]: r["level"] for r in (_cache.get("themes", (0, {"rows": []}))[1]["rows"])}
    f = flags(symbol, row, val, crowded)
    low = next((r for r in (screen_data or {}).get("bottom", []) if r["symbol"] == symbol), None)
    if low:
        f.append({"level": 2, "text": f"In the weekly screen's bottom 50 of companies worth $2B+ (grade {low['score']:.0f}/100): in the replay "
                                      "since 2012 that group trailed SPY by under a point a quarter, within luck: reread why you own it, don't sell on the grade alone."})
    short = None
    if market.asset_class(symbol) == "stock" and short_fn is not False:
        from . import shorts
        short = (short_fn or (lambda s: shorts.for_symbols([s], lambda _: row).get(s)))(symbol)
        if short and short["level"] != "normal":
            f.append({"level": 2 if short["level"] == "heavy" else 1, "text": short["text"]})
    return {"symbol": symbol, "screen": row, "valuation": val, "themes": theme_of(symbol), "flags": f, "short": short,
            "verdict": ("Priced for a lot going right: " + str(len(f)) + " warning" + ("s" if len(f) != 1 else "") if any(x["level"] >= 2 for x in f) or len(f) >= 2
                        else "One thing to check" if f else "No priced-in warnings")}
