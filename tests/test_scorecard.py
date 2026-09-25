from datetime import date, datetime, timedelta, timezone

from market_tracker import alerts, dilution, http, realtime, scorecard

TODAY = date(2026, 9, 25)


# ------------------------------------------------------------------ dilution

def _subs(*rows):
    forms, dates, accs = zip(*rows) if rows else ((), (), ())
    return lambda cik: {"filings": {"recent": {"form": list(forms), "filingDate": list(dates),
                                               "accessionNumber": list(accs)}}}


def _ago(days):
    return (TODAY - timedelta(days=days)).isoformat()


def test_dilution_levels():
    active = dilution.check("777777", TODAY, _subs(("4", _ago(2), "a1"), ("424B5", _ago(30), "a2"),
                                                   ("S-3", _ago(200), "a3")))
    assert active.level == "active" and len(active.notes) == 2 and active.latest[0]["form"] == "424B5"
    assert active.latest[0]["url"] == "https://www.sec.gov/Archives/edgar/data/777777/a2/"
    shelf = dilution.check("777777", TODAY, _subs(("S-3ASR", _ago(400), "a1"), ("424B5", _ago(200), "a2")))
    assert shelf.level == "shelf"          # the sale is too old to count, the shelf is still live
    assert dilution.check("1", TODAY, _subs(("10-K", _ago(10), "a"), ("S-3", _ago(1200), "b"))).level == "none"
    s1 = dilution.check("1", TODAY, _subs(("S-1", _ago(100), "a")))
    assert s1.level == "active" and "S-1" in s1.notes[0]


def test_dilution_text_and_errors():
    def down(cik):
        raise http.DataUnavailable("503")
    err = dilution.check("1", TODAY, down)
    assert err.error and not err.flagged and "couldn't read" in dilution.issue_section(err)
    clean = dilution.check("1", TODAY, _subs())
    assert "no shelf registration" in dilution.issue_section(clean) and dilution.short_label(clean) == ""
    active = dilution.check("1", TODAY, _subs(("424B5", _ago(5), "x")))
    assert "shares are being sold" in dilution.issue_section(active) and dilution.short_label(active) == "Dilution: active"


def test_cluster_issue_includes_the_dilution_section():
    buys = [alerts.Buy(filed="2026-09-2" + d, accession="a" + d, issuer_cik="0000777777", issuer_name="ACME",
                       symbol="ACME", insider=n, role="Director", trade_date="2026-09-1" + d, shares=1000,
                       price=50.0, value=50_000) for n, d in (("A", "1"), ("B", "2"), ("C", "3"))]
    c = alerts.find_clusters(buys, TODAY)[0]
    body = alerts.issue_body(c, "**Dilution check: shelf on file.**\n")
    assert body.index("| C |") < body.index("Dilution check") < body.index("How to read this")


# ------------------------------------------------------------------ scorecard

def _hist(start_price, daily, n=150, start=date(2026, 6, 1)):
    out, p, d = [], start_price, start
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d.isoformat(), p))
            p *= 1 + daily
        d += timedelta(days=1)
    return out


def _rec(symbol="ACME", alerted="2026-06-10", kind="cluster"):
    return scorecard.AlertRecord(alerted=alerted, kind=kind, symbol=symbol, issuer_cik="0000777777",
                                 company="ACME", detail="3 insiders, $150,000")


def test_score_one_buys_after_the_alert_and_compares_with_spy():
    stock, spy = _hist(10.0, 0.002), _hist(500.0, 0.001)
    row = scorecard.score_one(_rec(), stock, spy)
    assert row["entry_date"] == "2026-06-11"           # first close after the alert date
    assert row["status"] == "closed" and row["days_held"] == len(stock) - 1 - 8
    assert abs(row["ret_21"] - (1.002 ** 21 - 1)) < 1e-9
    assert abs(row["excess_21"] - ((1.002 ** 21 - 1) - (1.001 ** 21 - 1))) < 1e-9
    assert "excess_126" in row
    young = scorecard.score_one(_rec(alerted=stock[-30][0]), stock, spy)
    assert young["status"] == "open" and "excess_21" in young and "excess_63" not in young
    pending = scorecard.score_one(_rec(alerted=stock[-1][0]), stock, spy)
    assert pending["status"] == "pending" and pending["to_date"] is None


