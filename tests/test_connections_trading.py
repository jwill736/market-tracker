import base64
import time
from datetime import date
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from market_tracker import accounts, api, coinbase_sync, db, email_trades, robinhood_crypto, trading


def _mail(sender, subject, body, html=False, day="Thu, 24 Sep 2026 14:31:00 -0400", mid="<m1@x>"):
    m = EmailMessage()
    m["From"], m["Subject"], m["Date"], m["Message-ID"] = sender, subject, day, mid
    if html:
        m.set_content("plain fallback ignored")
        m.add_alternative(body, subtype="html")
        # keep only the html so the HTML path is exercised
        m = EmailMessage()
        m["From"], m["Subject"], m["Date"], m["Message-ID"] = sender, subject, day, mid
        m.set_content(body, subtype="html")
    else:
        m.set_content(body)
    return m.as_bytes()


def test_robinhood_share_and_dollar_orders():
    raw = _mail("Robinhood <notifications@robinhood.com>", "Your order has been executed",
                "Your order to buy 10 shares of AAPL through your Robinhood Individual account was executed at an average price of $150.25 on September 24, 2026.")
    trades, note = email_trades.parse_message(raw)
    assert note is None
    t = trades[0]
    assert (t["symbol"], t["side"], t["quantity"], t["price"], t["date"], t["account"]) == ("AAPL", "buy", 10, 150.25, "2026-09-24", "Robinhood")
    raw = _mail("Robinhood <notifications@robinhood.com>", "Order executed",
                "<p>Your order to <b>sell $1,000.00 of</b> NVDA was <i>executed</i> at an average price of $200.00.</p>", html=True, mid="<m2@x>")
    t = email_trades.parse_message(raw)[0][0]
    assert (t["symbol"], t["side"], t["quantity"], t["price"]) == ("NVDA", "sell", 5.0, 200.0)


def test_crypto_generic_and_stash_patterns():
    t = email_trades.parse_message(_mail("Coinbase <no-reply@coinbase.com>", "You bought Bitcoin", "You bought 0.0015 BTC at $64,000.00", mid="<m3@x>"))[0][0]
    assert (t["symbol"], t["side"], t["quantity"], t["price"], t["account"]) == ("BTC-USD", "buy", 0.0015, 64000.0, "Coinbase")
    t = email_trades.parse_message(_mail("Stash <hello@stash.com>", "Your investment is complete",
                                         "Your $20.00 investment in VOO is complete. It was bought at $500.00 per share.", mid="<m4@x>"))[0][0]
    assert (t["symbol"], t["side"], t["quantity"], t["account"]) == ("VOO", "buy", 0.04, "Stash")


def test_unreadable_and_non_trade_and_foreign_emails():
    trades, note = email_trades.parse_message(_mail("Stash <hello@stash.com>", "Your order went through",
                                                    "Great news! Your recurring investment was processed.", mid="<m5@x>"))
    assert trades == [] and note["account"] == "Stash" and "order went through" in note["subject"]
    assert email_trades.parse_message(_mail("Robinhood <x@robinhood.com>", "Your limit order was placed",
                                            "Your order to buy 1 share of AAPL was placed.", mid="<m6@x>")) == ([], None)
    assert email_trades.parse_message(_mail("Phisher <x@robinhood.com.evil.io>", "Order executed",
                                            "Your order to buy 10 shares of AAPL was executed at an average price of $1.00", mid="<m7@x>")) == ([], None)


def test_same_email_gives_the_same_key():
    raw = _mail("Robinhood <n@robinhood.com>", "x", "Your order to buy 2 shares of KO was executed at an average price of $60.00")
    assert email_trades.parse_message(raw)[0][0]["import_key"] == email_trades.parse_message(raw)[0][0]["import_key"]


class FakeImap:
    def __init__(self, msgs):
        self.msgs, self.readonly = msgs, None

    def login(self, u, p):
        assert p == "abcdefghijklmnop"

    def select(self, box, readonly=False):
        self.readonly = readonly
        return "OK", [b"1"]

    def search(self, charset, *crit):
        dom = crit[-1]
        return "OK", [b" ".join(str(i).encode() for i, (d, _) in enumerate(self.msgs, 1) if d == dom)]

    def fetch(self, num, what):
        return "OK", [(b"1", self.msgs[int(num) - 1][1])]

    def logout(self):
        pass


