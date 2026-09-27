from market_tracker import earnings, filings

RELEASE = """<html><body><p>EXAMPLE CORP REPORTS THIRD QUARTER RESULTS</p>
<p>Example Corp today reported revenue of $12.4 billion for the third quarter, up 14% from a year earlier.</p>
<p>Diluted earnings per share were $2.31, compared with $1.95 in the prior-year quarter.</p>
<p>Operating margin expanded to 31.2% as data center sales grew 42%.</p>
<p>We are pleased with the team's execution across every region this quarter.</p>
<p>The Company is raising its full-year 2026 revenue guidance to $49.0 billion to $49.5 billion.</p>
<p>For the fourth quarter of 2026, the Company expects revenue of $13.0 billion plus or minus 2%.</p>
<p>This press release contains forward-looking statements, including statements about revenue guidance for fiscal 2026 of $49 billion.</p>
</body></html>"""


def test_highlights_quote_the_numbers_in_order_and_skip_boilerplate():
    text = filings.html_to_text(RELEASE)
    h = earnings.highlights(text)
    assert h[0].startswith("Example Corp today reported revenue of $12.4 billion")
    assert any("per share were $2.31" in x for x in h) and not any("pleased" in x for x in h)
    assert not any("forward-looking" in x for x in h)


def test_outlook_direction():
    o = earnings.outlook(filings.html_to_text(RELEASE))
    assert o["direction"] == "raised" and len(o["lines"]) == 2 and not any("forward-looking" in x for x in o["lines"])
    assert earnings.outlook("The Company reaffirmed its full-year 2026 outlook of $10 billion in revenue.")["direction"] == "kept"
    assert earnings.outlook("Management lowered its fiscal 2026 guidance to $3.10 per share.")["direction"] == "lowered"
    assert earnings.outlook("Revenue rose 5% to $2.0 billion in the quarter.")["direction"] == "none"


def test_reaction_after_the_close_uses_the_next_day():
    bars = [(f"2026-07-{d:02d}", 100.0 + (d % 3) * 0.5) for d in range(1, 29)]
    bars = [(d, c) for d, c in bars if d != "2026-07-29"] + [("2026-07-29", 100.0), ("2026-07-30", 108.0)]
    r = earnings.reaction(bars, "2026-07-29", "2026-07-29T20:05:00.000Z")        # 4:05pm Eastern
    assert r["day"] == "2026-07-30" and r["move_pct"] == 8.0 and r["times_usual"] > 5
    r2 = earnings.reaction(bars, "2026-07-30", "2026-07-30T11:00:00.000Z")       # 7am Eastern: that day's move
    assert r2["day"] == "2026-07-30" and r2["move_pct"] == 8.0


def test_latest_release_finds_item_202_and_its_exhibit(monkeypatch):
    from market_tracker.providers import sec
    monkeypatch.setattr(filings, "cik_of", lambda s: "0000000123")
    subs = {"name": "Example Corp", "filings": {"recent": {
        "form": ["8-K", "10-Q", "8-K"], "items": ["5.02", "", "2.02,9.01"], "filingDate": ["2026-08-10", "2026-08-01", "2026-07-29"],
        "reportDate": ["2026-08-10", "2026-06-30", "2026-07-29"], "accessionNumber": ["a-1", "b-2", "c-3"],
        "primaryDocument": ["x.htm", "q.htm", "ex8k.htm"], "acceptanceDateTime": ["", "", "2026-07-29T20:05:00.000Z"]}}}
    index = {"directory": {"item": [{"name": "ex8k.htm"}, {"name": "exc-992.htm"}, {"name": "exc-991.htm"}, {"name": "0000000123-26-000003-index.htm"}]}}

    def get(url, **k):
        return index if url.endswith("index.json") else subs
    r = earnings.latest_release("EXM", get)
    assert r["filed"] == "2026-07-29" and r["url"].endswith("/exc-991.htm") and r["url"].startswith(sec.ARCHIVE.format(cik=123, acc="c3"))
