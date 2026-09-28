"""HTTP API and dashboard server."""

from __future__ import annotations

import asyncio
import functools
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from contextlib import asynccontextmanager
from urllib.parse import parse_qs

import anthropic
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (accounts, auth, brief, charts, cryptoradar, events, coinbase_sync, db, dilution, dividends, early, fundamentals, holdplan, people, pickers, http, importers, journal, livefeed, logos, notify, pulse, radar, reading, research, snaptrade,
               sentinel, service, strategy, taxes, trading, transfers)
from . import config
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
async def same_origin_writes(request: Request, call_next):
    """Changes (orders, keys, settings) only from this app's own pages: a request another website
    makes from your browser carries its own Origin and is refused."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        # The app's pages send X-Plumbline; a browser won't let another site add it without asking
        # this app first (and this app never agrees), so it proves the request came from here,
        # even behind a proxy (Tailscale, Fly) that changes the Host header.
        if origin and not request.headers.get("x-plumbline"):
            from urllib.parse import urlparse
            if urlparse(origin).netloc != request.headers.get("host", ""):
                return JSONResponse({"detail": "Refused: request from another site"}, status_code=403)
    return await call_next(request)


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


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Served from the root so it can cover the whole app (installing it on a phone)."""
    return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


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
        txs = db.ledger(conn)
    try:
        pos = service.pf.build_positions(txs)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    by_acct: dict[str, dict[str, float]] = {}
    for t in txs:
        d = by_acct.setdefault(t["symbol"].upper(), {})
        a = t.get("account") or ""
        d[a] = d.get(a, 0.0) + (t["quantity"] if t["side"] == "buy" else -t["quantity"])
    out = []
    for p in pos.values():
        if p.quantity <= 0:
            continue
        held = {a: round(q, 8) for a, q in by_acct.get(p.symbol, {}).items() if q > 1e-9}
        out.append({"symbol": p.symbol, "quantity": p.quantity, "avg_cost": p.avg_cost, "cost_basis": p.cost_basis,
                    "accounts": sorted(a for a in held if a), "by_account": held})
    return out


# ------------------------------------------------------------------ smart money

@app.get("/api/logo/{symbol}", include_in_schema=False)
async def logo_image(symbol: str):
    """A ticker's logo (fetched once, then served from disk), or a letter on a circle."""
    data, ctype = await asyncio.to_thread(logos.logo, symbol)
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "private, max-age=604800"})


@app.get("/api/names")
async def ticker_names(symbols: str = Query("", max_length=4000)):
    """{symbol: company or coin name} for a comma-separated list."""
    syms = [s for s in symbols.split(",") if s.strip()]
    return await asyncio.to_thread(logos.names, syms)


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
        existing = db.ledger(conn)
        candidate = existing + [dict(tx.model_dump(), symbol=symbol, id=10**12 - 1)]
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


events_cache = pulse.Cache(3600)


def _hold_settings(conn) -> dict:
    return holdplan.settings(conn)


async def _holdplan_data(refresh: bool = False) -> dict:
    try:
        return await asyncio.to_thread(holdplan.cached, refresh)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


_income_cache = sentinel.Cache(900)
_income_last: dict = {}


@app.get("/api/income")
async def income_view(refresh: bool = False):
    """Dividends received (imported, or estimated where an account has none), forward income,
    yield on cost, upcoming ex/pay dates and the next 12 months."""
    with db.connect() as conn:
        txs = db.list_transactions(conn)
        rows = db.income(conn)
    key = (len(txs), max((t["id"] for t in txs), default=0), len(rows), date.today().isoformat())
    if refresh:
        _income_cache.clear()

    def compute():
        positions = service.portfolio_summary(txs, False)["positions"] if txs else []
        out = dividends.build(positions, txs, rows, date.today())
        _income_last["upcoming"] = out["upcoming"]
        return out
    try:
        return await asyncio.to_thread(_income_cache.get, key, compute)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/events")
async def events_view(refresh: bool = False):
    """Earnings for your stocks with the options-implied move in dollars, Fed decisions and the
    next two weeks of market-moving US releases."""
    with db.connect() as conn:
        txs = db.list_transactions(conn)
    positions = []
    if txs:
        try:
            positions = (await asyncio.to_thread(service.portfolio_summary, txs, False))["positions"]
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    held = tuple(p["symbol"] for p in positions if p.get("quantity"))
    if refresh:
        events_cache.store.clear()
    return await asyncio.to_thread(events_cache.get, held, lambda: events.build(positions, date.today()))


pickers_cache = pulse.Cache(1800)


@app.get("/api/cryptoradar")
async def crypto_radar(refresh: bool = False):
    """Hacks, Coinbase incidents, supply overhang and depegs for the coins you hold or watch."""
    held, watched = await asyncio.to_thread(sentinel.my_symbols)
    syms = tuple(s for s in held + watched if market.asset_class(s) == "crypto")
    if refresh:
        sentinel.crypto_cache.clear()
    return await asyncio.to_thread(sentinel.crypto_cache.get, syms, lambda: cryptoradar.build(list(syms)))


brief_cache = pulse.Cache(600)


@app.get("/api/brief")
async def morning_brief(refresh: bool = False):
    """Today's morning brief: what needs a decision, new filings, earnings and releases, moves,
    early-wire hits, crypto items and tax dates. The 8:30 ET copy is also pushed to your phone."""
    if refresh:
        brief_cache.store.clear()
    try:
        return await asyncio.to_thread(brief_cache.get, "brief", brief.gather)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/brief/send")
async def send_brief_now():
    """Push the brief to your phone now (to try the notification)."""
    b = await asyncio.to_thread(brief_cache.get, "brief", brief.gather)
    sent = await asyncio.to_thread(notify.send, notify.Message(title=b["title"], body=brief.push_text(b), priority=3,
                                                              tags=("sunrise",)))
    return {"sent": bool(sent), "title": b["title"]}


def _picker_names(conn) -> list[str]:
    return sorted(w[3:] for w, g in db.follows(conn, include_pickers=True).items() if g == "picker" and w.startswith("st:"))


def _picker_view(user: str) -> dict:
    errors = []
    profile = {}
    try:
        profile, calls = pickers.fetch_calls(user)
        with db.connect() as conn:
            db.save_picker_calls(conn, calls)
    except (http.DataUnavailable, KeyError, TypeError) as exc:
        errors.append(str(exc)[:120])
    with db.connect() as conn:
        stored = db.picker_calls(conn, user)
    return {"username": user, "profile": {k: profile.get(k) for k in ("name", "followers", "official", "join_date", "ideas")},
            **pickers.score(stored, quote_fn=market.get_quote), "errors": errors}


@app.get("/api/pickers")
async def pickers_view(refresh: bool = False):
    """Stock pickers you follow, graded on their tagged calls against SPY, plus suggestions."""
    with db.connect() as conn:
        names = _picker_names(conn)
    if refresh:
        pickers_cache.store.clear()
    graded = await asyncio.gather(*[asyncio.to_thread(pickers_cache.get, n, lambda n=n: _picker_view(n)) for n in names])
    try:
        sugg = await asyncio.to_thread(pickers_cache.get, "__suggested", pickers.suggested)
    except (http.DataUnavailable, KeyError, TypeError):
        sugg = []
    return {"following": sorted(graded, key=lambda g: -(g["median_excess_pct"] if g["median_excess_pct"] is not None else -1e9)),
            "suggested": [u for u in sugg if u["username"].lower() not in names], "horizon": pickers.HORIZON}


class PickerIn(BaseModel):
    username: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_]+$")


@app.post("/api/pickers/follow")
async def follow_picker(body: PickerIn):
    user = body.username.lower()
    try:
        profile, _ = await asyncio.to_thread(pickers.fetch_calls, user, None, 1)
    except http.DataUnavailable as exc:
        raise HTTPException(404, f"Couldn't find {body.username} on StockTwits: {exc}")
    if not profile:
        raise HTTPException(404, f"Couldn't find {body.username} on StockTwits")
    with db.connect() as conn:
        db.follow(conn, "st:" + user, "picker")
        names = _picker_names(conn)
    pickers_cache.store.pop(user, None)
    return {"following": names}


@app.delete("/api/pickers/follow")
async def unfollow_picker(username: str):
    with db.connect() as conn:
        db.unfollow(conn, "st:" + username.lower())
        return {"following": _picker_names(conn)}


@app.get("/api/holdplan")
async def hold_plan(refresh: bool = False):
    """Buy-and-hold plan: Hold by default; Sell?/Trim/Review only when your tripwire, a serious
    filing, concentration or taxes say so. Plus the reinvest queue and the tax picture."""
    return await _holdplan_data(refresh)


@app.get("/api/taxes")
async def tax_view():
    """Realized gains this year, wash sales across accounts, the don't-buy list, the long-term
    clock and harvest candidates."""
    return (await _holdplan_data())["tax"]


def _yearend_settings(conn) -> dict:
    def num(k):
        v = db.get_meta(conn, k, "")
        return float(v) if v not in ("", None) else None
    return {"filing": db.get_meta(conn, "tax_filing", "single") or "single", "taxable_income": num("tax_income"),
            "carryover": num("tax_carryover") or 0.0, "zero_limit": num("tax_zero_limit")}


@app.get("/api/taxes/yearend")
async def tax_year_end():
    """Before Dec 31: realized gains, the losses worth taking to offset them and what that saves,
    and long-term gains you could take at 0% if your income is low enough."""
    plan = await _holdplan_data()
    with db.connect() as conn:
        cfg = _yearend_settings(conn)
        txs = db.ledger(conn)
        drip = {r["symbol"] for r in db.income(conn) if r["kind"] == "reinvested"} | set(json.loads(db.get_meta(conn, "drip_symbols", "[]") or "[]"))
    out = taxes.year_end(plan["tax"], txs, date.today(), st_rate=plan["rules"]["short_term_rate"], lt_rate=plan["rules"]["long_term_rate"],
                         upcoming_dividends=_income_last.get("upcoming"), drip_symbols=drip, **cfg)
    out["settings"] = cfg
    out["default_zero_limit"] = taxes.ZERO_RATE_LIMIT.get(cfg["filing"], taxes.ZERO_RATE_LIMIT["single"])
    return out


class YearEndSettings(BaseModel):
    filing: Literal["single", "married", "head", "separate"] = "single"
    taxable_income: float | None = Field(None, ge=0, le=1e9)
    carryover: float | None = Field(None, ge=0, le=1e9)
    zero_limit: float | None = Field(None, ge=0, le=1e7)


@app.post("/api/taxes/yearend/settings")
async def tax_year_end_settings(body: YearEndSettings):
    with db.connect() as conn:
        db.set_meta(conn, "tax_filing", body.filing)
        for k, meta in (("taxable_income", "tax_income"), ("carryover", "tax_carryover"), ("zero_limit", "tax_zero_limit")):
            v = getattr(body, k)
            db.set_meta(conn, meta, "" if v is None else str(v))
        return _yearend_settings(conn)


class ThesisIn(BaseModel):
    thesis: str = Field("", max_length=2000)
    wrong_if: str = Field("", max_length=1000)
    price_below: float | None = Field(None, ge=0)
    price_above: float | None = Field(None, ge=0)
    max_loss_pct: float | None = Field(None, ge=0, le=100)
    review_on: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    target_weight: float | None = Field(None, gt=0, le=1)
    rev_growth_min: float | None = Field(None, ge=-100, le=500)
    rev_growth_quarters: int | None = Field(None, ge=1, le=8)
    op_margin_min: float | None = Field(None, ge=-100, le=100)
    dilution_max: float | None = Field(None, ge=0, le=100)
    fcf_positive: bool = False


