from datetime import date

from fastapi.testclient import TestClient

from market_tracker import accounts, api, db, taxes, transfers
from market_tracker.analytics import portfolio as pf


def _leg(i, direction, qty, day, account, sym="BTC-USD"):
    return transfers.Leg(i, sym, direction, qty, day, account)


def test_match_pairs_by_symbol_days_and_fee():
    legs = [_leg(1, "out", 1.0, "2025-01-10", "Coinbase"),
            _leg(2, "in", 0.9995, "2025-01-11", "Robinhood"),          # 0.05% network fee: fits
            _leg(3, "in", 0.5, "2025-01-11", "Robinhood"),             # too little
            _leg(4, "out", 2.0, "2025-02-01", "Coinbase"),
            _leg(5, "in", 2.0, "2025-02-20", "Robinhood"),             # too late
            _leg(6, "in", 1.0, "2025-01-10", "Coinbase"),              # same account: never a move
            _leg(7, "out", 1.0, "2025-03-01", "Coinbase", sym="ETH-USD"),
            _leg(8, "in", 1.0, "2025-03-01", "Robinhood", sym="SOL-USD")]
    assert transfers.match(legs) == [(1, 2)]


def test_move_keeps_purchase_date_and_cost_across_accounts():
    txs = [{"id": 1, "symbol": "BTC-USD", "side": "buy", "quantity": 1.0, "price": 20000.0, "fees": 0, "date": "2024-01-02",
            "account": "Coinbase"},
           {"id": 2, "symbol": "BTC-USD", "side": "sell", "quantity": 0.999, "price": 60000.0, "fees": 0, "date": "2025-03-01",
            "account": "Robinhood"}]
    mv = [{"id": 9, "symbol": "BTC-USD", "sent_on": "2025-01-10", "arrived_on": "2025-01-11", "from": "Coinbase",
           "to": "Robinhood", "sent": 1.0, "received": 0.999}]
    rows = transfers.with_moves(txs, mv)
    lots, sales = taxes.lots_and_sales(rows)
    # The whole cost ($20,000) goes with the 0.999 that arrived; the sale is long-term (bought 2024-01-02).
    assert len(sales) == 1 and sales[0].long_term and sales[0].account == "Robinhood"
    assert round(sales[0].cost, 2) == 20000.0 and round(sales[0].gain, 2) == round(0.999 * 60000 - 20000, 2)
    assert not lots.get("BTC-USD")
    # Positions: nothing left (no fee dust), realized gain counts the sale only.
    pos = pf.build_positions(rows)["BTC-USD"]
    assert pos.quantity == 0 and round(pos.realized_pnl, 2) == round(0.999 * 60000 - 20000, 2)
    # The "in" row carries the cost per unit that arrived.
    arrived = [r for r in rows if r.get("transfer") == 9 and r["side"] == "buy"][0]
    assert round(arrived["price"] * arrived["quantity"], 2) == 20000.0
    # A move is neither a sale nor a purchase for wash sales or the benchmark.
    assert not taxes.recent_buys(rows, "BTC-USD", date(2025, 1, 20))
    assert transfers.real(rows) == txs


def test_same_day_buy_send_receive_sell_in_order():
    txs = [{"id": 1, "symbol": "ETH-USD", "side": "buy", "quantity": 2.0, "price": 3000.0, "fees": 0, "date": "2025-05-01",
            "account": "Coinbase"},
           {"id": 2, "symbol": "ETH-USD", "side": "sell", "quantity": 2.0, "price": 3100.0, "fees": 0, "date": "2025-05-01",
            "account": "Robinhood"}]
    mv = [{"id": 1, "symbol": "ETH-USD", "sent_on": "2025-05-01", "arrived_on": "2025-05-01", "from": "Coinbase",
           "to": "Robinhood", "sent": 2.0, "received": 2.0}]
    # Same day: bought in Coinbase, moved, sold in Robinhood. The move lands between the two.
    rows = transfers.with_moves(txs, mv)
    lots, sales = taxes.lots_and_sales(rows)
    assert round(sum(s.quantity for s in sales), 6) == 2.0 and all(s.account == "Robinhood" for s in sales)
    assert not lots.get("ETH-USD") and pf.build_positions(rows)["ETH-USD"].quantity == 0


def test_lot_methods_order_the_sale():
    txs = [{"id": 1, "symbol": "AAPL", "side": "buy", "quantity": 1, "price": 100.0, "date": "2023-01-01", "account": "RH"},
           {"id": 2, "symbol": "AAPL", "side": "buy", "quantity": 1, "price": 300.0, "date": "2025-06-01", "account": "RH"},
           {"id": 3, "symbol": "AAPL", "side": "buy", "quantity": 1, "price": 200.0, "date": "2025-07-01", "account": "RH"},
           {"id": 4, "symbol": "AAPL", "side": "sell", "quantity": 1, "price": 250.0, "date": "2025-08-01", "account": "RH"}]
    cost = {m: taxes.lots_and_sales(txs, {"RH": m})[1][0].cost for m in taxes.LOT_METHODS}
    assert cost == {"fifo": 100.0, "hifo": 300.0, "lifo": 200.0, "min_tax": 300.0}
    # A sale row can carry its own method.
    one = [dict(t, lot_method="lifo") if t["id"] == 4 else t for t in txs]
    assert taxes.lots_and_sales(one)[1][0].cost == 200.0


