from datetime import datetime, timezone

import httpx

from market_tracker import alerts, http, notify, realtime
from tests.conftest import fixture_text

NOW = datetime(2026, 9, 24, 18, 10, tzinfo=timezone.utc)  # 14:10 EDT
ACC = "0001140361-26-012345"


def _feed(form, start):
    return fixture_text("edgar_current.xml") if (form == "4" and start == 0) else '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


def _submission(entry):
    return "<SEC-DOCUMENT>\n<XML>\n" + fixture_text("form4.xml") + "\n</XML>\n</SEC-DOCUMENT>"


def _poll(state=None, buys=None, alerted=None, **kw):
    state = state or realtime.WatchState(last_poll="2026-09-24T18:00:00+00:00")
    buys = [] if buys is None else buys
    alerted = {} if alerted is None else alerted
    kw.setdefault("is_fund", lambda cik: False)
    kw.setdefault("submission_fn", _submission)
    res = realtime.poll(state, buys, alerted, now=NOW, feed_fn=_feed, **kw)
    return res, state, buys, alerted


def test_parse_feed():
    entries = realtime.parse_feed(fixture_text("edgar_current.xml"))
    assert len(entries) == 8
    e = entries[0]
    assert (e.accession, e.form, e.cik, e.name, e.role) == (ACC, "4", "0000320193", "Apple Inc.", "Issuer")
    assert entries[4].name == "Pershing Square Capital Management, L.P." and entries[4].role == "Filed by"
    assert realtime.parse_feed('<feed xmlns="http://www.w3.org/2005/Atom"></feed>') == []


def test_new_entries_stops_at_the_cursor_and_skips_seen():
    since = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)
    got = realtime.new_entries("4", set(), since, _feed)
    assert {e.accession for e in got} == {ACC, "0000070858-26-000001", "0001336528-26-000010", "0000999999-26-000002"}
    assert realtime.new_entries("4", {ACC}, since, _feed)[0].accession != ACC


def test_poll_finds_buys_big_buys_and_stakes():
    res, state, buys, alerted = _poll()
    assert res.filings == 3  # one Form 4 and two 13Ds; the 424B2 and the old filing are ignored
    assert len(res.new_buys) == 1 and res.new_buys[0].symbol == "AAPL" and res.new_buys[0].filed == "2026-09-24"
    assert [b.symbol for b in res.big] == ["AAPL"] and res.big[0].value == 1_502_500
    assert {s.subject_name: s.tracked for s in res.stakes} == {"ACME SMALLCAP CORP": "Bill Ackman",
                                                              "WIDGET HOLDINGS INC": ""}
    assert [s.subject_name for s in res.tracked_stakes] == ["ACME SMALLCAP CORP"]
    assert alerted["13d:0001336528-26-000010"] == "2026-09-24" and alerted["big:0000320193"] == "2026-09-24"
    assert ACC in state.seen and state.last_poll == "2026-09-24T18:10:00+00:00"
    assert len(buys) == 1


def test_a_second_poll_announces_nothing_new():
    res, state, buys, alerted = _poll()
    res2, *_ = _poll(state=state, buys=buys, alerted=alerted)
    assert res2.filings == 0 and not res2.big and not res2.tracked_stakes


def test_poll_completes_a_cluster_from_earlier_buys():
    earlier = [alerts.Buy(filed="2026-09-2" + d, accession=f"a{d}", issuer_cik="0000320193", issuer_name="Apple Inc.",
                          symbol="AAPL", insider=n, role="Director", trade_date="2026-09-1" + d, shares=1000,
                          price=100.0, value=100_000) for n, d in (("SMITH", "1"), ("JONES", "2"))]
    def recent(entry):  # the fixture's trade is from August; move it inside the 30-day window
        return _submission(entry).replace("2026-08-14", "2026-09-22")
    res, _, _, alerted = _poll(buys=list(earlier), submission_fn=recent)
    assert [c.symbol for c in res.clusters] == ["AAPL"] and len(res.clusters[0].insiders) == 3
    assert alerted["0000320193"] == "2026-09-24"


def test_failed_submission_is_retried_next_poll():
    def down(entry):
        raise http.DataUnavailable("503")
    res, state, *_ = _poll(submission_fn=down)
    assert res.failures == 1 and ACC not in state.seen


def test_funds_and_repeat_big_buys_are_suppressed():
    res, *_ = _poll(is_fund=lambda cik: True)
    assert res.big == []
    res2, *_ = _poll(alerted={"big:0000320193": "2026-09-10"})
    assert res2.big == []


def test_tracked_investor_matching():
    assert realtime.tracked_investor("0001336528", "anything") == "Bill Ackman"
    assert realtime.tracked_investor("0000000001", "ICAHN CARL C") == "Carl Icahn"
    assert realtime.tracked_investor("0000000002", "PERSHING SQUARE CAPITAL MANAGEMENT, L.P.") == "Bill Ackman"
    assert realtime.tracked_investor("0000000003", "CARLSON CAPITAL") == ""


