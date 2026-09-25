from fastapi.testclient import TestClient

from market_tracker import api, db, snaptrade as st

# Expected values computed with SnapTrade's own SDK function (compute_request_signature in
# passiv/snaptrade-sdks, sdks/python/snaptrade_client/request_after_hook.py) for these inputs.
ACT_SIG = "2xyYcKEeL2rdxaZGy5KB9OJehFBSbZdrrcN3LHmb0CE="
LOGIN_SIG = "yRERYs5gkLOscpgcrF5ojNjwMjjfj1xJer2QMHSTnlk="


def test_signing_matches_the_sdk(monkeypatch):
    monkeypatch.delenv("SNAPTRADE_USER_ID", raising=False)
    url, h = st.signed("/accounts/abc-123/activities", {"startDate": "2016-09-26", "endDate": "2026-09-25", "offset": 0, "limit": 1000},
                       now=1790000000, client_id="MYCLIENT", consumer_key="secretkey")
    assert url == ("https://api.snaptrade.com/accounts/abc-123/activities?startDate=2016-09-26&endDate=2026-09-25"
                   "&offset=0&limit=1000&clientId=MYCLIENT&timestamp=1790000000")
    assert h["Signature"] == ACT_SIG and "Content-Type" not in h
    _, h = st.signed("/snapTrade/login", None, {"connectionType": "read"}, now=1790000000, client_id="MYCLIENT", consumer_key="secretkey")
    assert h["Signature"] == LOGIN_SIG and h["Content-Type"] == "application/json"


def test_commercial_keys_add_the_user(monkeypatch):
    monkeypatch.setenv("SNAPTRADE_USER_ID", "u1")
    monkeypatch.setenv("SNAPTRADE_USER_SECRET", "s1")
    url, _ = st.signed("/accounts", now=1, client_id="c", consumer_key="k")
    assert url.endswith("/accounts?userId=u1&userSecret=s1&clientId=c&timestamp=1")


def _act(i, kind, sym, units=0, price=0, amount=None, day="2026-03-02", fee=0, stype="cs"):
    return {"id": i, "type": kind, "symbol": {"symbol": sym, "type": {"code": stype}} if sym else None, "units": units,
            "price": price, "amount": amount, "fee": fee, "trade_date": day + "T00:00:00Z", "description": f"{kind} {sym}"}


ACTS = [_act(1, "BUY", "AAPL", 10, 200, -2000, "2026-01-05", fee=0.5), _act(2, "SELL", "AAPL", -4, 210, 840, "2026-02-05"),
        _act(3, "DIVIDEND", "AAPL", amount=1.56, day="2026-02-12"), _act(4, "REI", "SCHD", 0.07, 27.5, -1.93, "2026-03-20"),
        _act(5, "CONTRIBUTION", None, amount=500), _act(6, "INTEREST", None, amount=0.42, day="2026-03-31"),
        _act(7, "BUY", "BTC", 0.01, 60000, -600, "2026-03-03", stype="crypto")]


def test_to_rows():
    txs, income, skipped, drip = st.to_rows(ACTS, "Robinhood", "Robinhood")
    assert [(t["symbol"], t["side"], t["quantity"], t["price"], t["fees"], t["date"]) for t in txs] == [
        ("AAPL", "buy", 10, 200, 0.5, "2026-01-05"), ("AAPL", "sell", 4, 210, 0, "2026-02-05"),
        ("SCHD", "buy", 0.07, 27.5, 0, "2026-03-20"), ("BTC-USD", "buy", 0.01, 60000, 0, "2026-03-03")]
    assert txs[0]["import_key"] == "st:1" and "reinvested" in txs[2]["note"]
    assert [(r["symbol"], r["kind"], r["amount"]) for r in income] == [("AAPL", "dividend", 1.56), ("", "interest", 0.42)]
    assert skipped == {"CONTRIBUTION": 1} and drip == {"SCHD"}
    # Coinbase coins get -USD even when the type code is missing
    txs, *_ = st.to_rows([_act(8, "BUY", "PEPE", 1000, 0.00001, stype="")], "Coinbase", "Coinbase")
    assert txs[0]["symbol"] == "PEPE-USD"


