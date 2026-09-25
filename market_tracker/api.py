"""HTTP API and dashboard server."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Literal

import anthropic
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, http, journal, research, service
from .investors import INVESTORS, by_key
from .providers import market, news, sec

app = FastAPI(title="Plumbline", version="0.1.0")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


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
        return market.get_quote(symbol).to_dict()
    except http.DataUnavailable:
        return None


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
        tx_id = db.add_transaction(conn, symbol, tx.side, tx.quantity, tx.price, tx.date, tx.fees, tx.note)
    return {"id": tx_id}


@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: int):
    with db.connect() as conn:
        if not db.delete_transaction(conn, tx_id):
            raise HTTPException(404, "Not found")
    return {"deleted": tx_id}


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
