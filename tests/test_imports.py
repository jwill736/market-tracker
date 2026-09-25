from fastapi.testclient import TestClient

from market_tracker import api, importers

CB_NEW = '''Transactions
User,someone,00000000-0000-0000-0000-000000000000
ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread,Notes
a1,2025-01-05 14:22:10 UTC,Buy,BTC,0.01,USD,"$40,000.00",$400.00,$405.99,$5.99,Bought 0.01 BTC for $405.99 USD
a2,2025-02-01 09:00:00 UTC,Staking Income,ETH,0.002,USD,"$3,000.00",$6.00,$6.00,$0.00,
a3,2025-03-01 10:00:00 UTC,Buy,ETH,1,USD,"$2,500.00","$2,500.00","$2,515.00",$15.00,
a4,2025-04-01 10:00:00 UTC,Convert,ETH,-0.5,USD,"$2,000.00","$1,000.00","$1,000.00",$0.00,"Converted 0.5 ETH to 1,000 USDC"
a5,2025-05-01 10:00:00 UTC,Send,BTC,-0.001,USD,"$60,000.00",$60.00,$60.00,$0.00,
a6,2025-06-01 10:00:00 UTC,Advanced Trade Sell,BTC,-0.004,USD,"$65,000.00",$260.00,$258.00,$2.00,
'''

CB_OLD = '''You can use this transaction report to inform your likely tax obligations.

Timestamp,Transaction Type,Asset,Quantity Transacted,Spot Price Currency,Spot Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread,Notes
2022-01-01T12:00:00Z,Buy,SOL,2,USD,170.00,340.00,345.00,5.00,Bought 2 SOL
2022-02-01T12:00:00Z,Rewards Income,SOL,0.01,USD,110.00,1.10,1.10,0,
'''


def test_coinbase_new_layout():
    res = importers.parse_coinbase(CB_NEW)
    got = [(t["symbol"], t["side"], t["quantity"], t["price"], t["fees"]) for t in res.transactions]
    assert got == [("BTC-USD", "buy", 0.01, 40000.0, 5.99), ("ETH-USD", "buy", 0.002, 3000.0, 0.0),
                   ("ETH-USD", "buy", 1.0, 2500.0, 15.0), ("ETH-USD", "sell", 0.5, 2000.0, 0.0),
                   ("USDC-USD", "buy", 1000.0, 1.0, 0.0), ("BTC-USD", "sell", 0.004, 65000.0, 2.0)]
    assert dict(res.skipped) == {"Send": 1} and not res.errors
    assert all(t["account"] == "Coinbase" for t in res.transactions)
    assert len({t["import_key"] for t in res.transactions}) == len(res.transactions)
    assert importers.parse_coinbase(CB_NEW).transactions[0]["import_key"] == res.transactions[0]["import_key"]


def test_coinbase_old_layout_and_bad_file():
    res = importers.parse_coinbase(CB_OLD)
    assert [(t["symbol"], t["quantity"], t["price"]) for t in res.transactions] == [("SOL-USD", 2, 170.0), ("SOL-USD", 0.01, 110.0)]
    assert "staking" not in res.transactions[0]["note"] and "rewards income" in res.transactions[1]["note"]
    assert importers.parse_coinbase("a,b\n1,2").errors


def test_holdings_list():
    res = importers.parse_holdings_list("symbol shares cost\nVOO 3.214 $1450.20 2024-03-01\nbtc, 0.004, 260\nbad line\nX 0 5",
                                        "Stash", "2026-09-25")
    got = [(t["symbol"], t["quantity"], round(t["price"], 4), t["date"], t["account"]) for t in res.transactions]
    assert got == [("VOO", 3.214, round(1450.20 / 3.214, 4), "2024-03-01", "Stash"),
                   ("BTC-USD", 0.004, 65000.0, "2026-09-25", "Stash")]
    assert "date unknown" in res.transactions[1]["note"] and len(res.errors) == 2


def test_import_endpoint_all_sources_and_accounts(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    c = TestClient(api.app)
    assert c.post("/api/import/coinbase", json={"csv": CB_NEW, "commit": True}).json()["new"] == 6
    r = c.post("/api/import/holdings", json={"csv": "VOO 2 800 2024-01-02\nBTC 0.01 500", "commit": True,
                                              "account": "Stash"}).json()
    assert r["new"] == 2
    c.post("/api/transactions", json={"symbol": "AAPL", "side": "buy", "quantity": 1, "price": 200,
                                      "date": "2026-01-02", "account": "Robinhood"})
    held = {h["symbol"]: h for h in c.get("/api/holdings").json()}
    assert held["BTC-USD"]["accounts"] == ["Coinbase", "Stash"] and held["AAPL"]["accounts"] == ["Robinhood"]
    assert round(held["BTC-USD"]["quantity"], 6) == 0.016
    over = "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\nz,2025-01-01 00:00:00 UTC,Sell,DOGE,100,USD,$0.10,$10,$10,$0,\n"
    bad = c.post("/api/import/coinbase", json={"csv": over})
    assert bad.status_code == 400 and "Quick add" in bad.json()["detail"]
    assert c.post("/api/import/nope", json={"csv": "x"}).status_code == 422
