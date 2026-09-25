"""Fundamentals tripwires: "I'm wrong if" checks from the company's own SEC filings.

Every US-listed company files its financials in machine-readable form (XBRL), and the SEC
serves the whole history for one company as a single file (companyfacts). From it, per quarter:
revenue and its growth on the same quarter a year earlier, gross and operating margin, diluted
EPS, free cash flow (operating cash flow minus capital spending) and the share count.

Cash-flow numbers are filed year-to-date (3, 6, 9 and 12 months), and the fourth quarter is
never filed on its own, so quarters are derived by subtracting consecutive year-to-date figures
that share a start date. Restated figures replace the originals (latest filing wins).

Automatic checks, for every stock you hold (funds and crypto have no filings):
- Revenue lower than a year earlier for 2 quarters running: Review.
- Share count up more than 10% in a year (your slice is being diluted): Review.
- Free cash flow over the last 4 quarters turned negative after being positive: Review.
- Operating margin down more than 5 points on a year earlier: a note.

Your own rules (set in the holding's thesis) replace the automatic thresholds: "revenue growth
below 10% for 2 quarters", "operating margin below 20%", "dilution above 3%", "free cash flow
must stay positive".
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date

from . import http

COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
CACHE_SECONDS = 12 * 3600

REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
           "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"]
GROSS = ["GrossProfit"]
OPERATING = ["OperatingIncomeLoss"]
NET = ["NetIncomeLoss", "ProfitLoss"]
EPS = ["EarningsPerShareDiluted", "EarningsPerShareBasic"]
OCF = ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]
SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding"]

AUTO_DILUTION = 10.0
AUTO_MARGIN_DROP = 5.0
AUTO_SHRINK_QUARTERS = 2


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _entries(facts: dict, concept: str, unit_prefix: str = "USD") -> list[dict]:
    node = ((facts.get("facts") or {}).get("us-gaap") or {}).get(concept) or {}
    for unit, rows in (node.get("units") or {}).items():
        if unit.startswith(unit_prefix):
            return [r for r in rows if r.get("form", "").startswith(("10-Q", "10-K", "20-F", "40-F")) and "val" in r]
    return []


def quarterly(rows: list[dict]) -> dict[str, float]:
    """{quarter end: value} for a flow item (revenue, cash flow...), from discrete quarters and
    from differences of year-to-date figures (which gives Q4 and the cash-flow quarters)."""
    latest: dict[tuple[str, str], tuple[str, float]] = {}
    for r in rows:
        if not r.get("start") or not r.get("end"):
            continue
        k = (r["start"], r["end"])
        if k not in latest or r.get("filed", "") >= latest[k][0]:
            latest[k] = (r.get("filed", ""), float(r["val"]))
    out: dict[str, float] = {}
    by_start: dict[str, list[tuple[str, float]]] = {}
    for (start, end), (_, val) in latest.items():
        if 70 <= _days(start, end) <= 110:
            out[end] = val
        by_start.setdefault(start, []).append((end, val))
    for start, spans in by_start.items():
        spans.sort()
        for (e1, v1), (e2, v2) in zip(spans, spans[1:]):
            if e2 not in out and 70 <= _days(e1, e2) <= 110 and _days(start, e1) >= 70:
                out[e2] = v2 - v1
    return out


def _best(facts: dict, concepts: list[str], unit: str = "USD") -> dict[str, float]:
    """The concept with the most recent quarter (companies switch tags over the years)."""
    best: dict[str, float] = {}
    for c in concepts:
        q = quarterly(_entries(facts, c, unit))
        if q and (not best or max(q) > max(best)):
            best = q
    return best


def _year_ago(series: dict[str, float], end: str) -> float | None:
    for e, v in series.items():
        if 350 <= _days(e, end) <= 380:
            return v
    return None


def _pct(a: float | None, b: float | None) -> float | None:
    return round((a / b - 1) * 100, 1) if a is not None and b not in (None, 0) and b > 0 else None


@dataclass
class Quarter:
    end: str
    revenue: float | None
    revenue_yoy: float | None
    gross_margin: float | None
    operating_margin: float | None
    operating_margin_yoy: float | None      # change in points
    eps: float | None
    eps_yoy: float | None
    fcf: float | None
    fcf_ttm: float | None
    shares: float | None
    shares_yoy: float | None


def metrics(facts: dict, keep: int = 8) -> list[Quarter]:
    """The last `keep` quarters, newest first."""
    rev = _best(facts, REVENUE)
    if not rev:
        return []
    gross, op = _best(facts, GROSS), _best(facts, OPERATING)
    eps, ocf, capex = _best(facts, EPS, "USD/shares"), _best(facts, OCF), _best(facts, CAPEX)
    shares = _best(facts, SHARES, "shares")
    fcf = {e: ocf[e] - capex.get(e, 0.0) for e in ocf}
    ends = sorted(rev, reverse=True)

    def margin(series, e):
        return round(series[e] / rev[e] * 100, 1) if e in series and rev.get(e) else None

    def ttm(e):
        prior = sorted((x for x in fcf if 0 <= _days(x, e) <= 290), reverse=True)[:4]
        return sum(fcf[x] for x in prior) if len(prior) == 4 else None

    out = []
    for e in ends[:keep]:
        om = margin(op, e)
        ya = next((x for x in rev if 350 <= _days(x, e) <= 380), None)
        om_ya = margin(op, ya) if ya else None
        out.append(Quarter(
            end=e, revenue=rev[e], revenue_yoy=_pct(rev[e], _year_ago(rev, e)),
            gross_margin=margin(gross, e), operating_margin=om,
            operating_margin_yoy=round(om - om_ya, 1) if om is not None and om_ya is not None else None,
            eps=eps.get(e), eps_yoy=_pct(eps.get(e), _year_ago(eps, e)),
            fcf=fcf.get(e), fcf_ttm=ttm(e),
            shares=shares.get(e), shares_yoy=_pct(shares.get(e), _year_ago(shares, e))))
    return out


# ------------------------------------------------------------------ the checks

def _find_ya_ttm(qs: list[Quarter]) -> float | None:
    """Trailing free cash flow as of about a year before the latest quarter."""
    if not qs:
        return None
    for q in qs[1:]:
        if 330 <= _days(q.end, qs[0].end) <= 400:
            return q.fcf_ttm
    return None


def check(qs: list[Quarter], thesis=None) -> list[dict]:
    """[{level: review|info, text}] from the automatic checks and the thesis's own rules."""
    if not qs:
        return []
    out: list[dict] = []
    t = thesis
    latest = qs[0]
    label = f"quarter to {latest.end}"

    want = getattr(t, "rev_growth_min", None)
    n = int(getattr(t, "rev_growth_quarters", None) or AUTO_SHRINK_QUARTERS)
    floor = want if want is not None else 0.0
    growth = [q.revenue_yoy for q in qs[:n]]
    if len(growth) == n and all(g is not None and g < floor for g in growth):
        seq = ", ".join(f"{g:+.0f}%" for g in reversed(growth))
        if want is None:
            out.append({"level": "review", "text": f"Revenue below a year earlier for {n} quarters running ({seq})"})
        else:
            out.append({"level": "review", "text": f"Your rule: revenue growth under {want:g}% for {n} quarters. It was {seq}"})

    om_min = getattr(t, "op_margin_min", None)
    if om_min is not None and latest.operating_margin is not None and latest.operating_margin < om_min:
        out.append({"level": "review", "text": f"Your rule: operating margin at least {om_min:g}%. It's {latest.operating_margin:.1f}% ({label})"})
    elif latest.operating_margin_yoy is not None and latest.operating_margin_yoy <= -AUTO_MARGIN_DROP:
        out.append({"level": "info", "text": f"Operating margin {latest.operating_margin:.1f}%, down "
                                             f"{abs(latest.operating_margin_yoy):.1f} points on a year earlier"})

    dil = getattr(t, "dilution_max", None)
    limit = dil if dil is not None else AUTO_DILUTION
    if latest.shares_yoy is not None and latest.shares_yoy > limit:
        who = "Your rule" if dil is not None else "Dilution"
        out.append({"level": "review", "text": f"{who}: share count up {latest.shares_yoy:.1f}% in a year "
                                               f"(over {limit:g}%), so each share owns less of the company"})

    ya = _find_ya_ttm(qs)
    if latest.fcf_ttm is not None and latest.fcf_ttm < 0:
        if getattr(t, "fcf_positive", False):
            out.append({"level": "review", "text": f"Your rule: free cash flow stays positive. Last 4 quarters: {_money(latest.fcf_ttm)}"})
        elif ya is not None and ya > 0:
            out.append({"level": "review", "text": f"Free cash flow over the last 4 quarters turned negative ({_money(latest.fcf_ttm)}; "
                                                   f"a year earlier {_money(ya)})"})
    return out


