"""Positions, P&L and portfolio-level risk from a transaction ledger (average-cost basis)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .indicators import correlation, max_drawdown, returns, sharpe


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_cost: float
    cost_basis: float
    realized_pnl: float
    price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pct: float | None = None
    day_change_pct: float | None = None
    weight: float | None = None


def build_positions(transactions: list[dict]) -> dict[str, Position]:
    positions: dict[str, Position] = {}
    for tx in sorted(transactions, key=lambda t: (t["date"], t.get("id", 0))):
        sym = tx["symbol"].upper()
        pos = positions.setdefault(sym, Position(sym, 0.0, 0.0, 0.0, 0.0))
        qty, price, fees = float(tx["quantity"]), float(tx["price"]), float(tx.get("fees") or 0)
        if tx["side"] == "buy":
            pos.cost_basis += qty * price + fees
            pos.quantity += qty
            pos.avg_cost = pos.cost_basis / pos.quantity
        else:
            if qty > pos.quantity + 1e-9:
                raise ValueError(f"Sell of {qty} {sym} on {tx['date']} exceeds holding of {pos.quantity}")
            pos.realized_pnl += qty * (price - pos.avg_cost) - fees
            pos.quantity -= qty
            pos.cost_basis = pos.avg_cost * pos.quantity
            if pos.quantity < 1e-9:
                pos.quantity, pos.cost_basis = 0.0, 0.0
    return positions


def value_positions(positions: dict[str, Position], quotes: dict[str, dict]) -> dict:
    open_pos = [p for p in positions.values() if p.quantity > 0]
    for p in open_pos:
        q = quotes.get(p.symbol)
        if not q:
            continue
        p.price = q["price"]
        p.market_value = p.quantity * p.price
        p.unrealized_pnl = p.market_value - p.cost_basis
        p.unrealized_pct = (p.unrealized_pnl / p.cost_basis * 100) if p.cost_basis else None
        p.day_change_pct = q.get("change_pct")
    total = sum(p.market_value or 0 for p in open_pos)
    for p in open_pos:
        p.weight = (p.market_value / total * 100) if total and p.market_value else None
    cost = sum(p.cost_basis for p in open_pos)
    return {
        "positions": [asdict(p) for p in sorted(open_pos, key=lambda p: -(p.market_value or 0))],
        "closed": [asdict(p) for p in positions.values() if p.quantity == 0],
        "total_value": total,
        "total_cost": cost,
        "unrealized_pnl": total - cost if total else None,
        "unrealized_pct": ((total / cost - 1) * 100) if cost and total else None,
        "realized_pnl": sum(p.realized_pnl for p in positions.values()),
        "day_change_value": sum((p.market_value or 0) * (p.day_change_pct or 0) / (100 + (p.day_change_pct or 0))
                                for p in open_pos),
    }


def _aligned_returns(histories: dict[str, list[tuple[str, float]]]) -> tuple[list[str], dict[str, list[float]]]:
    """Align on dates every asset traded (stocks skip weekends; crypto doesn't)."""
    date_sets = [set(d for d, _ in h) for h in histories.values() if h]
    if not date_sets:
        return [], {}
    common = sorted(set.intersection(*date_sets))
    out = {}
    for sym, hist in histories.items():
        lookup = dict(hist)
        out[sym] = returns([lookup[d] for d in common])
    return common, out


def risk_report(weights: dict[str, float], histories: dict[str, list[tuple[str, float]]],
                asset_classes: dict[str, str]) -> dict:
    """weights: symbol -> fraction of portfolio (sums to ~1)."""
    dates, rets = _aligned_returns({s: h for s, h in histories.items() if s in weights})
    warnings = []
    for sym, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        if w > 0.25:
            warnings.append(f"{sym} is {w * 100:.0f}% of the portfolio (single-name risk > 25%)")
    crypto_w = sum(w for s, w in weights.items() if asset_classes.get(s) == "crypto")
    if crypto_w > 0.3:
        warnings.append(f"Crypto is {crypto_w * 100:.0f}% of the portfolio; expect 50-80% drawdowns in bear markets")
    if len(weights) < 5 and weights:
        warnings.append(f"Only {len(weights)} positions — idiosyncratic risk dominates")

    if not rets or len(dates) < 30:
        return {"warnings": warnings, "note": "Not enough overlapping price history for risk statistics"}
    n = min(len(r) for r in rets.values())
    port = [sum(weights[s] * rets[s][-n + i] for s in rets) for i in range(n)]
    equity = [1.0]
    for r in port:
        equity.append(equity[-1] * (1 + r))
    ppy = 365 if all(asset_classes.get(s) == "crypto" for s in weights) else 252
    mean = sum(port) / n
    vol = math.sqrt(sum((r - mean) ** 2 for r in port) / n) * math.sqrt(ppy)
    syms = sorted(rets)
    corr = {a: {b: correlation(rets[a][-n:], rets[b][-n:]) for b in syms} for a in syms}
    high_corr = [(a, b, c) for a in syms for b in syms if a < b and (c := corr[a][b]) is not None and c > 0.8]
    for a, b, c in high_corr:
        warnings.append(f"{a} and {b} are {c:.2f} correlated — effectively one bet")
    sorted_port = sorted(port)
    var95 = -sorted_port[int(0.05 * n)]
    return {
        "days": n,
        "annual_vol": vol,
        "sharpe": sharpe(port, ppy),
        "max_drawdown": max_drawdown(equity),
        "var_95_1d": var95,
        "return_period": equity[-1] - 1,
        "correlation": corr,
        "warnings": warnings,
    }


def inverse_vol_weights(vols: dict[str, float]) -> dict[str, float]:
    """Risk-balanced target weights: each position contributes similar volatility."""
    inv = {s: 1 / v for s, v in vols.items() if v}
    total = sum(inv.values())
    return {s: x / total for s, x in inv.items()} if total else {}
