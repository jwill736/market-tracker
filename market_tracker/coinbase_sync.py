"""Read-only sync with your Coinbase account (Advanced Trade API).

Create a key at coinbase.com/settings/api (Coinbase Developer Platform) with the **View**
permission only, ECDSA signature algorithm, and put its name and private key in .env:

    COINBASE_API_KEY_NAME="organizations/.../apiKeys/..."
    COINBASE_API_PRIVATE_KEY="-----BEGIN EC PRIVATE KEY-----\\n...\\n-----END EC PRIVATE KEY-----\\n"

A View-only key can read balances and fills but cannot trade or move money. Each request is
signed with a short-lived JWT (ES256), as Coinbase requires.

Sync imports your fills (buys and sells with price and fees, so the cost basis is right) into
the ledger under the Coinbase account, skipping ones already imported, then compares each
coin's balance with the ledger. Differences come from rewards, staking, transfers in or out, or
trades older than the fills Coinbase returns; they are reported, not guessed.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from dataclasses import dataclass, field

from . import http
from .providers import market

API_HOST = "api.coinbase.com"
ACCOUNTS = "/api/v3/brokerage/accounts"
FILLS = "/api/v3/brokerage/orders/historical/fills"
STABLE = {"USD", "USDC"}


def configured() -> bool:
    return bool(os.environ.get("COINBASE_API_KEY_NAME") and os.environ.get("COINBASE_API_PRIVATE_KEY"))


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make_jwt(key_name: str, private_pem: str, method: str, path: str, now: float | None = None) -> str:
    """ES256 JWT for one request, valid two minutes (Coinbase's CDP key format)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    key = serialization.load_pem_private_key(private_pem.replace("\\n", "\n").encode(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("Coinbase's trading API needs an ECDSA key: create the key with the ECDSA algorithm")
    now = int(now or time.time())
    header = {"alg": "ES256", "kid": key_name, "nonce": secrets.token_hex(16), "typ": "JWT"}
    payload = {"sub": key_name, "iss": "cdp", "nbf": now, "exp": now + 120, "uri": f"{method} {API_HOST}{path}"}
    signing_input = _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + \
        _b64url(json.dumps(payload, separators=(",", ":")).encode())
    der = key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return signing_input + "." + _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _get(path: str, params: dict | None = None, get=None) -> dict:
    key_name, pem = os.environ.get("COINBASE_API_KEY_NAME", ""), os.environ.get("COINBASE_API_PRIVATE_KEY", "")
    token = make_jwt(key_name, pem, "GET", path)
    fetch = get or http.get
    return fetch(f"https://{API_HOST}{path}", params=params or {}, headers={"Authorization": f"Bearer {token}"}, ttl=0)


def balances(get=None) -> dict[str, float]:
    out: dict[str, float] = {}
    cursor = None
    for _ in range(10):
        params = {"limit": 250}
        if cursor:
            params["cursor"] = cursor
        data = _get(ACCOUNTS, params, get)
        for a in data.get("accounts") or []:
            cur = a.get("currency", "")
            qty = float((a.get("available_balance") or {}).get("value") or 0) + float((a.get("hold") or {}).get("value") or 0)
            if qty:
                out[cur] = out.get(cur, 0.0) + qty
        if not data.get("has_next"):
            break
        cursor = data.get("cursor")
    return out


def fills(get=None, pages: int = 20) -> list[dict]:
    out, cursor = [], None
    for _ in range(pages):
        params = {"limit": 250}
        if cursor:
            params["cursor"] = cursor
        data = _get(FILLS, params, get)
        out += data.get("fills") or []
        cursor = data.get("cursor")
        if not cursor:
            break
    return out


def fills_to_transactions(rows: list[dict]) -> list[dict]:
    txs = []
    for f in rows:
        product = f.get("product_id", "")
        base, _, quote = product.partition("-")
        if quote not in STABLE or not base:
            continue                     # crypto-to-crypto pairs: value unclear without a USD price
        qty, price = float(f.get("size") or 0), float(f.get("price") or 0)
        if f.get("size_in_quote"):
            qty = qty / price if price else 0
        if not qty or not price:
            continue
        txs.append({"symbol": market.normalize_symbol(base + "-USD"), "side": f.get("side", "").lower(),
                    "quantity": qty, "price": price, "fees": float(f.get("commission") or 0),
                    "date": (f.get("trade_time") or "")[:10], "note": "Coinbase sync",
                    "import_key": "cbapi:" + str(f.get("entry_id") or f.get("trade_id")), "account": "Coinbase"})
    return [t for t in txs if t["side"] in ("buy", "sell") and t["date"]]


@dataclass
class SyncResult:
    new: int = 0
    duplicates: int = 0
    differences: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def reconcile(bal: dict[str, float], ledger_qty: dict[str, float], tolerance: float = 1e-6) -> list[dict]:
    out = []
    coins = {c for c in bal if c not in STABLE} | {s.removesuffix("-USD") for s in ledger_qty}
    for c in sorted(coins):
        have, ledger = bal.get(c, 0.0), ledger_qty.get(c + "-USD", 0.0)
        if abs(have - ledger) > max(tolerance, 1e-6 * max(have, ledger)):
            out.append({"coin": c, "coinbase": have, "ledger": ledger, "difference": have - ledger})
    return out
