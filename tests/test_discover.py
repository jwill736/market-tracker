from market_tracker import discover


def test_sleepers_need_evidence_and_quiet():
    screen_data = {"small_mid": [{"symbol": "AAA", "name": "A Co", "score": 80, "flaws": [], "cap": 2e9, "sector": "Tech", "return_12m_pct": 50, "above_200d": 0.05},
                                 {"symbol": "HOT", "name": "Hot Co", "score": 85, "flaws": [], "cap": 3e9, "sector": "Tech", "return_12m_pct": 97, "above_200d": 0.5},
                                 {"symbol": "LOUD", "name": "Loud", "score": 75, "flaws": [], "cap": 3e9, "sector": "Tech", "return_12m_pct": 40, "above_200d": 0.1}],
                   "backlog": [{"symbol": "AAA", "why": "Backlog +40% vs revenue +10%"}]}
    ins = {"AAA": {"cik": "1", "company": "A Co", "value": 2e6, "reasons": ["3 insiders bought $2.0M"], "insider": "X", "trade_date": "2026-09-01"},
           "BBB": {"cik": "2", "company": "B Co", "value": 1e6, "reasons": ["2 insiders bought $1.0M"], "insider": "Y", "trade_date": "2026-09-01"}}
    lookup = {"BBB": {"cap": 1e9, "score": 55, "return_12m_pct": 30, "above_200d": 0.0, "sector": "Energy"}}.get
    news = {"AAA": 1, "HOT": 0, "LOUD": 12, "BBB": 2}
    rows = discover.sleepers(screen_data, ins, lookup, lambda s, n: news[s], lambda c: {"kind": "routine"} if c["insider"] == "Y" else {"kind": "opportunistic"})
    by = {r["symbol"]: r for r in rows}
    assert "HOT" not in by                          # already ran
    assert by["AAA"]["level"] == "sleeper" and by["AAA"]["evidence"] == 4 and any("not a yearly habit" in w for w in by["AAA"]["why"])
    assert by["LOUD"]["evidence"] == 1 and "people are watching" in by["LOUD"]["why"][-1]
    assert "BBB" not in by           # its insider buying is a yearly habit, and being quiet alone isn't evidence


def test_chatter_ranks_by_heat_and_warns():
    ape = {"results": [{"ticker": "MEME", "name": "Meme Co", "mentions": 400, "mentions_24h_ago": 50}, {"ticker": "SPY", "mentions": 999},
                       {"ticker": "SGOV", "name": "iShares 0-3 Month Treasury Bond ETF", "mentions": 300},
                       {"ticker": "KO", "name": "Coke", "mentions": 20, "mentions_24h_ago": 20}]}
    lookup = {"MEME": {"cap": 5e8, "score": 30, "return_12m_pct": 99, "above_200d": 0.9}, "KO": {"cap": 3e11, "score": 60, "return_12m_pct": 50}}.get
    rows = discover.chatter(ape, [{"symbol": "NEW", "title": "New Co", "summary": "Earnings beat"}], lookup)
    assert [r["symbol"] for r in rows][:1] == ["MEME"] and not {"SPY", "SGOV"} & {r["symbol"] for r in rows}
    meme = rows[0]
    assert meme["rising"] == 8.0 and meme["caution"] == "high" and len(meme["warnings"]) == 4
    new = next(r for r in rows if r["symbol"] == "NEW")
    assert new["stocktwits"] and "not on the screen" in new["warnings"][0]


def test_bucket():
    b = discover.bucket([{"symbol": "AAA", "market_value": 1500.0}, {"symbol": "VOO", "market_value": 8500.0}], {"AAA"})
    assert b["cap"] == 1000.0 and b["over"] and b["share"] == 15.0
