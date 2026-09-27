from market_tracker import db, golive, importers
from market_tracker.analytics import portfolio as pf

CSV = '''"Activity Date","Process Date","Settle Date","Instrument","Description","Trans Code","Quantity","Price","Amount"
"5/12/2025","5/12/2025","5/13/2025","AAPL","Apple
CUSIP: 037833100","Sell","2","$211.40","$422.79"
"2/3/2025","2/3/2025","2/3/2025","F","Ford Motor
Stock reward","REC","1","",""
"9/1/2024","9/1/2024","9/1/2024","MSFT","ACATS Transfer Out","ACATO","3","",""
"3/1/2024","3/1/2024","3/1/2024","AAPL","ACATS Transfer In
CUSIP: 037833100","ACATI","10","",""
"1/10/2024","1/10/2024","1/10/2024","","ACH Deposit","ACH","","","$5,000.00"
"The data provided is for informational purposes only."
'''


def test_transfers_in_and_rewards_import_with_cost_needed():
    res = importers.parse_robinhood(CSV)
    assert not res.errors and res.skipped == {"ACH": 1}
    arrived = [t for t in res.transactions if t["price"] == 0.0]
    assert [(t["symbol"], t["quantity"], t["note"]) for t in arrived] == [
        ("F", 1.0, "Stock reward: value when received needed"), ("AAPL", 10.0, "Transferred in (ACATS): original cost needed")]
    assert res.transfers == [{"symbol": "MSFT", "direction": "out", "quantity": 3.0, "day": "2024-09-01", "account": "Robinhood",
                              "import_key": res.transfers[0]["import_key"], "note": "ACATS transfer out"}]
    pf.build_positions(res.transactions)            # the later AAPL sale no longer "sells what you never had"


def test_cost_entered_later_and_money_decisions_wait_until_then():
    res = importers.parse_robinhood(CSV)
    with db.connect() as conn:
        conn.execute("DELETE FROM transactions")
        for t in res.transactions:
            db.add_transaction(conn, t["symbol"], t["side"], t["quantity"], t["price"], t["date"], t["fees"], t["note"],
                               import_key=t["import_key"], account=t["account"])
        rows = db.needs_cost(conn)
        assert [r["symbol"] for r in rows] == ["AAPL", "F"]
        held = [{"account": "Robinhood", "symbol": "AAPL", "value": 1000.0, "state": "statement", "detail": ""}]
        tr = golive.trust({"total_value": 1000.0, "checked_value": 1000.0, "holdings": held, "needs_cost": rows})
        assert not tr["trusted"] and "10 AAPL on 2024-03-01" in tr["text"]
        aapl = next(r for r in rows if r["symbol"] == "AAPL")
        assert db.set_cost(conn, aapl["id"], 142.5, "2021-06-01") and not db.set_cost(conn, aapl["id"], 1.0, "2021-06-01")
        assert [r["symbol"] for r in db.needs_cost(conn)] == ["F"]
        conn.execute("DELETE FROM transactions")


def test_unpriced_holdings_arent_called_empty():
    held = [{"account": "Robinhood", "symbol": "AAPL", "value": 0.0, "state": "unchecked", "detail": ""}]
    tr = golive.trust({"total_value": 0.0, "checked_value": 0.0, "holdings": held})
    assert not tr["trusted"] and tr["text"].startswith("Couldn't get prices")


def test_statement_reads_one_decimal_share_counts():
    from market_tracker import statement
    text = "Tesla TSLA Margin 2.5 $440.40 $1,101.00 0.00%\nVanguard VOO Margin 5.0165 $611.10\nApple AAPL Margin 8 $254.43"
    assert statement.quantities(text, {"TSLA", "VOO", "AAPL"}) == {"TSLA": 2.5, "VOO": 5.0165, "AAPL": 8.0}
