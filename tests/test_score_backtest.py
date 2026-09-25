import io
import random
import zipfile
from datetime import date, timedelta

import pytest

from market_tracker import history, score_backtest as sb
from market_tracker.providers import sec
from tests.conftest import synthetic_closes
from tests.test_providers import _move, _report


def _calendar(start="2019-01-01", n=600):
    d0 = date.fromisoformat(start)
    days = [d0 + timedelta(days=i) for i in range(n * 2)]
    return [d.isoformat() for d in days if d.weekday() < 5][:n]


def test_month_ends_and_closes_until():
    cal = ["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-28", "2020-03-02"]
    assert sb.month_ends(cal, "2020-01-01", "2020-12-31") == ["2020-01-31", "2020-02-28", "2020-03-02"]
    hist = [(d, float(i)) for i, d in enumerate(cal)]
    assert sb.closes_until(hist, "2020-02-15") == [0.0, 1.0, 2.0]
    assert sb.closes_until(hist, "2019-12-31") == []


def _filing(period, filed, holdings):
    return sec.Filing13F("buffett", "BERKSHIRE HATHAWAY INC", period, filed, f"acc-{period}", holdings)


def test_filings_as_of_uses_filing_date_not_period():
    f1 = _filing("2019-12-31", "2020-02-14", [])
    f2 = _filing("2020-03-31", "2020-05-15", [])
    assert sb.filings_as_of([f1, f2], "2020-04-30") == (f1, None)  # Q1 filing not public yet
    assert sb.filings_as_of([f1, f2], "2020-05-15") == (f2, f1)
    assert sb.filings_as_of([f1, f2], "2020-01-01") == (None, None)


def test_index_matches_live_smart_money_logic():
    reports = [_report("buffett", "Warren Buffett", {"new": [_move("XYZ")]}, [_move("XYZ", 6.0)]),
               _report("ackman", "Bill Ackman", {"added": [_move("XYZ")], "exited": [_move("ABC")]}),
               _report("simons", "Jim Simons", {"exited": [_move("XYZ")]}, [_move("ABC", 1.0)])]
    indexed = [(r, sb.index_report(r)) for r in reports]
    for ticker in ("XYZ", "ABC", "NONE"):
        assert sb.smart_money_from_index(ticker, indexed) == sec.smart_money_for_ticker(ticker, reports)


def _data_with_events():
    cal = _calendar("2019-01-01", 420)
    prices = {f"S{i}": list(zip(cal, synthetic_closes(len(cal), seed=i))) for i in range(12)}
    H = sec.Holding
    q4 = _filing("2019-12-31", "2020-02-14", [H("OLD CO", "c1", "COM", 100, 10, ticker="S1")])
    q1 = _filing("2020-03-31", "2020-05-15", [H("OLD CO", "c1", "COM", 100, 10, ticker="S1"),
                                              H("NEW CO", "c0", "COM", 900, 50, ticker="S0")])
    buy = sec.InsiderTrade("CEO", "CEO", "2020-04-20", "P", 1000, 10.0, True, 5000, filed="2020-05-05")
    return sb.HistoricalData(prices=prices, filings={"buffett": [q4, q1]}, insiders={"S0": [buy], "S1": []})


def test_score_history_is_point_in_time():
    data = _data_with_events()
    rows = {(r["date"], r["symbol"]): r for r in sb.score_history(data, ["2020-04-30", "2020-05-29"])}
    before, after = rows[("2020-04-30", "S0")], rows[("2020-05-29", "S0")]
    # Q1 13F (S0 new position) was filed 2020-05-15: invisible on 04-30, visible on 05-29.
    assert before["smart_money"] == 0.0 and after["smart_money"] > 0
    # Insider buy traded 04-20 but filed 05-05: only counts from the filing date.
    assert before["insider"] == 0.0 and after["insider"] == 50.0
    assert rows[("2020-05-29", "S5")]["insider"] is None  # no insider data loaded for S5
    assert before["fwd_21"] is not None


