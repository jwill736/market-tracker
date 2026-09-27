"""Glue for the idea pages: gathers the inputs for sleepers, chatter and the money flow from the
app's sources, and logs each day's ideas to the idea log (see ideas.py) so they get scored."""

from __future__ import annotations

from datetime import date, timedelta

from . import discover, http, moneyflow, screen
from .providers import market


def _lookup_fn(data):
    return lambda s: screen.lookup(data, s)


def _shorts_fn(data):
    from . import shorts
    return lambda syms: shorts.for_symbols(syms, _lookup_fn(data))


def _closes(sym: str, days: int = 300) -> list[float]:
    return [b.close for b in market.get_history(sym, days)]


def insider_candidates(today: date) -> dict[str, dict]:
    """Recent cluster and $1M+ insider buys (the filing watcher's list), with the largest buyer."""
    from . import pulse
    buys = pulse.load_watcher_buys()
    cands = pulse.insider_candidates(buys, today)
    since = (today - timedelta(days=30)).isoformat()
    for sym, c in cands.items():
        mine = [b for b in buys if b.symbol == sym and b.filed >= since]
        if mine:
            top = max(mine, key=lambda b: b.value)
            c.update(insider=top.insider, trade_date=top.trade_date, cik=top.issuer_cik)
    return cands


def classify_insider(c: dict) -> dict | None:
    from . import insiders
    if not c.get("insider") or not c.get("trade_date"):
        return None
    return insiders.classify(c["cik"], c["insider"], c["trade_date"])


def news_count(sym: str, name: str | None) -> int | None:
    from .providers import news
    d = news.get_news(sym, name, 7)
    return d.get("count") if d else None


def sleepers(today: date | None = None) -> dict:
    today = today or date.today()
    data = screen.load()
    try:
        cands = insider_candidates(today)
    except http.DataUnavailable:
        cands = {}
    rows = discover.sleepers(data, cands, _lookup_fn(data), news_count, classify_insider, shorts_fn=_shorts_fn(data))
    return {"as_of": today.isoformat(), "screen_as_of": (data or {}).get("as_of"), "sleepers": rows,
            "note": None if data else "The weekly screen hasn't run yet, so only insider-buying leads are shown."}


def chatter() -> dict:
    from . import early
    from .reading import BROWSER_UA
    data = screen.load()
    browser = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
    errors = []
    try:
        ape = http.get(discover.APEWISDOM, headers=browser, ttl=600)
    except http.DataUnavailable as exc:
        ape = {}
        errors.append(f"Reddit: {exc}")
    try:
        trend = [{"symbol": s.symbol, "title": s.name, "summary": s.headline} for s in
                 early.stocktwits_signals(http.get(early.STOCKTWITS_TRENDING, headers=browser, ttl=300)) if not s.symbol.endswith("-USD")]
    except http.DataUnavailable as exc:
        trend = []
        errors.append(f"StockTwits: {exc}")
    return {"rows": discover.chatter(ape, trend, _lookup_fn(data), shorts_fn=_shorts_fn(data)), "errors": errors}


def money_flow() -> dict:
    from . import fundamentals
    from .filings import cik_of
    from .providers import sec
    data = screen.load()

    def facts(sym):
        cik = cik_of(sym)
        if not cik:
            raise http.DataUnavailable(f"{sym}: no SEC filer")
        return sec._sec_get(fundamentals.COMPANYFACTS.format(cik=str(cik).zfill(10)), ttl=1)
    waves = moneyflow.waves(facts, _lookup_fn(data))
    try:
        lag = moneyflow.lagging(moneyflow.CUSTOMERS, lambda name: moneyflow._cached(f"sup:{name}", lambda: moneyflow.suppliers_of(name)),
                                _closes, _lookup_fn(data))
    except http.DataUnavailable:
        lag = []
    return {"waves": waves, "lagging": lag, "screen_as_of": (data or {}).get("as_of")}


