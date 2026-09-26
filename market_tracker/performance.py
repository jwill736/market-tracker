"""What your timing cost you, and what made the return.

Two returns tell different stories:
- time-weighted: how the holdings themselves did, whatever you added or took out when (what a
  fund reports);
- money-weighted (your internal rate of return): how YOUR dollars did, counting when they went
  in and out.
The gap between them is what your timing added or cost. Morningstar's "Mind the Gap" study
finds investors trail their own funds this way by about a percentage point a year on average;
a 2026 Financial Analysts Journal paper argues the average is smaller than that. Either way it
is your number, from your trades, not an average.

Also: what the portfolio would be worth had you never sold anything you bought (the cost, or
benefit, of your sales), and each holding's contribution in dollars this year and since your
first trade. Moves between your own accounts are neither flows nor trades here.

Prices are daily closes (split- and dividend-adjusted for stocks), so dividends count as
return; the result is an estimate to a percent or so, not an audited figure.
"""

from __future__ import annotations

from datetime import date, timedelta


def _series(bars: list[tuple[str, float]], days: list[str]) -> dict[str, float]:
    """Close on each day, carrying the last close over weekends and holidays."""
    out, i, last = {}, 0, None
    bars = sorted(bars)
    for d in days:
        while i < len(bars) and bars[i][0] <= d:
            last = bars[i][1]
            i += 1
        if last is not None:
            out[d] = last
    return out


def _days(start: date, end: date) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def xirr(flows: list[tuple[str, float]], guess: float = 0.1) -> float | None:
    """Annual rate r with sum(cf / (1+r)^(years from first)) = 0. flows: [(date, amount)], money you
    put in negative, money out (and the final value) positive."""
    if not flows or not any(a > 0 for _, a in flows) or not any(a < 0 for _, a in flows):
        return None
    d0 = date.fromisoformat(min(d for d, _ in flows))
    ts = [((date.fromisoformat(d) - d0).days / 365.25, a) for d, a in flows]

    def f(r):
        return sum(a / (1 + r) ** t for t, a in ts)
    lo, hi = -0.99, 10.0
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def analyze(transactions: list[dict], history_fn, today: date, start: date | None = None) -> dict:
    """transactions: your trades (no move rows). history_fn(symbol, days) -> [(date, close)]."""
    txs = sorted((t for t in transactions if t.get("transfer") is None), key=lambda t: (t["date"], t.get("id", 0)))
    if not txs:
        return {"empty": True}
    first = date.fromisoformat(txs[0]["date"][:10])
    start = max(start or first, first)
    span = (today - first).days + 10
    days = _days(first, today)
    syms = sorted({t["symbol"] for t in txs})
    closes: dict[str, dict[str, float]] = {}
    missing = []
    for s in syms:
        try:
            closes[s] = _series(history_fn(s, span), days)
        except Exception:  # noqa: BLE001 - a symbol with no history is valued at its trade prices
            closes[s] = {}
        if not closes[s]:
            missing.append(s)
    by_day: dict[str, list[dict]] = {}
    for t in txs:
        by_day.setdefault(t["date"][:10], []).append(t)
    qty: dict[str, float] = {}
    last_trade_px: dict[str, float] = {}
    kept: dict[str, float] = {}           # shares if nothing had ever been sold
    twr, prev_value = 1.0, None
    flows = []
    values = {}
    for d in days:
        flow = 0.0
        for t in by_day.get(d, []):
            amt = t["quantity"] * t["price"] + (t.get("fees") or 0) * (1 if t["side"] == "buy" else -1)
            if t["side"] == "buy":
                qty[t["symbol"]] = qty.get(t["symbol"], 0.0) + t["quantity"]
                kept[t["symbol"]] = kept.get(t["symbol"], 0.0) + t["quantity"]
                flow += amt
            else:
                qty[t["symbol"]] = qty.get(t["symbol"], 0.0) - t["quantity"]
                flow -= amt
            last_trade_px[t["symbol"]] = t["price"]
            if d >= start.isoformat():
                flows.append((d, -amt if t["side"] == "buy" else amt))
        value = sum(q * closes[s].get(d, last_trade_px.get(s, 0.0)) for s, q in qty.items() if q > 1e-12)
        values[d] = value
        if d >= start.isoformat():
            if prev_value is None:
                if d > first.isoformat():             # starting mid-history: the holdings on the start day are money "put in"
                    base = values.get((date.fromisoformat(d) - timedelta(days=1)).isoformat(), 0.0)
                    if base > 0:
                        flows.insert(0, ((date.fromisoformat(d) - timedelta(days=1)).isoformat(), -base))
                        prev_value = base
            if prev_value:
                twr *= (value - flow) / prev_value if prev_value > 0 else 1.0
            prev_value = value
    end_value = values[days[-1]]
    flows.append((days[-1], end_value))
    period_days = max(1, (today - start).days)
    years = period_days / 365.25
    twr_total = twr - 1
    twr_annual = (twr ** (1 / years) - 1) if years >= 1 and twr > 0 else None
    mwr = xirr(flows)
    # Never sold anything: every share bought, at today's price.
    held_value = sum(q * closes[s].get(days[-1], last_trade_px.get(s, 0.0)) for s, q in kept.items())
    taken_out = sum(a for d, a in flows[:-1] if a > 0)
    put_in = -sum(a for d, a in flows[:-1] if a < 0)
    sales_effect = (end_value + taken_out) - held_value
    return {"start": start.isoformat(), "end": today.isoformat(), "put_in": round(put_in, 2), "taken_out": round(taken_out, 2),
            "value": round(end_value, 2), "gain": round(end_value + taken_out - put_in, 2),
            "time_weighted": round(twr_total * 100, 2), "time_weighted_annual": round(twr_annual * 100, 2) if twr_annual is not None else None,
            "money_weighted_annual": round(mwr * 100, 2) if mwr is not None else None,
            "timing_gap": round((mwr - twr_annual) * 100, 2) if (mwr is not None and twr_annual is not None) else None,
            "never_sold_value": round(held_value, 2), "sales_effect": round(sales_effect, 2),
            "unpriced": missing, "contribution": contribution(txs, closes, last_trade_px, start, today)}


