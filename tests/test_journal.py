import random
from datetime import date, timedelta

import pytest

from market_tracker import journal


@pytest.fixture
def jpath(tmp_path, monkeypatch):
    path = str(tmp_path / "journal.csv")
    monkeypatch.setenv("MT_JOURNAL_PATH", path)
    return path


def _entry(d, sym, score, price=100.0, cls="stock", **comps):
    return {"date": d, "symbol": sym, "asset_class": cls, "price": price, "score": score,
            "label": "Bullish" if score >= 15 else "Bearish" if score <= -15 else "Neutral",
            "coverage": 0.5, **{c: comps.get(c) for c in journal.COMPONENTS},
            "version": journal.SCORE_VERSION}


def test_record_upserts_and_roundtrips(jpath):
    journal.record([_entry("2026-01-02", "AAPL", 20, trend=50.0), _entry("2026-01-02", "MSFT", -10)])
    journal.record([_entry("2026-01-02", "AAPL", 30)])  # same key replaces
    rows = journal.load()
    assert [(r["symbol"], r["score"]) for r in rows] == [("AAPL", 30.0), ("MSFT", -10.0)]
    assert rows[1]["trend"] is None


def test_entry_from_analysis():
    a = {"symbol": "BTC-USD", "asset_class": "crypto", "quote": {"price": 60000.0},
         "signal": {"score": 12.5, "label": "Neutral", "coverage": 0.5,
                    "components": {"trend": {"score": 50.0}, "momentum": {"score": -25.0},
                                   "smart_money": None, "insider": None, "news": None}}}
    e = journal.entry_from_analysis(a, on=date(2026, 3, 1))
    assert e["date"] == "2026-03-01" and e["trend"] == 50.0 and e["smart_money"] is None
    assert journal.entry_from_analysis({"signal": None, "quote": None, "symbol": "X"}) is None


def test_spearman():
    assert journal.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert journal.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert journal.spearman([1, 1, 2], [5, 5, 6]) == pytest.approx(1.0)  # ties
    assert journal.spearman([1, 2], [1, 2]) is None


def test_forward_return_and_crypto_scaling():
    hist = [("2026-01-01", 100.0), ("2026-01-02", 110.0), ("2026-01-05", 121.0)]
    assert journal.forward_return(hist, "2026-01-01", 2) == pytest.approx(0.21)
    assert journal.forward_return(hist, "2025-12-31", 1) == pytest.approx(0.10)  # first bar on/after
    assert journal.forward_return(hist, "2026-01-02", 5) is None  # not matured
    assert journal.bars_for(21, "crypto") == 30 and journal.bars_for(21, "stock") == 21


def _synthetic(skill: float, n_symbols=20, n_days=300, seed=0):
    """Scores that predict 21-day returns with the given skill (0 = pure noise)."""
    rng = random.Random(seed)
    start = date(2025, 1, 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(n_days)]
    rows, hists = [], {}
    for s in range(n_symbols):
        sym = f"S{s}"
        prices = [100.0]
        drifts = []
        for _ in range(n_days):
            drift = rng.gauss(0, 0.002)
            drifts.append(drift)
            prices.append(prices[-1] * (1 + drift + rng.gauss(0, 0.003)))
        hists[sym] = list(zip(dates, prices))
        for i in range(0, n_days - 30, 5):
            future = sum(drifts[i:i + 21])
            score = max(-100, min(100, skill * future * 2000 + (1 - skill) * rng.gauss(0, 40)))
            rows.append(_entry(dates[i], sym, score, trend=score))
    return rows, hists


def test_evaluate_detects_real_skill_and_its_absence():
    rows, hists = _synthetic(skill=0.9)
    rep = journal.evaluate(rows, hists.__getitem__, horizons=(21,))
    h = rep["horizons"][0]
    assert h["ic"] > 0.3 and h["component_ic"]["trend"] > 0.3
    assert h["bull_minus_bear"] > 0
    assert rep["verdict"].startswith("Encouraging")

    # Zero skill must not be reported as an edge in either direction (seed 3 happens to
    # produce IC -0.07, which is inside the noise band at this sample size).
    rows, hists = _synthetic(skill=0.0, seed=3)
    rep = journal.evaluate(rows, hists.__getitem__, horizons=(21,))
    h = rep["horizons"][0]
    assert abs(h["ic"]) < 2 * h["ic_se"]
    assert rep["verdict"].startswith("No detectable edge")


