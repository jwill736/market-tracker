from datetime import date, timedelta

from market_tracker import fundamentals as f
from market_tracker import holdplan


def _q_ends(n, last="2026-06-27"):
    """n quarter ends going back from `last`, ~91 days apart (newest last)."""
    d = date.fromisoformat(last)
    return [(d - timedelta(days=91 * i)).isoformat() for i in reversed(range(n))]


def _flow(values_by_end, *, ytd=False):
    """XBRL-style duration rows. ytd=True files cumulative 3/6/9/12-month figures from each
    fiscal-year start (like cash-flow statements), never a discrete Q2-Q4."""
    rows = []
    ends = sorted(values_by_end)
    if not ytd:
        for e in ends:
            start = (date.fromisoformat(e) - timedelta(days=90)).isoformat()
            rows.append({"start": start, "end": e, "val": values_by_end[e], "form": "10-Q", "filed": e})
        return rows
    for i in range(0, len(ends), 4):
        year = ends[i:i + 4]
        start = (date.fromisoformat(year[0]) - timedelta(days=90)).isoformat()
        total = 0.0
        for e in year:
            total += values_by_end[e]
            rows.append({"start": start, "end": e, "val": total, "form": "10-Q" if e != year[-1] else "10-K", "filed": e})
    return rows


def _facts(rev, op=None, ocf=None, capex=None, shares=None, gross=None):
    def unit(rows, u="USD"):
        return {"units": {u: rows}}
    g = {"RevenueFromContractWithCustomerExcludingAssessedTax": unit(_flow(rev))}
    if op:
        g["OperatingIncomeLoss"] = unit(_flow(op))
    if gross:
        g["GrossProfit"] = unit(_flow(gross))
    if ocf:
        g["NetCashProvidedByUsedInOperatingActivities"] = unit(_flow(ocf, ytd=True))
    if capex:
        g["PaymentsToAcquirePropertyPlantAndEquipment"] = unit(_flow(capex, ytd=True))
    if shares:
        g["WeightedAverageNumberOfDilutedSharesOutstanding"] = unit(_flow(shares), "shares")
    return {"facts": {"us-gaap": g}}


def test_quarterly_derives_q4_and_cash_flow_quarters_from_year_to_date():
    ends = _q_ends(8)
    vals = {e: float(i + 1) * 10 for i, e in enumerate(ends)}
    q = f.quarterly(_flow(vals, ytd=True))
    assert q == vals


def test_quarterly_prefers_latest_filing_for_restated_numbers():
    rows = [{"start": "2026-01-01", "end": "2026-03-31", "val": 100, "form": "10-Q", "filed": "2026-05-01"},
            {"start": "2026-01-01", "end": "2026-03-31", "val": 90, "form": "10-Q", "filed": "2026-08-01"}]
    assert f.quarterly(rows) == {"2026-03-31": 90.0}


def test_best_concept_is_the_one_with_the_newest_quarter():
    old = {"start": "2018-04-01", "end": "2018-06-30", "val": 5, "form": "10-K", "filed": "2018-11-05"}
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [old]}},
                                   "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": _flow({"2026-06-27": 9.0})}}}}}
    assert f._best(facts, f.REVENUE) == {"2026-06-27": 9.0}


def test_metrics_growth_margins_fcf_and_dilution():
    ends = _q_ends(8)
    rev = {e: 100.0 + i * 10 for i, e in enumerate(ends)}           # growing
    op = {e: rev[e] * 0.3 for e in ends}
    ocf = {e: 40.0 for e in ends}
    capex = {e: 10.0 for e in ends}
    shares = {e: 1000.0 * (1.02 ** i) for i, e in enumerate(ends)}
    qs = f.metrics(_facts(rev, op=op, ocf=ocf, capex=capex, shares=shares))
    latest = qs[0]
    assert latest.end == ends[-1]
    assert latest.revenue == 170.0
    assert latest.revenue_yoy == round((170 / 130 - 1) * 100, 1)
    assert latest.operating_margin == 30.0
    assert latest.fcf == 30.0 and latest.fcf_ttm == 120.0
    assert 8 < latest.shares_yoy < 8.5