@app.get("/api/fundamentals/{symbol}")
async def fundamentals_view(symbol: str):
    """Quarterly revenue growth, margins, EPS, free cash flow and share count from the company's
    SEC filings, with the checks that would raise it in the hold plan."""
    sym = market.normalize_symbol(symbol)
    with db.connect() as conn:
        raw = db.theses(conn)
    try:
        got, errors = await asyncio.to_thread(fundamentals.build, [sym], holdplan.theses_from(raw))
    except http.DataUnavailable as exc:
        raise HTTPException(502, str(exc))
    if sym not in got:
        return {"symbol": sym, "quarters": [], "checks": [], "line": "",
                "note": errors[0] if errors else "No company financials: funds, crypto and non-US listings don't file them with the SEC."}
    return {"symbol": sym, **got[sym]}


@app.get("/api/thesis")
async def theses_view():
    with db.connect() as conn:
        return db.theses(conn)


@app.post("/api/thesis/{symbol}")
async def save_thesis(symbol: str, body: ThesisIn):
    sym = market.normalize_symbol(symbol)
    with db.connect() as conn:
        db.save_thesis(conn, sym, body.model_dump())
        out = db.theses(conn)[sym]
    holdplan.clear_cache()
    return out


@app.delete("/api/thesis/{symbol}")
async def delete_thesis(symbol: str):
    with db.connect() as conn:
        db.delete_thesis(conn, market.normalize_symbol(symbol))
    holdplan.clear_cache()
    return {}


@app.get("/api/holdplan/newmoney")
async def hold_new_money(amount: float = Query(..., gt=0, le=10_000_000)):
    """Where the next deposit goes toward your targets, without selling."""
    return holdplan.new_money(await _holdplan_data(), amount)


class HoldSettings(BaseModel):
    cap: float | None = Field(None, gt=0, le=1)
    st_rate: float | None = Field(None, ge=0, le=0.6)
    lt_rate: float | None = Field(None, ge=0, le=0.4)


@app.post("/api/holdplan/settings")
async def hold_settings(body: HoldSettings):
    with db.connect() as conn:
        for k, meta in (("cap", "hold_cap"), ("st_rate", "tax_st_rate"), ("lt_rate", "tax_lt_rate")):
            v = getattr(body, k)
            if v is not None:
                db.set_meta(conn, meta, str(v))
        out = _hold_settings(conn)
    holdplan.clear_cache()
    return out


@app.get("/api/plan")
async def strategy_plan(cash: float = Query(0.0, ge=0, le=1e10)):
    """Sell / trim / hold / add for each holding, plus new buys, with shares, reasons and tax
    notes. Each day's first recommendations are logged and returned as `history`."""
    with db.connect() as conn:
        txs = db.ledger(conn)
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


scorecard_cache = pulse.Cache(1800)


@app.get("/api/early/scorecard")
async def early_scorecard(horizon: int = Query(5, ge=1, le=60)):
    """The wire's logged first sightings `horizon` closes later, against SPY over the same days."""
    with db.connect() as conn:
        logged = db.early_history(conn, limit=5000)
    return await asyncio.to_thread(scorecard_cache.get, f"h{horizon}:{len(logged)}",
                                   lambda: early.scorecard(logged, horizon=horizon))


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
async def coinbase_sync_now():
    """Import new Coinbase fills (read-only key) and compare balances with the ledger."""
    try:
        out = await asyncio.to_thread(accounts.run_sync, "coinbase")
    except accounts.SyncError as exc:
        raise HTTPException(exc.status, str(exc))
    holdplan.clear_cache()
    return out


@app.get("/api/sync/snaptrade")
def snaptrade_status():
    with db.connect() as conn:
        return {"configured": snaptrade.configured(), "last_sync": db.get_meta(conn, "snaptrade_last_sync", "") or None}


# ------------------------------------------------------------------ connections (set up from the app)

def _env_path() -> str:
    return str(Path.cwd() / ".env")


def _save_env(values: dict[str, str]) -> None:
    """Write keys into .env (next to the app) and into this running process, so no restart is needed."""
    import os
    from . import firstrun
    lines = firstrun.read_env(_env_path())
    for k, v in values.items():
        lines = firstrun.put(lines, k, v.replace('"', "").replace("\n", "\\n"))
        os.environ[k] = v
    with open(_env_path(), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


@app.get("/api/connections")
def connections_view():
    """Each free connection: set up or not, when it last ran, and what it said."""
    from . import email_trades, robinhood_crypto
    with db.connect() as conn:
        unread = json.loads(db.get_meta(conn, "email_unread", "[]") or "[]")
    return {
        "email": {"configured": email_trades.configured(), "last": accounts.last_sync("email"), "unread": unread,
                  "user": __import__("os").environ.get("MAIL_USER", "")},
        "coinbase": {"configured": coinbase_sync.configured(), "last": accounts.last_sync("coinbase")},
        "robinhood_crypto": {"configured": robinhood_crypto.configured(), "last": accounts.last_sync("robinhood_crypto"),
                             "public_key": db_meta("robinhood_public_key")},
        "snaptrade": {"configured": snaptrade.configured(), "last": accounts.last_sync("snaptrade")},
    }


def db_meta(key: str) -> str:
    with db.connect() as conn:
        return db.get_meta(conn, key, "")


class EmailIn(BaseModel):
    user: str = Field(..., min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    app_password: str = Field(..., min_length=8, max_length=64)
    imap_host: str | None = Field(None, max_length=100, pattern=r"^[a-z0-9.-]+$")


@app.post("/api/connections/email")
async def connect_email(body: EmailIn):
    """Check the login, then save it. Read-only: the app only reads broker emails."""
    from . import email_trades
    import os
    old = {k: os.environ.get(k) for k in ("MAIL_USER", "MAIL_APP_PASSWORD", "MAIL_IMAP_HOST")}
    os.environ["MAIL_USER"], os.environ["MAIL_APP_PASSWORD"] = body.user, body.app_password
    if body.imap_host:
        os.environ["MAIL_IMAP_HOST"] = body.imap_host
    problem = await asyncio.to_thread(email_trades.check_login)
    if problem:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        raise HTTPException(400, f"Couldn't sign in: {problem}. For Gmail, use an app password (not your normal password).")
    _save_env({"MAIL_USER": body.user, "MAIL_APP_PASSWORD": body.app_password, **({"MAIL_IMAP_HOST": body.imap_host} if body.imap_host else {})})
    try:
        result = await asyncio.to_thread(accounts.run_sync, "email")
    except accounts.SyncError as exc:
        result = {"error": str(exc)}
    holdplan.clear_cache()
    return {"connected": True, "first_sync": result}


class CoinbaseKeyIn(BaseModel):
    key_name: str = Field(..., min_length=10, max_length=300)
    private_key: str = Field(..., min_length=40, max_length=4000)


@app.post("/api/connections/coinbase")
async def connect_coinbase(body: CoinbaseKeyIn):
    import os
    try:
        coinbase_sync.make_jwt(body.key_name, body.private_key, "GET", "/api/v3/brokerage/accounts")
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"That private key doesn't load: {exc}. Create the key with the ECDSA algorithm.")
    os.environ["COINBASE_API_KEY_NAME"], os.environ["COINBASE_API_PRIVATE_KEY"] = body.key_name, body.private_key
    try:
        result = await asyncio.to_thread(accounts.run_sync, "coinbase")
    except accounts.SyncError as exc:
        os.environ.pop("COINBASE_API_KEY_NAME", None)
        os.environ.pop("COINBASE_API_PRIVATE_KEY", None)
        raise HTTPException(exc.status, str(exc))
    _save_env({"COINBASE_API_KEY_NAME": body.key_name, "COINBASE_API_PRIVATE_KEY": body.private_key.replace("\n", "\\n")})
    holdplan.clear_cache()
    return {"connected": True, "first_sync": result}


@app.post("/api/connections/robinhood/keypair")
def robinhood_keypair():
    """Make the key pair Robinhood asks for: the private half stays in .env, the public half is
    what you paste into Robinhood's API credentials page."""
    from . import robinhood_crypto
    priv, pub = robinhood_crypto.new_keypair()
    _save_env({"ROBINHOOD_CRYPTO_PRIVATE_KEY": priv})
    with db.connect() as conn:
        db.set_meta(conn, "robinhood_public_key", pub)
    return {"public_key": pub}


class RobinhoodKeyIn(BaseModel):
    api_key: str = Field(..., min_length=10, max_length=200, pattern=r"^[A-Za-z0-9_\-]+$")


@app.post("/api/connections/robinhood")
async def connect_robinhood(body: RobinhoodKeyIn):
    from . import robinhood_crypto
    import os
    if not os.environ.get("ROBINHOOD_CRYPTO_PRIVATE_KEY"):
        raise HTTPException(400, "Make the key pair first (step 1).")
    os.environ["ROBINHOOD_CRYPTO_API_KEY"] = body.api_key
    try:
        await asyncio.to_thread(robinhood_crypto.account)
    except robinhood_crypto.RobinhoodError as exc:
        os.environ.pop("ROBINHOOD_CRYPTO_API_KEY", None)
        raise HTTPException(400, f"Robinhood didn't accept it: {exc}")
    _save_env({"ROBINHOOD_CRYPTO_API_KEY": body.api_key})
    try:
        result = await asyncio.to_thread(accounts.run_sync, "robinhood_crypto")
    except accounts.SyncError as exc:
        result = {"error": str(exc)}
    holdplan.clear_cache()
    return {"connected": True, "first_sync": result}


@app.post("/api/sync/robinhood_crypto")
async def sync_robinhood_crypto_now():
    return await _sync_now("robinhood_crypto")


@app.post("/api/sync/email")
async def sync_email_now():
    return await _sync_now("email")


async def _sync_now(kind: str):
    try:
        out = await asyncio.to_thread(accounts.run_sync, kind)
    except accounts.SyncError as exc:
        raise HTTPException(exc.status, str(exc))
    holdplan.clear_cache()
    return out


# ------------------------------------------------------------------ trading

class TradeIn(BaseModel):
    venue: Literal["coinbase", "robinhood", "paper", "alpaca", "public", "ticket"]
    symbol: str = Field(..., min_length=1, max_length=20)
    side: Literal["buy", "sell"]
    dollars: float | None = Field(None, gt=0, le=1_000_000)
    quantity: float | None = Field(None, gt=0, le=1e12)
    limit_price: float | None = Field(None, gt=0, le=1e9)
    token: str | None = Field(None, max_length=200)

    def order(self) -> "trading.Order":
        return trading.Order(self.venue, market.normalize_symbol(self.symbol), self.side, self.dollars, self.quantity, self.limit_price)


@app.get("/api/trade/venues/{symbol}")
def trade_venues(symbol: str):
    with db.connect() as conn:
        cfg = trading.settings(conn)
    return {"venues": trading.venues(market.normalize_symbol(symbol)), "settings": cfg}


@app.post("/api/trade/preview")
async def trade_preview(body: TradeIn):
    with db.connect() as conn:
        txs = db.ledger(conn)
        cfg = trading.settings(conn)
        spent = trading.spent_today(conn)
        methods = _lot_methods(conn)
    try:
        plan = holdplan._cache.get("plan", (None, 0, None))[2]
        out = await asyncio.to_thread(functools.partial(trading.preview, body.order(), txs, plan, cfg, spent, methods=methods))
    except trading.TradeError as exc:
        raise HTTPException(400, str(exc))
    if body.side == "sell" and body.venue != "paper":
        out = _cooloff_gate(out, body.order().symbol, plan)
    return out


def _cooloff_gate(out: dict, symbol: str, plan: dict | None) -> dict:
    """A sell no rule backs waits 48 hours with its reason written down (cooloff.py)."""
    from datetime import datetime, timezone

    from . import cooloff, decisions
    if plan is None:
        try:
            plan = holdplan.cached()           # the hold plan's Sell?/Trim verdicts are what back a sell
        except Exception:  # noqa: BLE001 - without a plan, only open decisions can back it
            plan = None
    with db.connect() as conn:
        conn.executescript(decisions.SCHEMA)
        open_d = [dict(r) for r in conn.execute("SELECT kind, symbol, title FROM decisions WHERE status IN ('open', 'approved')")]
        entry = cooloff.load(conn).get(symbol)
    warn, block, cooling = cooloff.gate(symbol, plan, open_d, entry, datetime.now(timezone.utc))
    out["warnings"] = out["warnings"] + warn
    if block:
        out["blockers"] = out["blockers"] + block
        out["token"], out["expires_in"] = None, None
    out["cooling"] = cooling
    return out


@app.get("/api/cooloff/{symbol}")
def cooloff_view(symbol: str):
    """For a sell of this symbol: backed by a rule, waiting out the 48 hours, or needing a reason."""
    plan = holdplan._cache.get("plan", (None, 0, None))[2]
    return _cooloff_gate({"warnings": [], "blockers": []}, market.normalize_symbol(symbol), plan)


class CoolIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=10, max_length=300)
    override: bool = False


