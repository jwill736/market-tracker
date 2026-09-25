import base64
import json

import pytest
from fastapi.testclient import TestClient

from market_tracker import api, coinbase_sync


@pytest.fixture
def ec_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption()).decode()
    return key, pem


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_jwt_is_a_valid_es256_token(ec_key):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    key, pem = ec_key
    tok = coinbase_sync.make_jwt("organizations/o/apiKeys/k", pem.replace("\n", "\\n"), "GET",
                                 "/api/v3/brokerage/accounts", now=1_790_000_000)
    h, p, sig = tok.split(".")
    header, payload = json.loads(_b64d(h)), json.loads(_b64d(p))
    assert header["alg"] == "ES256" and header["kid"] == "organizations/o/apiKeys/k" and len(header["nonce"]) == 32
    assert payload == {"sub": "organizations/o/apiKeys/k", "iss": "cdp", "nbf": 1_790_000_000, "exp": 1_790_000_120,
                       "uri": "GET api.coinbase.com/api/v3/brokerage/accounts"}
    raw = _b64d(sig)
    assert len(raw) == 64                                        # raw r||s, not DER
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))   # raises if invalid


def test_ed25519_keys_are_refused_with_a_clear_message():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    pem = ed25519.Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                             serialization.NoEncryption()).decode()
    with pytest.raises(ValueError, match="ECDSA"):
        coinbase_sync.make_jwt("k", pem, "GET", "/x")


FILLS = {"fills": [
    {"entry_id": "e1", "trade_id": "t1", "product_id": "BTC-USD", "side": "BUY", "price": "60000", "size": "0.01",
     "commission": "1.20", "trade_time": "2026-03-01T15:00:00Z", "size_in_quote": False},
    {"entry_id": "e2", "trade_id": "t2", "product_id": "ETH-USDC", "side": "BUY", "price": "2500", "size": "500",
     "commission": "0.5", "trade_time": "2026-04-01T15:00:00Z", "size_in_quote": True},
    {"entry_id": "e3", "trade_id": "t3", "product_id": "ETH-BTC", "side": "SELL", "price": "0.04", "size": "0.1",
     "commission": "0", "trade_time": "2026-05-01T15:00:00Z"},
    {"entry_id": "e4", "trade_id": "t4", "product_id": "BTC-USD", "side": "SELL", "price": "70000", "size": "0.004",
     "commission": "0.8", "trade_time": "2026-06-01T15:00:00Z"}], "cursor": ""}
ACCOUNTS = {"accounts": [{"currency": "BTC", "available_balance": {"value": "0.006"}, "hold": {"value": "0"}},
                         {"currency": "ETH", "available_balance": {"value": "0.25"}, "hold": {"value": "0.05"}},
                         {"currency": "USD", "available_balance": {"value": "120.5"}, "hold": {"value": "0"}},
                         {"currency": "SOL", "available_balance": {"value": "0"}, "hold": {"value": "0"}}], "has_next": False}


def test_fills_balances_and_reconcile(ec_key, monkeypatch):
    _, pem = ec_key
    monkeypatch.setenv("COINBASE_API_KEY_NAME", "k")
    monkeypatch.setenv("COINBASE_API_PRIVATE_KEY", pem)
    seen = []

    def get(url, params=None, headers=None, **kw):
        seen.append(headers["Authorization"].startswith("Bearer "))
        return FILLS if "fills" in url else ACCOUNTS

    txs = coinbase_sync.fills_to_transactions(coinbase_sync.fills(get=get))
    assert [(t["symbol"], t["side"], round(t["quantity"], 6), t["price"]) for t in txs] == [
        ("BTC-USD", "buy", 0.01, 60000.0), ("ETH-USD", "buy", 0.2, 2500.0), ("BTC-USD", "sell", 0.004, 70000.0)]
    assert txs[0]["import_key"] == "cbapi:e1" and txs[0]["account"] == "Coinbase" and all(seen)
    bal = coinbase_sync.balances(get=get)
    assert bal == {"BTC": 0.006, "ETH": pytest.approx(0.3), "USD": 120.5}
    diff = coinbase_sync.reconcile(bal, {"BTC-USD": 0.006, "ETH-USD": 0.2})
    assert [(d["coin"], round(d["difference"], 6)) for d in diff] == [("ETH", 0.1)]     # e.g. staking rewards


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    monkeypatch.delenv("COINBASE_API_KEY_NAME", raising=False)
    return TestClient(api.app)


def test_sync_endpoint(client, monkeypatch):
    assert client.get("/api/sync/coinbase").json() == {"configured": False}
    assert client.post("/api/sync/coinbase").status_code == 400
    monkeypatch.setattr(coinbase_sync, "configured", lambda: True)
    monkeypatch.setattr(coinbase_sync, "fills", lambda: FILLS["fills"])
    monkeypatch.setattr(coinbase_sync, "balances", lambda: {"BTC": 0.006, "ETH": 0.3, "USD": 10})
    r = client.post("/api/sync/coinbase").json()
    assert r["new"] == 3 and r["duplicates"] == 0 and [d["coin"] for d in r["differences"]] == ["ETH"]
    again = client.post("/api/sync/coinbase").json()
    assert again["new"] == 0 and again["duplicates"] == 3


def test_backup_round_trip(client):
    client.post("/api/transactions", json={"symbol": "AAPL", "side": "buy", "quantity": 2, "price": 200, "date": "2026-01-02",
                                           "account": "Robinhood"})
    client.post("/api/watchlist/NVDA")
    client.post("/api/cash", json={"cash": 500})
    client.post("/api/people/follow", json={"who": "ARK ARKK", "group": "ark"})
    b = client.get("/api/backup").json()
    assert b["format"] == "plumbline-backup" and len(b["transactions"]) == 1 and b["cash"] == 500
    for t in b["transactions"]:
        client.delete(f"/api/transactions/{t['id']}")
    assert client.post("/api/backup/restore", json={"data": b}).json() == {"transactions_added": 1}
    assert client.post("/api/backup/restore", json={"data": b}).json() == {"transactions_added": 0}
    held = client.get("/api/holdings").json()
    assert held[0]["symbol"] == "AAPL" and held[0]["accounts"] == ["Robinhood"]
    assert client.post("/api/backup/restore", json={"data": {"x": 1}}).status_code == 400
    bad = dict(b, transactions=[{"symbol": "ZZZ", "side": "sell", "quantity": 5, "price": 1, "date": "2026-01-01"}])
    assert client.post("/api/backup/restore", json={"data": bad}).status_code == 400
    assert [h["symbol"] for h in client.get("/api/holdings").json()] == ["AAPL"]              # nothing half-restored
