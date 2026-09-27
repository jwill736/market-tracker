"""Hidden style bets: what your portfolio is really exposed to.

Your holdings' daily returns, weighted as you hold them today, regressed on the six standard
factors from Kenneth French's data library (free, updated monthly):
- Market: how much the portfolio moves with the whole US market (1.0 = one for one).
- Size (SMB): positive leans to small companies, negative to large ones.
- Value (HML): positive leans to cheap stocks (low price to book), negative to growth stocks.
- Profitability (RMW): positive leans to highly profitable companies, negative to weak ones.
- Investment (CMA): positive leans to companies that invest conservatively, negative to ones
  growing their assets fast.
- Momentum: positive leans to recent winners, negative to recent losers.

Fama & French (2015) for the first five, Carhart (1997) for momentum. A loading only counts as
a tilt when it's both sizable (0.15 or more) and statistically clear (t of 2 or more). The
"alpha" left over is shown for completeness: two years of daily data can't tell skill from
luck, so it's never presented as either.

Coins aren't in the model (the factors are US stocks), so their share is reported and left out.
The factor file lags by a month or two, so the window ends where French's data ends.
"""

from __future__ import annotations

import csv
import io
import math
import time
import zipfile

from . import http

FF5_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
MOM_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip"
FACTORS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]
NAMES = {"Mkt-RF": "Market", "SMB": "Size", "HML": "Value", "RMW": "Profitability", "CMA": "Investment", "Mom": "Momentum"}
TILT = {  # (positive, negative)
    "SMB": ("small companies", "large companies"),
    "HML": ("cheap (value) stocks", "expensive (growth) stocks"),
    "RMW": ("highly profitable companies", "less profitable companies"),
    "CMA": ("companies that invest conservatively", "companies growing their assets fast"),
    "Mom": ("recent winners", "recent losers"),
}
MIN_BETA, MIN_T = 0.15, 2.0
WINDOW = 504            # about two years of trading days
CACHE_SECONDS = 24 * 3600
_cache: dict[str, tuple[float, dict]] = {}


def parse_french(text: str) -> dict[str, dict[str, float]]:
    """{YYYY-MM-DD: {factor: decimal return}} from one of French's daily CSV files."""
    out: dict[str, dict[str, float]] = {}
    header: list[str] | None = None
    for row in csv.reader(io.StringIO(text)):
        cells = [c.strip() for c in row]
        if not any(cells):
            continue
        if len(cells) > 1 and not cells[0].isdigit():
            header = cells[1:]          # the column names (the later annual tables repeat them)
            continue
        if header and len(cells[0]) == 8 and cells[0].isdigit():     # daily rows only: YYYYMMDD
            day = f"{cells[0][:4]}-{cells[0][4:6]}-{cells[0][6:]}"
            try:
                out[day] = {h: float(v) / 100 for h, v in zip(header, cells[1:]) if v not in ("", "-99.99", "-999")}
            except ValueError:
                continue
    return out


