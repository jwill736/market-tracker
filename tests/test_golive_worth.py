from datetime import date, timedelta

from market_tracker import db, decisions, golive, worth

TODAY = date(2026, 9, 28)


def _conf(rows):
    total = sum(r["value"] for r in rows)
    return {"total_value": total, "checked_value": sum(r["value"] for r in rows if r["state"] in ("broker", "statement")), "holdings": rows}


def test_money_decisions_wait_until_the_numbers_match():
    items = decisions.build({"holdings": [{"symbol": "BIG", "verdict": "Trim", "value": 30000.0, "trim_value": 6000.0, "triggers": []}],
                             "reinvest": [], "tax": {"harvest": []}}, idle_cash=500.0,
                            shelter={"kind": "tax_setup", "title": "Tell Plumbline which accounts are tax-sheltered", "why": ["x"], "amount": None},
                            today=TODAY)
    ok = golive.trust(_conf([{"account": "Robinhood", "symbol": "BIG", "value": 9000.0, "state": "statement", "detail": ""},
                             {"account": "Stash", "symbol": "VOO", "value": 1000.0, "state": "unchecked", "detail": ""}]))
    assert ok["trusted"] and golive.gate(items, ok, TODAY) == items
    off = golive.trust(_conf([{"account": "Robinhood", "symbol": "BIG", "value": 9000.0, "state": "mismatch", "detail": "broker differs by -3"},
                              {"account": "Stash", "symbol": "VOO", "value": 1000.0, "state": "broker", "detail": ""}]))
    assert not off["trusted"] and "Robinhood BIG (broker differs by -3)" in off["text"]
    gated = golive.gate(items, off, TODAY)
    assert [d["kind"] for d in gated] == ["fix_data", "tax_setup"]
    assert gated[0]["title"] == "Check your numbers before acting on them" and "2 money decisions are waiting" in gated[0]["why"][1]
    thin = golive.trust(_conf([{"account": "Stash", "symbol": "VOO", "value": 1000.0, "state": "unchecked", "detail": ""}]))
    assert not thin["trusted"] and "Only 0%" in thin["text"]
    assert not golive.trust(None)["trusted"]


def test_settling_in_and_the_record_start():
    with db.connect() as conn:
        db.set_meta(conn, "live_since", "")
        assert golive.live_since(conn, False, TODAY) is None
        assert golive.live_since(conn, True, TODAY) == "2026-09-28"
        assert golive.live_since(conn, True, TODAY + timedelta(days=3)) == "2026-09-28"     # set once
    s = golive.settling("2026-09-28", TODAY + timedelta(days=5))
    assert s["settling"] and "Settling in until Oct 12" in s["text"]
    assert not golive.settling("2026-09-28", TODAY + timedelta(days=14))["settling"]
    assert golive.record_start("2026-09-28") == "2026-10-12"


def _bars(start, prices):
    d0 = date.fromisoformat(start)
    return [((d0 + timedelta(days=i)).isoformat(), p) for i, p in enumerate(prices)]


def test_held_off_sells_count_both_ways():
    txs = [{"symbol": "UP", "side": "buy", "quantity": 10, "price": 50, "date": "2026-01-02"},
           {"symbol": "SOLD", "side": "buy", "quantity": 5, "price": 50, "date": "2026-01-02"},
           {"symbol": "SOLD", "side": "sell", "quantity": 5, "price": 40, "date": "2026-06-05"}]
    log = [{"symbol": "UP", "reason": "scared of the drop", "at": "2026-06-01T15:00:00+00:00", "override": False},
           {"symbol": "SOLD", "reason": "need cash now!", "at": "2026-06-01T15:00:00+00:00", "override": False},
           {"symbol": "UP", "reason": "again", "at": "2026-09-25T15:00:00+00:00", "override": False},        # too recent
           {"symbol": "UP", "reason": "override", "at": "2026-06-01T15:00:00+00:00", "override": True}]
    hist = {"UP": _bars("2026-05-01", [40.0] * 32 + [45.0] * 150)}          # 40 through June 1, then 45
    kept = worth.held_off(log, txs, lambda s: hist.get(s, []), TODAY, None)
    assert kept == [{"symbol": "UP", "day": "2026-06-01", "reason": "scared of the drop", "shares": 10.0, "dollars": 50.0}]
    hist["UP"] = _bars("2026-05-01", [40.0] * 32 + [30.0] * 150)             # it kept falling: holding off cost you
    assert worth.held_off(log, txs, lambda s: hist.get(s, []), TODAY, None)[0]["dollars"] == -100.0
    assert worth.held_off(log, txs, lambda s: hist.get(s, []), TODAY, "2026-07-01") == []   # before the record starts


def test_worth_verdict():
    card = {"items": [{"you": "followed", "results": {30: {"dollars": 10.0}, 91: {"dollars": 120.0}}},
                      {"you": "skipped", "results": {91: {"dollars": 500.0}}}], "harvest_saved": 190.0}
    early = worth.view(card, [], {"2026-10": 1.2}, "2026-09-01", date(2026, 11, 1), 5.0)
    assert early["saved"] == {"followed": 120.0, "harvests": 190.0, "held_off": 0.0} and "Too early" in early["text"]
    late = worth.view({"items": [], "harvest_saved": 0.0}, [], {"2026-10": 3.0}, "2026-01-01", date(2026, 9, 1), 5.0)
    assert late["net"] < 0 and "switch it off and hold VOO" in late["text"]
    assert "Not live yet" in worth.view(card, [], {}, None, TODAY, 5.0)["text"]