def contribution(txs: list[dict], closes: dict[str, dict[str, float]], last_px: dict[str, float], start: date, today: date) -> list[dict]:
    """Each holding's gain in dollars over the period: value change plus sales minus purchases."""
    s0 = (start - timedelta(days=1)).isoformat()
    end = today.isoformat()
    out = []
    for sym in sorted({t["symbol"] for t in txs}):
        q0 = sum((t["quantity"] if t["side"] == "buy" else -t["quantity"]) for t in txs if t["symbol"] == sym and t["date"][:10] <= s0)
        q1 = sum((t["quantity"] if t["side"] == "buy" else -t["quantity"]) for t in txs if t["symbol"] == sym)
        px0 = closes[sym].get(s0) or next((t["price"] for t in txs if t["symbol"] == sym), 0.0)
        px1 = closes[sym].get(end, last_px.get(sym, 0.0))
        bought = sum(t["quantity"] * t["price"] + (t.get("fees") or 0) for t in txs if t["symbol"] == sym and t["side"] == "buy" and t["date"][:10] > s0)
        sold = sum(t["quantity"] * t["price"] - (t.get("fees") or 0) for t in txs if t["symbol"] == sym and t["side"] == "sell" and t["date"][:10] > s0)
        gain = q1 * px1 - q0 * px0 + sold - bought
        if abs(gain) >= 0.005 or q1 > 1e-9:
            out.append({"symbol": sym, "gain": round(gain, 2), "value": round(q1 * px1, 2)})
    total = sum(r["gain"] for r in out)
    for r in out:
        r["share"] = round(r["gain"] / total * 100, 1) if total else None
    return sorted(out, key=lambda r: -abs(r["gain"]))


def verdict(r: dict) -> str:
    if r.get("empty"):
        return "No trades yet."
    g = r.get("timing_gap")
    if g is None:
        return ("Less than a year of history: the timing gap needs at least a year to mean anything. "
                f"So far the holdings returned {r['time_weighted']:+.1f}%.")
    if g <= -1:
        return f"Your timing cost you about {-g:.1f} points a year: your dollars earned {r['money_weighted_annual']:.1f}% a year while the holdings earned {r['time_weighted_annual']:.1f}%."
    if g >= 1:
        return f"Your timing helped by about {g:.1f} points a year: your dollars earned {r['money_weighted_annual']:.1f}% against the holdings' {r['time_weighted_annual']:.1f}%."
    return f"Your timing made little difference ({g:+.1f} points a year): steady buying does that."