def _unzip_text(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        return z.read(name).decode("latin-1")


def load(fetch=None) -> dict[str, dict[str, float]]:
    """The six factors and the risk-free rate by day (cached a day)."""
    hit = _cache.get("ff")
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    fetch = fetch or (lambda u: http.get_bytes(u, headers={"User-Agent": "Mozilla/5.0 (market-tracker)"}))
    ff5 = parse_french(_unzip_text(fetch(FF5_URL)))
    mom = parse_french(_unzip_text(fetch(MOM_URL)))
    data = {}
    for d, row in ff5.items():
        m = mom.get(d, {})
        mv = m.get("Mom") if "Mom" in m else next(iter(m.values()), None)
        if mv is None or not all(k in row for k in ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF")):
            continue
        data[d] = dict(row, Mom=mv)
    if not data:
        raise http.DataUnavailable("Kenneth French's factor files had no daily rows")
    _cache["ff"] = (time.time(), data)
    return data


# ------------------------------------------------------------------ regression

def _solve(a: list[list[float]]) -> list[list[float]] | None:
    """Inverse of a small square matrix (Gauss-Jordan with partial pivoting)."""
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-18:
            return None
        m[col], m[piv] = m[piv], m[col]
        p = m[col][col]
        m[col] = [v / p for v in m[col]]
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col]
                m[r] = [v - f * w for v, w in zip(m[r], m[col])]
    return [row[n:] for row in m]


def ols(y: list[float], xs: list[list[float]]) -> dict | None:
    """y = a + sum(b_k x_k): coefficients, t-stats and R squared. xs: one row per observation."""
    n, k = len(y), len(xs[0]) + 1 if xs else 1
    if n <= k + 10:
        return None
    X = [[1.0] + row for row in xs]
    xtx = [[sum(X[i][p] * X[i][q] for i in range(n)) for q in range(k)] for p in range(k)]
    inv = _solve(xtx)
    if inv is None:
        return None
    xty = [sum(X[i][p] * y[i] for i in range(n)) for p in range(k)]
    b = [sum(inv[p][q] * xty[q] for q in range(k)) for p in range(k)]
    resid = [y[i] - sum(b[p] * X[i][p] for p in range(k)) for i in range(n)]
    sse = sum(r * r for r in resid)
    my = sum(y) / n
    sst = sum((v - my) ** 2 for v in y)
    s2 = sse / (n - k)
    se = [math.sqrt(max(inv[p][p] * s2, 0.0)) for p in range(k)]
    return {"coef": b, "t": [b[p] / se[p] if se[p] else 0.0 for p in range(k)], "r2": 1 - sse / sst if sst else 0.0, "n": n}


def _returns(bars: list[tuple[str, float]]) -> dict[str, float]:
    return {d1: c1 / c0 - 1 for (_, c0), (d1, c1) in zip(bars, bars[1:]) if c0 > 0}


def portfolio_returns(weights: dict[str, float], rets: dict[str, dict[str, float]], days: list[str]) -> dict[str, float]:
    """Each day, the holdings that traded, weighted as held today (renormalised over those present)."""
    out = {}
    for d in days:
        num = den = 0.0
        for s, w in weights.items():
            r = rets.get(s, {}).get(d)
            if r is not None:
                num += w * r
                den += w
        if den >= 0.5:          # at least half the portfolio has a price that day
            out[d] = num / den
    return out


def exposure(daily: dict[str, float], ff: dict[str, dict[str, float]]) -> dict | None:
    days = sorted(d for d in daily if d in ff)[-WINDOW:]
    if len(days) < 120:
        return None
    y = [daily[d] - ff[d]["RF"] for d in days]
    xs = [[ff[d][f] for f in FACTORS] for d in days]
    fit = ols(y, xs)
    if not fit:
        return None
    loadings = []
    for i, f in enumerate(FACTORS, start=1):
        beta, t = fit["coef"][i], fit["t"][i]
        loadings.append({"factor": f, "name": NAMES[f], "beta": round(beta, 2), "t": round(t, 1), "text": _explain(f, beta, t)})
    return {"start": days[0], "end": days[-1], "days": len(days), "r2": round(fit["r2"], 2), "loadings": loadings,
            "alpha_annual": round(fit["coef"][0] * 252 * 100, 1), "alpha_t": round(fit["t"][0], 1)}


def _explain(f: str, beta: float, t: float) -> str:
    if f == "Mkt-RF":
        if beta < 0:
            return "Has tended to move against the market."
        if beta < 0.3:
            return "Barely moves with the market."
        if abs(beta - 1) < 0.1:
            return "Moves about one for one with the market."
        return (f"Moves about {abs(beta - 1) * 100:.0f}% {'more' if beta > 1 else 'less'} than the market: "
                f"a 10% market fall has typically meant about {beta * 10:.0f}% for you.")
    if abs(beta) < MIN_BETA or abs(t) < MIN_T:
        return "No clear tilt."
    pos, neg = TILT[f]
    return f"Tilted toward {pos if beta > 0 else neg}."


def build(positions: list[dict], history_fn, ff: dict | None = None) -> dict:
    """positions: valued [{symbol, market_value}]. history_fn(symbol) -> [(date, close)]."""
    from .providers import market
    ff = ff or load()
    total = sum(p.get("market_value") or 0 for p in positions)
    stocks = {p["symbol"]: p["market_value"] for p in positions if (p.get("market_value") or 0) > 0 and market.asset_class(p["symbol"]) != "crypto"}
    crypto_value = total - sum(stocks.values())
    rets, missing = {}, []
    for s in stocks:
        try:
            rets[s] = _returns(history_fn(s))
        except Exception:  # noqa: BLE001 - a holding with no history is left out and named
            rets[s] = {}
        if not rets[s]:
            missing.append(s)
    sv = sum(v for s, v in stocks.items() if rets[s])
    weights = {s: v / sv for s, v in stocks.items() if rets[s]} if sv else {}
    days = sorted(ff)
    out = {"as_of": days[-1] if days else None, "crypto_share": round(crypto_value / total * 100, 1) if total else 0.0,
           "missing": missing, "holdings": len(weights)}
    if not weights:
        return dict(out, empty=True)
    mine = exposure(portfolio_returns(weights, rets, days), ff)
    ref = None
    try:
        ref = exposure(_returns(history_fn("VOO")), ff)
    except Exception:  # noqa: BLE001 - the reference is optional
        ref = None
    if not mine:
        return dict(out, empty=True, note="Not enough overlapping days between your holdings' prices and the factor data.")
    tilts = [ld for ld in mine["loadings"] if ld["factor"] != "Mkt-RF" and abs(ld["beta"]) >= MIN_BETA and abs(ld["t"]) >= MIN_T]
    return dict(out, **mine, reference=ref, tilts=[t["text"] for t in tilts], summary=summary(mine, tilts))


def summary(r: dict, tilts: list[dict]) -> str:
    mkt = r["loadings"][0]["beta"]
    parts = [f"Market sensitivity {mkt:.2f}"]
    parts += [t["text"].rstrip(".").replace("Tilted toward", "tilted toward") for t in tilts] or ["no strong style tilts beyond the market"]
    out = "; ".join(parts) + f". The factors explain {r['r2'] * 100:.0f}% of your daily moves."
    if r["r2"] < 0.5:
        out += " That's low: most of what moves your portfolio is specific to a few holdings, so the tilts above say little."
    return out
