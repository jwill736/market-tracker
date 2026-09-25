"""The app's background watch while it runs: the filing radar every 2 minutes; news and
professional reading every 10. Anything about what you own or watch becomes a heads-up in the
app and, with NTFY_TOPIC set, a push to your phone.

Heads-ups (each only once):
- radar: a scary filing by a company you own or watch (level 3 is pushed at top priority);
- news: a holding's headlines reach 3x its normal pace ("loud");
- reading: a curated pro pick or a desk story names a holding;
- topic: one of your topics heats up (2x its pace, 5+ mentions in a day).
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import db, http, mynews, notify, radar, reading
from .analytics import portfolio as pf
from .providers import market, sec

RADAR_SECONDS = 120
READING_SECONDS = 600
HISTORY_DAYS = 90
MARKET_KEEP = timedelta(days=3)
PUSH_PRIORITY = {3: 5, 2: 4, 1: 3}


def my_symbols() -> tuple[list[str], list[str]]:
    """(held, watched) symbols from the ledger and the watchlist."""
    with db.connect() as conn:
        txs = db.list_transactions(conn)
        watch = db.watchlist(conn)
    try:
        held = [p.symbol for p in pf.build_positions(txs).values() if p.quantity > 0]
    except ValueError:
        held = sorted({t["symbol"] for t in txs})
    return held, [w for w in watch if w not in held]


def company_names(symbols: list[str]) -> dict[str, str]:
    try:
        tm = sec.ticker_map()
    except http.DataUnavailable:
        return {}
    return {s: tm.by_ticker[s]["title"] for s in symbols if s in tm.by_ticker}


def symbol_ciks(symbols: list[str]) -> dict[str, str]:
    """CIK -> symbol for the stocks among `symbols` (crypto and funds without a CIK drop out)."""
    try:
        tm = sec.ticker_map()
    except http.DataUnavailable:
        return {}
    out = {}
    for s in symbols:
        if market.asset_class(s) == "stock":
            cik = tm.cik_for(s)
            if cik:
                out[cik] = s
    return out


def _submissions(cik: str) -> dict:
    return sec._sec_get(sec.SUBMISSIONS.format(cik=cik.zfill(10)), ttl=3600)


@dataclass
class Sentinel:
    market: dict[str, radar.Alert] = field(default_factory=dict)
    scanned_at: str = ""
    radar_errors: list[str] = field(default_factory=list)
    reading_at: str = ""
    task: asyncio.Task | None = None

    # -------------------------------------------------------------- radar
    def scan(self, get=http.get) -> list[radar.Alert]:
        alerts, errors = radar.scan_market(get)
        now = datetime.now(timezone.utc)
        for a in alerts:
            self.market.setdefault(a.accession, a)
        cutoff = (now - MARKET_KEEP).date().isoformat()
        self.market = {k: v for k, v in self.market.items() if v.filed >= cutoff}
        self.scanned_at, self.radar_errors = now.isoformat(timespec="seconds"), errors
        tickers = _safe_tickers()
        for a in self.market.values():
            a.symbol = a.symbol or tickers.get(a.cik, "")
        return alerts

    def mine(self, symbols: list[str], today: date | None = None, submissions_fn=_submissions,
             going_concern_fn=radar.going_concern_ciks) -> tuple[list[radar.Alert], list[str]]:
        """Radar history for your companies (90 days), plus anything newer from the live feed."""
        today = today or date.today()
        ciks = symbol_ciks(symbols)
        errors: list[str] = []

        def one(item):
            cik, sym = item
            try:
                return radar.company_alerts(cik, submissions_fn(cik), today - timedelta(days=HISTORY_DAYS), sym)
            except (http.DataUnavailable, KeyError, ValueError) as exc:
                errors.append(f"{sym}: {exc}")
                return []
        with ThreadPoolExecutor(max_workers=4) as pool:
            found = {a.accession: a for batch in pool.map(one, ciks.items()) for a in batch}
        for a in self.market.values():
            if a.cik in ciks and a.accession not in found:
                a.symbol = ciks[a.cik]
                found[a.accession] = a
        try:
            gc = going_concern_fn(today)
            for cik, hit in gc.items():
                if cik in ciks:
                    a = radar.going_concern_alert(cik, hit, ciks[cik])
                    found.setdefault(a.accession + ":gc", a)
        except http.DataUnavailable as exc:
            errors.append(f"going-concern search: {exc}")
        return sorted(found.values(), key=radar.when_sort_key, reverse=True), errors

    def radar_headsups(self, symbols: list[str], today: date | None = None) -> int:
        """New radar filings about your companies from the live feed become heads-ups."""
        today = today or date.today()
        ciks = symbol_ciks(symbols)
        n = 0
        for a in self.market.values():
            if a.cik not in ciks or a.filed < (today - timedelta(days=2)).isoformat():
                continue
            n += raise_headsup(f"radar:{a.accession}", "radar", a.level, f"{ciks[a.cik]}: {a.headline}",
                               f"{a.company} filed a {a.form} ({a.filed}). {a.why}", a.url, ciks[a.cik])
        return n

    # -------------------------------------------------------------- news and reading
    def reading_headsups(self, symbols: list[str], news_data: dict, reading_data: dict) -> int:
        n = 0
        for d in news_data.get("symbols", []):
            if d["loud"]:
                top = (d["headlines"] or [{}])[0]
                n += raise_headsup(f"news:{d['symbol']}:{date.today().isoformat()}", "news", 2,
                                   f"{d['symbol']}: {d['last_24h']} headlines today, {d['heat']}x its normal pace",
                                   top.get("title", ""), top.get("url", ""), d["symbol"])
        for m in reading_data.get("mentions", []):
            src = m.get("curator") or m.get("source_name") or ""
            n += raise_headsup(f"read:{m['url']}", "reading", 1, f"{', '.join(m['mentions'])} in {src}", m["title"],
                               m["url"], m["mentions"][0])
        for t in reading_data.get("topics", []):
            if t["hot"]:
                top = (t["latest"] or [{}])[0]
                n += raise_headsup(f"topic:{t['name']}:{date.today().isoformat()}", "topic", 1,
                                   f"Heating up: {t['name']} ({t['last_24h']} stories today vs {t['daily_pace']}/day)",
                                   top.get("title", ""), top.get("url", ""))
        return n

    # -------------------------------------------------------------- loop
    async def run(self) -> None:
        last_reading = 0.0
        while True:
            held, watched = await asyncio.to_thread(my_symbols)
            mine = held + watched
            try:
                await asyncio.to_thread(self.scan)
                await asyncio.to_thread(self.radar_headsups, mine)
            except Exception as exc:          # a bad poll must not end the loop
                self.radar_errors = [f"radar: {exc}"]
            loop_time = asyncio.get_running_loop().time()
            if mine and loop_time - last_reading >= READING_SECONDS:
                last_reading = loop_time
                try:
                    news_data = await asyncio.to_thread(news_cache.get, tuple(mine), lambda: build_news(mine))
                    read_data = await asyncio.to_thread(reading_cache.get, tuple(mine), lambda: build_reading(mine))
                    await asyncio.to_thread(self.reading_headsups, mine, news_data, read_data)
                    self.reading_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                except Exception:
                    pass
            await asyncio.sleep(RADAR_SECONDS)

    def start(self) -> None:
        if self.task is None and os.environ.get("MT_BACKGROUND", "1") != "0":
            self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            self.task = None


def raise_headsup(key: str, kind: str, level: int, title: str, body: str = "", url: str = "",
                  symbol: str = "") -> int:
    with db.connect() as conn:
        new = db.add_headsup(conn, key, kind, level, title, body, url, symbol,
                             datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if new:
        notify.send(notify.Message(title=title, body=body, url=url, priority=PUSH_PRIORITY.get(level, 3),
                                   tags=({"radar": ("rotating_light",), "news": ("newspaper",), "reading": ("books",),
                                          "topic": ("fire",)}.get(kind, ()))))
    return int(new)


def _safe_tickers() -> dict[str, str]:
    try:
        return radar.cik_to_ticker()
    except http.DataUnavailable:
        return {}


class Cache:
    def __init__(self, ttl: float):
        from .pulse import Cache as _C
        self._c = _C(ttl)

    def get(self, key, compute):
        return self._c.get(key, compute)

    def clear(self):
        self._c.store.clear()


news_cache = Cache(300)
reading_cache = Cache(600)
radar_cache = Cache(300)


def build_news(symbols: list[str]) -> dict:
    return mynews.build(symbols, company_names(symbols))


def build_reading(symbols: list[str]) -> dict:
    with db.connect() as conn:
        topics = db.topics(conn, reading.DEFAULT_TOPICS)
    return reading.build(symbols, company_names(symbols), topics)


sentinel = Sentinel()