def contracts_for(sym: str) -> dict:
    from . import fundamentals
    from .providers import sec
    qs = fundamentals.for_symbol(sym)
    rev = sum(q.revenue for q in qs[:4] if q.revenue) if len(qs) >= 4 else None
    row = sec.ticker_map().by_ticker.get(sym.upper()) or sec.ticker_map().by_ticker.get(sym.upper().replace("-", "."))
    name = (row or {}).get("title") or sym
    return moneyflow._cached(f"gov:{sym}", lambda: moneyflow.contracts(name, rev))


def events(today: date | None = None) -> dict:
    """Raised guidance the market agreed with, and spin-offs (registered and newly trading)."""
    from . import pead, spinoffs
    today = today or date.today()
    data = screen.load()
    errors = []
    try:
        drift = pead.build(today, _lookup_fn(data))
    except http.DataUnavailable as exc:
        drift = []
        errors.append(f"Results releases: {exc}")
    try:
        spins = spinoffs.build(today, lookup_fn=_lookup_fn(data))
    except http.DataUnavailable as exc:
        spins = []
        errors.append(f"Spin-offs: {exc}")
    return {"as_of": today.isoformat(), "pead": drift, "spinoffs": spins, "errors": errors}


def daily_items(today: date | None = None) -> list[dict]:
    """Today's ideas from every screen, for the idea log (each name is kept once a month per screen).
    One source failing (a site down, a format change) never stops the others: it's logged and skipped."""
    import logging
    log = logging.getLogger(__name__)
    today = today or date.today()
    data = screen.load()
    items: list[dict] = []

    def screens():
        # The $2B+ and small/mid lists, not the large-company one: in the replay since 2012 the top large companies
        # trailed SPY, the other two led it (not by enough to rule out luck; that's what the log is for).
        picks = [("all", r) for r in (data or {}).get("top_all", [])[:10]] + [("small_mid", r) for r in (data or {}).get("small_mid", [])[:5]]
        for group, r in picks:
            items.append({"symbol": r["symbol"], "source": "qvm", "price": r.get("price"), "reason": f"Screen grade {r['score']:.0f}/100",
                          "wrong_if": "Grade falls below 50, or it lags VOO by 15+ points over 12 months",
                          "data": {"grades": r.get("grades"), "list": group}})
        for r in (data or {}).get("backlog", [])[:5]:
            items.append({"symbol": r["symbol"], "source": "backlog", "price": r.get("price"), "reason": r["why"],
                          "wrong_if": "Backlog stops growing faster than revenue"})

    def sleeper_ideas():
        for r in sleepers(today)["sleepers"]:
            if r["evidence"] >= 2:
                items.append({"symbol": r["symbol"], "source": "sleeper", "reason": "; ".join(r["why"])[:300],
                              "wrong_if": "Insiders sell, or the screen grade falls below 50"})

    def chatter_ideas():
        for r in chatter()["rows"][:5]:
            items.append({"symbol": r["symbol"], "source": "chatter", "reason": f"{r['reddit']} Reddit mentions" + (", trending on StockTwits" if r.get("stocktwits") else ""),
                          "wrong_if": "Logged to measure chatter, not as a recommendation"})

    def event_ideas():
        ev = events(today)
        for r in ev["pead"]:
            items.append({"symbol": r["symbol"], "source": "pead", "reason": r["why"][:300],
                          "wrong_if": "The next release lowers the outlook, or it trails VOO by 10+ points after 3 months",
                          "data": {"filed": r["filed"], "move_pct": r["move_pct"]}})
        for r in ev["spinoffs"]:
            if r["loggable"]:
                items.append({"symbol": r["ticker"], "source": "spinoff",
                              "reason": f"Spin-off trading since {r['trading_since']} ({r['name'][:80]})",
                              "wrong_if": "It trails VOO by 15+ points after 12 months", "data": {"cik": r["cik"]}})
    for part in (screens, sleeper_ideas, chatter_ideas, event_ideas):
        try:
            part()
        except Exception as exc:  # noqa: BLE001 - see docstring
            log.warning("idea source %s skipped: %s", part.__name__, exc)
    for it in items:
        if not it.get("price"):
            try:
                it["price"] = market.get_quote(it["symbol"]).price
            except (http.DataUnavailable, KeyError, ValueError):
                pass
    return items
