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
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import db, early, http, mynews, notify, radar, reading
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
        txs = db.ledger(conn)
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
                    early_data = await asyncio.to_thread(build_early, set(mine))
                    await asyncio.to_thread(early_headsups, early_data)
                    await asyncio.to_thread(people_headsups)
                    await asyncio.to_thread(crypto_headsups, held)
                    self.reading_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                except Exception:
                    pass
            try:
                await asyncio.to_thread(send_brief_if_due)
                await asyncio.to_thread(send_weekly_if_due)
            except Exception:
                pass
            try:
                from . import accounts
                await asyncio.to_thread(accounts.auto_sync, None, raise_headsup)
            except Exception:
                pass
            try:
                await asyncio.to_thread(price_alerts_check)
                await asyncio.to_thread(run_schedules_daily)
            except Exception:
                pass
            try:
                await asyncio.to_thread(offsite_daily)
                await asyncio.to_thread(health_check)
                await asyncio.to_thread(settle_orders)
                await asyncio.to_thread(log_advice_daily)
                await asyncio.to_thread(log_ideas_daily)
                await asyncio.to_thread(thesis_check_weekly)
                await asyncio.to_thread(freshness_check)
                await asyncio.to_thread(decisions_daily)
                await asyncio.to_thread(big_moves_check)
                held_now = my_symbols()[0]
                if held_now:
                    await asyncio.to_thread(newsdesk_cache.get, tuple(sorted(held_now)), lambda: build_newsdesk(sorted(held_now)))
            except Exception:
                pass
            try:
                await asyncio.to_thread(scorecard_monthly)
                await asyncio.to_thread(letter_weekly)
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


def price_alerts_check() -> list[dict]:
    """Your price lines (sell-below, take-some-off, buy-the-dip) against live prices."""
    from . import price_alerts

    def q(sym):
        return market.get_live_quote(sym).price
    return price_alerts.check(q, raise_headsup=raise_headsup)


def run_schedules_daily(today: date | None = None) -> list[dict]:
    """Once a day: record auto-invest buys that came due."""
    from . import schedules
    today = today or date.today()
    with db.connect() as conn:
        if db.get_meta(conn, "schedules_ran", "") == today.isoformat():
            return []
        added = schedules.apply(conn, today, schedules.close_on)
        db.set_meta(conn, "schedules_ran", today.isoformat())
    for a in added:
        raise_headsup(f"sched:{a['symbol']}:{a['date']}", "sync", 1,
                      f"Auto-invest: bought {a['quantity']:g} {a['symbol']} in {a['account']} on {a['date']}",
                      "Recorded from your schedule at that day's close.", "", a["symbol"])
    return added


def send_brief_if_due(now: datetime | None = None, gather_fn=None) -> bool:
    """The morning brief, once per weekday at 8:30 ET (saved for the app, pushed to the phone)."""
    import json
    from . import brief
    now = now or datetime.now(timezone.utc)
    with db.connect() as conn:
        last = db.get_meta(conn, "brief_sent", "")
        cadence = db.get_meta(conn, "push_cadence", "weekly") or "weekly"
    if cadence == "weekly" or not brief.due(now, last):
        return False
    b = (gather_fn or brief.gather)(now=now)
    with db.connect() as conn:
        db.set_meta(conn, "brief_sent", b["date"])
        db.set_meta(conn, "brief_latest", json.dumps(b))
    notify.send(notify.Message(title=b["title"], body=brief.push_text(b), priority=3, tags=("sunrise",)))
    return True


def send_weekly_if_due(now: datetime | None = None, gather_fn=None) -> bool:
    """The weekly recap, Sunday 5pm Eastern (saved for the app; pushed unless you chose daily only)."""
    import json
    from . import weekly
    now = now or datetime.now(timezone.utc)
    with db.connect() as conn:
        last = db.get_meta(conn, "weekly_sent", "")
        cadence = db.get_meta(conn, "push_cadence", "weekly") or "weekly"
    if not weekly.due(now, last):
        return False
    r = (gather_fn or weekly.gather)(now=now)
    with db.connect() as conn:
        db.set_meta(conn, "weekly_sent", now.astimezone(weekly.ET).date().isoformat())
        db.set_meta(conn, "weekly_latest", json.dumps(r))
    if cadence != "daily":
        notify.send(notify.Message(title=r["title"], body=weekly.push_text(r), priority=3, tags=("calendar",)))
    return True


