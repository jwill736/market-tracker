from market_tracker import discover, shorts


def _post_factory(calls):
    def post(url, body):
        calls.append(body)
        assert "sortFields" not in body and body["dateRangeFilters"][0]["fieldName"] == "settlementDate"   # FINRA rejects sorting
        syms = body["domainFilters"][0]["values"]
        rows = []
        for s in syms:
            if s == "NONE":
                continue
            for d, q in (("2026-09-15", 30e6 if s == "HOT" else 1e6), ("2026-08-29", 25e6)):
                rows.append({"symbolCode": s, "settlementDate": d, "currentShortPositionQuantity": q,
                             "daysToCoverQuantity": 12.0 if s == "HOT" else 1.5, "averageDailyVolumeQuantity": 2e6})
        return sorted(rows, key=lambda r: r["settlementDate"])                 # oldest first, as FINRA sends them
    return post


def test_latest_and_assess_levels():
    shorts._cache.clear()
    calls = []
    got = shorts.latest(["HOT", "CALM", "NONE", "BRK-B"], _post_factory(calls))
    assert got["HOT"]["settlement"] == "2026-09-15" and got["HOT"]["short"] == 30e6 and "NONE" not in got
    assert "BRK.B" in calls[0]["domainFilters"][0]["values"] and "BRK-B" in got
    shorts.latest(["HOT"], _post_factory(calls))
    assert len(calls) == 1                                   # cached
    hot = shorts.assess(got["HOT"], cap=2e9, price=10.0)     # 200M shares, 30M short
    assert hot["level"] == "heavy" and hot["pct"] == 0.15 and "Heavily shorted" in hot["text"]
    calm = shorts.assess(got["CALM"], cap=2e9, price=10.0)
    assert calm["level"] == "normal" and shorts.assess(None, 1, 1) is None
    assert shorts.assess(dict(got["CALM"], days_to_cover=7.0), None, None)["level"] == "elevated"   # no market value: days to cover alone


def test_heavy_short_interest_counts_against_a_sleeper():
    screen_data = {"small_mid": [{"symbol": "AAA", "score": 80, "cap": 2e9, "flaws": [], "name": "A"},
                                 {"symbol": "BBB", "score": 80, "cap": 2e9, "flaws": [], "name": "B"}], "backlog": []}
    heavy = {"level": "heavy", "pct": 0.2, "text": "Heavily shorted: 20% of shares sold short."}
    rows = discover.sleepers(screen_data, {}, lambda s: None, lambda s, n: None, shorts_fn=lambda syms: {"BBB": heavy})
    assert [r["symbol"] for r in rows] == ["AAA"]           # BBB's one piece of evidence is cancelled out
    chat = discover.chatter({"results": [{"ticker": "BBB", "name": "B Corp", "mentions": 50, "mentions_24h_ago": 10}]}, [],
                            lambda s: {"cap": 5e9, "score": 60}, shorts_fn=lambda syms: {"BBB": heavy})
    assert chat[0]["short"] == "heavy" and any("heavily shorted" in w for w in chat[0]["warnings"])