@app.post("/api/cooloff")
def cooloff_start(body: CoolIn):
    """Write down why you want to sell, and start the 48 hours (or, with override, skip them: logged)."""
    from datetime import datetime, timezone

    from . import cooloff
    with db.connect() as conn:
        return cooloff.start(conn, market.normalize_symbol(body.symbol), body.reason, datetime.now(timezone.utc), body.override)


@app.post("/api/trade/place")
async def trade_place(body: TradeIn):
    if not body.token:
        raise HTTPException(400, "Preview the order first")

    def run():
        with db.connect() as conn:
            return trading.place(body.order(), body.token, conn)
    try:
        out = await asyncio.to_thread(run)
    except trading.TradeError as exc:
        raise HTTPException(400, str(exc))
    kind = {"coinbase": "coinbase", "robinhood": "robinhood_crypto"}.get(body.venue)
    if not kind:
        return out

    async def later():
        await asyncio.sleep(8)
        try:
            await asyncio.to_thread(accounts.run_sync, kind)
            holdplan.clear_cache()
        except accounts.SyncError:
            pass
    asyncio.create_task(later())
    return out


class TradeSettingsIn(BaseModel):
    enabled: bool
    max_order: float = Field(250.0, gt=0, le=100_000)
    daily_limit: float = Field(500.0, gt=0, le=1_000_000)


@app.post("/api/trade/settings")
def trade_settings(body: TradeSettingsIn):
    with db.connect() as conn:
        db.set_meta(conn, "trading_enabled", "1" if body.enabled else "")
        db.set_meta(conn, "trading_max_order", str(body.max_order))
        db.set_meta(conn, "trading_daily_limit", str(body.daily_limit))
        return trading.settings(conn)


@app.get("/api/trade/log")
def trade_log_view():
    with db.connect() as conn:
        return {"orders": trading.recent(conn), "settings": trading.settings(conn), "spent_today": trading.spent_today(conn)}


# ------------------------------------------------------------------ auto-invest schedules, buy-the-dip, goals

class ScheduleIn(BaseModel):
    account: str = Field("Stash", min_length=1, max_length=40)
    symbol: str = Field(..., min_length=1, max_length=20)
    amount: float = Field(..., gt=0, le=1_000_000)
    every: Literal["week", "2weeks", "month"] = "week"
    day: int = Field(0, ge=0, le=28)
    start: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


@app.get("/api/schedules")
def schedules_view():
    from . import schedules
    with db.connect() as conn:
        return [s.__dict__ for s in schedules.load(conn)]


@app.post("/api/schedules")
def schedule_add(body: ScheduleIn):
    from . import schedules
    if body.every == "month" and body.day < 1:
        raise HTTPException(422, "For a monthly schedule, give the day of the month (1-28)")
    if body.every != "month" and body.day > 6:
        raise HTTPException(422, "For a weekly schedule, give the weekday (0 = Monday … 6 = Sunday)")
    with db.connect() as conn:
        schedules.add(conn, body.account.strip(), market.normalize_symbol(body.symbol), body.amount, body.every, body.day, body.start)
        added = schedules.apply(conn, date.today(), schedules.close_on)
        out = [s.__dict__ for s in schedules.load(conn)]
    holdplan.clear_cache()
    return {"schedules": out, "recorded": added}


@app.delete("/api/schedules/{sid}")
def schedule_delete(sid: int):
    from . import schedules
    with db.connect() as conn:
        schedules.remove(conn, sid)
        return [s.__dict__ for s in schedules.load(conn)]


class TargetIn(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    price: float = Field(..., gt=0, le=1e9)
    note: str = Field("", max_length=200)


@app.get("/api/buy-targets")
def buy_targets_view():
    from . import price_alerts
    with db.connect() as conn:
        rows = price_alerts.targets(conn)
    for r in rows:
        t = livefeed_price(r["symbol"])
        r["now"] = t
        r["gap_pct"] = round((t / r["price"] - 1) * 100, 1) if t else None
    return rows


def livefeed_price(sym: str) -> float | None:
    try:
        return market.get_live_quote(sym).price
    except (http.DataUnavailable, KeyError, ValueError):
        return None


@app.post("/api/buy-targets")
def buy_target_set(body: TargetIn):
    from . import price_alerts
    with db.connect() as conn:
        price_alerts.set_target(conn, market.normalize_symbol(body.symbol), body.price, body.note.strip())
    holdplan.clear_cache()
    return buy_targets_view()


@app.delete("/api/buy-targets/{symbol}")
def buy_target_delete(symbol: str):
    from . import price_alerts
    with db.connect() as conn:
        price_alerts.remove_target(conn, market.normalize_symbol(symbol))
    holdplan.clear_cache()
    return buy_targets_view()


class GoalIn(BaseModel):
    target: float = Field(..., gt=0, le=1e10)
    year: int = Field(..., ge=2000, le=2100)
    monthly: float = Field(0, ge=0, le=1e7)
    mean: float = Field(0.07, ge=-0.2, le=0.3)
    vol: float = Field(0.15, ge=0, le=1)


@app.get("/api/goal")
async def goal_view():
    from . import goals
    with db.connect() as conn:
        raw = db.get_meta(conn, "goal", "")
    if not raw:
        return {"goal": None}
    g = json.loads(raw)
    plan = await _holdplan_data()
    years = max(0, g["year"] - date.today().year)
    return {"goal": g, "now_value": plan["base"],
            "projection": goals.project(plan["base"], g["monthly"], years, g["target"], g.get("mean", 0.07), g.get("vol", 0.15))}


@app.post("/api/goal")
async def goal_set(body: GoalIn):
    with db.connect() as conn:
        db.set_meta(conn, "goal", json.dumps(body.model_dump()))
    return await goal_view()


# ------------------------------------------------------------------ vs the market, look-through, fees, statement check

_analysis_cache = sentinel.Cache(1800)


def _valued_positions() -> tuple[list[dict], list[dict]]:
    """(valued positions, your trades): positions include moves between accounts; the trades don't."""
    with db.connect() as conn:
        led = db.ledger(conn)
    txs = [t for t in led if t.get("transfer") is None]
    return (service.portfolio_summary(led, False)["positions"] if txs else []), txs


@app.get("/api/benchmark")
async def benchmark_view(symbol: str = Query("VOO", max_length=10)):
    """Your gain against the same money put into one index fund on the same days."""
    from . import benchmark

    def run():
        positions, txs = _valued_positions()
        prices = {p["symbol"]: p["price"] for p in positions if p.get("price")}
        return benchmark.build(txs, prices, market.normalize_symbol(symbol))
    try:
        key = ("bench", symbol, _ledger_key(), date.today().isoformat())
        return await asyncio.to_thread(_analysis_cache.get, key, run)
    except (http.DataUnavailable, ValueError) as exc:
        raise HTTPException(502, str(exc))


def _ledger_key() -> tuple:
    """Changes whenever a trade is added or removed (so cached analyses refresh)."""
    with db.connect() as conn:
        r = conn.execute("SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS m FROM transactions").fetchone()
        g = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(pair IS NOT NULL), 0) AS p FROM transfer_legs").fetchone()
        return (r["n"], r["m"], g["n"], g["p"])


@app.get("/api/lookthrough")
async def lookthrough_view():
    """Your money in each company, directly and through the funds you own."""
    from . import lookthrough

    def run():
        positions, _ = _valued_positions()
        funds = {}
        errors = []
        for p in positions:
            if market.asset_class(p["symbol"]) != "stock":
                continue
            try:
                fd = lookthrough.holdings(p["symbol"])
            except (http.DataUnavailable, ValueError, KeyError) as exc:
                errors.append(f"{p['symbol']}: {str(exc)[:80]}")
                continue
            if fd:
                funds[p["symbol"]] = fd
        out = lookthrough.exposure(positions, funds)
        out["errors"] = errors
        return out
    key = ("look", _ledger_key(), date.today().isoformat())
    return await asyncio.to_thread(_analysis_cache.get, key, run)


@app.get("/api/fees")
async def fees_view():
    from . import fees

    def run():
        positions, _ = _valued_positions()
        return fees.build(positions)
    key = ("fees", _ledger_key(), date.today().isoformat())
    return await asyncio.to_thread(_analysis_cache.get, key, run)


class StatementIn(BaseModel):
    account: str = Field(..., min_length=1, max_length=40)
    pdf_base64: str = Field(..., min_length=10, max_length=20_000_000)


@app.post("/api/statement-check")
def statement_check(body: StatementIn):
    """Share counts on a statement PDF against the ledger for that account."""
    import base64
    from . import statement
    try:
        text = statement.text_of_pdf(base64.b64decode(body.pdf_base64))
    except Exception as exc:  # noqa: BLE001 - any unreadable PDF is the same answer
        raise HTTPException(400, f"Couldn't read that PDF: {str(exc)[:120]}")
    with db.connect() as conn:
        txs = db.ledger(conn)
    ledger: dict[str, float] = {}
    for t in txs:
        if (t.get("account") or "") == body.account:
            ledger[t["symbol"]] = ledger.get(t["symbol"], 0.0) + (t["quantity"] if t["side"] == "buy" else -t["quantity"])
    known = {s.replace("-USD", "") for s in ledger}
    found = statement.quantities(text, known)
    found = {(s + "-USD" if s + "-USD" in ledger else s): q for s, q in found.items()}
    out = statement.compare(found, {s: q for s, q in ledger.items() if q > 1e-9})
    out["read"] = len(found)
    if found:
        from . import confidence
        with db.connect() as conn:
            confidence.remember_statement(conn, body.account, out)
    return out


@app.post("/api/sync/snaptrade/connect")
async def snaptrade_connect():
    """A link to SnapTrade's page for connecting a broker (read-only access)."""
    try:
        return {"url": await asyncio.to_thread(snaptrade.connect_url)}
    except snaptrade.SnapTradeError as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/sync/snaptrade")
async def snaptrade_sync_now():
    """Import new trades, reinvested dividends, dividends and interest from every connected
    account, then compare positions with the ledger."""
    try:
        out = await asyncio.to_thread(accounts.run_sync, "snaptrade")
    except accounts.SyncError as exc:
        raise HTTPException(exc.status, str(exc))
    holdplan.clear_cache()
    return out


# ------------------------------------------------------------------ transfers between your accounts