def log_ideas_daily(today: date | None = None, items_fn=None, remote_fn=None, now: datetime | None = None) -> int:
    """Keep the idea log current. Normally the GitHub job (ideas.yml) logs the ideas and this imports
    them, at most once an hour. Without a GitHub log, the app logs the screens' ideas itself once a day."""
    from . import idealab, ideas
    today = today or date.today()
    hour = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H")
    with db.connect() as conn:
        if db.get_meta(conn, "ideas_synced", "") == hour:
            return 0
        db.set_meta(conn, "ideas_synced", hour)
    rows = (remote_fn or ideas.load_remote)()
    with db.connect() as conn:
        if rows is not None:
            return ideas.sync(conn, rows)
        if ideas.has_github_rows(conn) or db.get_meta(conn, "ideas_logged", "") == today.isoformat():
            return 0            # the GitHub log is briefly unreachable, or today is done
    items = (items_fn or idealab.daily_items)(today)
    with db.connect() as conn:
        n = ideas.log(conn, today.isoformat(), items)
        db.set_meta(conn, "ideas_logged", today.isoformat())
    return n


def decisions_daily(now: datetime | None = None, gather_fn=None) -> int:
    """Once a day from 8am ET: work out the decisions, push the new ones that matter, and write the
    stock decisions to the advice track record at today's price."""
    from . import advice, decisions
    from .weekly import ET
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    day = now.date()
    if now.hour < 8:
        return 0
    with db.connect() as conn:
        if db.get_meta(conn, "decisions_day", "") == day.isoformat():
            return 0
        db.set_meta(conn, "decisions_day", day.isoformat())
    from . import golive
    items = (gather_fn or decisions.gather)(day)
    with db.connect() as conn:
        visible = decisions.sync(conn, items, day)
        settle = golive.settling(golive.live_since(conn, bool(db.ledger(conn)), day), day)
        if not settle["settling"]:
            advice.record(conn, day.isoformat(), decisions.advice_items(visible), lambda s: market.get_quote(s).price)
    new = len([d for d in visible if d.get("new")])
    if settle["settling"]:                          # first weeks: only connection and data problems are pushed
        visible = [d for d in visible if d["kind"] in golive.ALWAYS_PUSH]
    msg = decisions.push_text(visible)
    if msg:
        from . import auth
        acts = decisions.push_actions(visible, os.environ.get("PLUMBLINE_URL", ""), auth.sign_action)
        notify.send(notify.Message(title=msg[0], body=msg[1], priority=4, tags=("compass",), actions=acts))
    return new


def letter_weekly(now: datetime | None = None, write_fn=None) -> int:
    """Sundays from 5:30pm ET (after the weekly recap): Claude writes this week's letter and it's pushed.
    Only with an Anthropic key, and only while this month's automatic spend is under the budget."""
    from . import assistant
    from .weekly import ET
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    week = f"{now.isocalendar()[0]}-{now.isocalendar()[1]}"
    if now.weekday() != 6 or (now.hour, now.minute) < (17, 30):
        return 0
    if write_fn is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return 0
    with db.connect() as conn:
        if db.get_meta(conn, "letter_week", "") == week:
            return 0
        db.set_meta(conn, "letter_week", week)
        spent, cap = assistant.month_spend(conn), assistant.budget(conn)
    if spent >= cap:
        notify.send(notify.Message(title="No letter this week", body=f"Plumbline's automatic Claude budget (${cap:.2f} this month) is used up "
                                   f"(${spent:.2f}). Raise it on the Decisions page, or write one there by hand.", priority=2))
        return 0
    out = (write_fn or assistant.letter)()
    out["written"] = now.strftime("%Y-%m-%dT%H")
    with db.connect() as conn:
        db.set_meta(conn, "letter_latest", json.dumps(out))
    body = assistant.plain(out.get("answer") or "")
    base = os.environ.get("PLUMBLINE_URL", "").rstrip("/")
    notify.send(notify.Message(title="Your letter from Plumbline", body=body[:1500] + ("…" if len(body) > 1500 else ""), priority=3,
                               tags=("envelope",), url=f"{base}/#decisions" if base else ""))
    return 1


