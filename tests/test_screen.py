from datetime import date

from market_tracker import screen


def test_pct_ranks_and_ties():
    r = screen.pct_ranks({"a": 1, "b": 2, "c": 2, "d": 3})
    assert r["a"] == 0 and r["d"] == 100 and r["b"] == r["c"] == 50
    assert screen.pct_ranks({"a": 1, "b": 3}, higher_better=False) == {"a": 100, "b": 0}


def test_momentum_skips_the_last_month():
    closes = [100.0] * 30 + [100.0 + i for i in range(230)]
    m, r12, above = screen.momentum(closes)
    assert round(m, 3) == round(closes[-22] / closes[-253] - 1, 3) and r12 > m and above > 0
    assert screen.momentum([1.0] * 100) == (None, None, None)


def test_metrics_and_score_with_disqualifier():
    f = {"assets": 100.0, "assets_ya": 90.0, "operating_income": 40.0, "net_income": 10.0, "equity": 50.0, "shares": 105.0, "shares_ya": 100.0,
         "rev_q": 30.0, "rev_q_ya": 25.0, "revenue": 110.0, "rpo": 200.0, "rpo_ya": 120.0}
    m = screen.metrics(f, cap=200.0)
    assert m["quality"] == 0.4 and m["earnings_yield"] == 0.05 and round(m["issuance"], 2) == 0.05 and round(m["rpo_growth"], 3) == 0.667
    assert screen.metrics(dict(f, operating_income=None), 200.0)["quality"] is None     # banks: no operating income
    assert screen.metrics(dict(f, rpo_ya=5.0), 200.0)["rpo_growth"] is None            # growth from a near-empty backlog
    rows = {}
    for i in range(20):
        rows[f"S{i}"] = {"sector": "Tech", "cap": 1e9, "quality": i / 20, "earnings_yield": i / 100, "book_to_market": i / 50,
                         "momentum": i / 10, "issuance": 0.0}
    rows["S19"]["issuance"] = 0.5                           # heavy dilution: bottom of its sector
    screen.score(rows)
    assert rows["S18"]["score"] > 80 and not rows["S18"]["flaws"]          # issuance ties at 0 sit mid-pack
    assert rows["S19"]["score"] == 50.0 and rows["S19"]["flaws"] == ["low_issuance"]
    assert rows["S0"]["score"] < 20


def test_backlog_picks_need_growth_cover_and_not_most_expensive():
    base = {"name": "", "sector": "X", "cap": 1e9, "price": 10.0, "score": 60.0, "flaws": []}
    rows = {"GROW": dict(base, rpo_growth=0.40, revenue_growth=0.10, rpo_cover=1.2, grades={"value": 55}),
            "PRICY": dict(base, rpo_growth=0.60, revenue_growth=0.10, rpo_cover=2.0, grades={"value": 5}),
            "THIN": dict(base, rpo_growth=0.40, revenue_growth=0.10, rpo_cover=0.2, grades={"value": 60}),
            "SLOW": dict(base, rpo_growth=0.12, revenue_growth=0.10, rpo_cover=1.0, grades={"value": 60})}
    picks = screen.backlog_picks(rows)
    assert [p["symbol"] for p in picks] == ["GROW"] and "Backlog +40% vs revenue +10%" in picks[0]["why"]


def test_build_end_to_end():
    today = date(2026, 9, 27)

    def get(url):
        tag, period = url.split("/")[-3], url.split("/")[-1].replace(".json", "")
        base = {"Assets": 1000.0, "WeightedAverageNumberOfDilutedSharesOutstanding": 100.0, "StockholdersEquity": 400.0, "OperatingIncomeLoss": 300.0,
                "NetIncomeLoss": 80.0, "RevenueFromContractWithCustomerExcludingAssessedTax": 900.0}
        if tag == "Assets" and period == "CY2026Q2I":
            return {"data": [{"cik": c, "val": 1000.0 + c} for c in range(1, 4101)]}
        if tag not in base:
            return {"data": []}
        return {"data": [{"cik": c, "val": base[tag] * (1 + c / 10) * (0.9 if period.startswith("CY2025Q2") else 1)} for c in range(1, 41)]}
    listed = {f"T{c}": {"name": f"Co {c}", "price": 10.0 + c, "cap": 1e9 * c, "sector": "Tech" if c % 2 else "Energy", "industry": ""}
              for c in range(1, 41)}
    listed["T40B"] = dict(listed["T40"], cap=1e8 * 399)          # a second share class of the same company

    class M:
        def cik_for(self, s):
            return str(int(s[1:].rstrip("B"))).zfill(10)
    import market_tracker.providers.sec as sec
    orig = sec.ticker_map
    sec.ticker_map = lambda: M()
    try:
        d = screen.build(today, get=get, history_fn=lambda s: [100.0 + i * int(s[1:]) / 10 for i in range(260)],
                         listed_fn=lambda: listed, sp500={"T40", "T39"}, log=lambda *_: None, workers=2)
    finally:
        sec.ticker_map = orig
    assert d["quarter"] == "CY2026Q2" and d["annual"] == 2025 and d["universe"] == 40
    assert {r["symbol"] for r in d["top_large"]} == {"T40", "T39"} and "T40B" not in d["lookup"]
    assert screen.lookup(d, "T40")["sector"] == "Energy" and screen.lookup(d, "NOPE") is None
