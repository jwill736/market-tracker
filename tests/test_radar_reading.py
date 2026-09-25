from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from market_tracker import api, db, mynews, radar, reading, sentinel

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

# Trimmed from EDGAR's live feed (2026-09-24).
FEED_8K = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings</title>
<entry>
<title>8-K - PGIM Private Credit Fund (0001923622) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1923622/000119312526401226/0001193125-26-401226-index.htm"/>
<summary type="html">
 &lt;b&gt;Filed:&lt;/b&gt; 2026-09-24 &lt;b&gt;AccNo:&lt;/b&gt; 0001193125-26-401226 &lt;b&gt;Size:&lt;/b&gt; 970 KB
&lt;br&gt;Item 1.01: Entry into a Material Definitive Agreement
&lt;br&gt;Item 9.01: Financial Statements and Exhibits
</summary>
<updated>2026-09-24T17:30:42-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=0001193125-26-401226</id>
</entry>
<entry>
<title>8-K - GameSquare Holdings, Inc. (0001714562) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1714562/000149315226044197/0001493152-26-044197-index.htm"/>
<summary type="html">
 &lt;b&gt;Filed:&lt;/b&gt; 2026-09-24 &lt;b&gt;AccNo:&lt;/b&gt; 0001493152-26-044197 &lt;b&gt;Size:&lt;/b&gt; 398 KB
&lt;br&gt;Item 5.02: Departure of Directors or Certain Officers; Election of Directors
&lt;br&gt;Item 9.01: Financial Statements and Exhibits
</summary>
<updated>2026-09-24T17:28:18-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=0001493152-26-044197</id>
</entry>
<entry>
<title>8-K - Troubled Co (0000000123) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/123/x-index.htm"/>
<summary type="html">&lt;br&gt;Item 4.01: Changes in Registrant's Certifying Accountant
&lt;br&gt;Item 4.02: Non-Reliance on Previously Issued Financial Statements</summary>
<updated>2026-09-24T16:05:00-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=0000000123-26-000001</id>
</entry>
</feed>"""

FEED_25 = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
<title>25-NSE - Jasper Therapeutics, Inc. (0001788028) (Subject)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1788028/000135445726000914/0001354457-26-000914-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-24 &lt;b&gt;AccNo:&lt;/b&gt; 0001354457-26-000914 &lt;b&gt;Size:&lt;/b&gt; 3 KB</summary>
<updated>2026-09-24T16:15:27-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="25-NSE"/>
<id>urn:tag:sec.gov,2008:accession-number=0001354457-26-000914</id>
</entry>
<entry>
<title>25-NSE - Nasdaq Stock Market LLC (0001354457) (Filed by)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1354457/000135445726000914/0001354457-26-000914-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-24</summary>
<updated>2026-09-24T16:15:27-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="25-NSE"/>
<id>urn:tag:sec.gov,2008:accession-number=0001354457-26-000914</id>
</entry>
</feed>"""


def test_classify_rules():
    assert radar.classify("8-K", ["2.02", "9.01"]) is None                 # earnings: not on the radar
    level, head, _, hits = radar.classify("8-K", ["4.01", "4.02", "9.01"])
    assert level == 3 and head.startswith("Past financial statements") and "auditor changed" in head
    assert [h["code"] for h in hits] == ["4.02", "4.01"]
    assert radar.classify("8-K", ["5.02"])[0] == 1
    assert radar.classify("NT 10-K", [])[0] == 2 and radar.classify("25-NSE", [])[0] == 3
    assert radar.classify("10-Q", []) is None


def test_feed_parsing_and_alerts():
    entries = radar.parse_feed(FEED_8K)
    assert [e.items for e in entries] == [["1.01", "9.01"], ["5.02", "9.01"], ["4.01", "4.02"]]
    alerts = radar.alerts_from_feed(entries)
    assert [(a.company, a.level) for a in alerts] == [("GameSquare Holdings, Inc.", 1), ("Troubled Co", 3)]
    assert alerts[1].filed == "2026-09-24" and alerts[1].url.endswith("x-index.htm")
    delist = radar.alerts_from_feed(radar.parse_feed(FEED_25))
    assert len(delist) == 1 and delist[0].company == "Jasper Therapeutics, Inc." and delist[0].level == 3


def test_scan_market_keeps_exact_forms_and_reports_errors():
    from market_tracker import http

    def get(url, **kw):
        if "type=8-K" in url:
            return FEED_8K
        if "type=25&" in url:
            return FEED_25                        # prefix match returns 25-NSE too: dropped for "25"
        raise http.DataUnavailable("down")

    alerts, errors = radar.scan_market(get, forms=["8-K", "25", "NT 10-K"])
    assert {a.company for a in alerts} == {"GameSquare Holdings, Inc.", "Troubled Co"}
    assert errors == ["NT 10-K: down"]