def test_automatic_checks_shrinking_revenue_and_dilution():
    ends = _q_ends(8)
    rev = {e: v for e, v in zip(ends, [100, 100, 100, 100, 100, 100, 95, 90])}
    shares = {e: v for e, v in zip(ends, [100, 100, 100, 100, 110, 112, 115, 120])}
    checks = f.check(f.metrics(_facts(rev, shares=shares)))
    texts = " | ".join(c["text"] for c in checks)
    assert "Revenue below a year earlier for 2 quarters running (-5%, -10%)" in texts
    assert "share count up 20.0%" in texts
    assert all(c["level"] == "review" for c in checks)


def test_one_bad_quarter_is_not_enough():
    ends = _q_ends(8)
    rev = {e: v for e, v in zip(ends, [100] * 7 + [90])}
    assert f.check(f.metrics(_facts(rev))) == []


def test_user_rules_replace_automatic_thresholds():
    ends = _q_ends(8)
    rev = {e: v for e, v in zip(ends, [100, 100, 100, 100, 108, 108, 108, 108])}   # +8% growth
    op = {e: rev[e] * 0.15 for e in ends}
    shares = {e: v for e, v in zip(ends, [100, 100, 100, 100, 104, 104, 104, 104])}
    t = holdplan.Thesis("X", rev_growth_min=10, rev_growth_quarters=3, op_margin_min=20, dilution_max=3)
    texts = [c["text"] for c in f.check(f.metrics(_facts(rev, op=op, shares=shares)), t)]
    assert any("Your rule: revenue growth under 10% for 3 quarters" in x for x in texts)
    assert any("Your rule: operating margin at least 20%" in x for x in texts)
    assert any("Your rule: share count up 4.0%" in x for x in texts)
    # Without rules, 8% growth and 4% dilution are fine.
    assert f.check(f.metrics(_facts(rev, op=op, shares=shares))) == []


def test_fcf_turning_negative():
    ends = _q_ends(8)
    rev = {e: 100.0 for e in ends}
    ocf = {e: v for e, v in zip(ends, [30, 30, 30, 30, -10, -10, -10, -10])}
    capex = {e: 5.0 for e in ends}
    checks = f.check(f.metrics(_facts(rev, ocf=ocf, capex=capex)))
    assert any("Free cash flow over the last 4 quarters turned negative" in c["text"] for c in checks)


def test_margin_drop_is_only_a_note():
    ends = _q_ends(8)
    rev = {e: 100.0 for e in ends}
    op = {e: v for e, v in zip(ends, [30, 30, 30, 30, 30, 30, 30, 20])}
    checks = f.check(f.metrics(_facts(rev, op=op)))
    assert checks == [{"level": "info", "text": "Operating margin 20.0%, down 10.0 points on a year earlier"}]


def test_holdplan_raises_review_from_fundamentals():
    pos = {"symbol": "ABC", "quantity": 10, "price": 50.0, "market_value": 500.0, "unrealized_pct": 5.0}
    fund = {"quarters": [{"end": "2026-06-27"}], "line": "Quarter to 2026-06-27: revenue -10% y/y",
            "checks": [{"level": "review", "text": "Revenue below a year earlier for 2 quarters running (-5%, -10%)"}]}
    row = holdplan.check_holding(pos, 5000.0, None, [], date(2026, 9, 25), 0.2, fund)
    assert row.verdict == "Review"
    assert row.fundamentals["line"].startswith("Quarter to")
    assert any(t.kind == "fundamentals" for t in row.triggers)


def test_build_skips_funds_and_uses_injected_fetch(monkeypatch):
    from market_tracker.providers import sec

    class TM:
        def cik_for(self, s):
            return "320193" if s == "AAPL" else None
    monkeypatch.setattr(sec, "ticker_map", lambda: TM())
    f.clear_cache()
    ends = _q_ends(8)
    facts = _facts({e: 100.0 for e in ends})
    got, errors = f.build(["AAPL", "VTI", "BTC-USD"], get=lambda url: facts)
    assert list(got) == ["AAPL"] and not errors
    assert got["AAPL"]["line"].startswith("Quarter to 2026-06-27")