def test_verdict_requires_significance():
    def rep(ic, n):
        return {"horizons": [{"ic": ic, "effective_n": n, "ic_se": journal.ic_standard_error(n)}]}
    assert journal.verdict(rep(0.08, 60)).startswith("No detectable edge")   # se 0.13
    assert journal.verdict(rep(0.08, 2000)).startswith("Small but real edge")  # se 0.022
    assert journal.verdict(rep(-0.3, 100)).startswith("Wrong-way")
    assert journal.verdict(rep(0.3, 100)).startswith("Encouraging")


def test_verdict_refuses_to_judge_small_samples():
    rows, hists = _synthetic(skill=0.9, n_symbols=2, n_days=60)
    rep = journal.evaluate(rows, hists.__getitem__, horizons=(21,))
    assert rep["verdict"].startswith("Too early")


def test_evaluate_reports_history_errors():
    from market_tracker import http

    def boom(sym):
        raise http.DataUnavailable("down")

    rep = journal.evaluate([_entry("2026-01-02", "AAPL", 20)], boom, horizons=(21,))
    assert rep["errors"] == ["AAPL: down"] and rep["horizons"][0]["n"] == 0


def test_report_markdown_renders():
    rows, hists = _synthetic(skill=0.5)
    md = journal.report_markdown(journal.evaluate(rows, hists.__getitem__))
    assert "## Signal track record" in md and "| Label |" in md


def test_merge_csv_text(jpath):
    journal.record([_entry("2026-01-02", "AAPL", 20)])
    remote = "date,symbol,asset_class,price,score,label,coverage,trend,momentum,smart_money,insider,news\n" \
             "2026-01-03,MSFT,stock,400,5,Neutral,0.5,,,,,\n"
    journal.merge_csv_text(remote)
    assert [r["symbol"] for r in journal.load()] == ["AAPL", "MSFT"]


def test_empty_sec_user_agent_falls_back_to_default(monkeypatch):
    from market_tracker.config import Settings
    monkeypatch.setenv("SEC_USER_AGENT", "")
    assert Settings().sec_user_agent == "market-tracker contact@example.com"
    monkeypatch.setenv("SEC_USER_AGENT", "app me@x.com")
    assert Settings().sec_user_agent == "app me@x.com"


def test_version_filtering(jpath):
    old = dict(_entry("2026-09-24", "AAPL", 80), version="1")
    new = dict(_entry("2026-09-25", "AAPL", 20), version=journal.SCORE_VERSION)
    journal.record([old, new])
    rows = journal.load()
    assert [r["version"] for r in rows] == ["1", journal.SCORE_VERSION]
    hist = [("2026-09-20", 100.0)] + [(f"2026-10-{d:02d}", 100.0 + d) for d in range(1, 31)]
    rep = journal.evaluate(rows, lambda s: hist, horizons=(5,))
    assert rep["entries"] == 1 and rep["excluded_other_versions"] == 1
    assert journal.evaluate(rows, lambda s: hist, horizons=(5,), version=None)["entries"] == 2
    assert "1 entries from older score versions excluded" in journal.report_markdown(rep)


def test_unversioned_rows_load_as_version_1(jpath):
    header = "date,symbol,asset_class,price,score,label,coverage,trend,momentum,smart_money,insider,news\n"
    with open(jpath, "w") as fh:
        fh.write(header + "2026-09-24,AAPL,stock,335.92,78.0,Strong bullish,0.65,100.0,72.0,,,51.4\n")
    assert journal.load()[0]["version"] == "1"
    journal.record([dict(_entry("2026-09-25", "MSFT", 5), version=journal.SCORE_VERSION)])
    with open(jpath) as fh:
        assert fh.readline().strip().endswith(",version")  # header upgraded on rewrite


def test_entry_from_analysis_stamps_current_version():
    a = {"symbol": "AAPL", "asset_class": "stock", "quote": {"price": 1.0},
         "signal": {"score": 1, "label": "Neutral", "coverage": 1,
                    "components": {c: None for c in journal.COMPONENTS}}}
    assert journal.entry_from_analysis(a)["version"] == journal.SCORE_VERSION
