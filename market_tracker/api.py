"""HTTP API and dashboard server."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Literal

from contextlib import asynccontextmanager
from urllib.parse import parse_qs

import anthropic
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (auth, charts, coinbase_sync, db, people, dilution, http, importers, journal, livefeed, notify, pulse, radar, reading, research,
               sentinel, service, strategy)
from .investors import INVESTORS, by_key
from .providers import market, news, sec


@asynccontextmanager
async def lifespan(app: FastAPI):
    livefeed.hub.start()
    sentinel.sentinel.start()
    yield
    await sentinel.sentinel.stop()
    await livefeed.hub.stop()


app = FastAPI(title="Plumbline", version="0.1.0", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")
throttle = auth.Throttle()


# ------------------------------------------------------------------ login (only when APP_PASSWORD is set)

@app.middleware("http")
async def require_login(request: Request, call_next):
    if auth.misconfigured() and request.url.path != "/healthz":
        return JSONResponse({"detail": "APP_PASSWORD is not set. Add it as a secret on your host, then restart."},
                            status_code=503)
    if not auth.enabled() or auth.is_open(request.url.path) or auth.valid_token(request.cookies.get(auth.COOKIE)):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Sign in required"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/api/session")
def session():
    return {"auth": auth.enabled()}


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/login", include_in_schema=False)
def login_form():
    return HTMLResponse(auth.login_page())


@app.post("/login", include_in_schema=False)
async def login(request: Request):
    client = request.client.host if request.client else "?"
    if throttle.blocked(client):
        return HTMLResponse(auth.login_page("Too many attempts. Wait 15 minutes."), status_code=429)
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    if not auth.check_password((form.get("password") or [""])[0]):
        throttle.fail(client)
        return HTMLResponse(auth.login_page("Wrong password."), status_code=401)
    throttle.reset(client)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.SESSION_DAYS * 86400, httponly=True,
                    secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
                    samesite="lax")
    return resp


@app.get("/logout", include_in_schema=False)
def logout():
    resp = RedirectResponse("/login" if auth.enabled() else "/", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=f"Upstream data unavailable: {exc}")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


# ------------------------------------------------------------------ market data

@app.get("/api/quote/{symbol}")
def quote(symbol: str):
    try:
        return market.get_quote(symbol).to_dict()
    except http.DataUnavailable as exc:
        raise _unavailable(exc)


@app.get("/api/analyze/{symbol}")
def analyze(symbol: str, smart_money: bool = True, news_: bool = Query(True, alias="news"),
            insiders: bool = True):
    return service.analyze(symbol, with_smart_money=smart_money, with_news=news_, with_insiders=insiders)


@app.get("/api/stream/quotes")
async def stream_quotes(symbols: str, interval: float = Query(5.0, ge=2.0, le=300.0)):
    """Server-sent events: pushes fresh quotes every `interval` seconds."""
    syms = [s for s in (x.strip() for x in symbols.split(",")) if s][:40]

    async def gen():
        while True:
            results = await asyncio.gather(*(asyncio.to_thread(_safe_quote, s) for s in syms))
            yield f"data: {json.dumps([r for r in results if r])}\n\n"
            await asyncio.sleep(interval)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _safe_quote(symbol: str) -> dict | None:
    try:
        return market.get_live_quote(symbol).to_dict()
    except http.DataUnavailable:
        return None


@app.get("/api/stream/live")
async def stream_live(request: Request, symbols: str, snapshot_only: bool = False):
    """Server-sent events: a snapshot of each symbol's price, then every live tick (see livefeed).
    `snapshot_only` ends the stream after the snapshot (a one-off batch quote)."""
    syms = [s for s in (x.strip() for x in symbols.split(",")) if s][:150]
    hub = livefeed.hub
    client = hub.subscribe(syms)

    async def snapshot(sym: str) -> dict | None:
        if sym in hub.latest:
            return hub.latest[sym].to_dict()
        q = await asyncio.to_thread(_safe_quote, sym)
        if q and q.get("previous_close"):
            hub.prev_close.setdefault(sym, q["previous_close"])
        return q and {"symbol": sym, "price": q["price"], "change_pct": q["change_pct"], "ts": q["as_of"],
                      "source": q["source"], "session": q.get("session", ""), "regular": q.get("regular_price")}

    async def gen():
        try:
            first = await asyncio.gather(*(snapshot(s) for s in client.symbols))
            yield f"event: snapshot\ndata: {json.dumps([x for x in first if x])}\n\n"
            while not snapshot_only:
                if await request.is_disconnected():
                    break
                try:
                    tick = await asyncio.wait_for(client.queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(tick.to_dict())}\n\n"
        finally:
            hub.unsubscribe(client)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/intraday/{symbol}")
def intraday(symbol: str, range_: Literal["1d", "5d", "1m", "3m", "1y", "5y"] = Query("1d", alias="range")):
    try:
        return livefeed.intraday(symbol, range_)
    except http.DataUnavailable as exc:
        raise _unavailable(exc)


@app.get("/api/candles/{symbol}")
def candle_chart(symbol: str, range_: Literal["1d", "1w", "1m", "3m", "1y", "5y"] = Query("1d", alias="range")):
    """OHLC candles with volume for the advanced chart."""
    try:
        return charts.candles(symbol, range_)
    except http.DataUnavailable as exc:
        raise _unavailable(exc)


history_cache = pulse.Cache(60)


@app.get("/api/portfolio/history")
async def portfolio_history(range_: Literal["1d", "1w", "1m", "3m", "1y", "all"] = Query("1d", alias="range")):
    """Your portfolio's value over the range, and the gain net of money added or withdrawn."""
    with db.connect() as conn:
        txs = db.list_transactions(conn)
        cash = float(db.get_meta(conn, "cash", "0") or 0)
    key = (range_, len(txs), max((t["id"] for t in txs), default=0))
    data = await asyncio.to_thread(history_cache.get, key, lambda: charts.portfolio_history(txs, range_))
    return dict(data, cash=cash)