SUBMISSIONS = {"name": "Apple Inc.", "filings": {"recent": {
    "accessionNumber": ["a-1", "a-2", "a-3", "a-4"], "form": ["8-K", "8-K", "10-Q", "8-K"],
    "filingDate": ["2026-09-01", "2026-07-30", "2026-07-31", "2026-01-02"],
    "items": ["5.02", "2.02,9.01", "", "4.02"], "primaryDocument": ["a", "b", "c", "d"],
    "acceptanceDateTime": ["2026-09-01T16:30:00.000Z", "", "", ""]}}}


def test_company_history_stops_at_the_window():
    got = radar.company_alerts("320193", SUBMISSIONS, date(2026, 6, 27), "AAPL")
    assert [(a.accession, a.level, a.symbol) for a in got] == [("a-1", 1, "AAPL")]      # 4.02 is too old
    assert got[0].url == "https://www.sec.gov/Archives/edgar/data/320193/a1/a-1-index.htm"


def test_going_concern_search_pages():
    pages = [{"hits": {"total": {"value": 101}, "hits": [
        {"_id": "x:1", "_source": {"ciks": ["0000073290"], "adsh": "0001-26-1", "form": "10-K", "file_date": "2026-08-31",
                                   "display_names": ["BIOMERICA INC  (BMRA)  (CIK 0000073290)"]}}]}},
             {"hits": {"total": {"value": 101}, "hits": [
                 {"_id": "y:1", "_source": {"ciks": ["0000073290"], "adsh": "0001-26-2", "form": "10-Q",
                                            "file_date": "2026-09-15", "display_names": ["BIOMERICA INC"]}}]}}]
    calls = []

    def get(url, params=None, **kw):
        calls.append(params["from"])
        return pages[len(calls) - 1]

    found = radar.going_concern_ciks(date(2026, 9, 25), get=get)
    assert calls == [0, 100] and found["0000073290"]["accession"] == "0001-26-2"
    a = radar.going_concern_alert("0000073290", found["0000073290"], "BMRA")
    assert a.level == 2 and a.company == "BIOMERICA INC" and a.symbol == "BMRA"


def test_sentinel_mine_merges_history_feed_and_going_concern(monkeypatch):
    monkeypatch.setattr(sentinel, "symbol_ciks", lambda syms: {"0000320193": "AAPL", "0000000123": "TRBL"})
    s = sentinel.Sentinel()
    s.market = {a.accession: a for a in radar.alerts_from_feed(radar.parse_feed(FEED_8K))}
    subs = {"0000320193": SUBMISSIONS, "0000000123": {"name": "Troubled Co", "filings": {"recent": {}}}}
    mine, errors = s.mine(["AAPL", "TRBL"], today=date(2026, 9, 25), submissions_fn=lambda c: subs[c],
                          going_concern_fn=lambda today: {"0000000123": {"accession": "g-1", "form": "10-Q",
                                                                         "filed": "2026-09-10", "name": "Troubled Co"}})
    got = {(a.symbol, a.headline.split(" +")[0]) for a in mine}
    assert ("AAPL", "Director or officer change") in got and ("TRBL", "Past financial statements can't be relied on") in got
    assert ("TRBL", "Going-concern doubt in its latest report") in got and not errors


def test_radar_headsups_once_and_pushed(monkeypatch):
    monkeypatch.setattr(sentinel, "symbol_ciks", lambda syms: {"0000000123": "TRBL"})
    sent = []
    monkeypatch.setattr(sentinel.notify, "send", lambda m: sent.append(m) or True)
    s = sentinel.Sentinel()
    s.market = {a.accession: a for a in radar.alerts_from_feed(radar.parse_feed(FEED_8K))}
    assert s.radar_headsups(["TRBL"], today=date(2026, 9, 25)) == 1
    assert s.radar_headsups(["TRBL"], today=date(2026, 9, 25)) == 0          # once only
    assert sent[0].priority == 5 and sent[0].title.startswith("TRBL: Past financial statements")
    with db.connect() as conn:
        assert db.list_headsup(conn)[0]["kind"] == "radar"


# ------------------------------------------------------------------ reading

