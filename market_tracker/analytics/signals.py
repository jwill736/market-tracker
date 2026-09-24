"""Composite conviction score combining trend, momentum, smart money, insiders and news.

Output is a score in [-100, 100] plus the component breakdown, so the user can see *why*.
Components that couldn't be computed are dropped and the remaining weights renormalized;
`coverage` reports how much of the model actually contributed.
"""

from __future__ import annotations

import math

WEIGHTS = {"trend": 0.25, "momentum": 0.25, "smart_money": 0.20, "insider": 0.15, "news": 0.15}


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def trend_component(ind: dict) -> tuple[float, str] | None:
    if ind.get("above_sma200") is None and ind.get("above_sma50") is None:
        return None
    score = 0.0
    parts = []
    if ind.get("above_sma200") is not None:
        score += 0.5 if ind["above_sma200"] else -0.5
        parts.append(("above" if ind["above_sma200"] else "below") + " 200-day average")
    if ind.get("above_sma50") is not None:
        score += 0.25 if ind["above_sma50"] else -0.25
        parts.append(("above" if ind["above_sma50"] else "below") + " 50-day average")
    if ind.get("golden_cross") is not None:
        score += 0.25 if ind["golden_cross"] else -0.25
        parts.append("50d > 200d" if ind["golden_cross"] else "50d < 200d")
    return _clamp(score), "; ".join(parts)


def momentum_component(ind: dict) -> tuple[float, str] | None:
    mom = ind.get("momentum_12_1")
    label = "12-1 month momentum"
    if mom is None:
        mom, label = ind.get("return_6m"), "6-month return"
    if mom is None:
        mom, label = ind.get("return_3m"), "3-month return"
    if mom is None:
        return None
    vol = ind.get("vol_annual") or 0.3
    score = math.tanh(mom / vol)  # risk-adjusted: +50% on a 100%-vol asset ≈ +25% on a 50%-vol asset
    note = f"{label} {mom * 100:+.1f}% (vol-adjusted)"
    rsi = ind.get("rsi14")
    if rsi is not None and rsi > 80:
        score -= 0.2
        note += f"; RSI {rsi:.0f} overbought"
    elif rsi is not None and rsi < 20:
        score += 0.2
        note += f"; RSI {rsi:.0f} oversold"
    return _clamp(score), note


def smart_money_component(sm: dict | None) -> tuple[float, str] | None:
    if not sm or not sm.get("investors_scanned"):
        return None
    buyers, sellers, holders = sm.get("buyers", []), sm.get("sellers", []), sm.get("holders", [])
    if not (buyers or sellers or holders):
        return 0.0, f"none of {sm['investors_scanned']} tracked investors hold or traded it"
    breadth = min(1.0, (len(buyers) + len(sellers)) / 3)
    score = sm.get("net_flow", 0.0) * breadth
    # Being a large position in a concentrated book is itself a conviction signal.
    big = [h for h in holders if h.get("weight_pct", 0) >= 5]
    score += 0.15 * min(len(big), 2)
    note = f"{len(buyers)} bought/added, {len(sellers)} trimmed/exited, {len(holders)} hold"
    if big:
        note += f" ({len(big)} as a ≥5% position)"
    return _clamp(score), note


def insider_component(ins: dict | None) -> tuple[float, str] | None:
    if not ins:
        return None
    if ins["cluster_buy"]:
        return 1.0, f"cluster buying: {ins['distinct_buyers']} insiders bought in {ins['window_days']}d"
    if ins["open_market_buys"]:
        return 0.5, f"{ins['open_market_buys']} open-market insider buy(s) (${ins['buy_value_usd']:,.0f})"
    if ins["discretionary_sell_value_usd"] > 0:
        return -0.3, (f"only selling: ${ins['discretionary_sell_value_usd']:,.0f} discretionary "
                      f"(+{ins['planned_10b5_1_sells']} planned 10b5-1)")
    return 0.0, "no open-market insider activity"


def news_component(news: dict | None) -> tuple[float, str] | None:
    if not news or not news.get("count"):
        return None
    confidence = min(1.0, news["count"] / 10)
    score = _clamp(news["avg_sentiment"] * 2) * confidence
    return score, (f"{news['count']} headlines: {news['positive']} positive / {news['negative']} negative "
                   f"(avg {news['avg_sentiment']:+.2f})")


def label_for(score: float) -> str:
    if score >= 40:
        return "Strong bullish"
    if score >= 15:
        return "Bullish"
    if score > -15:
        return "Neutral"
    if score > -40:
        return "Bearish"
    return "Strong bearish"


def suggested_max_weight(vol_annual: float | None, risk_budget: float = 0.02, cap: float = 0.20) -> float | None:
    """Largest position where a 1-sigma monthly move costs ≤ `risk_budget` of the portfolio."""
    if not vol_annual:
        return None
    monthly_sigma = vol_annual * math.sqrt(21 / 252)
    return min(cap, risk_budget / monthly_sigma)


def risk_flags(ind: dict, sm: dict | None, sm_staleness_days: int | None) -> list[str]:
    flags = []
    vol = ind.get("vol_annual")
    if vol and vol > 0.6:
        flags.append(f"Very high volatility ({vol * 100:.0f}% annualized) — size small")
    dd = ind.get("max_drawdown_1y")
    if dd is not None and dd < -0.3:
        flags.append(f"{dd * 100:.0f}% drawdown within the last year")
    rsi = ind.get("rsi14")
    if rsi and rsi > 75:
        flags.append(f"RSI {rsi:.0f}: short-term overbought, chasing risk")
    if sm_staleness_days and sm_staleness_days > 60:
        flags.append(f"13F data is {sm_staleness_days} days old — funds may have already exited")
    return flags


def composite(ind: dict, smart_money: dict | None = None, insiders: dict | None = None,
              news: dict | None = None, sm_staleness_days: int | None = None) -> dict:
    comps = {
        "trend": trend_component(ind),
        "momentum": momentum_component(ind),
        "smart_money": smart_money_component(smart_money),
        "insider": insider_component(insiders),
        "news": news_component(news),
    }
    available = {k: v for k, v in comps.items() if v is not None}
    total_w = sum(WEIGHTS[k] for k in available)
    score = sum(WEIGHTS[k] * v[0] for k, v in available.items()) / total_w * 100 if total_w else 0.0
    return {
        "score": round(score, 1),
        "label": label_for(score),
        "coverage": round(total_w, 2),
        "components": {k: ({"score": round(v[0] * 100, 1), "weight": WEIGHTS[k], "why": v[1]} if v else None)
                       for k, v in comps.items()},
        "suggested_max_weight": suggested_max_weight(ind.get("vol_annual")),
        "risk_flags": risk_flags(ind, smart_money, sm_staleness_days),
        "disclaimer": "A ranking aid, not a prediction. Weights are judgment calls; check the "
                      "backtest tab before relying on any component.",
    }
