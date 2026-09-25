from datetime import date

import pytest
from fastapi.testclient import TestClient

from market_tracker import api, livefeed, pulse, strategy
from market_tracker.providers import market
from market_tracker.pulse import Flag

TODAY = date(2026, 9, 25)


def analysis(score=20.0, max_w=0.10, above=True, price=100.0, **ind):
    return {"quote": {"price": price}, "signal": {"score": score, "label": "Bullish", "suggested_max_weight": max_w},
            "indicators": dict(above_sma200=above, **ind), "insiders": None}


def pos(sym, qty, price, cost=None):
    return {"symbol": sym, "quantity": qty, "price": price, "market_value": qty * price,
            "cost_basis": (cost if cost is not None else price) * qty}


def summary(*ps):
    return {"positions": list(ps), "total_value": sum(p["market_value"] for p in ps)}


# ------------------------------------------------------------------ lots and taxes

TXS = [
    {"symbol": "AAPL", "side": "buy", "quantity": 10, "price": 100.0, "fees": 0, "date": "2024-01-10", "id": 1},
    {"symbol": "AAPL", "side": "buy", "quantity": 10, "price": 150.0, "fees": 0, "date": "2026-06-01", "id": 2},
    {"symbol": "AAPL", "side": "sell", "quantity": 4, "price": 160.0, "fees": 0, "date": "2026-07-01", "id": 3},
]


def test_open_lots_use_oldest_first():
    lots = strategy.open_lots(TXS, "AAPL")
    assert [(lot.date, lot.quantity) for lot in lots] == [("2024-01-10", 6), ("2026-06-01", 10)]


def test_tax_notes():
    lots = strategy.open_lots(TXS, "AAPL")
    assert strategy.tax_note(lots, 6, 200.0, TODAY, False) == "Long-term gain of about $600"
    note = strategy.tax_note(lots, 16, 200.0, TODAY, False)
    assert "$500 of the $1,100 gain is short-term" in note and "2027-06-02" in note
    whole = strategy.tax_note(strategy.open_lots(TXS[1:2], "AAPL"), 10, 200.0, TODAY, False)
    assert whole.startswith("The whole gain is short-term (taxed as income, about $500)")
    loss = strategy.tax_note(lots, 16, 90.0, TODAY, False)
    assert "loss of about $660" in loss and "wash sale" in loss
    assert "wash sale" not in strategy.tax_note(lots, 16, 90.0, TODAY, True)       # not applied to crypto
    assert strategy.tax_note(lots, 1, 100.0, TODAY, False) is None                   # no gain, no note


# ------------------------------------------------------------------ rules per holding

def test_sell_needs_many_flags_and_a_bad_signal():
    flags = [Flag(2, "Below its 200-day"), Flag(2, "Composite bearish")]
    a = strategy.plan_holding(pos("X", 10, 50.0), analysis(score=-30), flags, base=10_000)
    assert a.action == "Sell" and a.shares == 10 and a.target_weight == 0
    b = strategy.plan_holding(pos("X", 10, 50.0), analysis(score=5), flags, base=10_000)
    assert b.action == "Trim" and b.target_weight == pytest.approx(0.025)             # 3+ flags, signal not bad: halve


def test_oversized_position_trims_to_its_limit():
    a = strategy.plan_holding(pos("BIG", 100, 50.0), analysis(max_w=0.10), [], base=10_000)   # 50%
    assert a.action == "Trim" and a.target_weight == 0.10 and a.shares == 80 and a.value == 4000
    assert "50% of your portfolio" in a.reasons[0]
    capped = strategy.plan_holding(pos("BIG", 100, 50.0), analysis(max_w=0.35), [], base=10_000)
    assert capped.limit == strategy.MAX_WEIGHT


def test_add_hold_and_missing_data():
    add = strategy.plan_holding(pos("S", 2, 100.0), analysis(score=30), [], base=10_000)            # 2% of a 10% limit
    assert add.action == "Add" and add.shares == 0
    assert add.target_weight == pytest.approx(0.06)                                                 # 2% + 4 points
    below = strategy.plan_holding(pos("S", 2, 100.0), analysis(score=30, above=False), [], base=10_000)
    assert below.action == "Hold"
    over = strategy.plan_holding(pos("S", 11, 100.0), analysis(max_w=0.10), [], base=10_000)       # 11% vs 10%
    assert over.action == "Hold" and "not by enough to trim" in over.reasons[0]
    assert strategy.plan_holding(pos("S", 2, 100.0), None, [], base=10_000).reasons[0].startswith("Couldn't")


# ------------------------------------------------------------------ whole plan

