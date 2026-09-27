"""The same trade arriving from two sources (a CSV and an API sync or trade emails) is recorded once."""
from market_tracker import accounts, coinbase_sync, db

CB_CSV = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,Price at Transaction,Subtotal,"
          "Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n"
          "b1,2024-01-05 14:03:21 UTC,Buy,BTC,0.01,USD,\"$44,000.00\",$440.00,$446.52,$6.52,Bought\n"
          "s1,2024-12-02 17:30:00 UTC,Advanced Trade Sell,BTC,-0.003,USD,\"$96,000.00\",-$288.00,-$286.20,$1.80,Sold\n")
FILLS = [{"entry_id": "e1", "trade_id": "t1", "trade_time": "2024-01-05T14:03:21Z", "price": "44000", "size": "0.01",
          "commission": "6.52", "product_id": "BTC-USD", "side": "BUY", "size_in_quote": False},
         {"entry_id": "e2", "trade_id": "t2", "trade_time": "2024-12-02T17:30:00Z", "price": "96000", "size": "288",
          "commission": "1.80", "product_id": "BTC-USD", "side": "SELL", "size_in_quote": True},
         {"entry_id": "e3", "trade_id": "t3", "trade_time": "2025-03-03T10:00:00Z", "price": "90000", "size": "0.001",
          "commission": "0.5", "product_id": "BTC-USD", "side": "BUY", "size_in_quote": False}]


def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from market_tracker import api
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    with db.connect() as conn:
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM transfer_legs")
    return TestClient(api.app)


def _btc(c) -> float:
    return {h["symbol"]: h for h in c.get("/api/holdings").json()}["BTC-USD"]["quantity"]


def test_csv_then_api_sync(monkeypatch):
    c = _client(monkeypatch)
    assert c.post("/api/import/coinbase", json={"csv": CB_CSV, "commit": True}).json()["new"] == 2
    monkeypatch.setattr(coinbase_sync, "configured", lambda: True)
    monkeypatch.setattr(coinbase_sync, "fills", lambda get=None, pages=20: FILLS)
    monkeypatch.setattr(coinbase_sync, "balances", lambda get=None: {"BTC": 0.008, "USD": 12.0})
    r = accounts.sync_coinbase()
    assert r["new"] == 1 and r["duplicates"] == 2 and r["differences"] == []
    assert abs(_btc(c) - 0.008) < 1e-9


def test_api_sync_then_csv(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(coinbase_sync, "configured", lambda: True)
    monkeypatch.setattr(coinbase_sync, "fills", lambda get=None, pages=20: FILLS)
    monkeypatch.setattr(coinbase_sync, "balances", lambda get=None: {"BTC": 0.008})
    assert accounts.sync_coinbase()["new"] == 3
    r = c.post("/api/import/coinbase", json={"csv": CB_CSV, "commit": True}).json()
    assert r["new"] == 0 and r["duplicates"] == 2
    assert abs(_btc(c) - 0.008) < 1e-9
