from datetime import date

import pytest
from fastapi.testclient import TestClient

from market_tracker import api, events, holdplan, sentinel, service

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
    api.holdplan_cache.store.clear()
    api.events_cache.store.clear()
    monkeypatch.setattr(service, "portfolio_summary", lambda txs, risk=True: {"positions": [pos("NKE", 20, 60)], "total_value": 1200})
    monkeypatch.setattr(sentinel.sentinel, "mine", lambda syms: ([], []))
    sentinel.radar_cache.clear()
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
    assert c.delete("/api/thesis/NKE").json() == {}
