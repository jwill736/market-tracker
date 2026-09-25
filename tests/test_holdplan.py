from datetime import date

import pytest
from fastapi.testclient import TestClient

from market_tracker import api, events, fundamentals, holdplan, sentinel, service

TODAY = date(2026, 9, 25)


def pos(sym, qty, price, value=None, cost_pct=None):
    return {"symbol": sym, "quantity": qty, "price": price, "market_value": value if value is not None else qty * price,
            "unrealized_pct": cost_pct}


def tx(i, sym, qty, price, day, side="buy", account="Robinhood"):
    return {"id": i, "symbol": sym, "side": side, "quantity": qty, "price": price, "fees": 0, "date": day, "account": account}


def test_everything_holds_unless_something_real_fires():
    positions = [pos("AAPL", 10, 200), pos("MSFT", 5, 400), pos("KO", 20, 60)]
    plan = holdplan.build(positions, [], {}, {}, TODAY)
    assert [h["verdict"] for h in plan["holdings"]] == ["Hold", "Hold", "Hold"]
    assert all(h["triggers"][0]["kind"] == "thesis" for h in plan["holdings"])      # nudged to write a reason


def test_tripwires_filings_and_concentration():
    positions = [pos("NVDA", 100, 220), pos("GPUS", 1000, 0.18, cost_pct=-60), pos("KO", 10, 60), pos("AAPL", 10, 250)]
    theses = {"GPUS": holdplan.Thesis("GPUS", "AI data centers", "if they keep issuing shares", price_below=0.20,
                                      max_loss_pct=50),
              "AAPL": holdplan.Thesis("AAPL", "services growth", price_above=240, review_on="2026-09-01")}
    radar = {"KO": [{"level": 2, "headline": "Auditor change (8-K 4.01)", "filed": "2026-09-10"}],
             "NVDA": [{"level": 2, "headline": "Old restatement", "filed": "2026-01-10"}]}             # outside 90 days
    plan = holdplan.build(positions, [], theses, radar, TODAY)
    by = {h["symbol"]: h for h in plan["holdings"]}
    assert by["GPUS"]["verdict"] == "Sell?" and "sell below $0.20" in by["GPUS"]["triggers"][0]["text"]
    assert plan["cap"] == 0.375                                                        # 4 holdings: 1.5 / 4
    assert by["NVDA"]["verdict"] == "Trim" and by["NVDA"]["trim_value"] == pytest.approx(22000 - 0.375 * plan["base"], abs=0.01)
    assert by["KO"]["verdict"] == "Review" and "Auditor change" in by["KO"]["triggers"][-1]["text"]
    assert by["AAPL"]["verdict"] == "Trim" and any("review date" in t["text"].lower() for t in by["AAPL"]["triggers"])
    assert [h["verdict"] for h in plan["holdings"]][:1] == ["Sell?"]


def test_wait_for_long_term_and_reinvest_queue():
    positions = [pos("NVDA", 100, 220), pos("MSFT", 10, 400), pos("VTI", 1, 300)]
    txs = [tx(1, "NVDA", 100, 100, "2025-10-20"), tx(2, "MSFT", 10, 380, "2024-01-01"), tx(3, "VTI", 1, 250, "2024-01-01")]
    theses = {"MSFT": holdplan.Thesis("MSFT", "cloud", target_weight=0.40)}
    plan = holdplan.build(positions, txs, theses, {}, TODAY, cash=1000, watch=["COST"])
    nvda = next(h for h in plan["holdings"] if h["symbol"] == "NVDA")
    assert nvda["verdict"] == "Trim" and nvda["wait_until"] == "2025-10-20".replace("2025-10-20", "2026-10-21")
    assert nvda["wait_saves"] > 0 and any("long-term" in t["text"] for t in nvda["triggers"])
    q = plan["reinvest"]
    assert q[0]["symbol"] == "MSFT" and q[0]["source"] == "target" and q[1]["symbol"] == "COST"
    assert plan["freed"] == pytest.approx(nvda["trim_value"] + 1000)