def scorecard_monthly(now: datetime | None = None, card_fn=None) -> int:
    """On the 1st of the month from 9am ET: how following Plumbline's calls has gone, in one push."""
    from . import cash, decisions
    from .weekly import ET
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    month = now.strftime("%Y-%m")
    if now.day != 1 or now.hour < 9:
        return 0
    with db.connect() as conn:
        if db.get_meta(conn, "scorecard_month", "") == month:
            return 0
        db.set_meta(conn, "scorecard_month", month)
        from . import golive
        since = golive.record_start(db.get_meta(conn, "live_since", "") or None)
        card = (card_fn or (lambda c: decisions.scorecard(c, lambda s: [(b.date, b.close) for b in market.get_history(s, 800)],
                                                          now.date(), cash.tbill_yield()[0], since)))(conn)
    if not card["followed"] and not card["skipped"]:
        return 0
    base = os.environ.get("PLUMBLINE_URL", "").rstrip("/")
    notify.send(notify.Message(title="Plumbline's record this month", body=card["text"], priority=3, tags=("bar_chart",),
                               url=f"{base}/#decisions" if base else ""))
    return 1


def freshness_check(now: datetime | None = None, check_fn=None) -> int:
    """Hourly: push once for each GitHub-built data file that has gone stale (see freshness.py)."""
    from . import freshness
    now = now or datetime.now(timezone.utc)
    hour = now.strftime("%Y-%m-%dT%H")
    with db.connect() as conn:
        if db.get_meta(conn, "freshness_hour", "") == hour:
            return 0
        db.set_meta(conn, "freshness_hour", hour)
        already = set(json.loads(db.get_meta(conn, "freshness_alerted", "[]") or "[]"))
    results = (check_fn or freshness.check)(now.date())
    due, keep = freshness.alerts_due(results, already)
    with db.connect() as conn:
        db.set_meta(conn, "freshness_alerted", json.dumps(sorted(keep)))
        db.set_meta(conn, "freshness_latest", json.dumps(results))
    for r in due:
        notify.send(notify.Message(title=f"Stale data: {r['name']}", body=r["text"], priority=3, tags=("hourglass",)))
    return len(due)


def thesis_check_weekly(now: datetime | None = None, items_fn=None, check_fn=None) -> int:
    """Once a week (Sunday afternoon, after the Sunday screen): has the reason behind any idea you
    bought or hold gone away? Each problem is pushed once."""
    import re as _re

    from .weekly import ET
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    back = (now.weekday() + 1) % 7
    sunday = (now - timedelta(days=back)).date()
    if back == 0 and now.hour < 12:
        sunday -= timedelta(days=7)
    with db.connect() as conn:
        if db.get_meta(conn, "thesis_week", "") == sunday.isoformat():
            return 0
        db.set_meta(conn, "thesis_week", sunday.isoformat())
        sent = set(json.loads(db.get_meta(conn, "thesis_alerted", "[]") or "[]"))
    found = (check_fn or _thesis_found)(now.date(), items_fn)
    n = 0
    for f in found:
        for r in f["reasons"]:
            key = f"{f['symbol']}|{f['source']}|" + _re.sub(r"[-+$\d.,%/]+", "#", r)
            if key in sent:
                continue
            sent.add(key)
            if f["source"] == "bottom":
                title, body = f"{f['symbol']}: in the screen's bottom 50", f"You hold it, and it's {r}. Check it before adding more."
            else:
                title = f"{f['symbol']}: the reason you bought may be gone"
                body = f"{r}. Logged {f['day']} ({thesis_label(f['source'])}). Check it before adding more."
            notify.send(notify.Message(title=title, body=body, priority=4, tags=("warning",)))
            n += 1
    with db.connect() as conn:
        db.set_meta(conn, "thesis_alerted", json.dumps(sorted(sent)[-500:]))
    return n


