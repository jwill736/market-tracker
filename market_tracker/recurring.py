"""A recurring buy plan: put the money you invest by hand on autopilot.

Money that arrives regularly (a paycheck) does best invested as it arrives, and every buy you
make by hand is a small timing decision (performance.py shows what your timing has cost). So if
you've been investing by hand most months, the app suggests one recurring buy of the same size
at your broker (Robinhood and Stash both do weekly or monthly recurring investments), into the
index, to record here as a schedule.

The sizing is your own habit: net money you put in by hand in each of the last six full months (buys
minus sells, not counting buys your schedules already make), and the typical month (the median, so
one big transfer doesn't set the size). It goes into the index: automating a single stock would be
automating a pick.
"""

from __future__ import annotations

import statistics
from datetime import date

from .schedules import Schedule

MONTHS = 6
MIN_MONTHLY = 100.0
MIN_ACTIVE_MONTHS = 3
PER_MONTH = {"week": 52 / 12, "2weeks": 26 / 12, "month": 1.0}


def _months_back(today: date, n: int) -> list[str]:
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        out.append(f"{y:04d}-{m:02d}")
    return out[::-1]


def plan(transactions: list[dict], schedules: list[Schedule], today: date, idle_cash: float = 0.0, target: str = "VOO") -> dict:
    """target: the index fund the recurring buy goes into."""
    months = _months_back(today, MONTHS)
    net = {m: 0.0 for m in months}
    for t in transactions:
        m = t["date"][:7]
        if m not in net or (t.get("import_key") or "").startswith("sched:"):
            continue
        amt = t["quantity"] * t["price"]
        net[m] += amt if t["side"] == "buy" else -amt
    active = [m for m in months if net[m] > 0]
    by_hand = statistics.median(max(0.0, v) for v in net.values())
    scheduled = sum(s.amount * PER_MONTH.get(s.every, 0.0) for s in schedules if s.active)
    out = {"months": [{"month": m, "by_hand": round(net[m], 2)} for m in months], "by_hand_monthly": round(by_hand, 2),
           "scheduled_monthly": round(scheduled, 2), "active_months": len(active), "suggest": None}
    if by_hand >= MIN_MONTHLY and len(active) >= MIN_ACTIVE_MONTHS:
        week = max(5, round(by_hand * 12 / 52 / 5) * 5)
        out["suggest"] = {"symbol": target, "weekly": week, "monthly": round(by_hand / 25) * 25 or round(by_hand),
                          "text": f"In a typical month you put about ${by_hand:,.0f} in by hand ({len(active)} of the last {MONTHS} months)"
                                  + (f", on top of ${scheduled:,.0f} a month already on autopilot" if scheduled else "")
                                  + f". Make it a recurring buy of ${week} a week into {target} at your broker and record it here as a schedule: "
                                    "invested as it arrives, with no timing decision to get wrong."}
    elif idle_cash >= MIN_MONTHLY and not scheduled:
        out["suggest"] = {"symbol": target, "weekly": None, "monthly": None,
                          "text": f"${idle_cash:,.0f} is sitting in cash and nothing is on autopilot. A recurring buy, even a small one, "
                                  "keeps new money from piling up uninvested."}
    return out