@app.get("/api/sparklines")
async def sparkline_data(symbols: str):
    syms = [market.normalize_symbol(s) for s in symbols.split(",") if s.strip()][:40]
    return await asyncio.to_thread(sparkline_cache.get, tuple(sorted(syms)), lambda: charts.sparklines(syms))


sparkline_cache = pulse.Cache(120)


class CashIn(BaseModel):
    cash: float = Field(ge=0, le=1e10)


@app.get("/api/cash")
def get_cash():
    with db.connect() as conn:
        return {"cash": float(db.get_meta(conn, "cash", "0") or 0)}


@app.post("/api/cash")
def set_cash(body: CashIn):
    with db.connect() as conn:
        db.set_meta(conn, "cash", str(body.cash))
    return {"cash": body.cash}


@app.get("/api/holdings")
def holdings():
    """Open positions from the ledger, without prices (cheap; the page fills prices live)."""
    with db.connect() as conn:
        txs = db.list_transactions(conn)
    try:
        pos = service.pf.build_positions(txs)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    accounts: dict[str, set[str]] = {}
    for t in txs:
        accounts.setdefault(t["symbol"].upper(), set()).add(t.get("account") or "")
    return [{"symbol": p.symbol, "quantity": p.quantity, "avg_cost": p.avg_cost, "cost_basis": p.cost_basis,
             "accounts": sorted(a for a in accounts.get(p.symbol, ()) if a)}
            for p in pos.values() if p.quantity > 0]


# ------------------------------------------------------------------ smart money

@app.get("/api/investors")
def investors():
    return [vars(i) for i in INVESTORS]


@app.get("/api/investors/{key}")
def investor(key: str):
    inv = by_key(key)
    if not inv:
        raise HTTPException(404, f"Unknown investor {key}")
    try:
        rep = sec.investor_report(inv)
    except http.DataUnavailable as exc:
        raise _unavailable(exc)
    rep.pop("holdings", None)
    return rep


@app.get("/api/smart-money/consensus")
def smart_money_consensus(refresh: bool = False):
    reports, errors = service.smart_money_reports(refresh=refresh)
    return dict(sec.consensus(reports), errors=errors, investors=len(reports))