def thesis_label(source: str) -> str:
    from . import ideas
    return ideas.SOURCES.get(source, source)


def _thesis_found(today: date, items_fn=None) -> list[dict]:
    from . import earnings, ideas, screen, thesis
    with db.connect() as conn:
        rows = ideas.logged(conn)
    # Only broken theses are pushed: a stock in the screen's bottom 50 didn't lag by more than luck in the
    # replay, so it's shown on the Ideas and Decisions pages but doesn't earn a phone alert.
    if not rows:
        return []
    held = set(my_symbols()[0])
    data = screen.load()
    items = (items_fn or (lambda rs: ideas.score(rs, lambda s: [(b.date, b.close) for b in market.get_history(s, 800)], today)["items"]))(rows)
    return thesis.check(items, held, today, lambda s: screen.lookup(data, s),
                        trades_fn=lambda s, days: sec.get_insider_trades(s, days), recap_fn=earnings.recap)


def crypto_headsups(held: list[str]) -> int:
    from . import cryptoradar
    coins = [s for s in held if market.asset_class(s) == "crypto"]
    if not coins:
        return 0
    data = crypto_cache.get(tuple(coins), lambda: cryptoradar.build(coins))
    n = 0
    for a in data["alerts"]:
        if a["level"] >= 2:
            n += raise_headsup(f"crypto:{a['kind']}:{a['date']}:{a['text'][:60]}", "crypto", a["level"], a["text"], url=a.get("url", ""))
    return n


def offsite_daily(now: datetime | None = None) -> dict | None:
    """Once a day, after 2am local time: the encrypted off-site backup, when one is set up."""
    import json
    from . import config, offsite
    if not offsite.configured():
        return None
    now = now or datetime.now(timezone.utc)
    local = now.astimezone()
    with db.connect() as conn:
        last = json.loads(db.get_meta(conn, "offsite_last", "null") or "null")
    if local.hour < 2 or (last and last.get("ok") and last["at"][:10] == now.date().isoformat()):
        return None
    if last and not last.get("ok") and now - datetime.fromisoformat(last["at"]) < timedelta(hours=1):
        return None     # failed recently: try again in an hour, not every loop
    try:
        out = offsite.run(config.settings.db_path, now)
        rec = {"at": out["at"], "ok": True, "to": out["to"], "errors": out["errors"]}
    except offsite.BackupError as exc:
        rec = {"at": now.isoformat(timespec="seconds"), "ok": False, "error": str(exc)}
    with db.connect() as conn:
        db.set_meta(conn, "offsite_last", json.dumps(rec))
    return rec


def health_check(now: datetime | None = None) -> list[dict]:
    """Push once when a connection has been failing for a while (see health.py)."""
    from . import health
    problems = health.problems(now)
    for p in problems:
        if p["push"]:
            raise_headsup(p["key"], "sync", 2, p["title"], p["body"], "", "")
    return problems


def settle_orders() -> list[dict]:
    """Stock orders sent to Alpaca or Public from the app: record fills in the ledger."""
    from . import brokers, trading
    if not (brokers.alpaca_configured() or brokers.public_configured()):
        return []
    with db.connect() as conn:
        added = trading.settle_pending(conn)
    for a in added:
        raise_headsup(f"fill:{a['account']}:{a['symbol']}:{a['quantity']}:{a['price']}", "sync", 1,
                      f"Filled: {a['side']} {a['quantity']:g} {a['symbol']} at ${a['price']:,.2f} ({a['account']})",
                      "Added to your ledger.", "", a["symbol"])
    return added


def log_advice_daily(today: date | None = None) -> int:
    """Once a day, after the market opens: write down the hold plan's actionable advice with prices."""
    from . import advice, holdplan
    from .providers import market
    today = today or date.today()
    with db.connect() as conn:
        if db.get_meta(conn, "advice_logged", "") == today.isoformat():
            return 0
    if not my_symbols()[0]:
        return 0
    plan = holdplan.cached()
    with db.connect() as conn:
        n = advice.record(conn, today.isoformat(), advice.from_plan(plan), lambda s: market.get_live_quote(s).price)
        db.set_meta(conn, "advice_logged", today.isoformat())
    return n


