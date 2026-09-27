from datetime import date

from market_tracker import filings, tenkrank

WIKI = """<table class="wikitable sortable" id="constituents"><tbody>
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>Sub-Industry</th><th>HQ</th><th>Date added</th><th>CIK</th><th>Founded</th></tr>
<tr><td><a href="#">MMM</a></td><td><a href="#">3M</a></td><td>Industrials</td><td>Industrial Conglomerates</td><td>Saint Paul, Minnesota</td><td>1957-03-04</td><td>0000066740</td><td>1902</td></tr>
<tr><td><a href="#">BRK.B</a></td><td><a href="#">Berkshire Hathaway</a></td><td>Financials</td><td>Multi-Sector Holdings</td><td>Omaha, Nebraska</td><td>2010-02-16</td><td>0001067983</td><td>1839</td></tr>
<tr><td><a href="#">AT&amp;T</a></td><td>not a ticker row</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
</tbody></table><table id="changes"><tr><td>ZZZ</td></tr></table>"""


def test_parse_universe():
    rows = tenkrank.parse_universe(WIKI)
    assert [r["symbol"] for r in rows] == ["MMM", "BRK-B"]
    assert rows[1] == {"symbol": "BRK-B", "name": "Berkshire Hathaway", "sector": "Financials", "cik": "0001067983"}


def _data(values, filed="2026-03-01"):
    return {"as_of": "2026-09-27", "companies": [{"symbol": f"S{i}", "name": f"Co {i}", "sector": "X", "filed": filed, "risk_new": v,
                                                  "risk_sim": 0.99, "new_count": 3, "sample": ["A new risk."], "url": ""} for i, v in enumerate(values)]}


def test_percentile_and_most_changed():
    data = _data([i / 100 for i in range(100)])
    p = tenkrank.percentile(data, 0.9, date(2026, 9, 27))
    assert p["pct"] == 90 and p["of"] == 100 and p["text"].startswith("More new text than 90%")
    assert tenkrank.percentile(data, 0.05, date(2026, 9, 27))["text"].startswith("Less new text than 94%")
    top = tenkrank.most_changed(data, 3, date(2026, 9, 27))
    assert [c["symbol"] for c in top] == ["S99", "S98", "S97"]
    stale = _data([0.1] * 100, filed="2024-01-01")
    assert tenkrank.percentile(stale, 0.2, date(2026, 9, 27)) is None      # last year's reports don't count


def test_run_reuses_unchanged_companies_and_reads_new_ones(monkeypatch):
    universe = [{"symbol": f"C{i}", "name": f"Co {i}", "sector": "X", "cik": str(i).zfill(10)} for i in range(460)]
    monkeypatch.setattr(tenkrank, "universe", lambda get=None, previous=None: universe)
    risk = "Item 1A. Risk Factors " + " ".join(f"Risk sentence number {i} explains a separate hazard for the business." for i in range(80))
    new_risk = risk + " A new cyber breach disrupted our operations and customers for several weeks this year."

    def fake_filings(symbol, forms, n=2, get=None, cik=None):
        new = "2" if symbol == "C1" else "1"
        return [{"accession": f"{symbol}-{new}", "url": f"u/{symbol}/{new}", "filed": "2026-02-01"},
                {"accession": f"{symbol}-0", "url": f"u/{symbol}/0", "filed": "2025-02-01"}]
    monkeypatch.setattr(filings, "filings_for", fake_filings)
    reads = []

    def text(url):
        reads.append(url)
        return (new_risk if url.endswith("/2") else risk) + " Item 1B. Unresolved"
    prev = {"companies": [{"symbol": f"C{i}", "name": f"Co {i}", "sector": "X", "acc": f"C{i}-1", "prev_acc": f"C{i}-0", "filed": "2026-02-01",
                           "risk_new": 0.01, "method": tenkrank.METHOD} for i in range(460)]}
    out = tenkrank.run(prev, get=lambda u, **k: {}, text_fn=text, log=lambda *_: None)
    assert reads == ["u/C1/0", "u/C1/2"]               # only the company with a new 10-K is read
    c1 = next(c for c in out["companies"] if c["symbol"] == "C1")
    assert c1["acc"] == "C1-2" and c1["new_count"] == 1 and 0 < c1["risk_new"] < 0.1 and "cyber breach" in c1["sample"][0]
    assert len(out["companies"]) == 460


def test_universe_falls_back_to_fund_holdings(monkeypatch):
    from market_tracker import http
    fake = [{"symbol": f"T{i}", "name": f"Co {i}", "sector": "", "cik": ""} for i in range(495)]
    monkeypatch.setattr(tenkrank, "universe_from_nport", lambda get=None: fake)

    def refused(url):
        raise http.DataUnavailable(f"{url}: 403 Forbidden")
    assert tenkrank.universe(refused) == fake
    monkeypatch.setattr(tenkrank, "universe_from_nport", lambda get=None: [])
    try:
        tenkrank.universe(refused)
        raise AssertionError("expected DataUnavailable")
    except http.DataUnavailable as exc:
        assert "403 Forbidden" in str(exc) and "IVV holdings: 0 matched" in str(exc)


def test_entries_from_an_older_method_are_recomputed(monkeypatch):
    monkeypatch.setattr(filings, "filings_for", lambda symbol, forms, n=2, get=None, cik=None: [
        {"accession": "A-1", "url": "u/1", "filed": "2026-02-01"}, {"accession": "A-0", "url": "u/0", "filed": "2025-02-01"}])
    risk = "Item 1A. Risk Factors " + " ".join(f"Risk sentence number {i} explains a separate hazard for the business." for i in range(80))
    old = {"symbol": "A", "acc": "A-1", "prev_acc": "A-0", "risk_new": 0.99}
    e = tenkrank.score_one({"symbol": "A", "name": "A", "sector": "X", "cik": ""}, old, text_fn=lambda u: risk + " Item 1B. Unresolved")
    assert e["method"] == tenkrank.METHOD and e["risk_new"] == 0.0


def test_unreliable_comparisons_are_left_out():
    data = _data([i / 100 for i in range(100)])
    data["companies"][99]["risk_sim"] = 0.0          # one year's section came out empty
    top = tenkrank.most_changed(data, 1, date(2026, 9, 27))
    assert top[0]["symbol"] == "S98" and tenkrank.percentile(data, 0.5, date(2026, 9, 27))["of"] == 99
