import json
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from market_tracker import api, brokers, db, trading
from market_tracker.providers import market


class Q:
    def __init__(self, price):
        self.price, self.change_pct, self.previous_close, self.as_of, self.source = price, 0.0, price, None, "test"


class R:
    def __init__(self, code, js=None):
        self.status_code, self._js = code, js
        self.content = b"x" if js is not None else b""
        self.text = json.dumps(js)

    def json(self):
        return self._js


def test_paper_account_preview_confirm_fill(monkeypatch):
    monkeypatch.setattr(market, "get_live_quote", lambda s: Q(200.0))
    c = TestClient(api.app)
    # Paper works with trading switched off: it's pretend money.
    p = c.post("/api/trade/preview", json={"venue": "paper", "symbol": "AAPL", "side": "buy", "dollars": 1000}).json()
    assert not p["blockers"] and p["token"] and "Paper account" in p["warnings"][0] and p["estimate"]["quantity"] == 5
    r = c.post("/api/trade/place", json={"venue": "paper", "symbol": "AAPL", "side": "buy", "dollars": 1000, "token": p["token"]})
    assert r.status_code == 200 and "Paper fill: 5 AAPL" in r.json()["note"]
    b = c.get("/api/brokers").json()
    assert b["paper"]["cash"] == 9000 and b["paper"]["positions"]["AAPL"]["quantity"] == 5
    assert c.get("/api/transactions").json() == []                    # the real ledger is untouched
    # Can't reuse the code; can't sell what the paper account doesn't hold; can't spend more than it has.
    again = c.post("/api/trade/place", json={"venue": "paper", "symbol": "AAPL", "side": "buy", "dollars": 1000, "token": p["token"]})
    assert again.status_code == 400
    s = c.post("/api/trade/preview", json={"venue": "paper", "symbol": "AAPL", "side": "sell", "quantity": 6}).json()
    assert s["blockers"] and not s["token"]
    big = c.post("/api/trade/preview", json={"venue": "paper", "symbol": "AAPL", "side": "buy", "dollars": 20000}).json()
    assert "pretend cash" in big["blockers"][0]
    # A limit that wouldn't fill now is refused.
    lp = c.post("/api/trade/preview", json={"venue": "paper", "symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150}).json()
    bad = c.post("/api/trade/place", json={"venue": "paper", "symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150,
                                           "token": lp["token"]})
    assert bad.status_code == 400 and "past your $150.00 limit" in bad.json()["detail"]
    assert c.post("/api/brokers/paper/reset", json={"start": 5000}).json()["cash"] == 5000


def test_alpaca_order_body_and_fill():
    b = brokers.alpaca_order_body("VOO", "buy", 25.0, None, None, "cid")
    assert b == {"symbol": "VOO", "side": "buy", "type": "market", "time_in_force": "day", "client_order_id": "cid", "notional": "25.00"}
    b = brokers.alpaca_order_body("VOO", "sell", None, 0.123456789, 512.345, "cid")
    assert b["qty"] == "0.123456789" and b["type"] == "limit" and b["limit_price"] == "512.35"
    assert brokers.alpaca_fill({"status": "new", "filled_qty": "0"}) is None
    assert brokers.alpaca_fill({"status": "filled", "filled_qty": "0.05", "filled_avg_price": "500.1", "filled_at": "2026-09-25T14:00:00Z"}) == \
        {"quantity": 0.05, "price": 500.1, "date": "2026-09-25"}


def test_alpaca_sends_key_headers_to_paper_by_default(monkeypatch):
    monkeypatch.setenv("ALPACA_KEY_ID", "AKTEST1234")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret123")
    seen = []

    def send(method, url, **kw):
        seen.append((method, url, kw.get("json")))
        return R(200, {"id": "ord-1", "status": "accepted"}) if method == "POST" else R(422, {"code": 1, "message": "bad symbol"})
    assert brokers.alpaca_place("VOO", "buy", 10, None, None, "c1", send)["id"] == "ord-1"
    assert seen[0][1] == "https://paper-api.alpaca.markets/v2/orders" and seen[0][2]["notional"] == "10.00"
    with pytest.raises(brokers.BrokerError, match="422: bad symbol"):
        brokers.alpaca_order("x", send)
    monkeypatch.setenv("ALPACA_LIVE", "1")
    brokers.alpaca_place("VOO", "buy", 10, None, None, "c2", send)
    assert seen[-1][1] == "https://api.alpaca.markets/v2/orders"


def test_public_token_account_and_order(monkeypatch):
    monkeypatch.setenv("PUBLIC_API_SECRET", "sec-xyz-123")
    monkeypatch.delenv("PUBLIC_ACCOUNT_ID", raising=False)
    brokers._public_token.clear()
    calls = []

    def send(method, url, headers, **kw):
        calls.append((method, url, headers.get("Authorization"), kw.get("json")))
        if url.endswith("/access-tokens"):
            return R(200, {"accessToken": "tok"})
        if url.endswith("/trading/account"):
            return R(200, {"accounts": [{"accountId": "IRA1", "accountType": "ROTH_IRA"}, {"accountId": "B1", "accountType": "BROKERAGE"}]})
        return R(200, {"orderId": kw["json"]["orderId"]})
    out = brokers.public_place("VOO", "buy", 12.5, None, None, "11111111-1111-4111-8111-111111111111", send)
    assert out["orderId"].startswith("1111")
    token_call, acct_call, order_call = calls
    assert token_call[3] == {"secret": "sec-xyz-123", "validityInMinutes": 60} and token_call[2] is None
    assert order_call[1] == "https://api.public.com/userapigateway/trading/B1/order" and order_call[2] == "Bearer tok"
    assert order_call[3] == {"orderId": "11111111-1111-4111-8111-111111111111", "instrument": {"symbol": "VOO", "type": "EQUITY"},
                             "orderSide": "BUY", "orderType": "MARKET", "expiration": {"timeInForce": "DAY"}, "amount": "12.50"}
    assert brokers.public_fill({"status": "FILLED", "filledQuantity": "0.0231", "averagePrice": "541.2", "closedAt": "2026-09-25T15:00:00Z"}) == \
        {"quantity": 0.0231, "price": 541.2, "date": "2026-09-25"}


def test_settle_pending_adds_live_fills_once(monkeypatch):
    monkeypatch.setenv("ALPACA_KEY_ID", "AKTEST1234")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret123")
    monkeypatch.setenv("ALPACA_LIVE", "1")
    with db.connect() as conn:
        o = trading.Order("alpaca", "VOO", "buy", 50.0)
        trading.log(conn, o, 50.0, "placed", "ord-9", {})
        trading.log(conn, trading.Order("public", "VTI", "buy", 20.0), 20.0, "placed", "pub-1", {})
        get_a = lambda i: {"status": "filled", "filled_qty": "0.1", "filled_avg_price": "500", "filled_at": "2026-09-25T14:00:00Z"}  # noqa: E731
        get_p = lambda i: {"status": "NEW"}  # noqa: E731
        added = trading.settle_pending(conn, get_a, get_p)
        assert added == [{"symbol": "VOO", "side": "buy", "quantity": 0.1, "price": 500.0, "account": "Alpaca"}]
        assert trading.settle_pending(conn, get_a, get_p) == []            # once
        tx = db.list_transactions(conn)
        assert [(t["symbol"], t["account"], t["import_key"]) for t in tx] == [("VOO", "Alpaca", "alp:ord-9")]
        assert trading.spent_today(conn) == 70.0                            # filled orders still count toward today's limit
