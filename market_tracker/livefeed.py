"""Live prices for the dashboard.

One PriceHub per server holds the latest price of every symbol any open page is watching and
pushes each change to those pages (over server-sent events, see api.py). Behind it:

- crypto: Coinbase's public WebSocket ticker, tick by tick;
- US stocks: Finnhub's WebSocket trade stream when FINNHUB_API_KEY is set (free plan: real-time
  trades for personal use), otherwise a Yahoo quote poll every few seconds.

The upstream connections are made once on the server, so ten open tabs cost the same as one,
and API keys never reach the browser.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from . import http
from .config import settings
from .providers import market

COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"
FINNHUB_WS = "wss://ws.finnhub.io?token={key}"
POLL_SECONDS = 5.0
POLL_PER_SYMBOL = 0.25  # the poll slows down as more symbols are watched (40 symbols: every 10 s)
POLL_CONCURRENCY = 6
MIN_GAP = 0.25          # seconds between pushes of the same symbol (BTC ticks dozens of times a second)
QUEUE_SIZE = 500


@dataclass
class Tick:
    symbol: str
    price: float
    change_pct: float | None
    ts: str
    source: str
    session: str = ""       # pre / regular / post / closed for stocks, 24h for crypto

    def to_dict(self) -> dict:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Client:
    def __init__(self, symbols: set[str]):
        self.symbols = symbols
        self.queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=QUEUE_SIZE)


class PriceHub:
    def __init__(self, min_gap: float = MIN_GAP, clock: Callable[[], float] = time.monotonic):
        self.latest: dict[str, Tick] = {}
        self.prev_close: dict[str, float] = {}
        self.wanted: Counter[str] = Counter()
        self.clients: set[Client] = set()
        self.version = 0              # bumped whenever the set of wanted symbols changes
        self.min_gap = min_gap
        self.clock = clock
        self._last_push: dict[str, float] = {}
        self._tasks: list[asyncio.Task] = []

    # -------------------------------------------------------------- subscriptions
    def subscribe(self, symbols: list[str]) -> Client:
        client = Client({market.normalize_symbol(s) for s in symbols if s.strip()})
        before = set(self.wanted)
        self.wanted.update(client.symbols)
        self.clients.add(client)
        if set(self.wanted) != before:
            self.version += 1
        return client

    def unsubscribe(self, client: Client) -> None:
        if client not in self.clients:
            return
        self.clients.discard(client)
        before = set(self.wanted)
        self.wanted.subtract(client.symbols)
        self.wanted += Counter()      # drop zero counts
        if set(self.wanted) != before:
            self.version += 1

    def crypto(self) -> set[str]:
        return {s for s in self.wanted if market.asset_class(s) == "crypto"}

    def stocks(self) -> set[str]:
        return {s for s in self.wanted if market.asset_class(s) != "crypto"}

    # -------------------------------------------------------------- publishing
    def publish(self, tick: Tick) -> bool:
        """Record a price and forward it to interested pages, at most once per MIN_GAP per symbol.
        Returns whether it was forwarded."""
        self.latest[tick.symbol] = tick
        now = self.clock()
        if now - self._last_push.get(tick.symbol, -1e9) < self.min_gap:
            return False
        self._last_push[tick.symbol] = now
        for c in self.clients:
            if tick.symbol in c.symbols:
                try:
                    c.queue.put_nowait(tick)
                except asyncio.QueueFull:
                    pass          # a stalled page misses ticks; the next one catches it up
        return True

    def change_from_prev(self, symbol: str, price: float) -> float | None:
        prev = self.prev_close.get(symbol)
        return (price / prev - 1) * 100 if prev else None

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._tasks:
            return
        self._tasks.append(asyncio.create_task(run_forever(self, coinbase_session, self.crypto)))
        if settings.finnhub_api_key:
            self._tasks.append(asyncio.create_task(run_forever(self, finnhub_session, self.stocks)))
        else:
            self._tasks.append(asyncio.create_task(poll_stocks(self)))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()


# ------------------------------------------------------------------ message parsing

def parse_coinbase(msg: dict) -> Tick | None:
    """A Coinbase 'ticker' message; the change is over 24 hours, as crypto never closes."""
    if msg.get("type") != "ticker" or "price" not in msg:
        return None
    price = float(msg["price"])
    open24 = float(msg.get("open_24h") or 0)
    return Tick(symbol=msg["product_id"], price=price, change_pct=(price / open24 - 1) * 100 if open24 else None,
                ts=msg.get("time") or _now_iso(), source="coinbase", session="24h")


def parse_finnhub(msg: dict, hub: PriceHub) -> list[Tick]:
    """A Finnhub 'trade' message holds a batch of trades; keep the last per symbol."""
    if msg.get("type") != "trade":
        return []
    last: dict[str, dict] = {}
    for t in msg.get("data") or []:
        last[t["s"]] = t
    out = []
    for sym, t in last.items():
        price = float(t["p"])
        ts = datetime.fromtimestamp(t["t"] / 1000, timezone.utc).isoformat(timespec="seconds")
        out.append(Tick(symbol=sym, price=price, change_pct=hub.change_from_prev(sym, price), ts=ts, source="finnhub"))
    return out


# ------------------------------------------------------------------ upstream sessions

async def _connect(url: str):
    from websockets.asyncio.client import connect  # imported lazily: only the server needs it
    return await connect(url, ping_interval=20, ping_timeout=20, max_size=2 ** 20)


async def _resubscribe_loop(hub: PriceHub, ws, wanted_fn, send_changes, handle) -> None:
    """Read messages, and whenever the wanted set changes, send (un)subscribe messages."""
    current: set[str] = set()
    seen_version = -1
    while True:
        if hub.version != seen_version:
            seen_version = hub.version
            target = wanted_fn()
            await send_changes(ws, target - current, current - target)
            current = target
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        handle(json.loads(raw))


async def coinbase_session(hub: PriceHub, connect=_connect) -> None:
    ws = await connect(COINBASE_WS)
    try:
        async def send(ws, add, remove):
            if add:
                await ws.send(json.dumps({"type": "subscribe", "product_ids": sorted(add), "channels": ["ticker"]}))
            if remove:
                await ws.send(json.dumps({"type": "unsubscribe", "product_ids": sorted(remove), "channels": ["ticker"]}))

        def handle(msg):
            tick = parse_coinbase(msg)
            if tick:
                hub.publish(tick)
        await _resubscribe_loop(hub, ws, hub.crypto, send, handle)
    finally:
        await ws.close()


async def finnhub_session(hub: PriceHub, connect=_connect) -> None:
    ws = await connect(FINNHUB_WS.format(key=settings.finnhub_api_key))
    try:
        async def send(ws, add, remove):
            for s in sorted(add):
                await _ensure_prev_close(hub, s)
                await ws.send(json.dumps({"type": "subscribe", "symbol": s}))
            for s in sorted(remove):
                await ws.send(json.dumps({"type": "unsubscribe", "symbol": s}))

        def handle(msg):
            for tick in parse_finnhub(msg, hub):
                hub.publish(tick)
        await _resubscribe_loop(hub, ws, hub.stocks, send, handle)
    finally:
        await ws.close()


async def _ensure_prev_close(hub: PriceHub, symbol: str) -> None:
    if symbol in hub.prev_close:
        return
    try:
        q = await asyncio.to_thread(market.get_quote, symbol)
    except http.DataUnavailable:
        return
    if q.previous_close:
        hub.prev_close[symbol] = q.previous_close
    hub.publish(Tick(symbol, q.price, q.change_pct, q.as_of, q.source, q.session))


async def run_forever(hub: PriceHub, session: Callable[[PriceHub], Awaitable[None]],
                      wanted_fn: Callable[[], set[str]], sleep=asyncio.sleep) -> None:
    """Keep a session alive while any page wants its symbols, reconnecting with backoff (up to
    a minute) when it drops."""
    delay = 1.0
    while True:
        if not wanted_fn():
            await sleep(1.0)
            continue
        started = time.monotonic()
        try:
            await session(hub)
        except asyncio.CancelledError:
            raise
        except Exception:          # network drops, handshake failures: reconnect
            pass
        delay = 1.0 if time.monotonic() - started > 60 else min(delay * 2, 60.0)
        await sleep(delay)


async def poll_stocks(hub: PriceHub, interval: float = POLL_SECONDS, quote_fn=market.get_live_quote,
                      sleep=asyncio.sleep) -> None:
    """Without a streaming key: fetch every watched stock's latest trade, pre-market and
    after-hours included, a few at a time, every few seconds."""
    gate = asyncio.Semaphore(POLL_CONCURRENCY)

    async def one(sym: str) -> None:
        async with gate:
            try:
                q = await asyncio.to_thread(quote_fn, sym)
            except http.DataUnavailable:
                return
        if q.previous_close:
            hub.prev_close[sym] = q.previous_close
        hub.publish(Tick(sym, q.price, q.change_pct, q.as_of or _now_iso(), q.source, getattr(q, "session", "")))

    while True:
        syms = sorted(hub.stocks())
        await asyncio.gather(*(one(s) for s in syms))
        await sleep(max(interval, len(syms) * POLL_PER_SYMBOL))


# ------------------------------------------------------------------ intraday chart data

def intraday(symbol: str, range_: str = "1d") -> dict:
    """Price points for the chart: 1-minute bars today, 5-minute for 5 days, daily closes for a
    month or a year. The reference is where the change is measured from."""
    sym = market.normalize_symbol(symbol)
    if range_ in ("1m", "1y"):
        bars = market.get_history(sym, 400)
        keep = bars[-(22 if range_ == "1m" else 252):] if market.asset_class(sym) != "crypto" \
            else bars[-(30 if range_ == "1m" else 365):]
        pts = [{"t": int(datetime.fromisoformat(b.date[:10]).replace(tzinfo=timezone.utc).timestamp()), "p": b.close}
               for b in keep]
        return {"symbol": sym, "points": pts, "reference": pts[0]["p"] if pts else None,
                "reference_label": "1 month ago" if range_ == "1m" else "1 year ago"}
    if market.asset_class(sym) == "crypto":
        granularity, span = (300, 86400) if range_ == "1d" else (900, 5 * 86400)
        end = int(time.time())
        candles = http.get(market.COINBASE.format(product=sym) + "/candles",
                           params={"granularity": granularity,
                                   "start": datetime.fromtimestamp(end - span, timezone.utc).isoformat(),
                                   "end": datetime.fromtimestamp(end, timezone.utc).isoformat()}, ttl=30)
        pts = sorted((int(c[0]), float(c[4])) for c in candles)
        base = pts[0][1] if pts else None
        return {"symbol": sym, "points": [{"t": t, "p": p} for t, p in pts], "reference": base,
                "reference_label": "24h ago" if range_ == "1d" else "5 days ago"}
    result = market._yahoo_chart(sym, range_, "1m" if range_ == "1d" else "5m", ttl=30)
    stamps = result.get("timestamp") or []
    closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    pts = [{"t": int(t), "p": float(c)} for t, c in zip(stamps, closes) if c is not None]
    meta = result.get("meta", {})
    ref = meta.get("chartPreviousClose") or meta.get("previousClose")
    return {"symbol": sym, "points": pts, "reference": float(ref) if ref else None,
            "reference_label": "previous close" if range_ == "1d" else "5 days ago"}


hub = PriceHub()
