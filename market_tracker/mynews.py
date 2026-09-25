"""News for everything you own or watch, stocks and crypto, from every account.

For each symbol: the headlines of the last 7 days, how many landed in the last 24 hours against
its normal daily pace, the mood of the headlines, whether in-depth outlets covered it, and the
latest headlines. A symbol is "loud" when the last 24 hours carry 3x its daily pace and at least
5 headlines: something is happening, and the headlines say what.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import http
from .providers import market, news
from .pulse import is_deep_source
from .reading import _age_h, short_company_name

LOUD_RATIO = 3.0
LOUD_MIN = 5


def digest_one(symbol: str, data: dict, now: datetime) -> dict:
    arts = data.get("articles") or []
    last24 = [a for a in arts if _age_h(a.get("published", ""), now) <= 24]
    older = [a for a in arts if 24 < _age_h(a.get("published", ""), now) <= 168]
    pace = len(older) / 6
    loud = len(last24) >= LOUD_MIN and len(last24) >= LOUD_RATIO * max(pace, 0.5)
    deep = [a for a in arts if is_deep_source(a.get("source", ""))]
    return {
        "symbol": symbol, "last_24h": len(last24), "daily_pace": round(pace, 1), "loud": loud,
        "heat": round(len(last24) / max(pace, 0.5), 1), "count_7d": len(arts),
        "mood": round(data.get("avg_sentiment", 0.0), 2),
        "positive": data.get("positive", 0), "negative": data.get("negative", 0),
        "terms": [t for t, _ in (data.get("top_terms") or [])[:6]],
        "deep": deep[:3], "headlines": arts[:6],
    }


def build(symbols: list[str], names: dict[str, str], *, news_fn=news.get_news, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)

    def one(sym: str):
        company = None if market.asset_class(sym) == "crypto" else (short_company_name(names[sym]) if names.get(sym) else None)
        try:
            return sym, news_fn(sym, company, 7), None
        except (http.DataUnavailable, KeyError, ValueError) as exc:
            return sym, None, f"{sym}: {exc}"

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, symbols))
    digests, feed, errors, seen = [], [], [], set()
    for sym, data, err in results:
        if err:
            errors.append(err)
        if not data:
            continue
        digests.append(digest_one(sym, data, now))
        for a in data.get("articles") or []:
            if _age_h(a.get("published", ""), now) > 48:
                continue
            key = (a.get("title", "").lower()[:70])
            if key in seen:
                continue
            seen.add(key)
            feed.append(dict(a, symbol=sym, deep=is_deep_source(a.get("source", ""))))
    digests.sort(key=lambda d: (not d["loud"], -d["heat"], -d["last_24h"]))
    feed.sort(key=lambda a: a.get("published", ""), reverse=True)
    return {"generated_at": now.isoformat(timespec="seconds"), "symbols": digests, "feed": feed[:200],
            "errors": errors}