@app.get("/api/transfers")
def transfers_view():
    """Moves between your accounts (paired), legs still waiting for a decision, and moves the
    latest balance checks suggest (an account short of a coin next to one with extra)."""
    with db.connect() as conn:
        legs = transfers.from_rows(db.transfer_legs(conn))
    return {"moves": transfers.moves(legs), **transfers.open_items(legs),
            "suggested": transfers.suggest_from_differences(accounts.balance_differences(), legs, date.today().isoformat()),
            "accounts": sorted({lg.account for lg in legs} | {"Coinbase", "Robinhood", "Stash"})}


class MoveIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    from_account: str = Field(min_length=1, max_length=60)
    to_account: str = Field(min_length=1, max_length=60)
    sent: float = Field(gt=0, le=1e12)
    received: float | None = Field(default=None, gt=0, le=1e12)
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    arrived: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@app.post("/api/transfers", status_code=201)
def add_move(body: MoveIn):
    """Record a move you made (both sides at once)."""
    if body.from_account.strip() == body.to_account.strip():
        raise HTTPException(400, "The two accounts are the same.")
    received = body.received or body.sent
    if received > body.sent * (1 + 1e-9):
        raise HTTPException(400, "More arrived than was sent.")
    sym = market.normalize_symbol(body.symbol)
    with db.connect() as conn:
        had = accounts.account_quantities([t for t in db.ledger(conn) if t["date"][:10] <= body.day], body.from_account.strip())
        if had.get(sym, 0.0) < body.sent * (1 - 1e-9):
            raise HTTPException(400, f"Your ledger shows {had.get(sym, 0.0):g} {sym} in {body.from_account.strip()} on "
                                     f"{body.day}; this sends {body.sent:g}.")
        o = db.add_leg(conn, sym, "out", body.sent, body.day, body.from_account.strip(), note="Recorded by hand")
        i = db.add_leg(conn, sym, "in", received, body.arrived or body.day, body.to_account.strip(), note="Recorded by hand")
        db.pair_legs(conn, o, i)
        try:
            service.pf.build_positions(db.ledger(conn))
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(400, str(exc))
    holdplan.clear_cache()
    return {"out": o, "in": i}


class ResolveIn(BaseModel):
    how: Literal["pair", "bought", "wallet", "sold", "ignore"]
    other: int | None = None                    # pair: the other leg's id
    cost: float | None = Field(default=None, ge=0, le=1e12)          # bought: price per unit originally paid
    acquired: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")   # bought: when
    account: str | None = Field(default=None, max_length=60)        # wallet: the wallet's name
    price: float | None = Field(default=None, ge=0, le=1e12)         # sold: price per unit


@app.get("/api/needs-cost")
def needs_cost_view():
    """Shares that arrived without a cost (transfers in from another broker, stock rewards)."""
    with db.connect() as conn:
        return {"rows": db.needs_cost(conn)}


class CostIn(BaseModel):
    price: float = Field(ge=0, le=1e9)
    acquired: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


@app.post("/api/needs-cost/{tid}")
def needs_cost_set(tid: int, body: CostIn):
    """What you originally paid per share, and when, for shares that arrived without a cost."""
    with db.connect() as conn:
        if not db.set_cost(conn, tid, body.price, body.acquired):
            raise HTTPException(404, "No such row waiting for a cost.")
        try:
            service.pf.build_positions(db.ledger(conn))
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(400, f"{exc}: that purchase date is after a sale of these shares.")
        rows = db.needs_cost(conn)
    holdplan.clear_cache()
    _analysis_cache.clear()
    return {"rows": rows}


@app.post("/api/transfers/{leg_id}/resolve")
def resolve_transfer(leg_id: int, body: ResolveIn):
    """Decide what an unpaired leg was: the other side of a move, coins bought elsewhere (their
    original cost and date), a move to a wallet of yours, or spent / sold."""
    with db.connect() as conn:
        rows = {r["id"]: r for r in db.transfer_legs(conn)}
        leg = rows.get(leg_id)
        if not leg:
            raise HTTPException(404, "No such transfer.")
        if leg["pair"] is not None or leg["resolved"]:
            raise HTTPException(400, "Already decided; delete it first to change it.")
        if body.how == "pair":
            other = rows.get(body.other or -1)
            if not other or other["direction"] == leg["direction"] or other["symbol"] != leg["symbol"] or other["pair"] is not None:
                raise HTTPException(400, "Pick an unpaired leg of the same coin going the other way.")
            o, i = (leg, other) if leg["direction"] == "out" else (other, leg)
            if i["quantity"] > o["quantity"] * (1 + 1e-9):
                raise HTTPException(400, "More arrived than was sent.")
            db.pair_legs(conn, o["id"], i["id"])
        elif body.how == "bought":
            if leg["direction"] != "in" or body.cost is None or not body.acquired:
                raise HTTPException(400, "Coins that arrived need what you paid per unit and the date you bought them.")
            db.add_transaction(conn, leg["symbol"], "buy", leg["quantity"], body.cost, body.acquired, 0.0,
                               f"Bought elsewhere; arrived in {leg['account']} {leg['day']}", import_key=f"tr:{leg_id}",
                               account=leg["account"])
            db.resolve_leg(conn, leg_id, "bought")
        elif body.how == "wallet":
            name = (body.account or "").strip()
            if leg["direction"] != "out" or not name:
                raise HTTPException(400, "Name the wallet the coins went to.")
            i = db.add_leg(conn, leg["symbol"], "in", leg["quantity"], leg["day"], name, note="Own wallet")
            db.pair_legs(conn, leg_id, i)
        elif body.how == "sold":
            if leg["direction"] != "out" or body.price is None:
                raise HTTPException(400, "Enter the price per unit they were spent or sold at.")
            db.add_transaction(conn, leg["symbol"], "sell", leg["quantity"], body.price, leg["day"], 0.0,
                               f"Spent / sold after leaving {leg['account']}", import_key=f"tr:{leg_id}", account=leg["account"])
            db.resolve_leg(conn, leg_id, "sold")
        else:
            db.resolve_leg(conn, leg_id, "ignore")
        try:
            service.pf.build_positions(db.ledger(conn))
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(400, str(exc))
    holdplan.clear_cache()
    return {"ok": True}


@app.delete("/api/transfers/{leg_id}")
def delete_transfer(leg_id: int):
    with db.connect() as conn:
        row = conn.execute("SELECT import_key, resolved FROM transfer_legs WHERE id = ?", (leg_id,)).fetchone()
        if not row:
            raise HTTPException(404, "No such transfer.")
        conn.execute("DELETE FROM transactions WHERE import_key = ?", (f"tr:{leg_id}",))
        db.delete_leg(conn, leg_id)
    holdplan.clear_cache()
    return {"ok": True}


# ------------------------------------------------------------------ taxes: lot methods and the export

def _lot_methods(conn) -> dict[str, str]:
    return json.loads(db.get_meta(conn, "lot_methods", "{}") or "{}")


@app.get("/api/lots/compare")
async def lots_compare(symbol: str, quantity: float = Query(gt=0, le=1e12), account: str = "",
                       price: float | None = Query(default=None, gt=0, le=1e12)):
    """A sale now under each cost-basis method: the lots it takes and the tax."""
    sym = market.normalize_symbol(symbol)
    with db.connect() as conn:
        led = db.ledger(conn)
        cfg = holdplan.settings(conn)
    if price is None:
        try:
            price = (await asyncio.to_thread(market.get_live_quote, sym)).price
        except http.DataUnavailable as exc:
            raise HTTPException(502, f"No price for {sym}: {exc}. Enter one.")
    lots, _ = taxes.lots_and_sales(led)
    return taxes.compare_methods(lots, sym, quantity, price, date.today(), account, cfg["st_rate"], cfg["lt_rate"])


@app.get("/api/lots/methods")
def get_lot_methods():
    with db.connect() as conn:
        accts = sorted({t.get("account") or "" for t in db.list_transactions(conn)} - {""})
        return {"methods": _lot_methods(conn), "accounts": accts, "choices": taxes.LOT_METHODS}


class LotMethodIn(BaseModel):
    account: str = Field(min_length=1, max_length=60)
    method: Literal["fifo", "hifo", "lifo", "min_tax"]


@app.post("/api/lots/methods")
def set_lot_method(body: LotMethodIn):
    with db.connect() as conn:
        m = _lot_methods(conn)
        if body.method == "fifo":
            m.pop(body.account, None)
        else:
            m[body.account] = body.method
        db.set_meta(conn, "lot_methods", json.dumps(m))
    holdplan.clear_cache()
    return {"methods": m}


@app.get("/api/taxes/export")
def tax_export_summary(year: int = Query(ge=2000, le=2100)):
    """Totals for the year's export (the CSVs have the rows)."""
    from . import taxexport
    with db.connect() as conn:
        led = db.ledger(conn)
        inc = db.income(conn)
    years = sorted({t["date"][:4] for t in led if t["side"] == "sell"} | {str(date.today().year)}, reverse=True)
    sales = taxexport.sales_rows(led, year)
    return {"year": year, "years": years, **taxexport.summary(sales, taxexport.income_rows(inc, led, year))}


@app.get("/api/taxes/export/{kind}.csv")
def tax_export_csv(kind: Literal["sales", "income"], year: int = Query(ge=2000, le=2100)):
    from . import taxexport
    with db.connect() as conn:
        led = db.ledger(conn)
        inc = db.income(conn)
    if kind == "sales":
        text = taxexport.to_csv(taxexport.sales_rows(led, year), taxexport.SALE_FIELDS)
    else:
        text = taxexport.to_csv(taxexport.income_rows(inc, led, year), taxexport.INCOME_FIELDS)
    return Response(text, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="plumbline-{kind}-{year}.csv"'})


# ------------------------------------------------------------------ cash waiting in your accounts

@app.get("/api/cash/accounts")
async def cash_accounts():
    from . import cash
    with db.connect() as conn:
        accts = cash.load(conn)
        names = sorted(({t.get("account") or "" for t in db.list_transactions(conn)} | set(accts)) - {""})
    y, live = await asyncio.to_thread(cash.tbill_yield)
    return dict(cash.view(accts, date.today(), y, live), names=names or ["Robinhood", "Stash", "Coinbase"])


class CashAcctIn(BaseModel):
    account: str = Field(min_length=1, max_length=60)
    amount: float = Field(ge=0, le=1e10)
    apy: float | None = Field(default=None, ge=0, le=20)


@app.post("/api/cash/accounts")
def set_cash_account(body: CashAcctIn):
    from . import cash
    with db.connect() as conn:
        cash.save(conn, body.account.strip(), body.amount, body.apy, date.today())
    holdplan.clear_cache()
    return {"ok": True}


# ------------------------------------------------------------------ off-site backup

@app.get("/api/offsite")
def offsite_view():
    from . import offsite
    c = offsite.config()
    with db.connect() as conn:
        last = json.loads(db.get_meta(conn, "offsite_last", "null") or "null")
    return {"configured": offsite.configured(), "has_key": bool(c["key"]), "dir": c["dir"], "repo": c["repo"],
            "has_token": bool(c["token"]), "last": last, "suggest": offsite.suggested_dir()}


class OffsiteIn(BaseModel):
    passphrase: str | None = Field(default=None, max_length=500)
    dir: str | None = Field(default=None, max_length=500)
    repo: str | None = Field(default=None, max_length=200)
    token: str | None = Field(default=None, max_length=500)
    create_dir: bool = False             # make the suggested synced folder (only that one) if it isn't there yet


def _offsite_run() -> dict:
    from . import offsite
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc)
    try:
        out = offsite.run(config.settings.db_path, now)
        rec = {"at": out["at"], "ok": True, "to": out["to"], "errors": out["errors"]}
    except offsite.BackupError as exc:
        rec = {"at": now.isoformat(timespec="seconds"), "ok": False, "error": str(exc)}
    with db.connect() as conn:
        db.set_meta(conn, "offsite_last", json.dumps(rec))
    return rec


