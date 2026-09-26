"""Robinhood Crypto: holdings, trades and orders through Robinhood's official, free Crypto
Trading API (robinhood.com/account/crypto → API trading, on the web).

Robinhood offers this for crypto only; stocks have no API for individuals, so the app reads
stock trades from confirmation emails (email_trades.py) and hands stock orders to the
Robinhood app.

Setup: generate a key pair (Settings → Accounts → Robinhood crypto → "Make a key" does this
locally and shows the public key to paste into Robinhood), create the credential on Robinhood's
site, then put both in .env:

    ROBINHOOD_CRYPTO_API_KEY="rh-api-..."
    ROBINHOOD_CRYPTO_PRIVATE_KEY="<base64 private key>"

Each request is signed as Robinhood's own sample client signs it: Ed25519 over
api_key + timestamp + path + method + body, base64, with x-api-key / x-timestamp /
x-signature headers. The key only works for crypto, and you choose its permissions when you
create it on Robinhood's site.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from urllib.parse import urlencode

import httpx

HOST = "https://trading.robinhood.com"
ACCOUNT = "Robinhood"


class RobinhoodError(Exception):
    pass


def configured() -> bool:
    return bool(os.environ.get("ROBINHOOD_CRYPTO_API_KEY") and os.environ.get("ROBINHOOD_CRYPTO_PRIVATE_KEY"))


def new_keypair() -> tuple[str, str]:
    """(private key, public key), both base64, as Robinhood's instructions produce them."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(priv).decode(), base64.b64encode(pub).decode()


def sign(api_key: str, private_b64: str, method: str, path: str, body: str, timestamp: int) -> dict:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    seed = base64.b64decode(private_b64)[:32]
    key = Ed25519PrivateKey.from_private_bytes(seed)
    msg = f"{api_key}{timestamp}{path}{method}{body}"
    return {"x-api-key": api_key, "x-signature": base64.b64encode(key.sign(msg.encode())).decode(),
            "x-timestamp": str(timestamp), "Content-Type": "application/json; charset=utf-8"}


def request(method: str, path: str, body: dict | None = None, *, now: float | None = None, send=None):
    if not configured():
        raise RobinhoodError("Add ROBINHOOD_CRYPTO_API_KEY and ROBINHOOD_CRYPTO_PRIVATE_KEY to .env first.")
    raw = json.dumps(body) if body is not None else ""
    headers = sign(os.environ["ROBINHOOD_CRYPTO_API_KEY"], os.environ["ROBINHOOD_CRYPTO_PRIVATE_KEY"], method, path, raw,
                   int(now if now is not None else time.time()))
    send = send or (lambda m, url, h, c: httpx.request(m, url, headers=h, content=c or None, timeout=20))
    try:
        resp = send(method, HOST + path, headers, raw)
    except httpx.HTTPError as exc:
        raise RobinhoodError(f"Couldn't reach Robinhood: {exc}") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json()
            detail = "; ".join(str(e.get("detail") or e) for e in detail.get("errors") or []) or str(detail)[:200]
        except ValueError:
            detail = resp.text[:200]
        raise RobinhoodError(f"Robinhood {resp.status_code}: {detail}")
    return resp.json() if resp.content else {}


def _pages(path: str, send=None, limit: int = 20) -> list[dict]:
    out, nxt = [], path
    for _ in range(limit):
        data = request("GET", nxt, send=send)
        out += data.get("results") or []
        link = data.get("next")
        if not link:
            break
        nxt = link.replace(HOST, "")
    return out


# ------------------------------------------------------------------ reading

def account(send=None) -> dict:
    return request("GET", "/api/v1/crypto/trading/accounts/", send=send)


def holdings(send=None) -> dict[str, float]:
    """{"BTC-USD": quantity}"""
    out: dict[str, float] = {}
    for h in _pages("/api/v1/crypto/trading/holdings/", send):
        q = float(h.get("total_quantity") or 0)
        if q:
            out[f"{h['asset_code']}-USD"] = q
    return out


def orders(send=None) -> list[dict]:
    return _pages("/api/v1/crypto/trading/orders/?" + urlencode({"state": "filled"}), send) + \
        _pages("/api/v1/crypto/trading/orders/?" + urlencode({"state": "partially_filled"}), send)


def orders_to_transactions(rows: list[dict]) -> list[dict]:
    """Each execution of a filled order becomes a ledger trade in the Robinhood account."""
    txs = []
    for o in rows:
        side = (o.get("side") or "").lower()
        sym = o.get("symbol") or ""
        if side not in ("buy", "sell") or not sym:
            continue
        execs = o.get("executions") or []
        if not execs and o.get("average_price") and o.get("filled_asset_quantity"):
            execs = [{"effective_price": o["average_price"], "quantity": o["filled_asset_quantity"], "timestamp": o.get("updated_at") or o.get("created_at")}]
        for i, e in enumerate(execs):
            qty, price = float(e.get("quantity") or 0), float(e.get("effective_price") or 0)
            if qty <= 0 or price <= 0:
                continue
            txs.append({"symbol": sym.upper(), "side": side, "quantity": qty, "price": price, "fees": 0.0,
                        "date": (e.get("timestamp") or o.get("created_at") or "")[:10], "account": ACCOUNT,
                        "import_key": f"rhc:{o.get('id')}:{i}", "note": "Robinhood Crypto API"})
    return [t for t in txs if t["date"]]


# ------------------------------------------------------------------ trading

def best_price(symbol: str, send=None) -> dict:
    data = request("GET", "/api/v1/crypto/marketdata/best_bid_ask/?" + urlencode({"symbol": symbol}), send=send)
    r = (data.get("results") or [{}])[0]
    return {"bid": float(r.get("bid_inclusive_of_sell_spread") or r.get("bid_price") or 0),
            "ask": float(r.get("ask_inclusive_of_buy_spread") or r.get("ask_price") or 0),
            "mid": float(r.get("price") or 0)}


def order_body(symbol: str, side: str, quantity: float, limit_price: float | None = None, client_id: str | None = None) -> dict:
    body = {"client_order_id": client_id or str(uuid.uuid4()), "side": side, "symbol": symbol,
            "type": "limit" if limit_price else "market"}
    if limit_price:
        body["limit_order_config"] = {"asset_quantity": f"{quantity:.8f}".rstrip("0").rstrip("."),
                                      "limit_price": f"{limit_price:.2f}", "time_in_force": "gtc"}
    else:
        body["market_order_config"] = {"asset_quantity": f"{quantity:.8f}".rstrip("0").rstrip(".")}
    return body


def place(body: dict, send=None) -> dict:
    return request("POST", "/api/v1/crypto/trading/orders/", body, send=send)


def cancel(order_id: str, send=None) -> dict:
    return request("POST", f"/api/v1/crypto/trading/orders/{order_id}/cancel/", send=send)
