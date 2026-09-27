"""Dividend safety: how likely each holding's dividend is to be cut, graded A to F.

From the company's own SEC filings (XBRL company facts) and its dividend history:
- Free-cash-flow payout: dividends paid over the last four quarters against operating cash flow
  minus capital spending. The strongest single warning sign: a company paying out more cash
  than its business brings in is funding the dividend with debt, savings or asset sales.
- Earnings payout: the same against net income.
- Debt: net debt (debt minus cash) against operating profit before depreciation (a rough
  EBITDA). Above about 3.5 times, lenders come before shareholders in a bad year.
- History: years in a row the dividend has grown, and any cut in the last three years
  (companies that cut once cut again more often than companies that never have).

Scoring starts at 100 and loses points for each warning; A is 85+, B 70+, C 55+, D 40+, F below.
The thresholds are common rules of thumb (the approach services like Simply Safe Dividends and
Morningstar describe), not a fitted model. Real estate trusts (REITs) must pay out most of their
income and are judged on funds from operations, which filings don't tag consistently: their
grade is shown with that caveat. Funds and coins aren't graded.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from . import filings, fundamentals, http

DIVIDENDS = ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends", "PaymentsOfOrdinaryDividends"]
DA = ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet", "Depreciation"]
DEBT_TOTAL = ["LongTermDebt", "DebtLongtermAndShorttermCombinedAmount", "LongTermDebtAndCapitalLeaseObligations"]
DEBT_PARTS = [["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligationsNoncurrent"],
              ["LongTermDebtCurrent", "DebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
              ["CommercialPaper", "ShortTermBorrowings"]]
CASH = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
REIT_SIC = "6798"
CACHE_SECONDS = 12 * 3600
_cache: dict[str, tuple[float, dict | None]] = {}


def ttm(facts: dict, concepts: list[str]) -> tuple[float | None, str | None]:
    """Sum of the latest four quarters (and the latest quarter's end)."""
    q = fundamentals._best(facts, concepts)
    if not q:
        return None, None
    ends = sorted(q, reverse=True)[:4]
    if len(ends) < 4 or fundamentals._days(ends[-1], ends[0]) > 300:
        return None, None
    return sum(q[e] for e in ends), ends[0]


def instant(facts: dict, concepts: list[str]) -> tuple[float | None, str | None]:
    """The latest balance-sheet value of the first concept that has one recently."""
    best = (None, None)
    for c in concepts:
        rows = [r for r in fundamentals._entries(facts, c) if r.get("end") and not r.get("start")]
        if rows:
            r = max(rows, key=lambda r: (r["end"], r.get("filed", "")))
            if best[1] is None or r["end"] > best[1]:
                best = (float(r["val"]), r["end"])
    return best


def total_debt(facts: dict) -> float | None:
    v, end = instant(facts, DEBT_TOTAL)
    parts = [instant(facts, cs) for cs in DEBT_PARTS]
    latest = max([e for _, e in parts if e] + ([end] if end else []), default=None)
    summed = sum(v2 for v2, e2 in parts if v2 is not None and e2 == latest)
    if v is not None and end == latest:
        # LongTermDebt usually includes the current portion; add short-term borrowings only.
        return v + (parts[2][0] if parts[2][0] is not None and parts[2][1] == latest else 0.0)
    return summed if latest else None


def streak(hist: list[tuple[str, float]], today: date) -> dict:
    """Years in a row the yearly total grew (complete calendar years), and cuts in the last 3 years."""
    by_year: dict[int, float] = {}
    for d, a in hist:
        by_year[int(d[:4])] = by_year.get(int(d[:4]), 0.0) + a
    years = sorted(y for y in by_year if y < today.year)
    n = 0
    for y in reversed(years):
        if y - 1 in by_year and by_year[y] > by_year[y - 1] * 1.001:
            n += 1
        else:
            break
    recent = [(d, a) for d, a in hist if (today - date.fromisoformat(d)).days <= 3 * 366]
    cut, peak = None, 0.0
    if recent:
        mid = sorted(a for _, a in recent)[len(recent) // 2]
        for d, a in recent:
            if a > mid * 2:
                continue                # a special dividend, not the regular rate
            if peak and a < peak * 0.9:
                cut = {"date": d, "from": peak, "to": a}
                peak = a                # the new rate is the reference from here on
            peak = max(peak, a)
    return {"years_growing": n, "cut": cut, "pays": bool(recent)}


def grade(m: dict) -> dict:
    score, reasons = 100, []
    fcf_p, eps_p, lev = m.get("fcf_payout"), m.get("earnings_payout"), m.get("net_debt_ebitda")
    if m.get("fcf") is not None and m["fcf"] <= 0:
        score -= 50
        reasons.append("Free cash flow over the last year was negative: the dividend isn't covered by the business.")
    elif fcf_p is not None:
        if fcf_p > 100:
            score -= 45
            reasons.append(f"Pays out {fcf_p:.0f}% of free cash flow: more than it brings in.")
        elif fcf_p > 75:
            score -= 25
            reasons.append(f"Pays out {fcf_p:.0f}% of free cash flow: little room if cash flow dips.")
        elif fcf_p > 50:
            score -= 10
            reasons.append(f"Pays out {fcf_p:.0f}% of free cash flow.")
        else:
            reasons.append(f"Pays out {fcf_p:.0f}% of free cash flow: well covered.")
    if eps_p is not None and eps_p > 100:
        score -= 10
        reasons.append(f"Dividends are {eps_p:.0f}% of profits.")
    if m.get("ebitda") is not None and m["ebitda"] <= 0 and (m.get("net_debt") or 0) > 0:
        score -= 20
        reasons.append("Operating profit is negative while carrying net debt.")
    elif lev is not None:
        if lev > 3.5:
            score -= 20
            reasons.append(f"Net debt is {lev:.1f}× a year's operating profit: heavy.")
        elif lev > 2:
            score -= 10
            reasons.append(f"Net debt is {lev:.1f}× a year's operating profit.")
    h = m.get("history") or {}
    if h.get("cut"):
        score -= 30
        reasons.append(f"Cut the dividend on {h['cut']['date']} (from {h['cut']['from']:.4g} to {h['cut']['to']:.4g} a share).")
    if (h.get("years_growing") or 0) >= 10:
        score += 5
        reasons.append(f"Raised it {h['years_growing']} years in a row.")
    elif h.get("years_growing"):
        reasons.append(f"Raised it {h['years_growing']} year{'s' if h['years_growing'] != 1 else ''} in a row.")
    score = max(0, min(100, score))
    letter = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "F"
    if fcf_p is None and m.get("fcf") is None:
        letter, reasons = "?", reasons + ["The filings don't show enough cash-flow data to grade it."]
    return {"grade": letter, "score": score, "reasons": reasons}


def assess(facts: dict, hist: list[tuple[str, float]], today: date, sic: str = "") -> dict:
    div, end = ttm(facts, DIVIDENDS)
    ocf, _ = ttm(facts, fundamentals.OCF)
    capex, _ = ttm(facts, fundamentals.CAPEX)
    net, _ = ttm(facts, fundamentals.NET)
    op, _ = ttm(facts, fundamentals.OPERATING)
    da, _ = ttm(facts, DA)
    fcf = (ocf - (capex or 0.0)) if ocf is not None else None
    debt = total_debt(facts)
    cash, _ = instant(facts, CASH)
    ebitda = (op + (da or 0.0)) if op is not None else None
    net_debt = (debt - (cash or 0.0)) if debt is not None else None
    div = abs(div) if div is not None else None
    m = {"quarter_end": end, "dividends_ttm": div, "fcf": fcf, "net_income": net, "ebitda": ebitda, "net_debt": net_debt,
         "fcf_payout": round(div / fcf * 100, 1) if div and fcf and fcf > 0 else None,
         "earnings_payout": round(div / net * 100, 1) if div and net and net > 0 else None,
         "net_debt_ebitda": round(net_debt / ebitda, 2) if net_debt is not None and ebitda and ebitda > 0 else None,
         "history": streak(hist, today), "reit": sic == REIT_SIC}
    g = grade(m)
    if m["reit"]:
        g["reasons"].append("A real estate trust: it must pay out most of its income, so high payout ratios are normal. "
                            "Judge it on funds from operations, which this grade doesn't see.")
    return dict(m, **g)


def for_symbol(sym: str, get=None, today: date | None = None) -> dict | None:
    """The grade for a dividend-paying stock; None for funds, coins, and stocks that don't pay."""
    from . import dividends
    from .providers import market, sec
    if market.asset_class(sym) != "stock":
        return None
    hit = _cache.get(sym)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    today = today or date.today()
    cik = filings.cik_of(sym)
    out = None
    if cik:
        hist = dividends.history(sym, get, years=15)
        if hist and (today - date.fromisoformat(hist[-1][0])).days <= 400:
            fetch = get or (lambda u: sec._sec_get(u, ttl=1))
            facts = fetch(fundamentals.COMPANYFACTS.format(cik=str(cik).zfill(10)))
            try:
                sic = str((get or sec._sec_get)(sec.SUBMISSIONS.format(cik=str(cik).zfill(10))).get("sic", ""))
            except (http.DataUnavailable, AttributeError):
                sic = ""
            out = dict(assess(facts, hist, today, sic), symbol=sym)
    _cache[sym] = (time.monotonic(), out)
    return out


def build(symbols: list[str], get=None) -> tuple[dict[str, dict], list[str]]:
    errors: list[str] = []

    def one(sym):
        try:
            return sym, for_symbol(sym, get)
        except (http.DataUnavailable, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{sym}: {str(exc)[:80]}")
            return sym, None
    with ThreadPoolExecutor(max_workers=3) as pool:
        got = dict(pool.map(one, symbols))
    return {s: g for s, g in got.items() if g}, errors