@app.post("/api/offsite")
async def offsite_setup(body: OffsiteIn):
    """Save the passphrase (as a derived key) and destinations, then back up once."""
    from . import offsite
    vals: dict[str, str] = {}
    try:
        if body.passphrase:
            vals.update(await asyncio.to_thread(offsite.new_key, body.passphrase))
        if body.dir is not None:
            d = body.dir.strip()
            sug = offsite.suggested_dir()
            if d and body.create_dir and sug and d == sug["path"] and not Path(d).is_dir():
                Path(d).mkdir(parents=False, exist_ok=True)
            if d and not Path(d).expanduser().is_dir():
                raise offsite.BackupError(f"The folder {d} doesn't exist on this computer.")
            vals["OFFSITE_DIR"] = d
        if body.repo is not None:
            vals["OFFSITE_REPO"] = body.repo.strip()
        if body.token:
            vals["OFFSITE_GITHUB_TOKEN"] = body.token.strip()
        repo = vals.get("OFFSITE_REPO", offsite.config()["repo"])
        token = vals.get("OFFSITE_GITHUB_TOKEN", offsite.config()["token"])
        if repo and token:
            await asyncio.to_thread(offsite.check_private, repo, token)
    except offsite.BackupError as exc:
        raise HTTPException(400, str(exc))
    if vals:
        _save_env(vals)
    if not offsite.configured():
        return {"saved": True, "backup": None}
    return {"saved": True, "backup": await asyncio.to_thread(_offsite_run)}


class OffsiteRestoreIn(BaseModel):
    file_base64: str = Field(min_length=10, max_length=200_000_000)
    passphrase: str = Field(min_length=1, max_length=500)


@app.post("/api/offsite/restore")
async def offsite_restore(body: OffsiteRestoreIn):
    """Replace this app's data with an encrypted backup's (the current data is kept beside it)."""
    import base64
    from . import offsite
    try:
        kept = await asyncio.to_thread(offsite.restore, base64.b64decode(body.file_base64), body.passphrase,
                                       config.settings.db_path)
    except (offsite.BackupError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    holdplan.clear_cache()
    return {"restored": True, "previous_kept_as": kept}


_update_lock = threading.Lock()


@app.get("/api/update")
def update_view(refresh: bool = False):
    """Whether a new version of the app is out (checked at most every 6 hours unless refresh)."""
    from . import updater
    return updater.cached_status(refresh)


@app.get("/api/update/version")
def update_version():
    """What's running now (the page waits for the new version after an update)."""
    from . import updater
    return {"version": updater.RUNNING if updater.RUNNING is not None else updater.version()}


@app.post("/api/update")
def update_apply():
    """Install the new version and restart: the page reloads itself when the new version answers."""
    from . import updater
    if not _update_lock.acquire(blocking=False):
        raise HTTPException(409, "An update is already running.")
    try:
        out = updater.apply()
    finally:
        _update_lock.release()
    if not out.get("updated"):
        if out.get("error"):
            raise HTTPException(400, out["error"])
        return out
    out["restarting"] = updater.restart()
    if out["restarting"] == "manual":
        out["text"] += " Restart Plumbline to use it."
    return out


@app.get("/api/doctor")
def doctor_view():
    """This install's health: the same checks as `mt doctor`, with the end of the log."""
    from . import doctor
    return doctor.run(in_app=True)


@app.get("/api/health")
def health_view():
    from . import health
    return {"checks": health.status()}


@app.get("/api/live/status")
def live_status():
    return livefeed.hub.status()


class FinnhubIn(BaseModel):
    key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


@app.post("/api/live/finnhub")
async def live_finnhub(body: FinnhubIn):
    """Check a free Finnhub key with one quote, save it, and switch stocks to its live trade stream."""
    import httpx

    def check() -> int:
        r = httpx.get("https://finnhub.io/api/v1/quote", params={"symbol": "AAPL", "token": body.key}, timeout=15)
        return r.status_code if r.status_code != 200 else (200 if (r.json() or {}).get("c") else 204)
    try:
        code = await asyncio.to_thread(check)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Couldn't reach Finnhub: {exc}")
    if code == 401 or code == 403:
        raise HTTPException(400, "Finnhub didn't accept that key.")
    if code not in (200, 204):
        raise HTTPException(502, f"Finnhub answered {code}; try again in a minute.")
    _save_env({"FINNHUB_API_KEY": body.key})
    await livefeed.hub.restart()
    return livefeed.hub.status()


# ------------------------------------------------------------------ brokers for the order engine

@app.get("/api/brokers")
async def brokers_view():
    from . import brokers
    with db.connect() as conn:
        rows = brokers.overview(conn)
        paper = brokers.paper_state(conn)
    alpaca = None
    if brokers.alpaca_configured():
        try:
            alpaca = await asyncio.to_thread(brokers.alpaca_account)
        except brokers.BrokerError as exc:
            alpaca = {"error": str(exc)}
    return {"brokers": rows, "paper": paper, "alpaca": alpaca}


class AlpacaIn(BaseModel):
    key_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9]+$")
    secret: str = Field(min_length=8, max_length=200)
    live: bool = False


@app.post("/api/brokers/alpaca")
async def brokers_alpaca(body: AlpacaIn):
    """Check the key against the account, then save it."""
    import os
    from . import brokers
    old = {k: os.environ.get(k) for k in ("ALPACA_KEY_ID", "ALPACA_SECRET_KEY", "ALPACA_LIVE")}
    os.environ.update({"ALPACA_KEY_ID": body.key_id, "ALPACA_SECRET_KEY": body.secret.strip(), "ALPACA_LIVE": "1" if body.live else ""})
    try:
        acct = await asyncio.to_thread(brokers.alpaca_account)
    except brokers.BrokerError as exc:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        raise HTTPException(400, f"{exc}. {'Live' if body.live else 'Paper'} keys only work in {'live' if body.live else 'paper'} mode.")
    _save_env({"ALPACA_KEY_ID": body.key_id, "ALPACA_SECRET_KEY": body.secret.strip(), "ALPACA_LIVE": "1" if body.live else ""})
    return {"ok": True, "account": acct}


class PublicIn(BaseModel):
    secret: str = Field(min_length=8, max_length=500)


@app.post("/api/brokers/public")
async def brokers_public(body: PublicIn):
    import os
    from . import brokers
    old = os.environ.get("PUBLIC_API_SECRET")
    os.environ["PUBLIC_API_SECRET"] = body.secret.strip()
    brokers._public_token.clear()
    os.environ.pop("PUBLIC_ACCOUNT_ID", None)
    try:
        acct = await asyncio.to_thread(brokers.public_account_id)
    except brokers.BrokerError as exc:
        if old is None:
            os.environ.pop("PUBLIC_API_SECRET", None)
        else:
            os.environ["PUBLIC_API_SECRET"] = old
        raise HTTPException(400, str(exc))
    _save_env({"PUBLIC_API_SECRET": body.secret.strip(), "PUBLIC_ACCOUNT_ID": acct})
    return {"ok": True}


class PaperResetIn(BaseModel):
    start: float = Field(default=10_000, ge=100, le=10_000_000)


@app.post("/api/brokers/paper/reset")
def brokers_paper_reset(body: PaperResetIn):
    from . import brokers
    with db.connect() as conn:
        brokers.paper_reset(conn, body.start)
        return brokers.paper_state(conn)


@app.get("/api/advice/record")
async def advice_record():
    """The app's advice, scored against just buying VOO."""
    from . import advice

    def run():
        with db.connect() as conn:
            rows = advice.logged(conn)
        return advice.score(rows, lambda s: [(b.date, b.close) for b in market.get_history(s, 400)], date.today())
    key = ("advice", _ledger_key(), date.today().isoformat())
    out = await asyncio.to_thread(_analysis_cache.get, key, run)
    return dict(out, items=out["items"][:200])


@app.get("/api/newsdesk")
async def newsdesk_view(refresh: bool = False):
    """Your holdings' news, clustered into stories, with sources counted and weighed, the event
    type, and what (if anything) it changes in the plan."""
    held = sorted(sentinel.my_symbols()[0])
    if not held:
        return {"desk": {}, "feeds": {}, "at": None}
    key = tuple(held)
    if refresh:
        sentinel.newsdesk_cache.clear()
    try:
        return await asyncio.to_thread(sentinel.newsdesk_cache.get, key, lambda: sentinel.build_newsdesk(held))
    except (http.DataUnavailable, ValueError) as exc:
        raise HTTPException(502, str(exc))


# ------------------------------------------------------------------ setup and how much is checked

@app.get("/api/confidence")
def confidence_view():
    """How much of the portfolio (by value) was checked against a broker or a statement lately,
    and the list of things to fix."""
    from . import confidence
    return confidence.current()


@app.get("/api/setup")
def setup_view():
    from . import setup
    with db.connect() as conn:
        steps = setup.steps(conn)
    import os
    return {"steps": steps, "done": sum(s["done"] for s in steps), "total": len(steps),
            "ntfy_topic": os.environ.get("NTFY_TOPIC", ""), "suggested_topic": setup.suggest_topic()}


