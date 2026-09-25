import json
from datetime import date, datetime, timezone

from market_tracker import alerts, dilution, http, journal, site
from market_tracker.providers import market


def NO_DILUTION(cik):
    return dilution.DilutionCheck(cik=cik)


def _quote(sym):
    if sym == "XOM":
        raise http.DataUnavailable("XOM: 503")
    return market.Quote(sym, market.asset_class(sym), 100.0, 99.0, 1.01, "USD", "stub", "2026-09-24T20:00:00+00:00")


def _row(sym, day, score, version=journal.SCORE_VERSION):
    return {"date": day, "symbol": sym, "asset_class": market.asset_class(sym), "price": 100.0, "score": score,
            "label": "Bullish" if score > 0 else "Bearish", "coverage": 0.8, "trend": 40.0, "momentum": -20.0,
            "smart_money": None, "insider": None, "news": 5.0, "version": version}


def _buy(insider, trade, filed, cik="0000777777", symbol="ACME"):
    return alerts.Buy(filed=filed, accession=f"0000777777-26-{insider}{trade[-2:]}", issuer_cik=cik,
                      issuer_name="ACME SMALLCAP CORP", symbol=symbol, insider=insider, role="Director",
                      trade_date=trade, shares=1000, price=50.0, value=50_000)


def test_latest_scores_uses_newest_current_version_row():
    rows = [_row("AAPL", "2026-09-20", 10), _row("AAPL", "2026-09-24", 30),
            _row("AAPL", "2026-09-25", -90, version="1"), _row("MSFT", "2026-09-24", -12)]
    latest = site.latest_scores(rows)
    assert latest["AAPL"]["score"] == 30 and latest["MSFT"]["score"] == -12


def test_build_data_shape_and_failures_are_contained():
    rows = [_row("AAPL", "2026-09-24", 30)]
    fresh = [_buy(n, f"2026-09-1{i}", "2026-09-23") for i, n in enumerate("ABC")]
    fund = [_buy(n, f"2026-09-1{i}", "2026-09-23", cik="0000040417", symbol="GAM") for i, n in enumerate("ABC")]
    data = site.build_data(rows, fresh + fund, {}, today=date(2026, 9, 24), quote_fn=_quote,
                           history_fn=lambda s: [], is_fund=lambda cik: cik == "0000040417", dilution_fn=NO_DILUTION,
                           now=datetime(2026, 9, 24, 21, tzinfo=timezone.utc))
    assert data["generated_at"] == "2026-09-24T21:00:00+00:00"
    by_sym = {r["symbol"]: r for r in data["universe"]}
    assert set(by_sym) == set(journal.DEFAULT_UNIVERSE)
    assert by_sym["AAPL"]["score"] == 30 and by_sym["AAPL"]["components"]["trend"] == 40.0
    assert by_sym["AAPL"]["price"] == 100.0
    assert "quote_error" in by_sym["XOM"] and "price" not in by_sym["XOM"]  # one failure doesn't sink the build
    assert [c["symbol"] for c in data["clusters"]] == ["ACME"]  # the fund is left out
    c = data["clusters"][0]
    assert c["status"] == "new" and c["insiders"] == 3 and len(c["buys"]) == 3
    assert c["buys"][0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/777777/")
    assert data["track_record"]["entries"] == 1


def test_cluster_status_labels():
    today = date(2026, 9, 24)
    one_day = [_buy(n, "2026-09-18", "2026-09-19", cik="0000000001") for n in "ABC"]
    old = [_buy(n, f"2026-09-0{i + 1}", "2026-09-05", cik="0000000002", symbol="OLDC") for i, n in enumerate("ABC")]
    seen = [_buy(n, f"2026-09-1{i}", "2026-09-23", cik="0000000003", symbol="SEEN") for i, n in enumerate("ABC")]
    rows = site.cluster_rows(one_day + old + seen, {"0000000003": "2026-09-23"}, today, lambda cik: False)
    assert {r["symbol"]: r["status"] for r in rows} == {"ACME": "one-day", "OLDC": "older", "SEEN": "alerted"}
    assert [r["status"] for r in rows] == ["alerted", "one-day", "older"]
    # Two weeks after its newest filing, an alerted cluster is listed as older.
    later = site.cluster_rows(seen, {"0000000003": "2026-09-23"}, date(2026, 10, 8), lambda cik: False)
    assert later[0]["status"] == "older"


def test_build_writes_static_files_and_data(tmp_path):
    jpath = tmp_path / "journal.csv"
    journal.save([_row("AAPL", "2026-09-24", 30)], str(jpath))
    adir = tmp_path / "alerts"
    adir.mkdir()
    alerts.save_buys([_buy(n, f"2026-09-1{i}", "2026-09-23") for i, n in enumerate("ABC")],
                     str(adir / "insider_buys.csv"), date(2026, 9, 24))
    out = tmp_path / "_site"
    site.build(str(out), str(jpath), str(adir), today=date(2026, 9, 24), quote_fn=_quote,
               history_fn=lambda s: [], is_fund=lambda cik: False, dilution_fn=NO_DILUTION)
    for name in ("index.html", "site.js", "site.css", "data.json"):
        assert (out / name).exists(), name
    data = json.loads((out / "data.json").read_text())
    assert data["backtest"]["components"][0]["name"] == "Smart money (13F)"
    assert len(data["clusters"]) == 1
    html = (out / "index.html").read_text()
    assert "Plumbline" in html and "site.js" in html


def test_big_buys_and_stakes_for_the_site():
    from market_tracker import realtime
    today = date(2026, 9, 24)
    big = [_buy("CEO", "2026-09-20", "2026-09-22")]
    big[0].value = 2_500_000
    small = [_buy("CFO", "2026-09-20", "2026-09-22")]
    old = [_buy("OLD", "2026-08-01", "2026-08-03")]
    old[0].value = 5_000_000
    rows = site.big_buy_rows(big + small + old, today, lambda cik: False)
    assert [(r["insider"], r["value"]) for r in rows] == [("CEO", 2_500_000)]
    assert site.big_buy_rows(big, today, lambda cik: True) == []

    def stake(acc, form, filed, tracked=""):
        return realtime.Stake(filed=filed, accession=acc, form=form, subject_cik="0000777777",
                              subject_name="ACME", filer_cik="1", filer_name="FILER", tracked=tracked, url="")
    stakes = [stake("a", "SCHEDULE 13D", "2026-09-20"), stake("b", "SCHEDULE 13G", "2026-09-21"),
              stake("c", "SCHEDULE 13G", "2026-09-22", tracked="Bill Ackman"), stake("d", "SCHEDULE 13D", "2026-07-01")]
    out = site.stake_rows(stakes, today)
    assert [r["filed"] for r in out] == ["2026-09-22", "2026-09-20"]  # untracked 13G and old filings left out
    assert out[0]["tracked"] == "Bill Ackman" and out[1]["url"].startswith("https://www.sec.gov/Archives/edgar/data/777777/")
