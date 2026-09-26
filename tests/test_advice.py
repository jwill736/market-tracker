from datetime import date

from market_tracker import advice, db


def _bars(start_price, daily):
    from datetime import timedelta
    d0 = date(2026, 1, 1)
    return [((d0 + timedelta(days=i)).isoformat(), start_price * (1 + daily) ** i) for i in range(240)]


def test_record_once_and_score_against_voo():
    plan = {"holdings": [{"symbol": "XYZ", "verdict": "Sell?", "price": 100.0, "triggers": [{"text": "Revenue shrank 2 quarters"}]},
                         {"symbol": "AAA", "verdict": "Hold", "price": 50.0, "triggers": []}],
            "reinvest": [{"symbol": "GOOD", "why": "Below target"}, {"symbol": "VOO", "why": "default"}, {"symbol": "ZZZ", "why": "x"}],
            "dip_hits": ["DIP"]}
    items = advice.from_plan(plan)
    assert [(i["symbol"], i["action"], i["source"]) for i in items] == [
        ("XYZ", "Sell?", "hold"), ("GOOD", "Buy", "reinvest"), ("VOO", "Buy", "reinvest"), ("DIP", "Buy", "dip")]
    prices = {"GOOD": 10.0, "VOO": 400.0, "DIP": 20.0}
    with db.connect() as conn:
        assert advice.record(conn, "2026-01-01", items, prices.get) == 4
        assert advice.record(conn, "2026-01-01", items, prices.get) == 0          # once a day
        db.log_plan(conn, "2026-01-01", [{"action": "Buy", "symbol": "GOOD", "price": 10.0, "shares": 1, "value": 10, "reasons": ["x"]}])
        rows = advice.logged(conn)
    hist = {"VOO": _bars(400, 0.0005), "XYZ": _bars(100, -0.001), "GOOD": _bars(10, 0.002), "DIP": _bars(20, -0.002)}
    r = advice.score(rows, hist.__getitem__, date(2026, 5, 1))
    by = {(i["symbol"], i["source"]): i for i in r["items"]}
    assert by[("XYZ", "hold")]["results"][91]["helped"]           # sold a loser: helped vs VOO
    assert by[("GOOD", "reinvest")]["results"][91]["helped"]      # bought a winner: helped
    assert not by[("DIP", "dip")]["results"][91]["helped"]        # bought the dip, it kept falling: hurt
    assert by[("GOOD", "strategy")]["results"][30]["helped"]
    assert 182 not in by[("XYZ", "hold")]["results"]              # 6 months hasn't passed
    assert r["horizons"][91]["resolved"] == 5 and not r["horizons"][91]["enough"]
    assert r["verdict"].startswith("Too early to tell: 5 pieces of advice have a 3-month result")


def test_verdict_when_enough():
    assert "WORSE" in advice.verdict({91: {"enough": True, "avg_edge": -3.0, "hit_rate": 40.0, "resolved": 30}})
    assert "beat VOO" in advice.verdict({91: {"enough": True, "avg_edge": 2.5, "hit_rate": 60.0, "resolved": 30}})