@app.get("/api/insiders/{symbol}")
def insider_trades(symbol: str, days: int = Query(90, ge=7, le=365)):
    try:
        return sec.summarize_insiders(sec.get_insider_trades(symbol, days=days), days=days)
    except http.DataUnavailable as exc:
        raise _unavailable(exc)


# ------------------------------------------------------------------ news

@app.get("/api/news/market")
def news_market():
    return news.market_news()


@app.get("/api/news/{symbol}")
def news_symbol(symbol: str, company: str | None = None):
    return news.get_news(symbol, company)


# ------------------------------------------------------------------ portfolio

class TransactionIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    price: float = Field(ge=0)
    fees: float = Field(0.0, ge=0)
    date: str = Field(default_factory=lambda: date.today().isoformat(), pattern=r"^\d{4}-\d{2}-\d{2}$")
    note: str | None = None
    account: str = Field("", max_length=40)


@app.get("/api/transactions")
def list_transactions():
    with db.connect() as conn:
        return db.list_transactions(conn)


@app.post("/api/transactions", status_code=201)
def add_transaction(tx: TransactionIn):
    symbol = market.normalize_symbol(tx.symbol)
    with db.connect() as conn:
        existing = db.list_transactions(conn)
        candidate = existing + [dict(tx.model_dump(), symbol=symbol, id=10**12)]
        try:
            service.pf.build_positions(candidate)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        tx_id = db.add_transaction(conn, symbol, tx.side, tx.quantity, tx.price, tx.date, tx.fees, tx.note,
                                   account=tx.account.strip())
    return {"id": tx_id}


@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: int):
    with db.connect() as conn:
        if not db.delete_transaction(conn, tx_id):
            raise HTTPException(404, "Not found")
    return {"deleted": tx_id}


@app.get("/api/pulse")
async def market_pulse(refresh: bool = False):
    """Movers, news attention, in-depth coverage and sleepers (cached for 3 minutes)."""
    if refresh:
        pulse.pulse_cache.store.clear()
    return await asyncio.to_thread(pulse.pulse_cache.get, "pulse", pulse.build)


@app.get("/api/sellwatch")
async def sell_watch():
    """Rule-based flags on each holding, with the reason for each."""
    with db.connect() as conn:
        txs = db.list_transactions(conn)
    if not txs:
        return {"holdings": []}
    try:
        summary = await asyncio.to_thread(service.portfolio_summary, txs, False)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    positions = [{"symbol": p["symbol"], "weight": p.get("weight"), "unrealized_pct": p.get("unrealized_pct")}
                 for p in summary["positions"]]
    key = tuple(sorted((p["symbol"], round(p["weight"] or 0)) for p in positions))

    def compute():
        return pulse.sell_watch(positions, analyze_fn=_analysis, cik_fn=pulse.cik_for_symbol, dilution_fn=_dilution)
    return {"holdings": await asyncio.to_thread(pulse.sellwatch_cache.get, key, compute)}


def _analysis(symbol: str) -> dict:
    return pulse.analysis_cache.get(symbol, lambda: service.analyze(symbol, with_smart_money=False))


def _dilution(cik: str) -> dilution.DilutionCheck:
    return pulse.dilution_cache.get(cik, lambda: dilution.check(cik, date.today()))


plan_cache = pulse.Cache(300)


def _plan_candidates(watch: list[str], held: set[str], limit: int = 10) -> list[tuple[str, str]]:
    """Symbols the plan may suggest buying: your watchlist, then the latest scan's sleepers."""
    out = [(s, "Watchlist") for s in watch]
    hit = pulse.pulse_cache.store.get("pulse")
    if hit:
        out += [(s["symbol"], "Sleeper: " + s["reasons"][0]) for s in hit[1].get("sleepers", [])]
    seen: set[str] = set()
    keep = []
    for sym, why in out:
        if sym not in held and sym not in seen:
            seen.add(sym)
            keep.append((sym, why))
    return keep[:limit]


