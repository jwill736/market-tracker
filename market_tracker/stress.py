"""If a past crash happened today, and which holdings move together.

Crisis replay: today's holdings through the S&P 500's three most recent big falls, peak to
trough:
- 2008 financial crisis (2007-10-09 to 2009-03-09: S&P 500 down about 57%, about 4 years to
  get back to the peak with dividends);
- 2020 Covid crash (2020-02-19 to 2020-03-23: down about 34% in five weeks, back within months);
- 2022 rate shock (2022-01-03 to 2022-10-12: down about 25%; the Nasdaq and crypto fell harder).
A holding that existed then uses its own prices. One that didn't uses a stand-in: its beta
(how much it moves with the market, measured over its last two years) times the S&P 500's
move, and for crypto before 2014, Bitcoin's 2022 fall scaled the same way. Stand-ins are
labelled; they are estimates, not history.

The point is the dollar figure: seeing "$18,400 lower" before a crash is what lets you decide
now, calmly, what you'll do at the bottom.

Overlap: correlations of daily moves over the last year. Pairs above 0.8 behave almost as one
holding, so owning both diversifies less than it looks.
"""

from __future__ import annotations

import math
from datetime import date

CRISES = [
    {"key": "2008", "name": "2008 financial crisis", "start": "2007-10-09", "end": "2009-03-09", "recovery": "about 4 years"},
    {"key": "2020", "name": "2020 Covid crash", "start": "2020-02-19", "end": "2020-03-23", "recovery": "about 5 months"},
    {"key": "2022", "name": "2022 rate shock", "start": "2022-01-03", "end": "2022-10-12", "recovery": "about 1½ years"},
]
MARKET = "SPY"
CRYPTO_BASE = "BTC-USD"
BTC_2022 = ("2021-11-08", "2022-11-21")      # Bitcoin's own peak to trough, the stand-in for crypto before 2014


def _close_on(bars: list[tuple[str, float]], day: str, after: bool = False) -> float | None:
    """Close on the day, or the nearest before (after=True: nearest after)."""
    best = None
    for d, c in bars:
        if d <= day:
            best = c
        elif after and best is None:
            return c
        else:
            break
    return best


def window_return(bars: list[tuple[str, float]], start: str, end: str) -> float | None:
    if not bars or bars[0][0] > start or bars[-1][0] < end:
        return None
    a, b = _close_on(bars, start), _close_on(bars, end)
    return (b / a - 1) if a and b else None


def daily_returns(bars: list[tuple[str, float]]) -> dict[str, float]:
    out = {}
    for (d0, c0), (d1, c1) in zip(bars, bars[1:]):
        if c0 > 0:
            out[d1] = c1 / c0 - 1
    return out


def beta(asset: list[tuple[str, float]], market: list[tuple[str, float]], days: int = 504) -> float | None:
    a, m = daily_returns(asset[-days - 1:]), daily_returns(market[-days * 2:])
    common = sorted(set(a) & set(m))
    if len(common) < 60:
        return None
    xa = [a[d] for d in common]
    xm = [m[d] for d in common]
    ma, mm = sum(xa) / len(xa), sum(xm) / len(xm)
    cov = sum((x - ma) * (y - mm) for x, y in zip(xa, xm))
    var = sum((y - mm) ** 2 for y in xm)
    return max(0.0, min(3.0, cov / var)) if var else None


def replay(positions: list[dict], history_fn, crises=CRISES) -> list[dict]:
    """positions: [{symbol, market_value}]. history_fn(symbol) -> [(date, close)] from 2007 on."""
    from .providers import market
    hist = {}

    def h(sym):
        if sym not in hist:
            try:
                hist[sym] = history_fn(sym)
            except Exception:  # noqa: BLE001 - no history: stand-in only
                hist[sym] = []
        return hist[sym]
    spy = h(MARKET)
    btc = h(CRYPTO_BASE)
    out = []
    total = sum(p.get("market_value") or 0 for p in positions)
    for c in crises:
        rows, loss = [], 0.0
        mkt = window_return(spy, c["start"], c["end"])
        for p in positions:
            v = p.get("market_value") or 0
            if not v:
                continue
            sym = p["symbol"]
            crypto = market.asset_class(sym) == "crypto"
            own = window_return(h(sym), c["start"], c["end"])
            how = "its own prices"
            r = own
            if r is None:
                base_bars, base_ret, base_name = (btc, window_return(btc, c["start"], c["end"]), "Bitcoin") if crypto else (spy, mkt, "the S&P 500")
                if crypto and base_ret is None:
                    base_ret, base_name = window_return(btc, *BTC_2022), "Bitcoin's 2022 fall"
                b = 1.0 if sym in (MARKET, CRYPTO_BASE) else (beta(h(sym), base_bars) if h(sym) else None)
                b = 1.0 if b is None else b
                r = b * base_ret if base_ret is not None else None
                how = f"stand-in: {b:.1f}× {base_name}"
            if r is None:
                continue
            r = max(r, -1.0)
            loss += v * r
            rows.append({"symbol": sym, "value": round(v, 2), "change_pct": round(r * 100, 1), "change": round(v * r, 2), "how": how})
        rows.sort(key=lambda x: x["change"])
        out.append(dict(c, market_pct=round(mkt * 100, 1) if mkt is not None else None, change=round(loss, 2),
                        change_pct=round(loss / total * 100, 1) if total else None, holdings=rows,
                        stand_ins=sum(1 for r in rows if r["how"] != "its own prices")))
    return out


def correlations(symbols: list[str], history_fn, days: int = 365) -> dict:
    rets = {}
    for s in symbols:
        try:
            rets[s] = daily_returns(history_fn(s)[-days - 1:])
        except Exception:  # noqa: BLE001
            continue
    syms = [s for s in symbols if len(rets.get(s, {})) >= 60]
    matrix: dict[str, dict[str, float]] = {}
    pairs = []
    for i, a in enumerate(syms):
        matrix[a] = {}
        for b in syms:
            common = sorted(set(rets[a]) & set(rets[b]))
            if len(common) < 60:
                continue
            xa, xb = [rets[a][d] for d in common], [rets[b][d] for d in common]
            ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
            sa = math.sqrt(sum((x - ma) ** 2 for x in xa))
            sb = math.sqrt(sum((y - mb) ** 2 for y in xb))
            rho = sum((x - ma) * (y - mb) for x, y in zip(xa, xb)) / (sa * sb) if sa and sb else 0.0
            matrix[a][b] = round(rho, 2)
        for b in syms[i + 1:]:
            if b in matrix[a] and matrix[a][b] >= 0.8:
                pairs.append({"a": a, "b": b, "rho": matrix[a][b]})
    off = [matrix[a][b] for a in syms for b in syms if a < b and b in matrix.get(a, {})]
    return {"symbols": syms, "matrix": matrix, "pairs": sorted(pairs, key=lambda p: -p["rho"]),
            "average": round(sum(off) / len(off), 2) if off else None, "as_of": date.today().isoformat()}
