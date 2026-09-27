from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from market_tracker import api, confidence, db

NOW = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)


def tx(sym, qty, acct, price=100.0, side="buy"):
    return {"symbol": sym, "side": side, "quantity": qty, "price": price, "date": "2026-01-02", "account": acct}


def test_score_weights_checked_money():
    ledger = [tx("BTC-USD", 1, "Coinbase"), tx("ETH-USD", 10, "Coinbase"), tx("AAPL", 10, "Robinhood"), tx("BTC-USD", 0.5, "Robinhood"),
              tx("VOO", 5, "Stash")]
    prices = {"BTC-USD": 50_000, "ETH-USD": 2_000, "AAPL": 200, "VOO": 500}
    syncs = {"coinbase": {"at": (NOW - timedelta(minutes=5)).isoformat(), "ok": True},
             "robinhood_crypto": {"at": (NOW - timedelta(days=3)).isoformat(), "ok": True}}      # stale: doesn't count
    diffs = {"coinbase": {"Coinbase": [{"symbol": "ETH-USD", "difference": -2.0}]}}
    stmts = {"Stash": {"day": "2026-09-10", "match": ["VOO"], "differences": []}}
    st = confidence.status(ledger, prices, syncs, diffs, stmts, NOW)
    by = {(r["account"], r["symbol"]): r for r in st["holdings"]}
    assert by[("Coinbase", "BTC-USD")]["state"] == "broker"
    assert by[("Coinbase", "ETH-USD")]["state"] == "mismatch" and "-2" in by[("Coinbase", "ETH-USD")]["detail"]
    assert by[("Robinhood", "AAPL")]["state"] == "unchecked" and by[("Robinhood", "BTC-USD")]["state"] == "unchecked"
    assert by[("Stash", "VOO")]["state"] == "statement"
    total = 50_000 + 20_000 + 2_000 + 25_000 + 2_500
    assert st["score"] == round((50_000 + 2_500) / total * 100)
    fixes = confidence.fix_list(st, [{"ask": "0.5 BTC-USD arrived in Robinhood…"}], [{"subject": "x"}], [])
    assert fixes[0]["level"] == 2 and "Coinbase ETH-USD" in fixes[0]["text"]
    assert any("arrived in Robinhood" in f["text"] for f in fixes) and any("couldn't understand" in f["text"] for f in fixes)
    assert any(f["text"].startswith("Robinhood:") for f in fixes)            # mostly unchecked
    # An old statement no longer counts.
    stale = confidence.status(ledger, prices, syncs, diffs, {"Stash": dict(stmts["Stash"], day="2026-07-01")}, NOW)
    assert {(r["account"], r["symbol"]): r for r in stale["holdings"]}[("Stash", "VOO")]["state"] == "unchecked"


def test_statement_check_is_remembered_and_setup_steps(monkeypatch):
    with db.connect() as conn:
        confidence.remember_statement(conn, "Stash", {"match": [{"symbol": "VOO"}], "differences": [{"symbol": "VTI"}],
                                                      "not_on_statement": [{"symbol": "QQQ"}]})
        _, _, stmts = confidence.load_inputs(conn)
    assert stmts["Stash"]["match"] == ["VOO"] and stmts["Stash"]["differences"] == ["VTI", "QQQ"]
    c = TestClient(api.app)
    s = c.get("/api/setup").json()
    keys = [x["key"] for x in s["steps"]]
    assert keys[:2] == ["backup", "alerts"] and "coinbase" in keys and "statement:Stash" in keys
    assert not any(x["done"] for x in s["steps"] if x["key"] in ("backup", "coinbase"))
    stash = next(x for x in s["steps"] if x["key"] == "statement:Stash")
    assert not stash["done"] and "2 differ" in stash["detail"]
    assert len(s["suggested_topic"]) >= 12
    assert c.post("/api/setup/test/coinbase").status_code == 400          # not connected: the sync says what's missing
    assert c.post("/api/setup/test/nope").status_code == 404
    conf = c.get("/api/confidence").json()
    assert conf["score"] is None and conf["holdings"] == []


def test_alerts_step_saves_topic_and_sends_test(monkeypatch, tmp_path):
    from market_tracker import notify
    monkeypatch.setenv("NTFY_TOPIC", "x")
    monkeypatch.delenv("NTFY_TOPIC")            # so the topic the endpoint sets is undone after the test
    monkeypatch.setattr(api, "_env_path", lambda: str(tmp_path / ".env"))
    sent = []
    monkeypatch.setattr(notify, "send", lambda m, client=None: sent.append(m) or True)
    c = TestClient(api.app)
    assert c.post("/api/setup/alerts", json={"topic": "short"}).status_code == 422
    assert c.post("/api/setup/alerts", json={"topic": "plumbline-abcdef123456"}).status_code == 200
    assert sent and "NTFY_TOPIC" in (tmp_path / ".env").read_text()
    step = next(x for x in c.get("/api/setup").json()["steps"] if x["key"] == "alerts")
    assert step["done"]
