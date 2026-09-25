import io
import zipfile
from datetime import date

import pytest
from fastapi.testclient import TestClient

from market_tracker import alerts, api, people, sentinel
from market_tracker.providers import market

# The live ARKK file's layout (2026-09-25), trimmed.
ARK_DAY1 = '''date,fund,company,ticker,cusip,shares,market value ($),weight (%)
09/24/2026,ARKK,TESLA INC,TSLA,88160R101,"2,164,429","$818,024,296.26",9.17%
09/24/2026,ARKK,COINBASE GLOBAL INC -CLASS A,COIN,19260Q107,"2,047,090","$407,800,798.90",4.57%
09/24/2026,ARKK,ROKU INC,ROKU,77543R102,"1,000,000","$90,000,000.00",1.00%
09/24/2026,ARKK,TINY CHANGE,TINY,1,"1,000,000","$1.00",0.10%
'''
ARK_DAY2 = '''date,fund,company,ticker,cusip,shares,market value ($),weight (%)
09/25/2026,ARKK,TESLA INC,TSLA,88160R101,"2,264,429","$818,024,296.26",9.30%
09/25/2026,ARKK,COINBASE GLOBAL INC -CLASS A,COIN,19260Q107,"1,947,090","$407,800,798.90",4.40%
09/25/2026,ARKK,CIRCLE INTERNET GROUP INC,CRCL,172573107,"4,822,555","$448,497,615.00",5.03%
09/25/2026,ARKK,TINY CHANGE,TINY,1,"1,005,000","$1.00",0.10%
,,"Holdings are subject to change.",,,,,
'''


def test_ark_csv_and_daily_trades():
    d1, prev = people.parse_ark_csv(ARK_DAY1)
    d2, cur = people.parse_ark_csv(ARK_DAY2)
    assert d1 == "2026-09-24" and d2 == "2026-09-25" and cur["TSLA"]["shares"] == 2264429 and "TINY" in cur
    moves = {m.symbol: m for m in people.ark_trades("ARKK", d1, prev, d2, cur)}
    assert set(moves) == {"TSLA", "COIN", "CRCL", "ROKU"}              # TINY's +0.5% is creation noise
    assert moves["TSLA"].action == "Buy" and moves["COIN"].action == "Sell"
    assert moves["CRCL"].action == "New" and moves["ROKU"].action == "Exit" and moves["TSLA"].disclosed == "2026-09-25"


def house_zip(members):
    xml = "<FinancialDisclosure>" + "".join(
        f"<Member><Prefix>Hon.</Prefix><Last>{last}</Last><First>{first}</First><FilingType>{ft}</FilingType>"
        f"<StateDst>CA11</StateDst><Year>2026</Year><FilingDate>{fd}</FilingDate><DocID>{doc}</DocID></Member>"
        for first, last, ft, fd, doc in members) + "</FinancialDisclosure>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("2026FD.txt", "ignored")
        z.writestr("2026FD.xml", xml)
    return buf.getvalue()


PTR_TEXT = """P T R  Filing ID #20031234
Name: Hon. Nancy Pelosi
ID Owner Asset Transaction
Type
Date Notification
Date
Amount Cap.
Gains >
$200?
SP NVIDIA Corporation - Common
Stock (NVDA) [ST]
P 09/02/2026 09/03/2026 $1,000,001 -
$5,000,000
SP Apple Inc. - Common Stock (AAPL)
[ST]
S (partial) 08/28/2026 08/29/2026 $500,001 - $1,000,000
JT Microsoft Corporation (MSFT) [OP] P 08/20/2026 08/21/2026 Over $50,000,000
Filing status: New
"""


def test_house_index_and_ptr_text():
    z = house_zip([("Nancy", "Pelosi", "P", "9/15/2026", "20031234"), ("Some", "One", "O", "9/16/2026", "10000001"),
                   ("Old", "Filer", "P", "7/01/2026", "20030000")])
    filings = people.parse_house_index(z, "2026-08-26")
    assert [(f["who"], f["filed"]) for f in filings] == [("Rep. Nancy Pelosi", "2026-09-15")]
    assert filings[0]["url"].endswith("/ptr-pdfs/2026/20031234.pdf")
    rows = people.parse_ptr_text(PTR_TEXT)
    assert [(r["symbol"], r["action"], r["owner"], r["traded"]) for r in rows] == [
        ("NVDA", "Buy", "spouse", "2026-09-02"), ("AAPL", "Sell (partial)", "spouse", "2026-08-28"),
        ("MSFT", "Buy", "joint", "2026-08-20")]
    assert rows[0]["amount"] == "$1,000,001 - $5,000,000" and rows[2]["amount"] == "Over $50,000,000"


def test_house_moves_keep_unreadable_filings_as_links():
    z = house_zip([("Nancy", "Pelosi", "P", "9/15/2026", "1"), ("Paper", "Filer", "P", "9/14/2026", "2")])

    def get_bytes(url, **kw):
        return z if url.endswith("FD.zip") else url.encode()

    def text_fn(data):
        if data.endswith(b"/2.pdf"):
            raise ValueError("scanned image")
        return PTR_TEXT

    moves, errors = people.house_moves(date(2026, 9, 25), get_bytes=get_bytes, text_fn=text_fn)
    assert [m.symbol for m in moves if m.who == "Rep. Nancy Pelosi"] == ["NVDA", "AAPL", "MSFT"]
    paper = [m for m in moves if m.who == "Rep. Paper Filer"]
    assert paper[0].action == "Report" and paper[0].url.endswith("/2.pdf") and errors


