"""Tiny vectorless backtester so every rule the app suggests can be checked against
buy-and-hold on the same history, with trading costs, before anyone trusts it.

Signals are computed on day t's close and applied to day t+1's return (no look-ahead).
"""

from __future__ import annotations

import math
from collections.abc import Callable

from .indicators import max_drawdown, sharpe, sma_series

# A rule maps the full close series to a per-day "hold the asset?" flag, where flag[i]
# may only use closes[0..i].
Rule = Callable[[list[float]], list[bool]]


def rule_buy_hold(closes: list[float]) -> list[bool]:
    return [True] * len(closes)


def rule_trend_sma200(closes: list[float]) -> list[bool]:
    return [s is not None and c > s for c, s in zip(closes, sma_series(closes, 200))]


def rule_momentum_12_1(closes: list[float]) -> list[bool]:
    return [i >= 252 and closes[i - 21] / closes[i - 252] > 1 for i in range(len(closes))]


RULES: dict[str, Rule] = {
    "buy_hold": rule_buy_hold,
    "trend_sma200": rule_trend_sma200,
    "momentum_12_1": rule_momentum_12_1,
}


def run(closes: list[float], rule: Rule, cost_bps: float = 10.0, warmup: int = 252,
        periods_per_year: int = 252) -> dict | None:
    if len(closes) <= warmup + 20:
        return None
    flags = rule(closes)
    equity = [1.0]
    daily = []
    position = False
    trades = 0
    invested_days = 0
    for i in range(warmup, len(closes) - 1):
        want = flags[i]
        growth = 1.0
        if want != position:
            trades += 1
            growth *= 1 - cost_bps / 10_000
            position = want
        if position:
            growth *= closes[i + 1] / closes[i]
            invested_days += 1
        r = growth - 1
        daily.append(r)
        equity.append(equity[-1] * (1 + r))
    years = len(daily) / periods_per_year
    total = equity[-1] - 1
    return {
        "total_return": total,
        "cagr": (equity[-1] ** (1 / years) - 1) if years > 0 and equity[-1] > 0 else None,
        "vol": (math.sqrt(sum(x * x for x in daily) / len(daily) - (sum(daily) / len(daily)) ** 2)
                * math.sqrt(periods_per_year)),
        "sharpe": sharpe(daily, periods_per_year),
        "max_drawdown": max_drawdown(equity),
        "trades": trades,
        "exposure": invested_days / len(daily),
        "days": len(daily),
    }


def compare(closes: list[float], periods_per_year: int = 252) -> dict:
    results = {name: run(closes, rule, periods_per_year=periods_per_year) for name, rule in RULES.items()}
    return {
        "results": results,
        "note": "In-sample on one asset; past rule performance is weak evidence. A rule that "
                "doesn't beat buy-and-hold on risk-adjusted terms here shouldn't be trusted.",
    }