def _rows_with_skill(skill, n_dates=60, n_syms=30, seed=0):
    rng = random.Random(seed)
    rows = []
    for d in range(n_dates):
        day = (date(2016, 1, 31) + timedelta(days=30 * d)).isoformat()
        for s in range(n_syms):
            ret = rng.gauss(0, 0.05)
            score = skill * ret * 1000 + (1 - skill) * rng.gauss(0, 50)
            rows.append({"date": day, "symbol": f"S{s}", "score": score, "trend": score, "momentum": rng.gauss(0, 50),
                         "smart_money": rng.choice([None, -50.0, 0.0, 50.0]), "insider": rng.choice([0.0, 100.0]),
                         "fwd_21": ret, "fwd_63": ret})
    return rows


def test_evaluate_detects_skill_and_its_absence():
    good = sb.evaluate(_rows_with_skill(0.8))
    ic = good["horizons"][21]["ic"]["score"]
    assert ic["mean"] > 0.3 and ic["t"] > 5
    assert good["verdicts"]["score"].startswith("works")
    assert good["horizons"][21]["top_minus_bottom_quintile"]["mean"] > 0
    assert good["horizons"][63]["dates_used"] == 20  # every third month: no overlapping windows
    noise = sb.evaluate(_rows_with_skill(0.0, seed=4))
    assert abs(noise["horizons"][21]["ic"]["momentum"]["t"]) < 2.5
    md = sb.report_markdown(good)
    assert "Composite score (without news)" in md and "| Mean IC |" in md


def test_constant_component_on_a_date_is_skipped():
    rows = _rows_with_skill(0.5, n_dates=3)
    for r in rows:
        r["insider"] = 0.0
    res = sb.evaluate(rows)
    assert res["horizons"][21]["ic"]["insider"]["n"] == 0


# ------------------------------------------------------------------ loaders (no network)

SUB_COLS = "ACCESSION_NUMBER\tFILING_DATE\tPERIOD_OF_REPORT\tDATE_OF_ORIG_SUB\tNO_SECURITIES_OWNED\tNOT_SUBJECT_SEC16\tFORM3_HOLDINGS_REPORTED\tFORM4_TRANS_REPORTED\tDOCUMENT_TYPE\tISSUERCIK\tISSUERNAME\tISSUERTRADINGSYMBOL\tREMARKS\tAFF10B5ONE"
OWN_COLS = "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\tRPTOWNER_TXT"
TX_COLS = "ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tSECURITY_TITLE\tSECURITY_TITLE_FN\tTRANS_DATE\tTRANS_DATE_FN\tDEEMED_EXECUTION_DATE\tDEEMED_EXECUTION_DATE_FN\tTRANS_FORM_TYPE\tTRANS_CODE\tEQUITY_SWAP_INVOLVED\tEQUITY_SWAP_TRANS_CD_FN\tTRANS_TIMELINESS\tTRANS_TIMELINESS_FN\tTRANS_SHARES\tTRANS_SHARES_FN\tTRANS_PRICEPERSHARE\tTRANS_PRICEPERSHARE_FN\tTRANS_ACQUIRED_DISP_CD\tTRANS_ACQUIRED_DISP_CD_FN\tSHRS_OWND_FOLWNG_TRANS"


def _tsv(cols, rows):
    n = len(cols.split("\t"))
    return "\n".join([cols] + ["\t".join(r + [""] * (n - len(r))) for r in rows]) + "\n"


def _insider_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", _tsv(SUB_COLS, [
            ["acc-1", "05-MAY-2020", "", "", "", "", "", "", "4", "320193", "Apple Inc", "AAPL", "", "0"],
            ["acc-2", "06-MAY-2020", "", "", "", "", "", "", "4", "320193", "Apple Inc", "AAPL", "", "1"],
            ["acc-3", "06-MAY-2020", "", "", "", "", "", "", "3", "320193", "Apple Inc", "AAPL", "", "0"],
            ["acc-4", "06-MAY-2020", "", "", "", "", "", "", "4", "999999", "Other Co", "OTH", "", "0"]]))
        z.writestr("REPORTINGOWNER.tsv", _tsv(OWN_COLS, [
            ["acc-1", "1", "DOE JANE", "Officer", "CFO"], ["acc-2", "2", "ROE RICH", "Director", ""]]))
        z.writestr("NONDERIV_TRANS.tsv", _tsv(TX_COLS, [
            ["acc-1", "1", "Common Stock", "", "20-APR-2020", "", "", "", "4", "P", "0", "", "", "", "1000.0", "",
             "250.5", "", "A", "", "5000.0"],
            ["acc-2", "2", "Common Stock", "", "01-MAY-2020", "", "", "", "4", "S", "0", "", "", "", "500", "",
             "260", "", "D", "", "100"],
            ["acc-4", "3", "Common Stock", "", "01-MAY-2020", "", "", "", "4", "P", "0", "", "", "", "1", "", "1", "",
             "A", "", "1"]]))
    return buf.getvalue()