def _money(x: float) -> str:
    sign = "-" if x < 0 else ""
    x = abs(x)
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= div:
            return f"{sign}${x / div:,.1f}{unit}"
    return f"{sign}${x:,.0f}"


def summary_line(qs: list[Quarter]) -> str:
    if not qs:
        return ""
    q = qs[0]
    bits = []
    if q.revenue_yoy is not None:
        bits.append(f"revenue {q.revenue_yoy:+.0f}% y/y")
    if q.operating_margin is not None:
        bits.append(f"operating margin {q.operating_margin:.0f}%")
    if q.fcf_ttm is not None:
        bits.append(f"free cash flow {_money(q.fcf_ttm)} (4 qtrs)")
    if q.shares_yoy is not None:
        bits.append(f"shares {q.shares_yoy:+.1f}% y/y")
    return f"Quarter to {q.end}: " + ", ".join(bits)


# ------------------------------------------------------------------ fetching

_cache: dict[str, tuple[float, list[Quarter]]] = {}


def for_symbol(sym: str, get=None) -> list[Quarter]:
    """Quarterly metrics for a stock; [] for funds, crypto and anything without SEC filings."""
    from .providers import sec
    hit = _cache.get(sym)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    cik = sec.ticker_map().cik_for(sym)
    if not cik:
        qs: list[Quarter] = []
    else:
        url = COMPANYFACTS.format(cik=str(cik).zfill(10))
        # The file is several MB; keep the few numbers we need, not the file.
        facts = get(url) if get else sec._sec_get(url, ttl=1)
        qs = metrics(facts)
    _cache[sym] = (time.monotonic(), qs)
    return qs


def build(symbols: list[str], theses: dict | None = None, get=None) -> tuple[dict[str, dict], list[str]]:
    """{symbol: {quarters, checks, line}} for the stocks among `symbols`, and any errors."""
    from .providers import market
    errors: list[str] = []
    stocks = [s for s in symbols if market.asset_class(s) == "stock"]

    def one(sym):
        try:
            return sym, for_symbol(sym, get)
        except (http.DataUnavailable, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{sym} financials: {str(exc)[:80]}")
            return sym, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        got = dict(pool.map(one, stocks))
    out = {}
    for sym, qs in got.items():
        if qs:
            out[sym] = {"quarters": [asdict(q) for q in qs], "checks": check(qs, (theses or {}).get(sym)),
                        "line": summary_line(qs)}
    return out, errors


def clear_cache() -> None:
    _cache.clear()