def test_verdict_needs_enough_resolved_alerts():
    few = scorecard.summarize([{"excess_63": 0.05}] * 5, 63)
    assert few["n"] == 5 and scorecard.verdict(few).startswith("Too early")
    many = scorecard.summarize([{"excess_63": 0.05 + 0.01 * (i % 3)} for i in range(40)], 63)
    assert scorecard.verdict(many).startswith("Beating SPY")
    noisy = scorecard.summarize([{"excess_63": (0.3 if i % 2 else -0.28)} for i in range(40)], 63)
    assert scorecard.verdict(noisy).startswith("No reliable difference")


def test_evaluate_groups_by_kind_and_survives_missing_prices():
    hist = {"SPY": _hist(500.0, 0.001), "ACME": _hist(10.0, 0.002)}

    def fn(sym):
        if sym not in hist:
            raise http.DataUnavailable(f"{sym}: 404")
        return hist[sym]
    recs = [_rec(), _rec(symbol="GONE", kind="big"), _rec(symbol="NONE")]
    out = scorecard.evaluate(recs, fn)
    assert set(out["by_kind"]) == {"cluster", "big"} and out["by_kind"]["cluster"]["horizons"][0]["n"] == 1
    assert [r["status"] for r in out["alerts"] if r["symbol"] == "GONE"] == ["no price"]
    assert all(r["symbol"] != "NONE" for r in out["alerts"]) and out["errors"]

    def down(sym):
        raise http.DataUnavailable("down")
    assert "error" in scorecard.evaluate(recs, down)   # no benchmark, no scorecard


def test_log_seed_and_roundtrip(tmp_path):
    buys = [alerts.Buy(filed="2026-09-20", accession="a", issuer_cik="0000777777", issuer_name="ACME",
                       symbol="ACME", insider="A", role="Director", trade_date="2026-09-19", shares=1, price=1.0, value=1.0)]
    alerted = {"0000777777": "2026-09-25", "big:0000777777": "2026-09-25", "0000999999": "2026-09-25"}
    path = str(tmp_path / "alert_log.csv")
    log = scorecard.ensure_log(path, alerted, buys)
    assert [(r.kind, r.symbol, r.alerted) for r in log] == [("cluster", "ACME", "2026-09-25")]
    scorecard.save_log(log + log + [_rec()], path)
    assert len(scorecard.load_log(path)) == 2
    assert len(scorecard.ensure_log(path, {}, [])) == 2   # an existing log is never re-seeded


# ------------------------------------------------------------------ watcher: offering filings

FEED = """<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>424B5 - ACME SMALLCAP CORP (0000777777) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/777777/x-index.htm"/>
<updated>2026-09-24T14:05:00-04:00</updated><category term="424B5"/>
<id>urn:tag:sec.gov,2008:accession-number=0000777777-26-000100</id></entry>
<entry><title>424B5 - SOMEONE ELSE INC (0000123456) (Filer)</title>
<updated>2026-09-24T14:04:00-04:00</updated><category term="424B5"/>
<id>urn:tag:sec.gov,2008:accession-number=0000123456-26-000100</id></entry>
<entry><title>424B2 - BIG BANK (0000070858) (Filer)</title>
<updated>2026-09-24T14:03:00-04:00</updated><category term="424B2"/>
<id>urn:tag:sec.gov,2008:accession-number=0000070858-26-000200</id></entry>
</feed>"""


def test_watcher_reports_offering_filings_by_alerted_companies_only():
    state = realtime.WatchState(last_poll="2026-09-24T18:00:00+00:00")
    res = realtime.poll(state, [], {}, now=datetime(2026, 9, 24, 18, 10, tzinfo=timezone.utc),
                        feed_fn=lambda form, start: FEED if (form == "424B" and start == 0) else "<feed/>",
                        submission_fn=lambda e: "", is_fund=lambda cik: False, watch_ciks={"0000777777"})
    assert [(e.name, e.form) for e in res.dilution] == [("ACME SMALLCAP CORP", "424B5")]
    assert state.seen == ["0000777777-26-000100"]
    assert "DILUTE" in "\n".join(realtime.summary_lines(res))