def test_suggest_from_balance_checks():
    diffs = {"Coinbase": [{"symbol": "BTC-USD", "difference": -0.5}], "Robinhood": [{"symbol": "BTC-USD", "difference": 0.4998}],
             "Stash": [{"symbol": "VTI", "difference": 1.0}]}
    got = transfers.suggest_from_differences(diffs, [], "2026-09-26")
    assert got == [{"symbol": "BTC-USD", "from": "Coinbase", "to": "Robinhood", "sent": 0.5, "received": 0.4998, "day": "2026-09-26"}]


CB = """Timestamp,Transaction Type,Asset,Quantity Transacted,Spot Price Currency,Spot Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread,Notes
2024-01-02T10:00:00Z,Buy,BTC,1,USD,20000,20000,20000,0,
2025-01-10T10:00:00Z,Send,BTC,1,USD,40000,40000,40000,0,Sent to Robinhood
"""


def test_import_send_then_record_the_other_side():
    c = TestClient(api.app)
    r = c.post("/api/import/coinbase", json={"csv": CB, "commit": True}).json()
    assert r["new"] == 1 and r["transfers_new"] == 1 and r["transfers_paired"] == 0
    v = c.get("/api/transfers").json()
    assert len(v["open"]) == 1 and v["open"][0]["direction"] == "out" and "left Coinbase" in v["open"][0]["ask"]
    leg = v["open"][0]["id"]
    # Coinbase still shows the coin until the move is decided.
    assert c.get("/api/holdings").json()[0]["by_account"] == {"Coinbase": 1.0}
    # It went to a wallet of mine.
    assert c.post(f"/api/transfers/{leg}/resolve", json={"how": "wallet", "account": "Ledger wallet"}).status_code == 200
    h = c.get("/api/holdings").json()[0]
    assert h["by_account"] == {"Ledger wallet": 1.0} and h["quantity"] == 1.0 and h["cost_basis"] == 20000.0
    v = c.get("/api/transfers").json()
    assert not v["open"] and v["moves"][0]["to"] == "Ledger wallet"
    # Undo it: back to one open leg.
    assert c.delete(f"/api/transfers/{v['moves'][0]['id']}").status_code == 200
    assert c.get("/api/holdings").json()[0]["by_account"] == {"Coinbase": 1.0}


def test_record_a_move_by_hand_and_coins_from_outside():
    c = TestClient(api.app)
    c.post("/api/transactions", json={"symbol": "ETH-USD", "side": "buy", "quantity": 2, "price": 1500, "date": "2024-03-01",
                                      "account": "Coinbase"})
    r = c.post("/api/transfers", json={"symbol": "ETH", "from_account": "Coinbase", "to_account": "Robinhood", "sent": 2,
                                       "received": 1.99, "day": "2025-02-01"})
    assert r.status_code == 201
    h = c.get("/api/holdings").json()[0]
    assert h["by_account"] == {"Robinhood": 1.99} and round(h["cost_basis"], 2) == 3000.0
    accts = {a["name"]: a for a in c.get("/api/accounts").json()["accounts"]}
    assert accts["Robinhood"]["positions"][0]["quantity"] == 1.99
    # Can't send more than the account had.
    bad = c.post("/api/transfers", json={"symbol": "ETH-USD", "from_account": "Robinhood", "to_account": "Stash", "sent": 5,
                                         "day": "2025-03-01"})
    assert bad.status_code == 400 and "shows 1.99 ETH-USD in Robinhood" in bad.json()["detail"]
    # Coins that came from outside: their original cost and date.
    with db.connect() as conn:
        leg = db.add_leg(conn, "SOL-USD", "in", 10, "2025-04-01", "Coinbase", import_key="cb:x")
    ok = c.post(f"/api/transfers/{leg}/resolve", json={"how": "bought", "cost": 20, "acquired": "2023-05-05"})
    assert ok.status_code == 200
    sol = [t for t in c.get("/api/transactions").json() if t["symbol"] == "SOL-USD"][0]
    assert (sol["price"], sol["date"], sol["account"]) == (20.0, "2023-05-05", "Coinbase")


def test_sync_diffs_are_remembered_for_suggestions():
    from datetime import datetime, timezone
    accounts.record("coinbase", {"new": 0, "by_account": {"Coinbase": [{"symbol": "BTC-USD", "difference": -1.0}]}}, None,
                    datetime.now(timezone.utc))
    accounts.record("robinhood_crypto", {"new": 0, "by_account": {"Robinhood": [{"symbol": "BTC-USD", "difference": 0.999}]}},
                    None, datetime.now(timezone.utc))
    s = TestClient(api.app).get("/api/transfers").json()["suggested"]
    assert s and s[0]["from"] == "Coinbase" and s[0]["to"] == "Robinhood"