def build_newsdesk(symbols: list[str]) -> dict:
    """The news desk for your holdings (see newsdesk.py), remembered 20 minutes; confirmed
    Tier A stories are pushed once each."""
    import json
    from . import logos, newsdesk, reading
    from .providers import market
    now = datetime.now(timezone.utc)
    raw = logos.names(symbols)
    names = {s: reading.short_company_name(n) for s, n in raw.items() if n and market.asset_class(s) != "crypto"}
    coins = {s: n for s, n in raw.items() if n and market.asset_class(s) == "crypto"}
    with db.connect() as conn:
        history = json.loads(db.get_meta(conn, "newsdesk_history", "[]") or "[]")
    radar_by: dict[str, list[dict]] = {}
    try:
        mine, _ = radar_cache.get(tuple(symbols), lambda: sentinel.mine(symbols))
        for a in mine:
            radar_by.setdefault(a.symbol, []).append(a.to_dict())
    except Exception:  # noqa: BLE001 - the desk works without the radar
        pass
    desk = newsdesk.build(symbols, names, radar_by, history, now, crypto_names=coins)
    with db.connect() as conn:
        db.set_meta(conn, "newsdesk_history", json.dumps(newsdesk.remember(history, desk, now)))
    for st in newsdesk.new_reviews(desk):
        raise_headsup(f"newsdesk:{st['symbol']}:{st['event']}:{st['first'][:10]}", "news", 2,
                      f"{st['symbol']}: {st['event'].replace('_', ' ')} ({st['sources']} sources)", st["title"],
                      st["items"][0]["url"] if st["items"] else "", st["symbol"])
    return {"desk": desk, "feeds": dict(newsdesk.FEED_STATUS), "at": now.isoformat(timespec="seconds")}


def typical_moves(symbols: list[str]) -> dict[str, float | None]:
    """Each symbol's usual daily move (standard deviation over 60 days), refreshed daily."""
    from . import moves

    def one(s):
        try:
            return moves.typical_move([b.close for b in market.get_history(s, 120)])
        except (http.DataUnavailable, ValueError):
            return None
    return typical_cache.get(("typical", tuple(sorted(symbols)), date.today().isoformat()), lambda: {s: one(s) for s in symbols})


def moved_today() -> dict:
    from . import moves, service
    with db.connect() as conn:
        led = db.ledger(conn)
    if not led:
        return moves.today([], {}, None)
    positions = [p for p in service.portfolio_summary(led, False)["positions"] if p.get("quantity")]
    held = sorted(p["symbol"] for p in positions)
    desk = newsdesk_cache.peek(tuple(held), 7200)
    return moves.today(positions, typical_moves(held), (desk or {}).get("desk"))


def big_moves_check() -> list[dict]:
    """Push once a day per holding that moved more than twice its usual amount (and 3%+)."""
    from . import moves
    out = moved_today()
    for r in out["big"]:
        title, body = moves.push_text(r)
        raise_headsup(f"bigmove:{r['symbol']}:{date.today().isoformat()}:{'up' if r['change'] > 0 else 'down'}", "news", 2, title, body,
                      (r.get("why") or {}).get("url", ""), r["symbol"])
    return out["big"]