@app.get("/api/plan")
async def strategy_plan(cash: float = Query(0.0, ge=0, le=1e10)):
    """Sell / trim / hold / add for each holding, plus new buys, with shares, reasons and tax
    notes. Each day's first recommendations are logged and returned as `history`."""
    with db.connect() as conn:
        txs = db.list_transactions(conn)
        watch = db.watchlist(conn)
    summary = {"positions": [], "total_value": 0.0}
    if txs:
        try:
            summary = await asyncio.to_thread(service.portfolio_summary, txs, False)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    positions = [p for p in summary["positions"] if p.get("quantity")]
    held = {p["symbol"] for p in positions}
    cands = _plan_candidates(watch, held)
    key = (tuple(sorted((p["symbol"], round(p["quantity"], 6)) for p in positions)), round(cash, 2),
           tuple(c[0] for c in cands))

    def compute():
        syms = [p["symbol"] for p in positions] + [c[0] for c in cands]
        with ThreadPoolExecutor(max_workers=6) as pool:
            analyses = dict(zip(syms, pool.map(lambda s: pulse._safe(_analysis, s), syms)))
        flags = {}
        for p in positions:
            a = analyses.get(p["symbol"])
            dil = None
            if a and market.asset_class(p["symbol"]) == "stock":
                cik = pulse.cik_for_symbol(p["symbol"])
                dil = pulse._safe(_dilution, cik) if cik else None
            # Position size is the plan's own rule (weights including cash), so no size flag here.
            flags[p["symbol"]] = pulse.sell_flags(a, None, dil, p.get("unrealized_pct")) if a else []
        return strategy.build_plan(summary, analyses, flags, txs, cash, cands)

    plan = await asyncio.to_thread(plan_cache.get, key, compute)
    with db.connect() as conn:
        db.log_plan(conn, plan["as_of"], plan["actions"])
        history = db.plan_history(conn)
    return dict(plan, history=history)


# ------------------------------------------------------------------ radar, news, reading, heads-ups

@app.get("/api/radar")
async def radar_view(refresh: bool = False):
    """Scary filings: yours (90 days, from each company's filing list, the live feed and the
    going-concern search) and the whole market's (last 3 days, from the live feed)."""
    s = sentinel.sentinel
    held, watched = await asyncio.to_thread(sentinel.my_symbols)
    if refresh or not s.scanned_at:
        await asyncio.to_thread(s.scan)
    key = tuple(held + watched)
    if refresh:
        sentinel.radar_cache.clear()
    mine, errors = await asyncio.to_thread(sentinel.radar_cache.get, key, lambda: s.mine(held + watched))
    market_alerts = sorted(s.market.values(), key=radar.when_sort_key, reverse=True)
    return {"scanned_at": s.scanned_at, "mine": [a.to_dict() for a in mine],
            "market": [a.to_dict() for a in market_alerts], "watching": len(sentinel.symbol_ciks(held + watched)),
            "held": held, "watched": watched, "errors": s.radar_errors + errors,
            "levels": radar.LEVEL_NAMES}


@app.get("/api/mynews")
async def my_news(refresh: bool = False):
    """Headlines for everything you own or watch, with a digest per symbol."""
    held, watched = await asyncio.to_thread(sentinel.my_symbols)
    syms = held + watched
    if not syms:
        return {"symbols": [], "feed": [], "errors": [], "generated_at": None}
    if refresh:
        sentinel.news_cache.clear()
    data = await asyncio.to_thread(sentinel.news_cache.get, tuple(syms), lambda: sentinel.build_news(syms))
    return dict(data, held=held)


@app.get("/api/reading")
async def reading_room(refresh: bool = False):
    """What professionals are reading, the desks' latest, your holdings in them, and your topics."""
    held, watched = await asyncio.to_thread(sentinel.my_symbols)
    syms = held + watched
    if refresh:
        sentinel.reading_cache.clear()
    return await asyncio.to_thread(sentinel.reading_cache.get, tuple(syms), lambda: sentinel.build_reading(syms))


@app.get("/api/early")
async def early_wire(limit: int = Query(80, ge=1, le=200), refresh: bool = False):
    """Tickers moving on social, press wires, SEC catalysts and crypto listings, marked early
    while the mainstream press hasn't covered them; plus the wire's own logged history."""
    held, watched = await asyncio.to_thread(sentinel.my_symbols)
    if refresh:
        sentinel.early_cache.clear()
    data = await asyncio.to_thread(sentinel.build_early, set(held + watched))
    return dict(data, signals=data["signals"][:limit])


