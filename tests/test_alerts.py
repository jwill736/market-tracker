from datetime import date

from market_tracker import alerts
from tests.conftest import fixture_text

INDEX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    September 24, 2026
Comments:              webmaster@sec.gov

Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
3           SOME NEW DIRECTOR                                             2000001     20260924    edgar/data/2000001/0002000001-26-000001.txt
4           APPLE INC                                                     320193      20260924    edgar/data/320193/0001140361-26-012345.txt
4           DOE JANE                                                      1214156     20260924    edgar/data/1214156/0001140361-26-012345.txt
4/A         APPLE INC                                                     320193      20260924    edgar/data/320193/0001140361-26-099999.txt
4           ACME SMALLCAP CORP                                            777777      20260924    edgar/data/777777/0000777777-26-000042.txt
"""


def test_parse_form_index_dedupes_filers_and_filters_forms():
    rows = alerts.parse_form_index(INDEX)
    assert [r["accession"] for r in rows] == ["0001140361-26-012345", "0000777777-26-000042"]
    assert rows[0]["filename"] == "edgar/data/320193/0001140361-26-012345.txt"
    assert rows[0]["company"] == "APPLE INC" and rows[0]["date"] == "20260924"


def test_index_url_quarter():
    assert alerts.index_url(date(2026, 9, 24)).endswith("/2026/QTR3/form.20260924.idx")
    assert "/QTR4/" in alerts.index_url(date(2026, 10, 1))


def _submission():
    return ("<SEC-DOCUMENT>\n<DOCUMENT>\n<TYPE>4\n<TEXT>\n<XML>\n" + fixture_text("form4.xml") +
            "\n</XML>\n</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>")


def test_buys_from_submission_keeps_only_open_market_purchases():
    buys = alerts.buys_from_submission(_submission(), "0001140361-26-012345", "2026-09-24")
    assert len(buys) == 1  # the F (tax withholding) row is dropped
    b = buys[0]
    assert (b.symbol, b.issuer_cik, b.insider, b.trade_date) == ("AAPL", "0000320193", "DOE JANE", "2026-08-14")
    assert b.value == 10_000 * 150.25
    assert alerts.buys_from_submission("<SEC-DOCUMENT>no xml</SEC-DOCUMENT>", "x", "2026-09-24") == []


def NOT_FUND(cik):
    return False


def _buy(insider, trade, filed, value=50_000, cik="0000777777", symbol="ACME", accession=None):
    return alerts.Buy(filed=filed, accession=accession or f"a-{insider}-{trade}", issuer_cik=cik,
                      issuer_name="ACME SMALLCAP CORP", symbol=symbol, insider=insider, role="Director", trade_date=trade, shares=1000,
                      price=value / 1000, value=value)


def test_find_clusters_rules():
    buys = [_buy("A", "2026-09-01", "2026-09-02"), _buy("B", "2026-09-10", "2026-09-11"),
            _buy("C", "2026-09-20", "2026-09-22"), _buy("C", "2026-09-21", "2026-09-22"),
            _buy("X", "2026-09-20", "2026-09-22", cik="0000111111"),
            _buy("Y", "2026-09-20", "2026-09-22", cik="0000111111")]
    clusters = alerts.find_clusters(buys, date(2026, 9, 24))
    assert len(clusters) == 1  # the second company has only 2 insiders
    c = clusters[0]
    assert c.insiders == ["A", "B", "C"] and c.total_value == 200_000
    assert (c.first_trade, c.last_trade) == ("2026-09-01", "2026-09-21")
    # Not public yet: C's filing is dated after the as-of date.
    assert alerts.find_clusters(buys, date(2026, 9, 21)) == []
    # Too small in total.
    small = [_buy(n, "2026-09-10", "2026-09-11", value=20_000) for n in "ABC"]
    assert alerts.find_clusters(small, date(2026, 9, 24)) == []
    # Outside the 30-day window.
    assert alerts.find_clusters(buys, date(2026, 11, 30)) == []


def test_new_clusters_suppresses_repeats_for_30_days():
    buys = [_buy(n, f"2026-09-1{i}", "2026-09-20") for i, n in enumerate("ABC")]
    c = alerts.find_clusters(buys, date(2026, 9, 24))
    assert alerts.new_clusters(c, {}, date(2026, 9, 24), NOT_FUND) == c
    assert alerts.new_clusters(c, {"0000777777": "2026-09-20"}, date(2026, 9, 24), NOT_FUND) == []
    assert alerts.new_clusters(c, {"0000777777": "2026-08-01"}, date(2026, 9, 24), NOT_FUND) == c


def test_storage_roundtrip_dedupes_and_prunes(tmp_path):
    path = str(tmp_path / "buys.csv")
    old = _buy("OLD", "2026-01-02", "2026-01-05")
    b = _buy("A", "2026-09-10", "2026-09-11")
    alerts.save_buys([old, b, b], path, date(2026, 9, 24))
    assert alerts.load_buys(path) == [b]
    apath = str(tmp_path / "alerted.csv")
    alerts.save_alerted({"0000777777": "2026-09-24"}, apath)
    assert alerts.load_alerted(apath) == {"0000777777": "2026-09-24"}


def test_issue_text():
    c = alerts.find_clusters([_buy(n, "2026-09-10", "2026-09-11") for n in "ABC"], date(2026, 9, 24))[0]
    assert alerts.issue_title(c) == "Insider cluster buy: ACME (ACME SMALLCAP CORP), 3 insiders, $150,000"
    body = alerts.issue_body(c)
    assert "| 2026-09-10 | A | Director |" in body and "edgar/data/777777/" in body
    assert "mt analyze ACME" in body


def test_only_officers_and_directors_count():
    assert alerts.is_officer_or_director("Director")
    assert alerts.is_officer_or_director("Chief Financial Officer, 10% owner")
    assert not alerts.is_officer_or_director("10% owner")
    assert not alerts.is_officer_or_director("Insider")
    assert not alerts.is_officer_or_director("")
    xml_owner = fixture_text("form4.xml").replace(
        "<isOfficer>1</isOfficer><isTenPercentOwner>0</isTenPercentOwner><officerTitle>Chief Financial Officer</officerTitle>",
        "<isOfficer>0</isOfficer><isTenPercentOwner>1</isTenPercentOwner><officerTitle></officerTitle>")
    sub = "<XML>\n" + xml_owner + "\n</XML>"
    assert alerts.buys_from_submission(sub, "a", "2026-09-24") == []


def test_same_day_clusters_carry_a_warning():
    same = alerts.find_clusters([_buy(n, "2026-09-18", "2026-09-19") for n in "ABC"], date(2026, 9, 24))[0]
    spread = alerts.find_clusters([_buy(n, f"2026-09-1{i}", "2026-09-19") for i, n in enumerate("ABC")], date(2026, 9, 24))[0]
    assert same.trade_days == 1 and "same day" in alerts.issue_body(same)
    assert spread.trade_days == 3 and "same day" not in alerts.issue_body(spread)


def test_single_day_clusters_are_listed_but_not_alerted():
    one_day = alerts.find_clusters([_buy(n, "2026-09-18", "2026-09-19") for n in "ABC"], date(2026, 9, 24))
    assert len(one_day) == 1 and alerts.new_clusters(one_day, {}, date(2026, 9, 24), NOT_FUND) == []
    # A later buy by another insider makes it a multi-day cluster, which does alert.
    grown = alerts.find_clusters([_buy(n, "2026-09-18", "2026-09-19") for n in "ABC"]
                                 + [_buy("D", "2026-09-22", "2026-09-23")], date(2026, 9, 24))
    assert len(alerts.new_clusters(grown, {}, date(2026, 9, 24), NOT_FUND)) == 1


def _spread(filed="2026-09-23", **kw):
    return alerts.find_clusters([_buy(n, f"2026-09-1{i}", filed, **kw) for i, n in enumerate("ABC")],
                                date(2026, 9, 24))[0]


def test_old_clusters_are_listed_but_not_alerted():
    # The newest filing is 10 days old: the buying isn't news any more.
    old = _spread(filed="2026-09-14")
    assert alerts.skip_reason(old, {}, date(2026, 9, 24), NOT_FUND) == "OLD"
    # A fresh filing (another insider buying) makes the same cluster alert.
    grown = alerts.find_clusters(old.buys + [_buy("D", "2026-09-22", "2026-09-23")], date(2026, 9, 24))
    assert alerts.skip_reason(grown[0], {}, date(2026, 9, 24), NOT_FUND) is None


def test_funds_and_untradable_issuers_are_skipped():
    assert alerts.skip_reason(_spread(symbol="NONE"), {}, date(2026, 9, 24), NOT_FUND) == "NOTK"
    assert alerts.skip_reason(_spread(symbol=""), {}, date(2026, 9, 24), NOT_FUND) == "NOTK"
    assert alerts.skip_reason(_spread(), {}, date(2026, 9, 24), lambda cik: True) == "FUND"
    assert alerts.skip_reason(_spread(), {}, date(2026, 9, 24), NOT_FUND) is None


def test_fund_lookup_uses_sec_industry_code(monkeypatch):
    def filings(*forms):
        return {"recent": {"form": list(forms)}}
    codes = {"0000040417": {"sic": "6726", "filings": filings("N-CSR")},
             "0000777777": {"sic": "3990", "filings": filings("10-K", "4", "8-K")},
             "0000999999": {"sic": ""},
             # A closed-end fund filed under an operating-company industry code.
             "0000040418": {"sic": "6211", "filings": filings("N-CEN", "NPORT-P", "4")}}
    monkeypatch.setattr(alerts.sec, "_sec_get", lambda url, ttl: codes[url.split("CIK")[1][:10]])
    assert alerts.issuer_is_fund("40417") and alerts.issuer_is_fund("0000999999")
    assert alerts.issuer_is_fund("40418")
    assert not alerts.issuer_is_fund("0000777777")

    def down(url, ttl):
        raise alerts.http.DataUnavailable("503")
    monkeypatch.setattr(alerts.sec, "_sec_get", down)
    assert not alerts.issuer_is_fund("0000040417")  # fail open: better a stray alert than a missed one


def test_a_trade_filed_twice_counts_once(tmp_path):
    # Seen in the backfill: the same 100,000-share purchase under two consecutive accessions.
    dup = [_buy("A", "2026-09-08", "2026-09-09", accession="acc-1263"),
           _buy("A", "2026-09-08", "2026-09-09", accession="acc-1264"),
           _buy("B", "2026-09-10", "2026-09-11"), _buy("C", "2026-09-12", "2026-09-14")]
    c = alerts.find_clusters(dup, date(2026, 9, 24))[0]
    assert len(c.buys) == 3 and c.total_value == 150_000
    path = str(tmp_path / "buys.csv")
    alerts.save_buys(dup, path, date(2026, 9, 24))
    assert len(alerts.load_buys(path)) == 3