def test_plan_funds_adds_and_buys_from_sales_and_cash():
    s = summary(pos("BIG", 100, 50.0), pos("SMALL", 2, 100.0))           # 5,000 + 200
    analyses = {"BIG": analysis(max_w=0.10), "SMALL": analysis(score=40),
                "NEW": analysis(score=50, price=20.0), "WEAK": analysis(score=10), "DOWN": analysis(score=60, above=False)}
    plan = strategy.build_plan(s, analyses, {}, [{"symbol": "BIG", "side": "buy", "quantity": 100, "price": 40.0,
                                                   "fees": 0, "date": "2026-01-02"}],
                               cash=500, candidates=[("NEW", "Watchlist"), ("WEAK", "Watchlist"), ("DOWN", "Sleeper: x"),
                                                     ("SMALL", "Watchlist")], today=TODAY)
    by = {a["symbol"]: a for a in plan["actions"]}
    base = 5700
    assert by["BIG"]["action"] == "Trim" and by["BIG"]["value"] == pytest.approx(5000 - 0.10 * base)
    assert "short-term" in by["BIG"]["tax"]
    assert by["SMALL"]["action"] == "Add" and by["SMALL"]["value"] == pytest.approx(0.04 * base)   # one 4-point step
    assert by["NEW"]["action"] == "Buy" and by["NEW"]["value"] == pytest.approx(0.04 * base) and by["NEW"]["shares"] == 11.4
    assert "WEAK" not in by and "DOWN" not in by                         # signal too low / below the 200-day
    assert [a["action"] for a in plan["actions"]] == ["Trim", "Add", "Buy"]
    t = plan["totals"]
    assert t["cash_after"] == pytest.approx(500 + t["sell_value"] - t["buy_value"]) and t["cash_after"] >= 0


def test_adds_without_money_become_holds():
    others = [pos(f"P{i}", 1, 1000.0) for i in range(9)]             # nine holdings near their 10% limit
    analyses = {f"P{i}": analysis(score=0) for i in range(9)} | {"A": analysis(score=40)}
    plan = strategy.build_plan(summary(pos("A", 1, 100.0), *others), analyses, {}, [], cash=0, today=TODAY)
    assert all(a["action"] == "Hold" for a in plan["actions"])
    a = next(a for a in plan["actions"] if a["symbol"] == "A")
    assert a["action"] == "Hold" and "no cash" in a["reasons"][-1]


# ------------------------------------------------------------------ extended-hours prices

def chart(regular, bars, periods, prev=100.0, regular_time=1000):
    return {"meta": {"regularMarketPrice": regular, "chartPreviousClose": prev, "regularMarketTime": regular_time,
                     "currentTradingPeriod": periods},
            "timestamp": [t for t, _ in bars], "indicators": {"quote": [{"close": [c for _, c in bars]}]}}


PERIODS = {"pre": {"start": 0, "end": 500}, "regular": {"start": 500, "end": 1000}, "post": {"start": 1000, "end": 1500}}


def test_live_quote_uses_after_hours_trades():
    q = market.parse_live_quote("AAPL", chart(110.0, [(900, 109.0), (1200, 112.0), (1260, None)], PERIODS), now=1300)
    assert q.session == "post" and q.price == 112.0 and q.change_pct == pytest.approx(12.0)
    reg = market.parse_live_quote("AAPL", chart(110.0, [(900, 109.5)], PERIODS, regular_time=950), now=950)
    assert reg.session == "regular" and reg.price == 110.0
    assert market.market_session({"currentTradingPeriod": PERIODS}, now=2000) == "closed"
    assert market.market_session({"currentTradingPeriod": PERIODS}, now=100) == "pre"


def test_poll_passes_the_session_along():
    import asyncio
    hub = livefeed.PriceHub(min_gap=0)
    client = hub.subscribe(["AAPL"])

    def quote(sym):
        return market.Quote(sym, "stock", 112.0, 100.0, 12.0, "USD", "yahoo", "t", "post")

    async def stop(_):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(livefeed.poll_stocks(hub, quote_fn=quote, sleep=stop))
    assert client.queue.get_nowait().session == "post"


# ------------------------------------------------------------------ endpoint

@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    for c in (api.plan_cache, pulse.analysis_cache, pulse.pulse_cache):
        c.store.clear()
    return TestClient(api.app)


def test_plan_endpoint_logs_calls_once_a_day(client, monkeypatch):
    monkeypatch.setattr(market, "get_quote", lambda s: market.Quote(s, "stock", 50.0, 49.0, 2.0, "USD", "t", "t"))
    monkeypatch.setattr(api.service, "analyze", lambda s, **kw: analysis(score=30, max_w=0.10, price=50.0))
    monkeypatch.setattr(pulse, "cik_for_symbol", lambda s: None)
    for sym, qty in (("AAA", 100), ("BBB", 5)):
        client.post("/api/transactions", json={"symbol": sym, "side": "buy", "quantity": qty, "price": 40.0,
                                               "date": "2026-01-02"})
    client.post("/api/watchlist/NEWCO")
    plan = client.get("/api/plan?cash=1000").json()
    by = {a["symbol"]: a for a in plan["actions"]}
    assert by["AAA"]["action"] == "Trim" and by["BBB"]["action"] == "Add" and by["NEWCO"]["action"] == "Buy"
    assert {(h["symbol"], h["action"]) for h in plan["history"]} == {("AAA", "Trim"), ("BBB", "Add"), ("NEWCO", "Buy")}
    api.plan_cache.store.clear()
    again = client.get("/api/plan?cash=1000").json()
    assert len(again["history"]) == 3                                   # same day: not logged twice
    assert client.get("/api/plan?cash=-5").status_code == 422


def test_plan_endpoint_with_nothing_held(client):
    plan = client.get("/api/plan").json()
    assert plan["actions"] == [] and plan["history"] == []