def test_imap_read_is_readonly(monkeypatch):
    # setenv restores the original (unset) state after the test
    monkeypatch.setenv("MAIL_USER", "me@gmail.com")
    monkeypatch.setenv("MAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    fake = FakeImap([("robinhood.com", _mail("R <n@robinhood.com>", "x", "Your order to buy 2 shares of KO was executed at an average price of $60.00"))])
    res = email_trades.read(date(2026, 9, 1), connect=lambda: fake)
    assert fake.readonly is True and [t["symbol"] for t in res.trades] == ["KO"]


def test_robinhood_signature_verifies_with_the_public_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    priv, pub = robinhood_crypto.new_keypair()
    h = robinhood_crypto.sign("rh-api-abc", priv, "POST", "/api/v1/crypto/trading/orders/", '{"a": 1}', 1790000000)
    assert h["x-api-key"] == "rh-api-abc" and h["x-timestamp"] == "1790000000"
    Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(
        base64.b64decode(h["x-signature"]), b'rh-api-abc1790000000/api/v1/crypto/trading/orders/POST{"a": 1}')


def test_robinhood_orders_and_order_body():
    rows = [{"id": "o1", "side": "buy", "symbol": "BTC-USD", "state": "filled",
             "executions": [{"effective_price": "60000", "quantity": "0.001", "timestamp": "2026-09-20T10:00:00Z"},
                            {"effective_price": "60100", "quantity": "0.002", "timestamp": "2026-09-20T10:00:01Z"}]},
            {"id": "o2", "side": "sell", "symbol": "ETH-USD", "average_price": 3000, "filled_asset_quantity": 0.5, "created_at": "2026-09-21T00:00:00Z"}]
    txs = robinhood_crypto.orders_to_transactions(rows)
    assert [(t["symbol"], t["side"], t["quantity"], t["price"], t["import_key"]) for t in txs] == [
        ("BTC-USD", "buy", 0.001, 60000.0, "rhc:o1:0"), ("BTC-USD", "buy", 0.002, 60100.0, "rhc:o1:1"), ("ETH-USD", "sell", 0.5, 3000.0, "rhc:o2:0")]
    b = robinhood_crypto.order_body("BTC-USD", "buy", 0.0015, client_id="c1")
    assert b == {"client_order_id": "c1", "side": "buy", "symbol": "BTC-USD", "type": "market", "market_order_config": {"asset_quantity": "0.0015"}}
    b = robinhood_crypto.order_body("BTC-USD", "sell", 1, limit_price=70000, client_id="c2")
    assert b["type"] == "limit" and b["limit_order_config"] == {"asset_quantity": "1", "limit_price": "70000.00", "time_in_force": "gtc"}


def test_coinbase_order_configuration():
    assert coinbase_sync.order_configuration("buy", 25, None, None) == {"market_market_ioc": {"quote_size": "25.00"}}
    assert coinbase_sync.order_configuration("sell", None, 0.0015, None) == {"market_market_ioc": {"base_size": "0.0015"}}
    assert coinbase_sync.order_configuration("buy", None, 0.5, 2000) == {"limit_limit_gtc": {"base_size": "0.5", "limit_price": "2000.00", "post_only": False}}


# ------------------------------------------------------------------ trading safety

CFG = {"enabled": True, "max_order": 250.0, "daily_limit": 500.0}
TXS = [{"symbol": "BTC-USD", "side": "buy", "quantity": 0.01, "price": 40000, "date": "2025-01-02", "account": "Coinbase", "fees": 0}]


def quote(order):
    price = 60000.0
    qty = order.quantity or order.dollars / price
    return {"price": price, "usd": qty * price, "quantity": qty, "fees": 0.5, "errors": [], "broker": "Coinbase"}


def test_preview_blocks_and_seals():
    o = trading.Order("coinbase", "BTC-USD", "buy", dollars=100)
    pv = trading.preview(o, TXS, None, CFG, 0, now=1000, today=date(2026, 9, 26), quote_fn=quote)
    assert pv["token"] and not pv["blockers"]
    assert trading.preview(o, TXS, None, dict(CFG, enabled=False), 0, quote_fn=quote)["token"] is None
    big = trading.Order("coinbase", "BTC-USD", "buy", dollars=300)
    assert "per-order limit" in trading.preview(big, TXS, None, CFG, 0, quote_fn=quote)["blockers"][0]
    assert "daily limit" in trading.preview(o, TXS, None, CFG, 450, quote_fn=quote)["blockers"][0]
    oversell = trading.Order("coinbase", "BTC-USD", "sell", quantity=0.02)
    assert "0.01 BTC-USD in Coinbase" in trading.preview(oversell, TXS, None, dict(CFG, max_order=5000, daily_limit=5000), 0, quote_fn=quote)["blockers"][0]
    sell = trading.Order("coinbase", "BTC-USD", "sell", quantity=0.004)
    w = trading.preview(sell, TXS, None, CFG, 0, today=date(2026, 9, 26), quote_fn=quote)["warnings"]
    assert any("Gain of about $80 (long-term)" in x for x in w)


def test_place_needs_the_exact_previewed_order_and_a_fresh_seal(monkeypatch):
    sent = []
    monkeypatch.setattr(coinbase_sync, "create_order", lambda pid, side, cfg, cid, send=None: sent.append((pid, side, cfg)) or
                        {"success": True, "success_response": {"order_id": "cb-1"}})
    o = trading.Order("coinbase", "BTC-USD", "buy", dollars=100)
    now = time.time()
    token = trading.preview(o, TXS, None, CFG, 0, now=now, quote_fn=quote)["token"]
    with db.connect() as conn:
        db.set_meta(conn, "trading_enabled", "1")
        with pytest.raises(trading.TradeError, match="changed"):
            trading.place(trading.Order("coinbase", "BTC-USD", "buy", dollars=200), token, conn, now=now)
        with pytest.raises(trading.TradeError, match="expired"):
            trading.place(o, token, conn, now=now + 200)
        out = trading.place(o, token, conn, now=now + 5)
        assert out["broker_order_id"] == "cb-1" and sent == [("BTC-USD", "buy", {"market_market_ioc": {"quote_size": "100.00"}})]
        with pytest.raises(trading.TradeError, match="already sent"):
            trading.place(o, token, conn, now=now + 6)
        assert trading.spent_today(conn) == 100.0 and trading.recent(conn)[0]["status"] == "placed"
        db.set_meta(conn, "trading_enabled", "")
        t2 = trading.preview(o, TXS, None, CFG, 0, now=now, quote_fn=quote)["token"]
        with pytest.raises(trading.TradeError, match="off"):
            trading.place(o, t2, conn, now=now + 1)


def test_rejection_is_logged_not_raised_silently(monkeypatch):
    monkeypatch.setattr(coinbase_sync, "create_order", lambda *a, **k: {"success": False, "error_response": {"message": "INSUFFICIENT_FUND"}})
    o = trading.Order("coinbase", "BTC-USD", "buy", dollars=50)
    now = time.time()
    token = trading.preview(o, TXS, None, CFG, 0, now=now, quote_fn=quote)["token"]
    with db.connect() as conn:
        db.set_meta(conn, "trading_enabled", "1")
        with pytest.raises(trading.TradeError, match="INSUFFICIENT_FUND"):
            trading.place(o, token, conn, now=now + 1)
        assert trading.recent(conn)[0]["status"] == "failed" and trading.spent_today(conn) == 0


def test_stocks_are_tickets():
    assert trading.venues("AAPL") == ["ticket"]


# ------------------------------------------------------------------ endpoints

def test_connection_endpoints(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    for k in ("MAIL_USER", "MAIL_APP_PASSWORD", "ROBINHOOD_CRYPTO_API_KEY", "ROBINHOOD_CRYPTO_PRIVATE_KEY", "MAIL_IMAP_HOST"):
        monkeypatch.setenv(k, "x")       # so the test removes whatever the endpoints set, afterwards
        monkeypatch.delenv(k)
    env = tmp_path / ".env"
    monkeypatch.setattr(api, "_env_path", lambda: str(env))
    c = TestClient(api.app)
    assert c.get("/api/connections").json()["email"]["configured"] is False
    monkeypatch.setattr(email_trades, "check_login", lambda connect=None: "imap.gmail.com: AUTHENTICATIONFAILED")
    r = c.post("/api/connections/email", json={"user": "me@gmail.com", "app_password": "wrongwrong"})
    assert r.status_code == 400 and "app password" in r.json()["detail"] and not env.exists()
    monkeypatch.setattr(email_trades, "check_login", lambda connect=None: "")
    monkeypatch.setitem(accounts.SYNCS, "email", (email_trades.configured, lambda: {"new": 3, "differences": []}))
    r = c.post("/api/connections/email", json={"user": "me@gmail.com", "app_password": "abcdefghijklmnop"})
    assert r.json()["first_sync"]["new"] == 3 and 'MAIL_USER="me@gmail.com"' in env.read_text()
    pub = c.post("/api/connections/robinhood/keypair").json()["public_key"]
    assert len(base64.b64decode(pub)) == 32 and "ROBINHOOD_CRYPTO_PRIVATE_KEY" in env.read_text()
    assert c.get("/api/connections").json()["robinhood_crypto"]["public_key"] == pub
    monkeypatch.setattr(robinhood_crypto, "account", lambda send=None: (_ for _ in ()).throw(robinhood_crypto.RobinhoodError("Robinhood 401: bad key")))
    r = c.post("/api/connections/robinhood", json={"api_key": "rh-api-123"})
    assert r.status_code == 400 and "bad key" in r.json()["detail"]
    assert c.get("/api/trade/venues/AAPL").json()["venues"] == ["ticket"]
    s = c.post("/api/trade/settings", json={"enabled": True, "max_order": 100, "daily_limit": 300}).json()
    assert s == {"enabled": True, "max_order": 100.0, "daily_limit": 300.0}
    assert c.post("/api/trade/place", json={"venue": "coinbase", "symbol": "BTC", "side": "buy", "dollars": 10}).status_code == 400