SENATE_HTML = """<table><tr><th>#</th></tr>
<tr><td>1</td><td>09/10/2026</td><td>Spouse</td><td><a>AMZN</a></td><td>Amazon.com, Inc.</td><td>Stock</td><td>Purchase</td><td>$15,001 - $50,000</td><td>--</td></tr>
<tr><td>2</td><td>09/11/2026</td><td>Self</td><td>--</td><td>US Treasury Bill</td><td>Other</td><td>Purchase</td><td>$1,001 - $15,000</td><td>--</td></tr>
<tr><td>3</td><td>09/12/2026</td><td>Joint</td><td>GOOGL</td><td>Alphabet Inc.</td><td>Stock</td><td>Sale (Partial)</td><td>$1,001 - $15,000</td><td>--</td></tr></table>"""


def test_senate_ptr_table():
    rows = people.parse_senate_ptr(SENATE_HTML)
    assert [(r["symbol"], r["action"], r["owner"]) for r in rows] == [("AMZN", "Buy", "spouse"), ("GOOGL", "Sell (partial)", "joint")]


def test_insider_moves_sum_per_filing():
    buys = [alerts.Buy("2026-09-20", "a1", "1", "Acme", "ACME", "Jane CEO", "Director, CEO", "2026-09-18", 1000, 200, 200_000),
            alerts.Buy("2026-09-20", "a1", "1", "Acme", "ACME", "Jane CEO", "Director, CEO", "2026-09-19", 500, 200, 100_000),
            alerts.Buy("2026-09-21", "b1", "2", "Small", "SMOL", "Bob", "Director", "2026-09-19", 10, 10, 1_000),
            alerts.Buy("2026-08-01", "c1", "3", "Old", "OLD", "Al", "Director", "2026-07-30", 1, 1, 9_000_000)]
    moves = people.insider_moves(buys, date(2026, 9, 25))
    assert [(m.symbol, m.amount) for m in moves] == [("ACME", "$300,000")] and moves[0].who == "Jane CEO (Director)"


def hist(sym, days):
    series = {"AAA": [("2026-09-01", 10.0), ("2026-09-10", 12.0), ("2026-09-20", 15.0)],
              "BBB": [("2026-09-01", 50.0), ("2026-09-10", 40.0), ("2026-09-20", 30.0)],
              "SPY": [("2026-09-01", 100.0), ("2026-09-10", 101.0), ("2026-09-20", 102.0)]}
    return [market.PriceBar(d, c) for d, c in series[sym]]


def test_copy_sim_uses_disclosure_dates():
    moves = [people.Move("Rep. X", "congress", "AAA", "Buy", "2026-08-20", "2026-09-01"),
             people.Move("Rep. X", "congress", "BBB", "Buy", "2026-08-25", "2026-09-01"),
             people.Move("Rep. X", "congress", "BBB", "Sell", "2026-09-05", "2026-09-10")]
    r = people.copy_sim(moves, history_fn=hist, today=date(2026, 9, 25))
    # AAA: $1000 at 10 -> 100 units * 15 = 1500; BBB: $1000 at 50 -> 20 units sold at 40 = 800
    assert r["invested"] == 2000 and r["value"] == pytest.approx(2300) and r["return_pct"] == pytest.approx(15.0)
    assert r["benchmark_return_pct"] == pytest.approx(2.0) and r["trades"] == 3 and r["since"] == "2026-09-01"


def test_build_and_endpoints(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    sentinel.people_cache.clear()
    d1, prev = people.parse_ark_csv(ARK_DAY1)
    d2, cur = people.parse_ark_csv(ARK_DAY2)
    fake = dict(
        ark_fn=lambda: (people.ark_trades("ARKK", d1, prev, d2, cur), {"ARKK": f"{d1} → {d2}"}),
        house_fn=lambda: ([people.Move("Rep. Nancy Pelosi", "congress", "AAA", "Buy", "2026-08-20", "2026-09-01",
                                       "A (spouse)", "$1,001 - $15,000", "u")], []),
        senate_fn=lambda: ([], ["Senate eFD: blocked"]),
        insiders_fn=lambda: [], activists_fn=lambda: [])
    real_build = people.build
    monkeypatch.setattr(people, "build", lambda today=None, follows=None: real_build(date(2026, 9, 25), follows, **fake))
    monkeypatch.setattr(people.market, "get_history", hist)
    c = TestClient(api.app)
    d = c.get("/api/people").json()
    assert d["sections"]["congress"][0]["lag_days"] == 12 and len(d["sections"]["ark"]) == 4
    assert "Senate eFD: blocked" in d["errors"]
    assert c.post("/api/people/follow", json={"who": "Rep. Nancy Pelosi", "group": "congress"}).json() == {"Rep. Nancy Pelosi": "congress"}
    d = c.get("/api/people").json()
    assert d["following"][0]["symbol"] == "AAA"
    copy = c.get("/api/people/copy", params={"who": "Rep. Nancy Pelosi"}).json()
    assert copy["return_pct"] == pytest.approx(50.0)
    assert c.get("/api/people/copy", params={"who": "Nobody"}).status_code == 404
    sent = []
    monkeypatch.setattr(sentinel.notify, "send", lambda m: sent.append(m) or True)
    monkeypatch.setattr(sentinel, "date", type("D", (), {"today": staticmethod(lambda: date(2026, 9, 2))}))
    assert sentinel.people_headsups() == 1 and sent[0].title == "Rep. Nancy Pelosi: Buy AAA"
    assert c.delete("/api/people/follow", params={"who": "Rep. Nancy Pelosi"}).json() == {}