class AlertTopicIn(BaseModel):
    topic: str = Field(min_length=12, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@app.post("/api/setup/alerts")
async def setup_alerts(body: AlertTopicIn):
    """Save the ntfy topic and send a test push to it."""
    _save_env({"NTFY_TOPIC": body.topic})
    ok = await asyncio.to_thread(notify.send, notify.Message(title="Plumbline test", body="Alerts reach this phone.", url="", priority=3, tags=("white_check_mark",)))
    with db.connect() as conn:
        db.set_meta(conn, "ntfy_tested", "1" if ok else "")
    if not ok:
        raise HTTPException(502, "Saved, but the test push didn't go through. Check the topic and try again.")
    return {"ok": True}


@app.post("/api/setup/test/{key}")
async def setup_test(key: str):
    """Run one step's test now."""
    if key in ("coinbase", "robinhood_crypto", "email", "snaptrade"):
        try:
            r = await asyncio.to_thread(accounts.run_sync, key)
        except accounts.SyncError as exc:
            raise HTTPException(exc.status, str(exc))
        holdplan.clear_cache()
        return {"ok": True, "text": f"Worked: {r.get('new', 0)} new trades" + (f", {len(r['differences'])} balance differences to check" if r.get("differences") else ", balances match")}
    if key == "backup":
        rec = await asyncio.to_thread(_offsite_run)
        if not rec["ok"]:
            raise HTTPException(400, rec["error"])
        return {"ok": True, "text": "Backed up to " + ", ".join(rec["to"])}
    if key == "alerts":
        ok = await asyncio.to_thread(notify.send, notify.Message(title="Plumbline test", body="Alerts reach this phone.", url="", priority=3, tags=("white_check_mark",)))
        with db.connect() as conn:
            db.set_meta(conn, "ntfy_tested", "1" if ok else "")
        if not ok:
            raise HTTPException(502, "The test push didn't go through.")
        return {"ok": True, "text": "Test push sent: check your phone"}
    if key == "finnhub":
        st = livefeed.hub.status()
        return {"ok": st["mode"] == "finnhub", "text": f"Stocks: {st['mode']}, last trade {st['last_stock_tick'] or 'none yet'}"}
    raise HTTPException(404, "No test for that step.")


# ------------------------------------------------------------------ review: timing, contribution, crises, overlap

def _closes(sym: str, days: int) -> list[tuple[str, float]]:
    return [(b.date, b.close) for b in market.get_history(sym, days)]


@app.get("/api/performance")
async def performance_view(period: Literal["ytd", "all"] = "all"):
    """Your timing (money- against time-weighted return), what never selling would be worth,
    and each holding's contribution in dollars."""
    from . import performance
    today = date.today()

    def run():
        with db.connect() as conn:
            txs = db.list_transactions(conn)
            inc = db.income(conn)
        r = performance.analyze(txs, _closes, today, date(today.year, 1, 1) if period == "ytd" else None, inc)
        return dict(r, verdict=performance.verdict(r), period=period)
    key = ("performance", period, _ledger_key(), today.isoformat())
    try:
        return await asyncio.to_thread(_analysis_cache.get, key, run)
    except http.DataUnavailable as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/crises")
async def crises_view():
    """Today's holdings through the 2008, 2020 and 2022 falls (stand-ins where a holding is younger)."""
    from . import stress
    days = (date.today() - date(2007, 9, 1)).days

    def run():
        positions, _ = _valued_positions()
        return {"crises": stress.replay(positions, lambda s: _closes(s, days)),
                "total": round(sum(p.get("market_value") or 0 for p in positions), 2)}
    key = ("crises", _ledger_key(), date.today().isoformat())
    try:
        return await asyncio.to_thread(_analysis_cache.get, key, run)
    except http.DataUnavailable as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/overlap")
async def overlap_view():
    """Correlations of daily moves between your holdings over the last year."""
    from . import stress

    def run():
        positions, _ = _valued_positions()
        syms = [p["symbol"] for p in sorted(positions, key=lambda p: -(p.get("market_value") or 0))][:15]
        return stress.correlations(syms, lambda s: _closes(s, 400))
    key = ("overlap", _ledger_key(), date.today().isoformat())
    try:
        return await asyncio.to_thread(_analysis_cache.get, key, run)
    except http.DataUnavailable as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/moved")
async def moved_view():
    """Today's change in dollars by holding, with the news desk's likely reason for each."""
    try:
        return await asyncio.to_thread(sentinel.moved_today)
    except (http.DataUnavailable, ValueError) as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/tenk")
async def tenk_view():
    """Each held company's latest 10-K against last year's: how much of Risk Factors and Legal
    Proceedings is new, and the new sentences."""
    from . import filings

    def run():
        positions, _ = _valued_positions()
        syms = [p["symbol"] for p in sorted(positions, key=lambda p: -(p.get("market_value") or 0)) if market.asset_class(p["symbol"]) == "stock"][:15]
        out, errors = [], []

        def one(sym):
            try:
                return filings.tenk_changes(sym), None
            except (http.DataUnavailable, KeyError, ValueError) as exc:
                return None, f"{sym}: {exc}"
        with ThreadPoolExecutor(max_workers=3) as pool:
            for r, err in pool.map(one, syms):
                if err:
                    errors.append(err)
                elif r:
                    out.append(r)
        from . import tenkrank
        ranking = tenkrank.load()
        for r in out:
            r["rank"] = tenkrank.percentile(ranking, ((r.get("sections") or {}).get("risk") or {}).get("new_share"))
        order = {"big": 0, "some": 1, "little": 2}
        out.sort(key=lambda r: (order.get(r.get("level"), 3), -(((r.get("sections") or {}).get("risk") or {}).get("new_share") or 0)))
        return {"companies": out, "errors": errors, "skipped": [s for s in syms if s not in {r["symbol"] for r in out}],
                "ranking_as_of": (ranking or {}).get("as_of")}
    key = ("tenk", _ledger_key(), date.today().isoformat())
    return await asyncio.to_thread(_analysis_cache.get, key, run)


@app.get("/api/tenk/most-changed")
async def tenk_most_changed(n: int = Query(25, ge=1, le=100)):
    """S&P 500 companies whose latest 10-K Risk Factors changed most (from the weekly ranking job)."""
    from . import tenkrank
    data = await asyncio.to_thread(tenkrank.load)
    if not data:
        raise HTTPException(503, "The S&P 500 ranking hasn't been built yet (the weekly job fills it).")
    return {"as_of": data.get("as_of"), "universe": data.get("universe"), "companies": tenkrank.most_changed(data, n)}


# ------------------------------------------------------------------ what to buy: idea log, screen, sleepers, chatter, money flow, economy

@app.get("/api/ideas")
async def ideas_view():
    """Every logged idea, scored against VOO at 3, 6 and 12 months, and the leaderboard by kind."""
    from . import ideas
    today = date.today()

    def run():
        from . import earnings, screen, thesis
        from .providers import sec
        with db.connect() as conn:
            rows = ideas.logged(conn)
        out = dict(ideas.score(rows, lambda s: _closes(s, 800), today), sources=ideas.SOURCES)
        try:
            positions, _ = _valued_positions()
            held = {p["symbol"] for p in positions if p.get("quantity")}
            data = screen.load()
            out["broken"] = thesis.check(out["items"], held, today, lambda s: screen.lookup(data, s),
                                         trades_fn=lambda s, days: sec.get_insider_trades(s, days), recap_fn=earnings.recap)
            out["bottom_held"] = thesis.bottom_held(held, data)
        except (http.DataUnavailable, ValueError) as exc:
            out["broken"], out["bottom_held"], out["broken_error"] = [], [], str(exc)
        return out
    with db.connect() as conn:
        conn.executescript(ideas.SCHEMA)
        n = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0), SUM(decision != '') FROM ideas").fetchone()
    return await asyncio.to_thread(_analysis_cache.get, ("ideas", tuple(n), _ledger_key(), today.isoformat()), run)


class IdeaIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=3, max_length=300)
    wrong_if: str = Field(default="", max_length=200)


@app.post("/api/ideas", status_code=201)
def add_idea(body: IdeaIn):
    """Log your own idea at today's price, to be scored like the app's."""
    from . import ideas
    sym = market.normalize_symbol(body.symbol)
    try:
        price = market.get_quote(sym).price
    except (http.DataUnavailable, KeyError, ValueError):
        raise HTTPException(502, f"No price for {sym}.")
    with db.connect() as conn:
        n = ideas.log(conn, date.today().isoformat(), [{"symbol": sym, "source": "manual", "price": price, "reason": body.reason,
                                                        "wrong_if": body.wrong_if}])
    if not n:
        raise HTTPException(409, f"{sym} is already logged as your idea this month.")
    return {"logged": sym, "price": price}


class DecisionIn(BaseModel):
    decision: Literal["bought", "passed"]


@app.post("/api/ideas/{idea_id}/decision")
def idea_decision(idea_id: int, body: DecisionIn):
    from . import ideas
    with db.connect() as conn:
        if not ideas.decide(conn, idea_id, body.decision, date.today().isoformat()):
            raise HTTPException(409, "Already decided (a decision can be recorded once).")
    return {"id": idea_id, "decision": body.decision}


@app.get("/api/size/{symbol}")
async def size_view(symbol: str, source: str = Query("", max_length=20)):
    """How many dollars to put into an idea: per-stock, speculative, sector and volatility limits."""
    import json as _json
    from datetime import datetime, timezone

    from . import ideas, screen, sizing
    sym = market.normalize_symbol(symbol)

    def run():
        with db.connect() as conn:
            logged = ideas.logged(conn)
            cash = float(db.get_meta(conn, "cash", "0") or 0)
            cooling = _json.loads(db.get_meta(conn, "cooling", "{}") or "{}")
        src = source or next((r["source"] for r in logged if r["symbol"] == sym), "manual")
        if src not in ideas.SOURCES:
            raise HTTPException(400, f"Unknown kind of idea: {src}")
        positions, _ = _valued_positions()
        data = screen.load()
        spec = {r["symbol"] for r in logged if r["source"] in sizing.SPECULATIVE and r["decision"] == "bought"}
        try:
            price = market.get_quote(sym).price
        except (http.DataUnavailable, KeyError, ValueError):
            raise HTTPException(502, f"No price for {sym}.")
        try:
            vol = sizing.annual_vol([c for _, c in _closes(sym, 300)])
        except (http.DataUnavailable, KeyError, ValueError):
            vol = None
        now = datetime.now(timezone.utc)
        started = None
        if src == "chatter":
            if sym not in cooling:
                cooling[sym] = now.isoformat()
                with db.connect() as conn:
                    db.set_meta(conn, "cooling", _json.dumps(cooling))
            started = datetime.fromisoformat(cooling[sym])
        return sizing.size(sym, src, price, positions, cash, lambda s: (screen.lookup(data, s) or {}).get("sector"), spec, vol, started, now)
    return await asyncio.to_thread(run)


@app.get("/api/screen")
async def screen_view():
    """The weekly quality, value and momentum screen and the backlog screen."""
    from . import screen
    data = await asyncio.to_thread(screen.load)
    if not data:
        raise HTTPException(503, "The weekly screen hasn't run yet (the 'Stock screen' job fills it).")
    return {k: v for k, v in data.items() if k != "lookup"}


@app.get("/api/decisions")
async def decisions_view():
    """What to do this week, ranked, with amounts, reasons and how strong the evidence is."""
    from . import decisions

    def run():
        return decisions.gather()
    items = await asyncio.to_thread(_analysis_cache.get, ("decisions", _ledger_key(), datetime_hour()), run)
    from . import golive
    with db.connect() as conn:
        visible = decisions.sync(conn, items, date.today())
        past = decisions.history(conn, 20)
        settle = golive.settling(golive.live_since(conn, bool(db.ledger(conn)), date.today()), date.today())
    return {"as_of": datetime_hour(), "decisions": visible, "history": past, "evidence": decisions.EVIDENCE, "settling": settle}


@app.post("/act/{token}")
def act_from_push(token: str):
    """A push button (Later, Skip) records its decision here; the link is signed and expires in a week."""
    from . import decisions
    text = auth.read_action(token)
    if not text or not text.startswith("decide|"):
        raise HTTPException(403, "This link is invalid or has expired: open Plumbline instead.")
    _, status, key = text.split("|", 2)
    with db.connect() as conn:
        try:
            return decisions.decide(conn, key, status, date.today())
        except KeyError:
            raise HTTPException(404, "That decision no longer exists.")


@app.get("/api/decisions/scorecard")
async def decisions_scorecard():
    """Did following Plumbline's calls beat doing nothing? Approved and skipped calls, scored."""
    from . import cash, decisions

    def run():
        from . import golive
        y, _ = cash.tbill_yield()
        with db.connect() as conn:
            since = golive.record_start(db.get_meta(conn, "live_since", "") or None)
            return decisions.scorecard(conn, lambda s: _closes(s, 800), date.today(), y, since)
    with db.connect() as conn:
        conn.executescript(decisions.SCHEMA)
        stamp = tuple(conn.execute("SELECT COUNT(*), MAX(decided) FROM decisions WHERE decided != ''").fetchone())
    return await asyncio.to_thread(_analysis_cache.get, ("dscore", stamp, date.today().isoformat()), run)


@app.get("/api/worth")
async def worth_view():
    """Is Plumbline worth it? Dollars it saved you against hosting and Claude costs."""
    import json as _json
    import os as _os

    from . import cash, decisions, golive, worth

    def run():
        today = date.today()
        y, _ = cash.tbill_yield()
        with db.connect() as conn:
            live = db.get_meta(conn, "live_since", "") or None
            since = golive.record_start(live)
            card = decisions.scorecard(conn, lambda s: _closes(s, 800), today, y, since)
            log = _json.loads(db.get_meta(conn, "cooloff_log", "[]") or "[]")
            txs, sp, host = db.ledger(conn), worth.spend(conn), worth.hosting_monthly(conn, _os.environ.get("PLUMBLINE_URL", ""))
        kept = worth.held_off(log, txs, lambda s: _closes(s, 800), today, since)
        return worth.view(card, kept, sp, live, today, host)
    return await asyncio.to_thread(_analysis_cache.get, ("worth", _ledger_key(), date.today().isoformat()), run)