AR_POST = """<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>Thursday links: important economic risks</title><link>https://abnormalreturns.com/2026/09/24/x/</link>
<pubDate>Thu, 24 Sep 2026 17:08:33 +0000</pubDate><description><![CDATA[]]></description>
<content:encoded><![CDATA[<div class="links content-section"><h4 class="link-group-title">Markets</h4><ul class="link-group"><li><a class="link" href="https://www.ft.com/content/6354c1ec" target="_blank">Is hyperscaler borrowing really driving rates higher?  <span class="source">(ft.com)</span></a></li><li><a class="link" href="https://www.wsj.com/finance/investing/a-perfect-storm?st=5zm&amp;reflink=x" target="_blank">Why everyone is freaking out about rates. <span class="source">(wsj.com)</span></a></li></ul><h4 class="link-group-title">Companies</h4><ul class="link-group"><li><a class="link" href="https://www.bloomberg.com/nvidia-story" target="_blank">Nvidia's next act. <span class="source">(bloomberg.com)</span></a></li></ul></div>]]></content:encoded>
</item></channel></rss>"""

RITHOLTZ = """<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>10 Thursday AM Reads</title><link>https://ritholtz.com/2026/09/10-thursday-am-reads/</link>
<pubDate>Thu, 24 Sep 2026 10:30:00 +0000</pubDate>
<content:encoded><![CDATA[<p>My morning reads:</p><ul><li><a href="https://www.ft.com/content/6354c1ec">Is hyperscaler borrowing really driving rates higher?</a> <em>FT</em></li><li><a href="https://ritholtz.com/2026/09/other/">Another Ritholtz post here</a></li><li><a href="https://www.amazon.com/dp/123">A book on Amazon with a long title</a></li><li><a href="https://example.com/x">Short</a></li></ul>]]></content:encoded>
</item></channel></rss>"""

