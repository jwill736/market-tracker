"""Technical indicators and risk statistics on plain lists of closes (oldest first)."""

from __future__ import annotations

import math
from statistics import fmean, pstdev


def returns(closes: list[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]


def log_returns(closes: list[float]) -> list[float]:
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]


def sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return fmean(values[-window:])


def sma_series(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= window:
            running -= values[i - window]
        out.append(running / window if i >= window - 1 else None)
    return out


def ema_series(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI."""
    if len(closes) <= period:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_gain = fmean(gains[:period])
    avg_loss = fmean(losses[:period])
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict | None:
    if len(closes) < slow + signal:
        return None
    line = [f - s for f, s in zip(ema_series(closes, fast), ema_series(closes, slow))]
    sig = ema_series(line, signal)
    return {"macd": line[-1], "signal": sig[-1], "histogram": line[-1] - sig[-1]}


def period_return(closes: list[float], lookback: int, skip: int = 0) -> float | None:
    """Return from `lookback` bars ago to `skip` bars ago."""
    if len(closes) <= lookback:
        return None
    end = closes[-1 - skip]
    start = closes[-1 - lookback]
    return end / start - 1 if start else None


def annualized_vol(closes: list[float], window: int | None = None, periods_per_year: int = 252) -> float | None:
    r = log_returns(closes[-(window + 1):] if window else closes)
    if len(r) < 2:
        return None
    return pstdev(r) * math.sqrt(periods_per_year)


def ewma_vol(closes: list[float], lam: float = 0.94, periods_per_year: int = 252) -> float | None:
    """RiskMetrics exponentially weighted volatility: reacts faster to regime changes."""
    r = log_returns(closes)
    if len(r) < 20:
        return None
    var = fmean(x * x for x in r[:20])
    for x in r[20:]:
        var = lam * var + (1 - lam) * x * x
    return math.sqrt(var * periods_per_year)


def max_drawdown(closes: list[float]) -> float:
    peak = -math.inf
    worst = 0.0
    for c in closes:
        peak = max(peak, c)
        if peak > 0:
            worst = min(worst, c / peak - 1)
    return worst


def sharpe(daily_returns: list[float], periods_per_year: int = 252, risk_free: float = 0.0) -> float | None:
    if len(daily_returns) < 2:
        return None
    sd = pstdev(daily_returns)
    if sd == 0:
        return None
    excess = fmean(daily_returns) - risk_free / periods_per_year
    return excess / sd * math.sqrt(periods_per_year)


def correlation(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 3:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = fmean(a), fmean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va and vb else None


def snapshot(closes: list[float], periods_per_year: int = 252) -> dict:
    """All indicators for the latest bar."""
    price = closes[-1]
    s50, s200 = sma(closes, 50), sma(closes, 200)
    return {
        "price": price,
        "sma20": sma(closes, 20),
        "sma50": s50,
        "sma200": s200,
        "above_sma50": price > s50 if s50 else None,
        "above_sma200": price > s200 if s200 else None,
        "golden_cross": (s50 > s200) if s50 and s200 else None,
        "rsi14": rsi(closes),
        "macd": macd(closes),
        "return_1m": period_return(closes, 21),
        "return_3m": period_return(closes, 63),
        "return_6m": period_return(closes, 126),
        "return_12m": period_return(closes, 252),
        "momentum_12_1": period_return(closes, 252, skip=21),
        "vol_annual": annualized_vol(closes, 252, periods_per_year),
        "vol_ewma": ewma_vol(closes, periods_per_year=periods_per_year),
        "max_drawdown_1y": max_drawdown(closes[-252:]),
        "high_52w": max(closes[-252:]),
        "low_52w": min(closes[-252:]),
    }