class DecideIn(BaseModel):
    key: str = Field(min_length=3, max_length=200)
    status: Literal["approved", "skipped", "later", "done"]


@app.post("/api/decisions/decide")
def decisions_decide(body: DecideIn):
    from . import decisions
    with db.connect() as conn:
        try:
            return decisions.decide(conn, body.key, body.status, date.today())
        except KeyError:
            raise HTTPException(404, "No such decision")


class AskPlumblineIn(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    conversation: str | None = Field(default=None, max_length=64)


def _claude_errors(fn):
    import anthropic
    try:
        return fn()
    except anthropic.AuthenticationError:
        raise HTTPException(400, "No working Anthropic API key: add ANTHROPIC_API_KEY to .env (Ask and the deep dive use it).")
    except anthropic.RateLimitError:
        raise HTTPException(429, "Anthropic's rate limit: try again in a minute.")
    except anthropic.APIStatusError as exc:
        raise HTTPException(502, f"Claude API error {exc.status_code}: {exc.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Couldn't reach the Claude API.")
    except TypeError as exc:                    # the SDK found no credentials at all
        if "auth" in str(exc).lower() or "api_key" in str(exc).lower():
            raise HTTPException(400, "No Anthropic API key: add ANTHROPIC_API_KEY to .env to use Ask.")
        raise


@app.post("/api/ask")
async def ask_view(body: AskPlumblineIn):
    """Ask Plumbline: Claude answers from your own data through the app's read-only tools."""
    from . import assistant
    return await asyncio.to_thread(_claude_errors, lambda: assistant.ask(body.question.strip(), body.conversation))


@app.get("/api/letter")
def letter_view():
    """This week's letter, if it's been written."""
    import json as _json
    with db.connect() as conn:
        return _json.loads(db.get_meta(conn, "letter_latest", "null") or "null") or {"answer": None}


@app.get("/api/claude-budget")
def claude_budget_view():
    """This month's estimated Claude spend and the automatic letter's budget."""
    import os as _os

    from . import assistant
    with db.connect() as conn:
        return {"spent": round(assistant.month_spend(conn), 2), "budget": assistant.budget(conn),
                "key": bool(_os.environ.get("ANTHROPIC_API_KEY")), "push": bool(_os.environ.get("PLUMBLINE_URL"))}


class BudgetIn(BaseModel):
    budget: float = Field(ge=0, le=100)


@app.post("/api/claude-budget")
def claude_budget_set(body: BudgetIn):
    from . import assistant
    with db.connect() as conn:
        db.set_meta(conn, "claude_auto_budget", str(round(body.budget, 2)))
        return {"spent": round(assistant.month_spend(conn), 2), "budget": assistant.budget(conn)}


@app.post("/api/letter")
async def letter_write():
    """Write this week's letter (Claude, from the decisions and the weekly recap)."""
    import json as _json

    from . import assistant
    out = await asyncio.to_thread(_claude_errors, assistant.letter)
    out["written"] = datetime_hour()
    with db.connect() as conn:
        db.set_meta(conn, "letter_latest", _json.dumps(out))
    return out


class UsageIn(BaseModel):
    page: str = Field(min_length=1, max_length=30, pattern=r"^[a-z]+$")


@app.post("/api/usage")
def usage_record(body: UsageIn):
    """Count a page open (stays on this app; see usage.py)."""
    from . import usage
    with db.connect() as conn:
        usage.record(conn, body.page, date.today())
    return {"ok": True}


class HideIn(BaseModel):
    page: str = Field(min_length=1, max_length=30, pattern=r"^[a-z]+$")
    hide: bool


@app.get("/api/usage")
def usage_view():
    from . import usage
    with db.connect() as conn:
        return usage.view(conn, PAGES, date.today())


@app.post("/api/usage/hide")
def usage_hide(body: HideIn):
    from . import usage
    if body.page not in PAGES:
        raise HTTPException(404, "No such page")
    with db.connect() as conn:
        return {"hidden": usage.set_hidden(conn, body.page, body.hide)}


PAGES = ["home", "decisions", "ask", "hold", "income", "plan", "review", "ideas", "sleepers", "chatter", "moneyflow", "economy", "pulse", "early", "people",
         "radar", "smart", "analyze", "research", "dashboard", "journal", "mynews", "reading", "portfolio", "accounts", "taxes"]


@app.get("/api/paper")
async def paper_view():
    """Each idea list run as a monthly equal-weight paper portfolio, against VOO."""
    from . import paper

    def run():
        books = paper.load()
        return {"lists": paper.performance(books, lambda s: _closes(s, 800), date.today()), "books": len(books),
                "note": None if books else "The first paper books are recorded on the idea-log job's first run of the month."}
    return await asyncio.to_thread(_analysis_cache.get, ("paper", date.today().isoformat()), run)


@app.get("/api/evidence")
async def evidence_view():
    """Which idea lists have earned a dollar amount (a forward record ahead of VOO) and which are research only."""
    from . import evidence, ideas
    try:
        board = (await ideas_view()).get("leaderboard")
    except Exception:  # noqa: BLE001 - without the scorecard, only the paper books count
        board = []
    try:
        lists = (await paper_view()).get("lists")
    except Exception:  # noqa: BLE001
        lists = []
    st = evidence.status(lists, board, ideas.SOURCES)
    return {"sources": st, "earned": sorted(evidence.earned(st))}


@app.get("/api/signal-backtests")
async def signal_backtests_view():
    """Opportunistic insider buying since 2013 and spin-offs since 2005, replayed against SPY."""
    from . import insider_backtest, spinoff_backtest
    ins, spin = await asyncio.gather(asyncio.to_thread(insider_backtest.load), asyncio.to_thread(spinoff_backtest.load))
    if spin and not spin.get("stress"):
        spin = dict(spin, stress=spinoff_backtest.stress(spin))       # files written before the stress test existed
    return {"insider": {k: v for k, v in (ins or {}).items() if k != "recent"} or None,
            "spinoff": {k: v for k, v in (spin or {}).items() if k != "cases"} | {"cases": (spin or {}).get("cases", [])[:20]} if spin else None}


@app.get("/api/freshness")
async def freshness_view():
    """How old each GitHub-built data file is (screen, idea log, watcher, backtests), and which are stale."""
    from . import freshness
    return freshness.summary(await asyncio.to_thread(freshness.check))


@app.get("/api/screen/backtest")
async def screen_backtest_view():
    """The screen replayed quarter by quarter since 2012 on what was public then, against SPY."""
    from . import screen_backtest
    data = await asyncio.to_thread(screen_backtest.load)
    if not data:
        raise HTTPException(503, "The screen backtest hasn't run yet (the 'Screen backtest' job fills it).")
    return {k: v for k, v in data.items() if k != "periods"} | {"periods": [
        {"quarter": p["quarter"], "day": p["day"], "companies": p["companies"], "spy": p["spy"],
         "large": {k: p["groups"]["large"].get(k) for k in ("3m", "6m", "12m", "top")},
         "bottom": {k: p["groups"]["bottom"].get(k) for k in ("3m", "12m")}} for p in data.get("periods", [])]}


@app.get("/api/priced-in/{symbol}")
async def priced_in_view(symbol: str):
    """Is it already priced in? Valuation against its own history, run-up, dilution, crowded themes."""
    from . import hype, screen
    sym = market.normalize_symbol(symbol)

    def run():
        try:
            hype.themes()
        except http.DataUnavailable:
            pass
        return hype.for_symbol(sym, screen.load())
    return await asyncio.to_thread(_analysis_cache.get, ("pricedin", sym, date.today().isoformat()), run)


@app.get("/api/themes")
async def themes_view():
    """How crowded each popular theme is: new fund registrations mentioning it, last 6 months vs the 6 before."""
    from . import hype
    try:
        return {"themes": await asyncio.to_thread(hype.themes)}
    except http.DataUnavailable as exc:
        raise HTTPException(502, f"SEC full-text search: {exc}")


@app.get("/api/macro")
async def macro_view():
    """The economy panel: rates, credit spreads, recession gauges, oil, orders; and how fast to put new money in."""
    from . import macro
    try:
        return await asyncio.to_thread(macro.build)
    except http.DataUnavailable as exc:
        raise HTTPException(502, f"FRED: {exc}")


@app.get("/api/moneyflow")
async def moneyflow_view():
    """Spending waves and who gets paid, and small suppliers that haven't followed a big customer's move."""
    from . import idealab
    return await asyncio.to_thread(_analysis_cache.get, ("moneyflow", date.today().isoformat()), idealab.money_flow)


@app.get("/api/contracts/{symbol}")
async def contracts_view(symbol: str):
    """Federal contract money obligated to the company over the last year, against its revenue."""
    from . import idealab
    sym = market.normalize_symbol(symbol)
    if market.asset_class(sym) != "stock":
        raise HTTPException(404, "Only companies get federal contracts.")
    try:
        return await asyncio.to_thread(idealab.contracts_for, sym)
    except http.DataUnavailable as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/idea-events")
async def idea_events_view():
    """Raised guidance the market agreed with (last ten days), and spin-offs registered or newly trading."""
    from . import idealab
    return await asyncio.to_thread(_analysis_cache.get, ("events", date.today().isoformat()), idealab.events)


@app.get("/api/sleepers")
async def sleepers_view():
    """Small and mid caps where several pieces of evidence line up and few are watching."""
    from . import discover, idealab

    def run():
        out = idealab.sleepers()
        positions, _ = _valued_positions()
        from . import ideas
        with db.connect() as conn:
            spec = {r["symbol"] for r in ideas.logged(conn) if r["source"] in ("sleeper", "chatter") and r["decision"] == "bought"}
        return dict(out, bucket=discover.bucket(positions, spec))
    return await asyncio.to_thread(_analysis_cache.get, ("sleepers", _ledger_key(), date.today().isoformat()), run)


@app.get("/api/chatter")
async def chatter_view():
    """What Reddit and StockTwits are talking about, with the warnings next to each name."""
    from . import idealab
    return await asyncio.to_thread(_analysis_cache.get, ("chatter", datetime_hour()), idealab.chatter)


def datetime_hour() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%dT%H")


# ------------------------------------------------------------------ earnings recap, dividend safety, style bets, weekly recap

@app.get("/api/earnings/{symbol}")
async def earnings_view(symbol: str):
    """The company's latest results press release (8-K exhibit 99.1): headline sentences, outlook,
    the stock's reaction, and the audited quarter once the 10-Q is out."""
    from . import earnings
    sym = market.normalize_symbol(symbol)
    if market.asset_class(sym) != "stock":
        raise HTTPException(404, "Only company stocks file results releases.")

    def run():
        return earnings.recap(sym)
    try:
        r = await asyncio.to_thread(_analysis_cache.get, ("earnings", sym, date.today().isoformat()), run)
    except http.DataUnavailable as exc:
        raise HTTPException(502, f"SEC: {exc}")
    if not r:
        raise HTTPException(404, f"No results release (8-K item 2.02) on file for {sym}: funds don't file them.")
    return r


@app.get("/api/income/safety")
async def dividend_safety_view():
    """A-F dividend safety grade per dividend-paying stock you hold, with the reasons."""
    from . import divsafety

    def run():
        positions, _ = _valued_positions()
        syms = [p["symbol"] for p in positions if market.asset_class(p["symbol"]) == "stock" and p.get("quantity")]
        grades, errors = divsafety.build(syms)
        order = {"F": 0, "D": 1, "C": 2, "?": 3, "B": 4, "A": 5}
        return {"grades": sorted(grades.values(), key=lambda g: (order.get(g["grade"], 3), g["symbol"])), "errors": errors,
                "not_graded": [s for s in syms if s not in grades]}
    return await asyncio.to_thread(_analysis_cache.get, ("divsafety", _ledger_key(), date.today().isoformat()), run)


@app.get("/api/factors")
async def factors_view():
    """Your portfolio's loadings on the market, size, value, profitability, investment and momentum factors."""
    from . import factors

    def run():
        positions, _ = _valued_positions()
        return factors.build(positions, lambda s: _closes(s, 800))
    try:
        return await asyncio.to_thread(_analysis_cache.get, ("factors", _ledger_key(), date.today().isoformat()), run)
    except http.DataUnavailable as exc:
        raise HTTPException(502, f"Factor data: {exc}")


weekly_cache = pulse.Cache(1800)


@app.get("/api/weekly")
async def weekly_view(refresh: bool = False):
    """The weekly recap: the week in dollars, decisions, confirmed news, next week, and your data."""
    from . import weekly
    if refresh:
        weekly_cache.store.clear()
    r = await asyncio.to_thread(weekly_cache.get, "weekly", weekly.gather)
    with db.connect() as conn:
        cadence = db.get_meta(conn, "push_cadence", "weekly") or "weekly"
    return dict(r, cadence=cadence)


@app.post("/api/weekly/send")
async def weekly_send():
    """Push this week's recap to your phone now (to try it)."""
    from . import weekly
    r = await asyncio.to_thread(weekly_cache.get, "weekly", weekly.gather)
    sent = await asyncio.to_thread(notify.send, notify.Message(title=r["title"], body=weekly.push_text(r), priority=3, tags=("calendar",)))
    if not sent:
        raise HTTPException(400, "Phone alerts aren't set up (Accounts → Setup → Phone alerts).")
    return {"sent": True}


class CadenceIn(BaseModel):
    cadence: Literal["weekly", "daily", "both"]


@app.post("/api/push-cadence")
def push_cadence(body: CadenceIn):
    """Which recap pushes to your phone: the weekly recap, the daily morning brief, or both."""
    with db.connect() as conn:
        db.set_meta(conn, "push_cadence", body.cadence)
    return {"cadence": body.cadence}


class AskIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    question: str = Field(min_length=3, max_length=600)
    filings: list[Literal["10-K", "10-Q", "earnings"]] = Field(default_factory=lambda: ["10-K"], min_length=1, max_length=3)


@app.post("/api/ask-filing")
async def ask_filing(body: AskIn):
    """A question answered from the company's own 10-K / 10-Q, with the passages cited."""
    from . import askfiling
    sym = market.normalize_symbol(body.symbol)
    if market.asset_class(sym) == "crypto":
        raise HTTPException(400, "Coins don't file reports with the SEC.")
    try:
        out = await asyncio.to_thread(askfiling.ask, sym, body.question.strip(), list(dict.fromkeys(body.filings)))
    except anthropic.AuthenticationError:
        raise HTTPException(400, "No working Anthropic API key: add ANTHROPIC_API_KEY to .env (the deep dive uses the same key).")
    except anthropic.RateLimitError:
        raise HTTPException(429, "Anthropic's rate limit: try again in a minute.")
    except anthropic.APIStatusError as exc:
        raise HTTPException(502, f"Claude API error {exc.status_code}: {exc.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Couldn't reach the Claude API.")
    except http.DataUnavailable as exc:
        raise HTTPException(502, f"SEC: {exc}")
    if out.get("error"):
        raise HTTPException(404, out["error"])
    if out.get("refused"):
        raise HTTPException(400, "Claude declined to answer that question.")
    return out


@app.get("/api/accounts")
def accounts_view():
    """Each account: what's in it, how it reaches this app, and how fresh it is."""
    with db.connect() as conn:
        txs = db.ledger(conn)
        inc = db.income(conn)
    return {"accounts": accounts.overview(txs, inc),
            "connections": {"coinbase_api": coinbase_sync.configured(), "snaptrade": snaptrade.configured(),
                            "coinbase_last": accounts.last_sync("coinbase"), "snaptrade_last": accounts.last_sync("snaptrade")}}


def _shelter_view():
    from . import cash, shelter
    today = date.today()
    with db.connect() as conn:
        txs, inc, st, accts = db.ledger(conn), db.income(conn), shelter.settings(conn), cash.load(conn)
    positions, _ = _valued_positions()
    price = {p["symbol"]: p.get("price") or 0.0 for p in positions}
    pos = [{"account": a["name"], "symbol": p["symbol"], "value": p["quantity"] * price.get(p["symbol"], 0.0)}
           for a in accounts.overview(txs, inc, today) for p in a["positions"]]
    year_ago = (today - timedelta(days=365)).isoformat()
    div: dict = {}
    for r in inc:
        if r["kind"] in ("dividend", "reinvested") and r["day"] >= year_ago:
            div[(r["account"], r["symbol"])] = div.get((r["account"], r["symbol"]), 0.0) + r["amount"]
    names = sorted({t.get("account") or "Unlabeled" for t in txs} | set(accts))
    return shelter.view(st, names, pos, div, today)


@app.get("/api/shelter")
def shelter_view():
    """Which accounts are tax-sheltered, this year's contribution room, and what belongs where."""
    return _shelter_view()


class ShelterAccountIn(BaseModel):
    account: str = Field(min_length=1, max_length=60)
    type: Literal["taxable", "roth_ira", "trad_ira", "401k", "hsa"]
    contributed: float | None = Field(default=None, ge=0, le=1_000_000)


@app.post("/api/shelter/account")
def shelter_account(body: ShelterAccountIn):
    from . import shelter
    with db.connect() as conn:
        shelter.save(conn, body.account, body.type, body.contributed, date.today().year)
    _analysis_cache.clear()
    return _shelter_view()


class ShelterPersonIn(BaseModel):
    age50: bool = False
    hsa_family: bool = False


@app.post("/api/shelter/person")
def shelter_person(body: ShelterPersonIn):
    from . import shelter
    with db.connect() as conn:
        shelter.save_person(conn, body.age50, body.hsa_family)
    return _shelter_view()


@app.get("/api/recurring")
def recurring_view():
    """What you invest by hand each month, what's on autopilot, and a recurring buy to cover the rest."""
    from . import cash, recurring, schedules
    with db.connect() as conn:
        txs, scheds, accts = db.ledger(conn), schedules.load(conn), cash.load(conn)
    idle = sum(r["amount"] for r in cash.view(accts, date.today(), cash.FALLBACK_YIELD, False)["accounts"] if r["idle"])
    return recurring.plan(txs, scheds, date.today(), idle)


@app.get("/api/backup")
def backup():
    """Everything you entered, as one JSON file: trades, watchlist, cash, topics, follows."""
    with db.connect() as conn:
        return {"format": "plumbline-backup", "version": 1, "exported": date.today().isoformat(),
                "transactions": db.list_transactions(conn), "watchlist": db.watchlist(conn),
                "cash": float(db.get_meta(conn, "cash", "0") or 0), "topics": db.topics(conn), "follows": db.follows(conn, include_pickers=True),
                "theses": db.theses(conn), "income": db.income(conn)}


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
            service.pf.build_positions(db.ledger(conn))
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(400, f"Restoring would leave an impossible ledger: {exc}")
        db.add_income(conn, [dict(r, import_key=r.get("import_key") or "bk:" + "|".join(str(r.get(k)) for k in ("symbol", "day", "amount", "kind")))
                             for r in d.get("income") or [] if r.get("day") and r.get("amount") is not None and r.get("kind")])
        for w in d.get("watchlist") or []:
            db.add_watch(conn, w)
        if d.get("cash"):
            db.set_meta(conn, "cash", str(float(d["cash"])))
        db.topics(conn, reading.DEFAULT_TOPICS)
        for name, terms in (d.get("topics") or {}).items():
            db.set_topic(conn, name, terms)
        for who, grp in (d.get("follows") or {}).items():
            db.follow(conn, who, grp)
        have = db.theses(conn)
        for sym, t in (d.get("theses") or {}).items():
            if sym not in have:
                db.save_thesis(conn, sym, {k: v for k, v in t.items() if k not in ("symbol", "updated")})
    holdplan.clear_cache()
    return {"transactions_added": added}


class ImportIn(BaseModel):
    csv: str = Field(min_length=1, max_length=5_000_000)
    commit: bool = False
    account: str = Field("Stash", max_length=40)      # for the holdings list only


IMPORT_HINTS = {
    "robinhood": "Either the file starts after some of these shares were bought (export the full history), or they came "
                 "from another broker some way the file doesn't show: record them under Portfolio → Accounts → Transfers "
                 "(arrived, with what you paid), then import again.",
    "coinbase": "Coins received from another wallet or exchange have no purchase in this file: pair them with the account "
                "they came from, or enter their original cost, under Portfolio → Transfers, then import again.",
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
    if res.errors and not (res.transactions or res.income or res.transfers):
        raise HTTPException(400, res.errors[0])
    with db.connect() as conn:
        # By key, and by content against other sources (trade emails, API syncs): the same trade under another key.
        prefix = {"robinhood": "rh:", "coinbase": "cb:", "holdings": "hl:"}[source]
        others = [t for t in db.list_transactions(conn) if not (t.get("import_key") or "").startswith(prefix)]
        new, _ = snaptrade.new_only(res.transactions, db.import_keys(conn), others, snaptrade.match)
        known_legs = {r["import_key"] for r in db.transfer_legs(conn)}
        new_legs = [g for g in res.transfers if g["import_key"] not in known_legs]
        known_income = db.income_keys(conn)
        new_income = [r for r in res.income if r["import_key"] not in known_income]
        if body.commit:
            for g in new_legs:
                db.add_leg(conn, g["symbol"], g["direction"], g["quantity"], g["day"], g["account"], g["import_key"], g["note"])
            paired = db.auto_pair(conn)
        else:
            paired = 0
        # In a preview the file's arrivals aren't stored yet: hold them the way the ledger will (transfers.pending_arrivals).
        preview_in = [] if body.commit else [
            {"id": -1e12, "symbol": g["symbol"], "side": "buy", "quantity": g["quantity"], "price": 0.0, "fees": 0.0, "date": g["day"],
             "account": g["account"], "transfer": f"pending:new{k}"} for k, g in enumerate(new_legs) if g["direction"] == "in"]
        try:
            positions = service.pf.build_positions(db.ledger(conn) + new + preview_in)
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(400, f"{exc}. {IMPORT_HINTS[source]}".strip())
        if body.commit:
            for t in new:
                db.add_transaction(conn, t["symbol"], t["side"], t["quantity"], t["price"], t["date"], t["fees"],
                                   t["note"], import_key=t["import_key"], account=t.get("account", ""))
            db.add_income(conn, new_income)
    touched = {t["symbol"] for t in res.transactions}
    return {"new": len(new), "duplicates": len(res.transactions) - len(new), "skipped": dict(res.skipped),
            "transfers_new": len(new_legs), "transfers_paired": paired,
            "income_new": len(new_income), "income_total": round(sum(r["amount"] for r in new_income), 2),
            "needs_cost": sum(1 for t in new if t.get("note") in db.NEEDS_COST_NOTES) + sum(1 for g in new_legs if g["direction"] == "in"),
            "errors": res.errors, "committed": body.commit,
            "positions": sorted([{"symbol": p.symbol, "quantity": p.quantity, "avg_cost": p.avg_cost}
                                 for p in positions.values() if p.quantity > 0 and p.symbol in touched],
                                key=lambda x: x["symbol"])}


@app.get("/api/portfolio")
def portfolio(risk: bool = True):
    with db.connect() as conn:
        txs = db.ledger(conn)
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