DESK = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Tesla shares jump on deliveries</title><link>https://example.com/1</link><pubDate>Fri, 25 Sep 2026 10:00:00 +0000</pubDate><description>Record quarter.</description></item>
<item><title>Fed's Warsh says rate cuts can wait</title><link>https://example.com/2</link><pubDate>Fri, 25 Sep 2026 09:00:00 +0000</pubDate></item>
<item><title>Treasury yields hit a new high</title><link>https://example.com/3</link><pubDate>Fri, 25 Sep 2026 08:00:00 +0000</pubDate></item>
<item><title>Inflation cools in August</title><link>https://example.com/4</link><pubDate>Thu, 24 Sep 2026 20:00:00 +0000</pubDate></item>
<item><title>Bond market sell-off deepens</title><link>https://example.com/5</link><pubDate>Thu, 24 Sep 2026 18:00:00 +0000</pubDate></item>
<item><title>FOMC minutes show a split</title><link>https://example.com/6</link><pubDate>Thu, 24 Sep 2026 15:00:00 +0000</pubDate></item>
<item><title>Old story about the Fed</title><link>https://example.com/7</link><pubDate>Mon, 21 Sep 2026 12:00:00 +0000</pubDate></item>
<item><title>Ancient WSJ story</title><link>https://example.com/8</link><pubDate>Mon, 27 Jan 2025 12:00:00 +0000</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title type="text">Bitcoin steadies</title>
<link rel="alternate" href="https://example.com/btc"/><updated>2026-09-25T06:00:00Z</updated><summary>Calm.</summary></entry></feed>"""


def test_parse_rss_atom_and_broken_xml():
    rss = reading.parse_feed(DESK)
    assert rss[0]["title"] == "Tesla shares jump on deliveries" and rss[0]["published"].startswith("2026-09-25T10:00")
    atom = reading.parse_feed(ATOM)
    assert atom[0]["url"] == "https://example.com/btc" and atom[0]["summary"] == "Calm."
    loose = reading.parse_feed("<rss><channel><item><title>A &nbsp; B</title><link>https://x.y/z</link>"
                               "<pubDate>Fri, 25 Sep 2026 10:00:00 +0000</pubDate></item></channel></rss>")
    assert loose[0]["url"] == "https://x.y/z"


def test_curated_picks_rank_double_picks_first():
    feeds = {"abnormalreturns": reading.parse_feed(AR_POST), "ritholtz": reading.parse_feed(RITHOLTZ)}
    picks = reading.curated_picks(feeds, NOW)
    by = {p.title: p for p in picks}
    assert by["Is hyperscaler borrowing really driving rates higher?"].picked_by == ["Abnormal Returns", "Ritholtz"]
    assert by["Why everyone is freaking out about rates."].section == "Markets"
    assert by["Why everyone is freaking out about rates."].url.endswith("reflink=x")
    assert "Another Ritholtz post here" not in by and "A book on Amazon with a long title" not in by and "Short" not in by


def test_build_reading_matches_holdings_and_heats_topics():
    def fetch():
        return {"abnormalreturns": reading.parse_feed(AR_POST), "ritholtz": reading.parse_feed(RITHOLTZ),
                "wsj": reading.parse_feed(DESK), "coindesk": reading.parse_feed(ATOM)}, ["Barron's: 403"]

    data = reading.build(["TSLA", "NVDA", "BTC-USD"], {"TSLA": "Tesla, Inc.", "NVDA": "NVIDIA CORP"},
                         {"Rates & the Fed": reading.DEFAULT_TOPICS["Rates & the Fed"]}, fetch=fetch, now=NOW)
    titles = [i["title"] for i in data["items"]]
    assert "Ancient WSJ story" not in titles and "Old story about the Fed" not in titles       # older than 72 h
    mentions = {m["title"]: m["mentions"] for m in data["mentions"]}
    assert mentions["Tesla shares jump on deliveries"] == ["TSLA"] and mentions["Bitcoin steadies"] == ["BTC-USD"]
    assert mentions["Nvidia's next act."] == ["NVDA"]
    topic = data["topics"][0]
    assert topic["last_24h"] == 5 and topic["hot"]                     # 5 Fed/rates stories today, none the days before
    assert data["picks"][0]["picked_by"] == ["Abnormal Returns", "Ritholtz"]
    assert data["errors"] == ["Barron's: 403"]


# ------------------------------------------------------------------ holdings news

def art(title, hours, source="Yahoo"):
    return {"title": title, "url": "https://example.com/" + title, "source": source, "sentiment": 0,
            "published": (NOW - timedelta(hours=hours)).isoformat()}


def test_news_digest_flags_loud_symbols():
    data = {"NVDA": {"avg_sentiment": 0.2, "articles": [art(f"n{i}", i) for i in range(8)] + [art("old", 100)],
                     "top_terms": [("chips", 3)]},
            "AAPL": {"avg_sentiment": 0.0, "articles": [art(f"a{i}", 30 + i * 10, "Reuters") for i in range(6)]}}
    out = mynews.build(["AAPL", "NVDA", "BTC-USD"], {"AAPL": "Apple Inc."},
                       news_fn=lambda s, c, d: data[s] if s in data else (_ for _ in ()).throw(
                           mynews.http.DataUnavailable("no news")), now=NOW)
    nv, ap = out["symbols"]
    assert nv["symbol"] == "NVDA" and nv["loud"] and nv["last_24h"] == 8 and nv["terms"] == ["chips"]
    assert not ap["loud"] and ap["deep"] and ap["daily_pace"] == 1.0
    assert out["errors"] == ["BTC-USD: no news"] and out["feed"][0]["symbol"] == "NVDA"
    assert all(a["title"] != "old" for a in out["feed"])


# ------------------------------------------------------------------ endpoints

@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    for c in (sentinel.news_cache, sentinel.reading_cache, sentinel.radar_cache):
        c.clear()
    return TestClient(api.app)


def test_topics_endpoints_seed_once(client):
    t = client.get("/api/topics").json()
    assert "Rates & the Fed" in t
    client.delete("/api/topics/Rates & the Fed")
    client.post("/api/topics", json={"name": "GLP-1", "terms": "ozempic, wegovy, glp-1"})
    t = client.get("/api/topics").json()
    assert "Rates & the Fed" not in t and t["GLP-1"] == "ozempic, wegovy, glp-1"


def test_headsup_endpoint(client):
    sentinel.raise_headsup("k1", "news", 2, "NVDA loud", "x", "https://e.com", "NVDA")
    h = client.get("/api/headsup").json()
    assert h["unread"] == 1 and h["items"][0]["title"] == "NVDA loud" and h["push"] is False
    client.post("/api/headsup/read")
    assert client.get("/api/headsup").json()["unread"] == 0


def test_radar_endpoint(client, monkeypatch):
    s = sentinel.sentinel
    monkeypatch.setattr(s, "scan", lambda get=None: None)
    monkeypatch.setattr(s, "scanned_at", "2026-09-25T12:00:00+00:00")
    monkeypatch.setattr(s, "market", {a.accession: a for a in radar.alerts_from_feed(radar.parse_feed(FEED_8K))})
    monkeypatch.setattr(s, "mine", lambda syms: ([], []))
    monkeypatch.setattr(sentinel, "symbol_ciks", lambda syms: {})
    r = client.get("/api/radar").json()
    assert [a["level"] for a in r["market"]] == [1, 3] and r["market"][1]["level_name"] == "Act today"
    assert r["mine"] == [] and r["held"] == []


def test_news_and_reading_endpoints(client, monkeypatch):
    client.post("/api/watchlist/NVDA")
    monkeypatch.setattr(sentinel, "build_news", lambda syms: {"symbols": [{"symbol": syms[0]}], "feed": [], "errors": []})
    monkeypatch.setattr(sentinel, "build_reading", lambda syms: {"picks": [], "mentions": [], "symbols": syms})
    assert client.get("/api/mynews").json()["symbols"][0]["symbol"] == "NVDA"
    assert client.get("/api/reading").json()["symbols"] == ["NVDA"]