def raise_headsup(key: str, kind: str, level: int, title: str, body: str = "", url: str = "",
                  symbol: str = "") -> int:
    with db.connect() as conn:
        new = db.add_headsup(conn, key, kind, level, title, body, url, symbol,
                             datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if new:
        notify.send(notify.Message(title=title, body=body, url=url, priority=PUSH_PRIORITY.get(level, 3),
                                   tags=({"radar": ("rotating_light",), "news": ("newspaper",), "reading": ("books",),
                                          "topic": ("fire",), "early": ("zap",), "people": ("eyes",),
                                          "crypto": ("coin",)}.get(kind, ()))))
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

    def peek(self, key, max_age: float):
        """The stored value if it's younger than max_age seconds, without computing anything."""
        import time
        hit = self._c.store.get(key)
        return hit[1] if hit and time.monotonic() - hit[0] < max_age else None


early_cache = Cache(120)


def build_early(mine: set[str]) -> dict:
    """The early wire, with Coinbase's pair list remembered between runs (new pairs = listings)
    and each day's first sighting of a strong or early ticker logged with its price."""
    import json
    with db.connect() as conn:
        pairs = set(json.loads(db.get_meta(conn, "coinbase_pairs", "[]") or "[]"))
    data = early_cache.get("early", lambda: early.build(mine, known_pairs=pairs))
    today = datetime.now(timezone.utc).date().isoformat()
    with db.connect() as conn:
        if data.get("pairs"):
            db.set_meta(conn, "coinbase_pairs", json.dumps(data["pairs"]))
        logged = db.early_logged(conn, today)
    todo = [s for s in data["signals"] if (s["early"] or s["strength"] >= 50) and s["symbol"] not in logged][:10]

    def price(sym):
        try:
            return market.get_quote(sym).price
        except http.DataUnavailable:
            return None
    with ThreadPoolExecutor(max_workers=5) as pool:
        prices = dict(zip([s["symbol"] for s in todo], pool.map(price, [s["symbol"] for s in todo])))
    with db.connect() as conn:
        for s in todo:
            db.log_early(conn, today, s["symbol"], ",".join(s["kinds"]), s["strength"], s["early"], prices.get(s["symbol"]),
                         (s["reasons"] or [""])[0][:300])
        history = db.early_history(conn)
    for s in data["signals"]:
        s["yours"] = s["symbol"] in mine
    return dict(data, history=history)


def early_headsups(data: dict) -> int:
    n = 0
    for s in data.get("signals", []):
        why = (s["reasons"] or [""])[0]
        if "depeg" in s["kinds"]:
            n += raise_headsup(f"depeg:{s['symbol']}:{date.today().isoformat()}", "early", 3,
                               f"Stablecoin off its peg: {s['symbol']}", why, s["signals"][0].get("url", ""), s["symbol"])
        elif s["yours"] and s["strength"] >= 30:
            n += raise_headsup(f"early:{s['symbol']}:{date.today().isoformat()}", "early", 2,
                               f"{s['symbol']}: early signal{' (not in the mainstream yet)' if s['early'] else ''}", why,
                               s["signals"][0].get("url", ""), s["symbol"])
        elif "listing" in s["kinds"] and s["strength"] >= 40:
            n += raise_headsup(f"listing:{s['symbol']}", "early", 1, f"New listing: {s['symbol']}", why,
                               s["signals"][0].get("url", ""), s["symbol"])
    return n


def people_headsups() -> int:
    """New disclosed moves by people you follow."""
    from . import people
    with db.connect() as conn:
        follows = db.follows(conn)
    if not follows:
        return 0
    data = people_cache.get("people", lambda: people.build(follows=follows))
    n = 0
    for ms in data["sections"].values():
        for m in ms:
            if m["who"] in follows and m["symbol"] and m["disclosed"] >= (date.today() - timedelta(days=3)).isoformat():
                n += raise_headsup(f"people:{m['who']}:{m['symbol']}:{m['action']}:{m['disclosed']}", "people", 2,
                                   f"{m['who']}: {m['action']} {m['symbol']}", f"{m['detail']} {m['amount']}".strip(),
                                   m["url"], m["symbol"])
    return n


people_cache = Cache(1800)
crypto_cache = Cache(300)
news_cache = Cache(300)
reading_cache = Cache(600)
radar_cache = Cache(300)
newsdesk_cache = Cache(1200)
typical_cache = Cache(86400)


def build_news(symbols: list[str]) -> dict:
    return mynews.build(symbols, company_names(symbols))


def build_reading(symbols: list[str]) -> dict:
    with db.connect() as conn:
        topics = db.topics(conn, reading.DEFAULT_TOPICS)
    return reading.build(symbols, company_names(symbols), topics)


sentinel = Sentinel()