@app.get("/api/people")
async def people_view(refresh: bool = False):
    """Congress trades, ARK's daily trades, big insider buys and activist stakes; your follows."""
    with db.connect() as conn:
        follows = db.follows(conn)
    if refresh:
        sentinel.people_cache.clear()
    data = await asyncio.to_thread(sentinel.people_cache.get, "people", lambda: people.build(follows=follows))
    followed = [m for ms in data["sections"].values() for m in ms if m["who"] in follows]
    return dict(data, follows=follows, following=sorted(followed, key=lambda m: m["disclosed"], reverse=True))


class FollowIn(BaseModel):
    who: str = Field(min_length=1, max_length=120)
    group: str = Field("congress", max_length=20)


@app.post("/api/people/follow", status_code=201)
def follow_person(body: FollowIn):
    with db.connect() as conn:
        db.follow(conn, body.who, body.group)
        return db.follows(conn)


@app.delete("/api/people/follow")
def unfollow_person(who: str):
    with db.connect() as conn:
        db.unfollow(conn, who)
        return db.follows(conn)


copy_cache = pulse.Cache(3600)


@app.get("/api/people/copy")
async def copy_person(who: str):
    """What copying this person's disclosed moves would have made, against SPY."""
    data = await asyncio.to_thread(sentinel.people_cache.get, "people", lambda: people.build())
    moves = [people.Move(**{k: v for k, v in m.items() if k != "lag_days"})
             for ms in data["sections"].values() for m in ms if m["who"] == who]
    if not moves:
        raise HTTPException(404, f"No disclosed moves for {who}")
    return await asyncio.to_thread(copy_cache.get, (who, len(moves)), lambda: dict(people.copy_sim(moves), who=who))


class TopicIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    terms: str = Field(min_length=1, max_length=400)


@app.get("/api/topics")
def list_topics():
    with db.connect() as conn:
        return db.topics(conn, reading.DEFAULT_TOPICS)


@app.post("/api/topics", status_code=201)
def save_topic(t: TopicIn):
    with db.connect() as conn:
        db.topics(conn, reading.DEFAULT_TOPICS)
        db.set_topic(conn, t.name.strip(), t.terms.strip())
        out = db.topics(conn)
    sentinel.reading_cache.clear()
    return out


@app.delete("/api/topics/{name}")
def remove_topic(name: str):
    with db.connect() as conn:
        db.delete_topic(conn, name)
        out = db.topics(conn)
    sentinel.reading_cache.clear()
    return out


@app.get("/api/headsup")
def headsups():
    with db.connect() as conn:
        items = db.list_headsup(conn)
    return {"items": items, "unread": sum(1 for i in items if not i["read"]),
            "push": notify.configured(), "reading_at": sentinel.sentinel.reading_at}


@app.post("/api/headsup/read")
def headsup_read():
    with db.connect() as conn:
        db.mark_headsup_read(conn)
    return {"ok": True}


@app.get("/api/sync/coinbase")
def coinbase_status():
    return {"configured": coinbase_sync.configured()}


@app.post("/api/sync/coinbase")
def coinbase_sync_now():
    """Import new Coinbase fills (read-only key) and compare balances with the ledger."""
    if not coinbase_sync.configured():
        raise HTTPException(400, "Add COINBASE_API_KEY_NAME and COINBASE_API_PRIVATE_KEY (a View-only key) to .env first.")
    try:
        txs = coinbase_sync.fills_to_transactions(coinbase_sync.fills())
        bal = coinbase_sync.balances()
    except (http.DataUnavailable, ValueError) as exc:
        raise HTTPException(502, f"Coinbase: {exc}")
    res = coinbase_sync.SyncResult()
    with db.connect() as conn:
        known = db.import_keys(conn)
        new = [t for t in txs if t["import_key"] not in known]
        existing = db.list_transactions(conn)
        try:
            positions = service.pf.build_positions(existing + new)
        except ValueError as exc:
            raise HTTPException(400, f"{exc}. Coins that arrived by transfer or reward need adding first (Quick add).")
        for t in new:
            db.add_transaction(conn, t["symbol"], t["side"], t["quantity"], t["price"], t["date"], t["fees"], t["note"],
                               import_key=t["import_key"], account="Coinbase")
    res.new, res.duplicates = len(new), len(txs) - len(new)
    cb_qty: dict[str, float] = {}
    for t in existing + new:
        if (t.get("account") or "") == "Coinbase":
            cb_qty[t["symbol"]] = cb_qty.get(t["symbol"], 0.0) + (t["quantity"] if t["side"] == "buy" else -t["quantity"])
    res.differences = coinbase_sync.reconcile(bal, cb_qty)
    return {"new": res.new, "duplicates": res.duplicates, "differences": res.differences,
            "positions": sorted(p.symbol for p in positions.values() if p.quantity > 0 and p.symbol.endswith("-USD"))}


