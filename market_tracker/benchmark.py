"""Are you beating the market? Your portfolio against the same money put into one index fund.

Every dollar you invested goes, on the same day, into the benchmark (VOO by default); every
sale takes the same dollars out of it. What each would be worth today, plus what you took out,
is the fair comparison: it counts when you added money, so a lucky or unlucky deposit date
doesn't flatter either side. Shown for the whole portfolio and for each account.
"""

from __future__ import annotations

from datetime import date

BENCH = "VOO"


def _close_on(bars: list[tuple[str, float]], day: str) -> float | None:
    """Close on the day, or the last one before it (weekends, holidays)."""
    lo, hi, best = 0, len(bars) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if bars[mid][0] <= day:
            best = bars[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return best if best is not None else (bars[0][1] if bars else None)


def compare(transactions: list[dict], bench_bars: list[tuple[str, float]], prices: dict[str, float],
            bench_price: float | None = None, account: str | None = None) -> dict | None:
    """transactions: the ledger; bench_bars: [(date, close)] oldest first; prices: symbol -> price now."""
    txs = sorted((t for t in transactions if t.get("transfer") is None and (account is None or (t.get("account") or "") == account)),
                 key=lambda t: t["date"])
    if not txs or not bench_bars:
        return None
    bench_now = bench_price or bench_bars[-1][1]
    invested = taken_out = 0.0
    bench_shares = 0.0
    held: dict[str, float] = {}
    for t in txs:
        amount = t["quantity"] * t["price"]
        px = _close_on(bench_bars, t["date"][:10])
        if not px:
            continue
        if t["side"] == "buy":
            invested += amount + (t.get("fees") or 0)
            bench_shares += (amount + (t.get("fees") or 0)) / px
            held[t["symbol"]] = held.get(t["symbol"], 0.0) + t["quantity"]
        else:
            taken_out += amount - (t.get("fees") or 0)
            bench_shares -= (amount - (t.get("fees") or 0)) / px
            held[t["symbol"]] = held.get(t["symbol"], 0.0) - t["quantity"]
    missing = [s for s, q in held.items() if q > 1e-9 and s not in prices]
    value = sum(q * prices.get(s, 0.0) for s, q in held.items() if q > 1e-9)
    bench_value = bench_shares * bench_now
    yours = value + taken_out - invested
    theirs = bench_value + taken_out - invested
    return {"invested": round(invested, 2), "taken_out": round(taken_out, 2), "value": round(value, 2),
            "bench_value": round(bench_value, 2), "gain": round(yours, 2), "bench_gain": round(theirs, 2),
            "gain_pct": round(yours / invested * 100, 2) if invested else None,
            "bench_gain_pct": round(theirs / invested * 100, 2) if invested else None,
            "ahead": round(yours - theirs, 2), "since": txs[0]["date"][:10], "unpriced": missing}


def build(transactions: list[dict], prices: dict[str, float], bench: str = BENCH, history_fn=None, today: date | None = None) -> dict:
    from .providers import market
    history_fn = history_fn or market.get_history
    today = today or date.today()
    first = min((t["date"][:10] for t in transactions), default=today.isoformat())
    days = (today - date.fromisoformat(first)).days + 10
    bars = [(b.date, b.close) for b in history_fn(bench, max(days, 30))]
    overall = compare(transactions, bars, prices)
    per = {}
    for acct in sorted({t.get("account") or "" for t in transactions}):
        r = compare(transactions, bars, prices, account=acct)
        if r and r["invested"]:
            per[acct or "Unlabeled"] = r
    return {"benchmark": bench, "overall": overall, "accounts": per, "as_of": today.isoformat()}