def test_account_names_match_csv_imports():
    assert st.account_name({"institution_name": "Robinhood"}) == "Robinhood"
    assert st.account_name({"institution_name": "Coinbase Advanced"}) == "Coinbase"
    assert st.account_name({"institution_name": "Charles Schwab"}) == "Charles Schwab"


def test_csv_imports_are_not_doubled_but_identical_fills_are_kept():
    earlier = [{"symbol": "AAPL", "side": "buy", "quantity": 10, "date": "2026-01-05", "account": "Robinhood", "import_key": "rh:x"}]
    rows = [{"symbol": "AAPL", "side": "buy", "quantity": 10, "date": "2026-01-06", "account": "Robinhood", "import_key": "st:1"},
            {"symbol": "AAPL", "side": "buy", "quantity": 10, "date": "2026-01-05", "account": "Robinhood", "import_key": "st:2"},
            {"symbol": "AAPL", "side": "buy", "quantity": 10, "date": "2026-01-05", "account": "Stash", "import_key": "st:3"}]
    fresh, dup = st.new_only(rows, set(), earlier, st.match)
    assert [r["import_key"] for r in fresh] == ["st:2", "st:3"] and dup == 1
    fresh, dup = st.new_only(rows, {"st:2"}, [], st.match)
    assert [r["import_key"] for r in fresh] == ["st:1", "st:3"] and dup == 1


def test_reconcile_positions():
    positions = [{"symbol": {"symbol": {"symbol": "AAPL", "type": {"code": "cs"}}}, "units": 6},
                 {"symbol": {"symbol": {"symbol": "VTI"}}, "units": 3}]
    diffs = st.reconcile(positions, {"AAPL": 6.0, "NVDA": 2.0})
    assert diffs == [{"symbol": "NVDA", "broker": 0.0, "ledger": 2.0, "difference": -2.0},
                     {"symbol": "VTI", "broker": 3.0, "ledger": 0.0, "difference": 3.0}]


def test_sync_endpoint(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    c = TestClient(api.app)
    monkeypatch.delenv("SNAPTRADE_CLIENT_ID", raising=False)
    assert c.get("/api/sync/snaptrade").json()["configured"] is False
    assert c.post("/api/sync/snaptrade").status_code == 400
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "c")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "k")
    acct = {"id": "a1", "institution_name": "Robinhood", "name": "Individual"}
    holdings = {"positions": [{"symbol": {"symbol": {"symbol": "AAPL"}}, "units": 6},
                              {"symbol": {"symbol": {"symbol": "SCHD"}}, "units": 0.07}]}
    monkeypatch.setattr(st, "fetch_all", lambda today=None: [{"account": acct, "activities": ACTS[:6], "holdings": holdings}])
    # The first buy was already imported from the Robinhood CSV
    c.post("/api/transactions", json={"symbol": "AAPL", "side": "buy", "quantity": 10, "price": 200, "date": "2026-01-05",
                                      "account": "Robinhood"})
    r = c.post("/api/sync/snaptrade").json()
    assert r["new"] == 2 and r["duplicates"] == 1 and r["income_new"] == 2 and r["differences"] == []
    again = c.post("/api/sync/snaptrade").json()
    assert again["new"] == 0 and again["income_new"] == 0
    with db.connect() as conn:
        assert db.get_meta(conn, "drip_symbols") == '["SCHD"]'
        assert len(db.income(conn)) == 2
    assert c.get("/api/sync/snaptrade").json()["last_sync"]
    monkeypatch.setattr(st, "connect_url", lambda: "https://app.snaptrade.com/connect?x")
    assert c.post("/api/sync/snaptrade/connect").json()["url"].startswith("https://app.snaptrade.com")