@app.get("/api/backup")
def backup():
    """Everything you entered, as one JSON file: trades, watchlist, cash, topics, follows."""
    with db.connect() as conn:
        return {"format": "plumbline-backup", "version": 1, "exported": date.today().isoformat(),
                "transactions": db.list_transactions(conn), "watchlist": db.watchlist(conn),
                "cash": float(db.get_meta(conn, "cash", "0") or 0), "topics": db.topics(conn), "follows": db.follows(conn)}


class RestoreIn(BaseModel):
    data: dict


@app.post("/api/backup/restore")
def restore(body: RestoreIn):
    """Add a backup's contents. Trades already present (same symbol, side, date, quantity and
    price) are skipped, so restoring twice changes nothing."""
    d = body.data
    if d.get("format") != "plumbline-backup":
        raise HTTPException(400, "This isn't a Plumbline backup file.")
    added = 0
    with db.connect() as conn:
        keys = db.import_keys(conn)
        for t in d.get("transactions") or []:
            key = t.get("import_key") or "bk:" + "|".join(str(t.get(k)) for k in ("symbol", "side", "date", "quantity", "price"))
            if key in keys:
                continue
            db.add_transaction(conn, t["symbol"], t["side"], float(t["quantity"]), float(t["price"]), t["date"],
                               float(t.get("fees") or 0), t.get("note"), import_key=key, account=t.get("account") or "")
            keys.add(key)
            added += 1
        try:
            service.pf.build_positions(db.list_transactions(conn))
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(400, f"Restoring would leave an impossible ledger: {exc}")
        for w in d.get("watchlist") or []:
            db.add_watch(conn, w)
        if d.get("cash"):
            db.set_meta(conn, "cash", str(float(d["cash"])))
        db.topics(conn, reading.DEFAULT_TOPICS)
        for name, terms in (d.get("topics") or {}).items():
            db.set_topic(conn, name, terms)
        for who, grp in (d.get("follows") or {}).items():
            db.follow(conn, who, grp)
    return {"transactions_added": added}


class ImportIn(BaseModel):
    csv: str = Field(min_length=1, max_length=5_000_000)
    commit: bool = False
    account: str = Field("Stash", max_length=40)      # for the holdings list only


IMPORT_HINTS = {
    "robinhood": "The file probably starts after some of these shares were bought: export the full history, "
                 "or record the earlier buys first.",
    "coinbase": "Coins received from another wallet or exchange have no purchase in this file: add them with "
                "Quick add (their original cost), then import again.",
    "holdings": "",
}