def test_stake_text_and_storage(tmp_path):
    res, *_ = _poll()
    s = res.tracked_stakes[0]
    assert realtime.stake_title(s) == "Activist stake (13D): Bill Ackman in ACME SMALLCAP CORP"
    assert "Item 4" in realtime.stake_body(s) and s.url.endswith("-index.htm")
    path = str(tmp_path / "stakes.csv")
    realtime.save_stakes(res.stakes + res.stakes, path, NOW.date())
    assert realtime.load_stakes(path) == sorted(res.stakes, key=lambda x: (x.filed, x.accession))


def test_state_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    realtime.WatchState(seen=[str(i) for i in range(6000)], last_poll="x").save(path)
    st = realtime.WatchState.load(path)
    assert len(st.seen) == realtime.KEEP_SEEN and st.seen[-1] == "5999" and st.last_poll == "x"
    assert realtime.WatchState.load(str(tmp_path / "missing.json")).seen == []


def test_notify_posts_to_topic_and_never_raises(monkeypatch):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert not notify.send(notify.Message("t", "b"), client) and not sent
    monkeypatch.setenv("NTFY_TOPIC", "secret-topic-123")
    assert notify.send(notify.Message("Insider buy: AAPL $1.5M", "body", url="https://x", tags=("moneybag",)), client)
    r = sent[0]
    assert str(r.url) == "https://ntfy.sh/secret-topic-123" and r.headers["Title"] == "Insider buy: AAPL $1.5M"
    assert r.headers["Click"] == "https://x" and r.content == b"body"

    def boom(request):
        raise httpx.ConnectError("down")
    assert not notify.send(notify.Message("t", "b"), httpx.Client(transport=httpx.MockTransport(boom)))


def _full_page(start, newest):
    from datetime import timedelta
    rows = []
    for i in range(100):
        t = (newest - timedelta(seconds=start + i)).strftime("%Y-%m-%dT%H:%M:%S-00:00")
        rows.append(f'<entry><title>4 - Co {start + i} (000{start + i:07d}) (Issuer)</title>'
                    f'<link href="https://www.sec.gov/Archives/edgar/data/1/000{start + i:07d}-26-{start + i:06d}-index.htm"/>'
                    f'<summary>Filed</summary><updated>{t}</updated>'
                    f'<category term="4" label="form type"/><id>urn:tag:sec.gov,2008:accession-number=000{start + i:07d}-26-{start + i:06d}</id></entry>')
    return '<feed xmlns="http://www.w3.org/2005/Atom">' + "".join(rows) + "</feed>"


def test_a_window_longer_than_the_feed_is_reported_as_a_gap():
    since = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)       # six hours before NOW
    gaps = []
    got = realtime.new_entries("4", set(), since, lambda f, s: _full_page(s, NOW), max_pages=2, gaps=gaps)
    assert len(got) == 200 and len(gaps) == 1 and gaps[0].startswith("4: read 200 entries back to 2026-09-24T18:06")
    gaps = []
    realtime.new_entries("4", set(), datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc), _feed, gaps=gaps)
    assert gaps == []                                               # the cursor was reached
    res = realtime.PollResult(gaps=["4: read 500 entries back to x, not back to y"])
    assert any(line.startswith("  GAP") for line in realtime.summary_lines(res))


def _buy(insider, price, shares, day="2026-09-16", acc="a", cik="0000896493", sym="GPUS"):
    return alerts.Buy("2026-09-18", acc, cik, "Hyperscale Data, Inc.", sym, insider, "Director", day, shares, price, shares * price)


def test_mistyped_prices_do_not_count():
    buys = [_buy("Ault", 0.1832, 125_000, acc="1"), _buy("Nisser", 0.1871, 250_000, acc="2"),
            _buy("Cragun", 0.1865, 100_000, acc="3"), _buy("Horne", 18.0, 1_000, acc="4")]   # really $0.18
    assert alerts.implausible_prices(buys) == {alerts._trade_key(buys[3])}
    c = alerts.find_clusters(buys, NOW.date(), min_insiders=3, min_total=1)
    assert len(c) == 1 and "Horne" not in c[0].insiders
    # a $1M "buy" at 100x the day's close is dropped; a real one isn't
    fake = _buy("Typo", 25.0, 50_000, acc="9", cik="1", sym="XYZ")
    real = _buy("Real", 0.26, 5_000_000, acc="8", cik="2", sym="ABC")
    closes = {"XYZ": 0.25, "ABC": 0.25}
    got = realtime.big_buys([fake, real], close_fn=lambda s, d: closes[s])
    assert [b.insider for b in got] == ["Real"]
