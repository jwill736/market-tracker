import io
import zipfile

from market_tracker import insider_backtest as ib


def _zip(subs, owners, trans):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        def tsv(name, head, rows):
            z.writestr(name, "\t".join(head) + "\n" + "\n".join("\t".join(r) for r in rows) + "\n")
        tsv("SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "ISSUERCIK", "DOCUMENT_TYPE", "AFF10B5ONE"], subs)
        tsv("REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"], owners)
        tsv("NONDERIV_TRANS.tsv", ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES", "TRANS_PRICEPERSHARE"], trans)
    return buf.getvalue()


def test_parse_zip_keeps_director_and_officer_open_market_buys():
    blob = _zip([["a1", "15-MAR-2020", "11", "4", "0"], ["a2", "16-MAR-2020", "11", "4", "1"], ["a3", "16-MAR-2020", "11", "4", "0"],
                 ["a4", "16-MAR-2020", "99", "4", "0"]],
                [["a1", "DOE JANE", "Director", ""], ["a2", "ROE RICK", "Officer", "CEO"], ["a3", "BIG FUND LP", "TenPercentOwner", ""],
                 ["a4", "X Y", "Director", ""]],
                [["a1", "12-MAR-2020", "P", "1000", "50"], ["a1", "12-MAR-2020", "S", "10", "50"], ["a2", "13-MAR-2020", "P", "1", "1"],
                 ["a3", "13-MAR-2020", "P", "99999", "9"], ["a4", "13-MAR-2020", "P", "1", "1"]])
    buys = ib.parse_zip(blob, {"0000000011"})
    assert [(b.insider, b.value, b.filed) for b in buys] == [("doe jane", 50000.0, "2020-03-15")]   # plan, 10% owner, sale, other issuer dropped


def _b(cik, who, trade, filed, value):
    return ib.Buy(cik, who, "director", trade, filed, value)


def test_events_routine_opportunistic_and_calendar_stats():
    buys = [_b("1", "habit h", f"{y}-06-10", f"{y}-06-12", 200_000) for y in (2017, 2018, 2019, 2020)]
    buys += [_b("2", "new n", "2020-06-03", "2020-06-05", 80_000), _b("2", "other o", "2020-06-20", "2020-06-22", 50_000),
             _b("3", "tiny t", "2020-06-01", "2020-06-02", 5_000)]
    ev = {(e["cik"], e["month"]): e for e in ib.events(buys)}
    assert not ev[("1", "2020-06")]["opportunistic"] and ev[("1", "2017-06")]["opportunistic"]   # a habit by 2020
    assert ev[("2", "2020-06")]["cluster"] and ev[("2", "2020-06")]["value"] == 130_000
    assert ("3", "2020-06") not in ev                                                            # under $100k
    days = [f"2020-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
    up = [(d, 10 * 1.002 ** i) for i, d in enumerate(days)]
    flat = [(d, 100.0) for d in days]
    rows = ib.score([ev[("2", "2020-06")]], {"UP": up}, {"2": "UP"}, flat, shares_at=lambda c, d: 1e8)
    assert rows[0]["entry"] == "2020-06-23"   # after the month's last filing and rows[0]["3m"] > 0.1 and rows[0]["3m_spy"] == 0 and rows[0]["cap"] > 1e9
    many = [dict(rows[0], month=f"20{y:02d}-{m:02d}") for y in range(14, 20) for m in range(1, 13)]
    st = ib.calendar_stats(many, "3m")
    assert st["months"] == 72 and st["beat_pct"] == 100 and st["avg_edge"] > 10
    assert ib.calendar_stats(many[:5], "3m") is None
    summary = ib.summarize(many)
    assert summary["opportunistic"]["3m"]["months"] == 72 and summary["routine"]["3m"] is None
    assert "Opportunistic insider buying" in ib.verdict({**summary, "opportunistic": {**summary["opportunistic"], "6m": summary["opportunistic"]["3m"]}})