@app.post("/api/import/{source}")
def import_trades(source: Literal["robinhood", "coinbase", "holdings"], body: ImportIn):
    """Preview (commit=false) or import a Robinhood or Coinbase CSV, or a quick holdings list
    (Stash and other accounts with no export)."""
    if source == "robinhood":
        res = importers.parse_robinhood(body.csv)
    elif source == "coinbase":
        res = importers.parse_coinbase(body.csv)
    else:
        res = importers.parse_holdings_list(body.csv, body.account.strip() or "Other", date.today().isoformat())
    if res.errors and not res.transactions:
        raise HTTPException(400, res.errors[0])
    with db.connect() as conn:
        existing = db.list_transactions(conn)
        known = db.import_keys(conn)
        new = [t for t in res.transactions if t["import_key"] not in known]
        try:
            positions = service.pf.build_positions(existing + new)
        except ValueError as exc:
            raise HTTPException(400, f"{exc}. {IMPORT_HINTS[source]}".strip())
        if body.commit:
            for t in new:
                db.add_transaction(conn, t["symbol"], t["side"], t["quantity"], t["price"], t["date"], t["fees"],
                                   t["note"], import_key=t["import_key"], account=t.get("account", ""))
    touched = {t["symbol"] for t in res.transactions}
    return {"new": len(new), "duplicates": len(res.transactions) - len(new), "skipped": dict(res.skipped),
            "errors": res.errors, "committed": body.commit,
            "positions": sorted([{"symbol": p.symbol, "quantity": p.quantity, "avg_cost": p.avg_cost}
                                 for p in positions.values() if p.quantity > 0 and p.symbol in touched],
                                key=lambda x: x["symbol"])}


@app.get("/api/portfolio")
def portfolio(risk: bool = True):
    with db.connect() as conn:
        txs = db.list_transactions(conn)
    try:
        return service.portfolio_summary(txs, with_risk=risk)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/watchlist")
def get_watchlist():
    with db.connect() as conn:
        return db.watchlist(conn)


@app.post("/api/watchlist/{symbol}", status_code=201)
def add_watch(symbol: str):
    with db.connect() as conn:
        db.add_watch(conn, market.normalize_symbol(symbol))
        return db.watchlist(conn)


@app.delete("/api/watchlist/{symbol}")
def remove_watch(symbol: str):
    with db.connect() as conn:
        db.remove_watch(conn, market.normalize_symbol(symbol))
        return db.watchlist(conn)


# ------------------------------------------------------------------ journal

@app.post("/api/journal/record")
def journal_record(symbols: list[str] | None = None):
    if not symbols:
        with db.connect() as conn:
            symbols = db.watchlist(conn) or journal.DEFAULT_UNIVERSE
    entries, skipped = [], []
    for sym in symbols[:40]:
        # Same components as the daily GitHub Actions job, so rows are comparable.
        entry = journal.entry_from_analysis(service.analyze(sym))
        (entries.append(entry) if entry else skipped.append(sym))
    journal.record(entries)
    return {"recorded": len(entries), "skipped": skipped, "path": journal.journal_path()}


@app.get("/api/journal/report")
def journal_report():
    rows = journal.load()
    if not rows:
        return {"entries": 0, "horizons": [], "errors": [], "verdict":
                "No journal entries yet. Record today's scores, then keep recording daily."}
    return journal.evaluate(rows, journal.history_closes)


# ------------------------------------------------------------------ research

@app.get("/api/research/{symbol}")
def research_stream(symbol: str, question: str | None = None):
    """Server-sent events: status updates, memo text chunks, then the structured verdict."""

    def gen():
        def sse(obj: dict) -> str:
            return f"data: {json.dumps(obj)}\n\n"

        yield sse({"type": "status", "text": "Collecting market, smart-money and news data…"})
        analysis = service.analyze(symbol)
        memo = ""
        try:
            for event in research.stream_memo(analysis, question):
                if event["type"] == "done":
                    memo = event["memo"]
                else:
                    yield sse(event)
            if memo:
                yield sse({"type": "status", "text": "Extracting verdict…"})
                verdict = research.extract_verdict(analysis["symbol"], memo)
                yield sse({"type": "verdict", "verdict": verdict.model_dump() if verdict else None})
        except anthropic.AuthenticationError:
            yield sse({"type": "error", "text": "No valid Anthropic credentials. Set ANTHROPIC_API_KEY."})
        except anthropic.RateLimitError:
            yield sse({"type": "error", "text": "Rate limited by the Claude API; try again shortly."})
        except anthropic.APIStatusError as exc:
            yield sse({"type": "error", "text": f"Claude API error {exc.status_code}: {exc.message}"})
        except anthropic.APIConnectionError:
            yield sse({"type": "error", "text": "Could not reach the Claude API."})
        yield sse({"type": "end"})

    return StreamingResponse(gen(), media_type="text/event-stream")
