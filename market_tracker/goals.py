"""Goal tracker: "$X by year Y". From what you have now and what you add each month, the range
of outcomes (bad, typical and good markets) and the chance of getting there.

Returns are simulated a year at a time (7% average, 15% swings a year by default: roughly a
stock-heavy portfolio's long history, before inflation); both are settable. It's a range, not a
promise, and the typical case is the middle of that range, not a forecast.
"""

from __future__ import annotations

import random

PATHS = 4000


def project(now_value: float, monthly: float, years: int, target: float, mean: float = 0.07, vol: float = 0.15,
            seed: int = 7) -> dict:
    rng = random.Random(seed)
    finals = []
    yearly = monthly * 12
    for _ in range(PATHS):
        v = now_value
        for _ in range(years):
            r = rng.gauss(mean, vol)
            v = v * (1 + r) + yearly * (1 + r / 2)      # contributions spread over the year
            v = max(v, 0.0)
        finals.append(v)
    finals.sort()

    def pct(p):
        return round(finals[min(len(finals) - 1, int(p * len(finals)))], 0)
    hit = sum(1 for f in finals if f >= target) / len(finals)
    # The monthly amount that gives even odds of the target (typical market).
    need = None
    if target and years:
        g = (1 + mean) ** years
        annuity = ((1 + mean) ** years - 1) / mean if mean else years
        need = max(0.0, (target - now_value * g) / (annuity * 12))
    return {"years": years, "target": target, "chance": round(hit, 3), "bad": pct(0.10), "typical": pct(0.50), "good": pct(0.90),
            "contributed": round(now_value + yearly * years, 0), "monthly_for_even_odds": round(need, 0) if need is not None else None,
            "assumptions": {"mean": mean, "vol": vol}}
