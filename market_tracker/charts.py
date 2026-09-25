"""Chart data for the app's home and symbol pages: candlesticks with volume, your portfolio's
value over time, and small sparklines for the holdings list.

Portfolio value over a range counts the shares you held at each moment (from the ledger), so a
purchase makes the line step up without counting as a gain: the gain shown is the change in
value minus the money you put in (or plus what you took out) during the range.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from . import http, livefeed
from .providers import market

# range -> (Yahoo range, Yahoo interval, Coinbase granularity seconds, span in days)
CANDLE_RANGES = {
    "1d": ("1d", "5m", 300, 1),
    "1w": ("5d", "30m", 3600, 7),
    "1m": ("1mo", "1d", 21600, 30),
    "3m": ("3mo", "1d", 86400, 92),
    "1y": ("1y", "1d", 86400, 365),
    "5y": ("5y", "1wk", 86400, 1825),
}
DAILY_RANGES = {"1m": 31, "3m": 92, "1y": 366, "all": None}
INTRADAY_RANGES = {"1d": ("1d", 300), "1w": ("5d", 1800)}


@dataclass
class Candle:
    t: int
    o: float
    h: float
    l: float  # noqa: E741  (OHLC field names)
    c: float
    v: float

    def to_dict(self) -> dict:
        return {"t": self.t, "o": self.o, "h": self.h, "l": self.l, "c": self.c, "v": self.v}


# ------------------------------------------------------------------ candles

def parse_yahoo_candles(result: dict) -> list[Candle]:
    stamps = result.get("timestamp") or []
    q = (result.get("indicators", {}).get("quote") or [{}])[0]
    out = []
    for i, t in enumerate(stamps):
        try:
            o, h, lo, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        except (KeyError, IndexError):
            continue
        if None in (o, h, lo, c):
            continue
        v = (q.get("volume") or [0] * len(stamps))[i] or 0
        out.append(Candle(int(t), float(o), float(h), float(lo), float(c), float(v)))
    return out


def parse_coinbase_ohlc(rows: list) -> list[Candle]:
    # [time, low, high, open, close, volume], newest first
    return sorted((Candle(int(r[0]), float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5])) for r in rows),
                  key=lambda c: c.t)


def weekly(candles: list[Candle]) -> list[Candle]:
    """Daily candles -> weekly (weeks starting Monday)."""
    out: dict[str, Candle] = {}
    for c in candles:
        d = datetime.fromtimestamp(c.t, timezone.utc).date()
        k = (d - timedelta(days=d.weekday())).isoformat()
        w = out.get(k)
        if w is None:
            out[k] = Candle(c.t, c.o, c.h, c.l, c.c, c.v)
        else:
            w.h, w.l, w.c, w.v = max(w.h, c.h), min(w.l, c.l), c.c, w.v + c.v
    return [out[k] for k in sorted(out)]


def candles(symbol: str, range_: str = "1d", get=http.get) -> dict:
    sym = market.normalize_symbol(symbol)
    yr, yi, gran, span = CANDLE_RANGES[range_]
    if market.asset_class(sym) == "crypto":
        end = int(datetime.now(timezone.utc).timestamp())
        rows: dict[int, list] = {}
        step = gran * 300
        start = end - span * 86400
        cur = start
        while cur < end:
            chunk = get(market.COINBASE.format(product=sym) + "/candles",
                        params={"granularity": gran, "start": _iso(cur), "end": _iso(min(cur + step, end))}, ttl=60)
            for r in chunk or []:
                rows[int(r[0])] = r
            cur += step
        out = parse_coinbase_ohlc(list(rows.values()))
        if range_ == "5y":
            out = weekly(out)
        ref = out[0].o if out else None
        label = "24h ago" if range_ == "1d" else "start of range"
    else:
        params = {"range": yr, "interval": yi}
        if range_ in ("1d", "1w"):
            params["includePrePost"] = "true"
        data = get(market.YAHOO_CHART.format(symbol=sym), params=params, ttl=30 if range_ == "1d" else 300)
        try:
            result = data["chart"]["result"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise http.DataUnavailable(f"no chart data for {sym}") from exc
        out = parse_yahoo_candles(result)
        meta = result.get("meta", {})
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        ref = float(prev) if range_ == "1d" and prev else (out[0].o if out else None)
        label = "previous close" if range_ == "1d" else "start of range"
    return {"symbol": sym, "range": range_, "candles": [c.to_dict() for c in out], "reference": ref,
            "reference_label": label}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


# ------------------------------------------------------------------ portfolio value over time

def _qty_timeline(transactions: list[dict]) -> dict[str, list[tuple[str, float]]]:
    """symbol -> [(date, quantity held after that date's trades)], oldest first."""
    held: dict[str, float] = {}
    out: dict[str, list[tuple[str, float]]] = {}
    for tx in sorted(transactions, key=lambda t: (t["date"], t.get("id", 0))):
        sym = tx["symbol"].upper()
        q = float(tx["quantity"]) * (1 if tx["side"] == "buy" else -1)
        held[sym] = max(held.get(sym, 0.0) + q, 0.0)
        seq = out.setdefault(sym, [])
        if seq and seq[-1][0] == tx["date"]:
            seq[-1] = (tx["date"], held[sym])
        else:
            seq.append((tx["date"], held[sym]))
    return out


def _qty_on(seq: list[tuple[str, float]], day: str) -> float:
    q = 0.0
    for d, v in seq:
        if d > day:
            break
        q = v
    return q


def _net_flow(transactions: list[dict], after: str, until: str) -> float:
    """Money put in (buys) minus taken out (sales) on dates in (after, until]."""
    flow = 0.0
    for t in transactions:
        if after < t["date"] <= until:
            gross = float(t["quantity"]) * float(t["price"])
            fees = float(t.get("fees") or 0)
            flow += gross + fees if t["side"] == "buy" else -(gross - fees)
    return flow


def portfolio_history(transactions: list[dict], range_: str = "1d", *, today: date | None = None,
                      history_fn: Callable = market.get_history, intraday_fn: Callable = livefeed.intraday) -> dict:
    today = today or date.today()
    timeline = _qty_timeline(transactions)
    symbols = [s for s, seq in timeline.items() if seq]
    if not symbols:
        return {"range": range_, "points": [], "start": None, "end": None, "gain": None, "gain_pct": None}
    errors: list[str] = []

    if range_ in INTRADAY_RANGES:
        held = {s: _qty_on(timeline[s], today.isoformat()) for s in symbols}
        held = {s: q for s, q in held.items() if q > 0}
        rng, bucket = INTRADAY_RANGES[range_]

        def load(sym):
            try:
                return sym, intraday_fn(sym, "1d" if range_ == "1d" else "5d")
            except (http.DataUnavailable, KeyError, ValueError) as exc:
                errors.append(f"{sym}: {exc}")
                return sym, None
        with ThreadPoolExecutor(max_workers=6) as pool:
            data = dict(pool.map(load, held))
        series = {s: d for s, d in data.items() if d and d.get("points")}
        grid = sorted({p["t"] // bucket * bucket for d in series.values() for p in d["points"]})
        points = []
        last: dict[str, float] = {s: (d.get("reference") or d["points"][0]["p"]) for s, d in series.items()}
        idx = {s: 0 for s in series}
        for t in grid:
            for s, d in series.items():
                pts = d["points"]
                while idx[s] < len(pts) and pts[idx[s]]["t"] // bucket * bucket <= t:
                    last[s] = pts[idx[s]]["p"]
                    idx[s] += 1
            points.append({"t": t, "v": round(sum(held[s] * last[s] for s in series), 2)})
        ref_value = sum(held[s] * (d.get("reference") or d["points"][0]["p"]) for s, d in series.items())
        start = ref_value if range_ == "1d" else (points[0]["v"] if points else None)
        end = points[-1]["v"] if points else None
        gain = (end - start) if (start is not None and end is not None) else None
        return {"range": range_, "points": points, "start": start, "end": end, "gain": gain,
                "gain_pct": (gain / start * 100) if gain is not None and start else None,
                "reference": start, "reference_label": "previous close" if range_ == "1d" else "5 days ago",
                "errors": errors}

    first = min(seq[0][0] for seq in timeline.values())
    days = DAILY_RANGES[range_]
    start_day = first if days is None else max(first, (today - timedelta(days=days)).isoformat())
    span = (today - date.fromisoformat(first)).days + 10 if days is None else days + 10

    def load_hist(sym):
        try:
            return sym, {b.date: b.close for b in history_fn(sym, max(span, 30))}
        except (http.DataUnavailable, KeyError, ValueError) as exc:
            errors.append(f"{sym}: {exc}")
            return sym, {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        closes = dict(pool.map(load_hist, symbols))
    dates = sorted({d for c in closes.values() for d in c if start_day <= d <= today.isoformat()})
    last: dict[str, float] = {}
    points = []
    for s, c in closes.items():                    # carry in the last close before the range
        before = [d for d in c if d < start_day]
        if before:
            last[s] = c[max(before)]
    for d in dates:
        for s, c in closes.items():
            if d in c:
                last[s] = c[d]
        v = sum(_qty_on(timeline[s], d) * last.get(s, 0.0) for s in symbols)
        points.append({"t": int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()), "v": round(v, 2)})
    if not points:
        return {"range": range_, "points": [], "start": None, "end": None, "gain": None, "gain_pct": None,
                "errors": errors}
    start_v, end_v = points[0]["v"], points[-1]["v"]
    first_date = dates[0]
    flow = _net_flow(transactions, first_date, dates[-1])
    if range_ == "all":
        start_v = 0.0
        flow = _net_flow(transactions, "0000-00-00", dates[-1])
    gain = end_v - start_v - flow
    basis = start_v + max(flow, 0.0)
    return {"range": range_, "points": points, "start": start_v, "end": end_v, "gain": round(gain, 2),
            "gain_pct": (gain / basis * 100) if basis else None, "net_deposits": round(flow, 2),
            "reference": points[0]["v"], "reference_label": "start of range", "errors": errors}


# ------------------------------------------------------------------ sparklines

def sparklines(symbols: list[str], intraday_fn: Callable = livefeed.intraday, n: int = 48) -> dict:
    def one(sym):
        try:
            d = intraday_fn(sym, "1d")
        except (http.DataUnavailable, KeyError, ValueError):
            return sym, None
        pts = d.get("points") or []
        if len(pts) > n:
            step = len(pts) / n
            pts = [pts[int(i * step)] for i in range(n)] + [pts[-1]]
        return sym, {"p": [round(p["p"], 6) for p in pts], "reference": d.get("reference")}
    with ThreadPoolExecutor(max_workers=6) as pool:
        return {s: v for s, v in pool.map(one, symbols) if v}
