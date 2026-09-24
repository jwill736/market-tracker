"""Probabilistic price ranges, not point predictions.

Nobody can reliably forecast direction from price history; what *can* be estimated with
some skill is the spread of outcomes (volatility clusters and persists). So this module
answers "where is the price likely to be, and how wide is the uncertainty?":

* `lognormal_cone`: analytic quantiles using EWMA volatility and a drift that is shrunk
  heavily toward zero (sample means of returns are mostly noise).
* `bootstrap`: resamples historical daily returns in blocks, preserving fat tails and
  short-term autocorrelation that the lognormal model ignores.
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist, fmean

from .indicators import ewma_vol, log_returns

QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)
DRIFT_SHRINK = 0.25  # keep 25% of the historical drift estimate


def lognormal_cone(closes: list[float], horizons: tuple[int, ...] = (5, 21, 63),
                   periods_per_year: int = 252) -> list[dict]:
    r = log_returns(closes)
    if len(r) < 30:
        return []
    price = closes[-1]
    sigma_ann = ewma_vol(closes, periods_per_year=periods_per_year) or 0.0
    sigma_d = sigma_ann / math.sqrt(periods_per_year)
    mu_d = fmean(r[-252:]) * DRIFT_SHRINK
    nd = NormalDist()
    out = []
    for h in horizons:
        m = mu_d * h
        s = sigma_d * math.sqrt(h)
        q = {f"p{int(p * 100)}": price * math.exp(m + s * nd.inv_cdf(p)) for p in QUANTILES}
        prob_up = 1 - nd.cdf((0 - m) / s) if s else (1.0 if m > 0 else 0.0)
        out.append({"horizon_days": h, "method": "lognormal", **q, "prob_up": prob_up,
                    "expected_move_pct": s * 100})
    return out


def bootstrap(closes: list[float], horizons: tuple[int, ...] = (5, 21, 63), paths: int = 4000,
              block: int = 5, seed: int | None = 7) -> list[dict]:
    r = log_returns(closes[-757:])  # up to ~3y
    if len(r) < 60:
        return []
    rng = random.Random(seed)
    price = closes[-1]
    demeaned_mu = fmean(r) * (1 - DRIFT_SHRINK)
    r = [x - demeaned_mu for x in r]  # apply the same drift shrinkage
    out = []
    max_h = max(horizons)
    finals: dict[int, list[float]] = {h: [] for h in horizons}
    for _ in range(paths):
        total = 0.0
        step = 0
        while step < max_h:
            start = rng.randrange(0, len(r) - block)
            for x in r[start:start + block]:
                total += x
                step += 1
                if step in finals:
                    finals[step].append(total)
                if step >= max_h:
                    break
    for h in horizons:
        vals = sorted(finals[h])
        q = {f"p{int(p * 100)}": price * math.exp(vals[min(len(vals) - 1, int(p * len(vals)))]) for p in QUANTILES}
        out.append({"horizon_days": h, "method": "bootstrap", **q,
                    "prob_up": sum(1 for v in vals if v > 0) / len(vals),
                    "prob_down_20pct": sum(1 for v in vals if v < math.log(0.8)) / len(vals)})
    return out


def forecast(closes: list[float], periods_per_year: int = 252) -> dict:
    horizons = (7, 30, 90) if periods_per_year == 365 else (5, 21, 63)
    return {
        "price": closes[-1] if closes else None,
        "lognormal": lognormal_cone(closes, horizons, periods_per_year),
        "bootstrap": bootstrap(closes, horizons),
        "note": "Ranges, not predictions. p5-p95 is where ~90% of outcomes landed under each model; "
                "real tails are fatter than either model assumes.",
    }
