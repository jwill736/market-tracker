"""Assembles quotes, indicators, forecasts, smart-money, insider and news data per symbol."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import http
from .analytics import backtest, forecast, indicators, signals
from .analytics import portfolio as pf
from .providers import market, news, sec

_sm_lock = threading.Lock()
_sm_cache: tuple[float, list[dict], list[str]] | None = None
SMART_MONEY_TTL = 6 * 3600


def smart_money_reports(refresh: bool = False) -> tuple[list[dict], list[str]]:
    """All tracked investors' latest 13F reports (cached; 13Fs change quarterly)."""
    global _sm_cache
    with _sm_lock:
        if not refresh and _sm_cache and _sm_cache[0] > time.time():
            return _sm_cache[1], _sm_cache[2]
        reports, errors = sec.all_investor_reports()
        if reports:
            _sm_cache = (time.time() + SMART_MONEY_TTL, reports, errors)
        return reports, errors


def periods_per_year(symbol: str) -> int:
    return 365 if market.asset_class(symbol) == "crypto" else 252


def analyze(symbol: str, *, with_smart_money: bool = True, with_news: bool = True,
            with_insiders: bool = True) -> dict:
    sym = market.normalize_symbol(symbol)
    cls = market.asset_class(sym)
    ppy = periods_per_year(sym)
    errors: list[str] = []

    def safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (http.DataUnavailable, KeyError, ValueError) as exc:
            errors.append(f"{getattr(fn, '__name__', 'source')}: {exc}")
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_quote = pool.submit(safe, market.get_quote, sym)
        f_hist = pool.submit(safe, market.get_history, sym, 1300)
        f_news = pool.submit(safe, news.get_news, sym) if with_news else None
        f_ins = (pool.submit(safe, sec.get_insider_trades, sym)
                 if with_insiders and cls == "stock" else None)
        quote = f_quote.result()
        history = f_hist.result() or []
        news_data = f_news.result() if f_news else None
        insider_trades = f_ins.result() if f_ins else None

    closes = [b.close for b in history]
    if quote and closes:
        closes = closes[:-1] + [quote.price] if history[-1].date >= quote.as_of[:10] else closes + [quote.price]
    ind = indicators.snapshot(closes, ppy) if len(closes) >= 30 else {}

    sm = staleness = None
    if with_smart_money and cls == "stock":
        reports, sm_errors = smart_money_reports()
        errors += sm_errors[:3]
        if reports:
            sm = sec.smart_money_for_ticker(sym, reports)
            staleness = min(r["staleness_days"] for r in reports)

    insiders = sec.summarize_insiders(insider_trades) if insider_trades is not None else None
    return {
        "symbol": sym,
        "asset_class": cls,
        "quote": quote.to_dict() if quote else None,
        "indicators": ind,
        "history": [{"date": b.date, "close": b.close} for b in history[-400:]],
        "forecast": forecast.forecast(closes, ppy) if len(closes) >= 60 else None,
        "backtest": backtest.compare(closes, ppy) if len(closes) > 300 else None,
        "smart_money": sm,
        "smart_money_staleness_days": staleness,
        "insiders": insiders,
        "news": news_data,
        "signal": signals.composite(ind, sm, insiders, news_data, staleness) if ind else None,
        "errors": errors,
    }


def portfolio_summary(transactions: list[dict], with_risk: bool = True) -> dict:
    positions = pf.build_positions(transactions)
    open_syms = [s for s, p in positions.items() if p.quantity > 0]
    quotes: dict[str, dict] = {}
    histories: dict[str, list[tuple[str, float]]] = {}
    errors: list[str] = []

    def load(sym: str):
        try:
            q = market.get_quote(sym).to_dict()
            h = [(b.date, b.close) for b in market.get_history(sym, 400)] if with_risk else []
            return sym, q, h, None
        except (http.DataUnavailable, KeyError, ValueError) as exc:
            return sym, None, [], f"{sym}: {exc}"

    with ThreadPoolExecutor(max_workers=6) as pool:
        for sym, q, h, err in pool.map(load, open_syms):
            if q:
                quotes[sym] = q
            if h:
                histories[sym] = h
            if err:
                errors.append(err)

    summary = pf.value_positions(positions, quotes)
    if with_risk and summary["total_value"]:
        weights = {p["symbol"]: p["market_value"] / summary["total_value"]
                   for p in summary["positions"] if p["market_value"]}
        classes = {s: market.asset_class(s) for s in weights}
        summary["risk"] = pf.risk_report(weights, histories, classes)
        vols = {s: indicators.annualized_vol([c for _, c in h], 252, periods_per_year(s))
                for s, h in histories.items() if s in weights}
        target = pf.inverse_vol_weights({s: v for s, v in vols.items() if v})
        summary["rebalance_hint"] = [
            {"symbol": s, "current_pct": weights[s] * 100, "risk_balanced_pct": target[s] * 100,
             "vol_annual": vols.get(s)}
            for s in sorted(target, key=lambda s: -weights[s])
        ]
    summary["errors"] = errors
    return summary