def test_endpoints(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    holdplan.clear_cache()
    api.events_cache.store.clear()
    monkeypatch.setattr(service, "portfolio_summary", lambda txs, risk=True: {"positions": [pos("NKE", 20, 60)], "total_value": 1200})
    monkeypatch.setattr(sentinel.sentinel, "mine", lambda syms: ([], []))
    sentinel.radar_cache.clear()
    monkeypatch.setattr(fundamentals, "build", lambda syms, theses=None, get=None: ({}, []))
    monkeypatch.setattr(events, "build", lambda positions, today: {"earnings": [{"symbol": "NKE", "date": "2026-10-01", "days": 6,
                                                                               "move_pct": 7.5, "move_dollars": 90.0}],
                                                                  "macro": [], "errors": []})
    c = TestClient(api.app)
    c.post("/api/transactions", json={"symbol": "NKE", "side": "buy", "quantity": 20, "price": 80, "date": "2026-02-01"})
    r = c.post("/api/thesis/nke", json={"thesis": "brand", "wrong_if": "margins keep falling", "price_below": 65})
    assert r.status_code == 200 and r.json()["price_below"] == 65
    assert c.post("/api/thesis/NKE", json={"review_on": "tomorrow"}).status_code == 422
    plan = c.get("/api/holdplan").json()
    h = plan["holdings"][0]
    assert h["verdict"] == "Sell?" and h["earnings"]["move_dollars"] == 90.0
    assert plan["tax"]["harvest"][0]["symbol"] == "NKE"
    assert c.post("/api/holdplan/settings", json={"cap": 0.15}).json()["cap"] == 0.15
    assert c.get("/api/events").json()["earnings"][0]["symbol"] == "NKE"
    nm = c.get("/api/holdplan/newmoney?amount=300").json()
    assert nm["skipped"][0]["symbol"] == "NKE" and nm["buys"][0]["symbol"] == "VTI"      # NKE is flagged Sell?
    assert c.get("/api/holdplan/newmoney?amount=0").status_code == 422
    assert c.post("/api/taxes/yearend/settings", json={"filing": "married", "taxable_income": 50000}).json()["taxable_income"] == 50000
    ye = c.get("/api/taxes/yearend").json()
    assert ye["harvest"][0]["symbol"] == "NKE" and ye["zero_bracket"]["limit"] == 98900
    assert c.post("/api/taxes/yearend/settings", json={"filing": "nope"}).status_code == 422
    assert c.delete("/api/thesis/NKE").json() == {}


# ------------------------------------------------------------------ new money

def _plan(rows, base, blackout=(), cap=0.2):
    return {"holdings": rows, "base": base, "cap": cap, "tax": {"blackout": list(blackout)}}


def _row(sym, value, base, verdict="Hold", target=None, price=10.0):
    return {"symbol": sym, "value": value, "weight": value / base, "verdict": verdict, "price": price,
            "thesis": {"target_weight": target} if target else None}


def test_new_money_no_targets_keeps_the_mix():
    rows = [_row("A", 600, 1000, price=20), _row("B", 400, 1000)]
    out = holdplan.new_money(_plan(rows, 1000, cap=0.75), 500)
    got = {b["symbol"]: b["amount"] for b in out["buys"]}
    assert got == {"A": 300.0, "B": 200.0}
    assert out["buys"][0]["shares"] == 15.0 and not out["has_targets"]


def test_new_money_fills_the_biggest_shortfall_and_skips_flagged_and_wash_sale():
    base = 10000
    rows = [_row("A", 1000, base, target=0.20), _row("B", 1800, base, target=0.20),
            _row("C", 500, base, verdict="Review", target=0.2), _row("D", 200, base, target=0.1),
            _row("E", 6500, base)]
    blackout = [{"symbol": "D", "avoid": ["D"], "until": "2026-10-20"}]
    out = holdplan.new_money(_plan(rows, base, blackout, cap=0.7), 1000)
    got = {b["symbol"]: b["amount"] for b in out["buys"]}
    # A is 1,200 short of 20% of 11,000, B 400 short, and E (no target) keeps its share of the
    # 60% the targets leave: 100 short. 1,000 split 12:4:1.
    assert got == {"A": 705.88, "B": 235.29, "E": 58.82}
    assert {s["symbol"] for s in out["skipped"]} == {"C", "D"}
    assert "Wash-sale window until 2026-10-20" in next(s["why"] for s in out["skipped"] if s["symbol"] == "D")


def test_new_money_leftover_goes_to_the_broad_fund_and_small_amounts_are_not_split():
    base = 1000
    rows = [_row("A", 150, base, target=0.2), _row("B", 850, base, target=0.8)]
    out = holdplan.new_money(_plan(rows, base, cap=0.9), 1000)
    got = {b["symbol"]: b["amount"] for b in out["buys"]}
    assert got["A"] == 250.0 and got["B"] == 750.0
    out = holdplan.new_money(_plan([_row("A", 900, 2000, target=0.5), _row("B", 1100, 2000, target=0.5)], 2000, cap=0.9), 8)
    assert [(b["symbol"], b["amount"]) for b in out["buys"]] == [("A", 8.0)]
    out = holdplan.new_money(_plan([], 0), 100)
    assert out["buys"][0]["symbol"] == "VTI" and out["buys"][0]["amount"] == 100