def test_parse_insider_zip():
    out = history.parse_insider_zip(_insider_zip(), {"0000320193": "AAPL"})
    assert list(out) == ["AAPL"]
    buy, sell = sorted(out["AAPL"], key=lambda t: t.date)
    assert (buy.insider, buy.role, buy.code, buy.date, buy.filed) == ("DOE JANE", "CFO, Officer", "P", "2020-04-20", "2020-05-05")
    assert buy.shares == 1000 and buy.price == 250.5 and buy.acquired and not buy.ten_b5_1
    assert sell.code == "S" and sell.ten_b5_1 and not sell.acquired


def test_insider_zip_urls_reads_index_and_filters(monkeypatch):
    page = ('<a href="/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q2_form345.zip">x</a>'
            '<a href="/files/structureddata/data/insider-transactions-data-sets/2014q4_form345.zip">x</a>'
            '<a href="/files/structureddata/data/insider-transactions-data-sets/2015q1_form345.zip">x</a>')
    monkeypatch.setattr(history.http, "get", lambda *a, **k: page)
    assert history.insider_zip_urls("2015q1") == [
        "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/2015q1_form345.zip",
        "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q2_form345.zip"]


def test_propagate_tickers_by_cusip():
    H = sec.Holding
    old = sec.Filing13F("x", "X", "2015-12-31", "2016-02-14", "a", [H("FACEBOOK INC", "30303M102", "COM", 1, 1)])
    new = sec.Filing13F("x", "X", "2026-06-30", "2026-08-14", "b", [H("META PLATFORMS", "30303M102", "COM", 1, 1, ticker="META")])
    assert history.propagate_tickers_by_cusip({"x": [old, new]}) == 1
    assert old.holdings[0].ticker == "META"


def test_all_filings_follows_older_pages(monkeypatch):
    cols = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
    recent = {c: [] for c in cols}
    for c, v in zip(cols, ("a2", "13F-HR", "2024-02-14", "2023-12-31", "x.xml")):
        recent[c].append(v)
    older = {c: [v] for c, v in zip(cols, ("a1", "13F-HR", "2015-02-14", "2014-12-31", "y.xml"))}
    main = {"name": "BERKSHIRE", "filings": {"recent": recent, "files": [{"name": "CIK1-submissions-001.json"}]}}
    monkeypatch.setattr(sec, "_sec_get", lambda url, ttl=0, as_json=True: older if "submissions-001" in url else main)
    name, rows = sec.all_filings("0000000001", {"13F-HR"})
    assert name == "BERKSHIRE" and [r["accessionNumber"] for r in rows] == ["a2", "a1"]


def test_median_gap_flags_non_daily_series():
    daily = [(d, 1.0) for d in _calendar("2020-01-01", 30)]
    monthly = [(f"2020-{m:02d}-01", 1.0) for m in range(1, 13)]
    assert history.median_gap_days(daily) == 1
    assert history.median_gap_days(monthly) > 3
    assert history.median_gap_days(daily[:2]) is None


def test_get_history_rejects_non_daily_granularity(monkeypatch):
    from market_tracker import http
    from market_tracker.providers import market
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params)
        return {"chart": {"result": [{"meta": {"dataGranularity": "1mo"}, "timestamp": [], "indicators": {"quote": [{}]}}]}}

    monkeypatch.setattr(market.http, "get", fake_get)
    with pytest.raises(http.DataUnavailable, match="1mo bars"):
        market.get_history("AAPL", 3200)
    assert "period1" in calls[0] and calls[0]["interval"] == "1d"  # explicit window, never range=max
