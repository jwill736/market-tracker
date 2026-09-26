"""The brokers the app's own order engine can send stock orders to.

Robinhood and Stash have no stock API for individuals (Robinhood's 2026 "agentic trading" works
only in a separate account, through an AI agent). So the order engine (preview, your limits,
the sealed one-time confirm, the trade log: trading.py) sends stock orders to:

- Paper: the app's own simulated account. Fills at the live price with pretend money (starts at
  $10,000, resettable). No broker, no risk: the way to use the whole flow today.
- Alpaca: a real broker with a free API for individuals, commission-free, fractional shares and
  dollar amounts. Its paper mode (a separate key) is also pretend money; a live key trades real
  money. Endpoints and fields from Alpaca's official SDK (alpaca-py).
- Public.com: a consumer broker with a free "Individual Trader API", fractional shares by
  dollar amount. Real money only (no paper mode). Endpoints and fields from Public's official
  SDK (publicdotcom-py). Not yet run against a live account.

Using Alpaca or Public means holding money there: new money can go there by bank transfer.
Moving existing Robinhood or Stash shares (an ACATS transfer) costs about $75-100 per account,
and fractional shares get sold rather than moved.

Filled orders at Alpaca (live) and Public come into the ledger as trades in accounts "Alpaca"
and "Public" (settle_pending). Paper fills never touch the real ledger.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

PAPER_START = 10_000.0
ALPACA_PAPER = "https://paper-api.alpaca.markets"
ALPACA_LIVE = "https://api.alpaca.markets"
ALPACA_DATA = "https://data.alpaca.markets"
PUBLIC_API = "https://api.public.com"


class BrokerError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ paper (the app's own)

PAPER_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL
);
"""


def paper_state(conn) -> dict:
    from . import db
    conn.executescript(PAPER_SCHEMA)
    start = float(db.get_meta(conn, "paper_start", str(PAPER_START)) or PAPER_START)
    cash, pos = start, {}
    trades = [dict(r) for r in conn.execute("SELECT * FROM paper_trades ORDER BY id")]
    for t in trades:
        p = pos.setdefault(t["symbol"], {"quantity": 0.0, "cost": 0.0})
        if t["side"] == "buy":
            cash -= t["quantity"] * t["price"]
            p["cost"] += t["quantity"] * t["price"]
            p["quantity"] += t["quantity"]
        else:
            cash += t["quantity"] * t["price"]
            if p["quantity"] > 0:
                p["cost"] *= max(0.0, 1 - t["quantity"] / p["quantity"])
            p["quantity"] -= t["quantity"]
    return {"start": start, "cash": round(cash, 2), "trades": trades[::-1][:50],
            "positions": {s: {"quantity": round(p["quantity"], 8), "cost": round(p["cost"], 2)} for s, p in pos.items()
                          if p["quantity"] > 1e-9}}


def paper_fill(conn, symbol: str, side: str, quantity: float, price: float) -> dict:
    """Fill a paper order at `price`. Checks pretend cash and pretend holdings."""
    st = paper_state(conn)
    if quantity <= 0 or price <= 0:
        raise BrokerError("Nothing to fill")
    if side == "buy" and quantity * price > st["cash"] + 0.005:
        raise BrokerError(f"The paper account has ${st['cash']:,.2f}; this costs ${quantity * price:,.2f}")
    if side == "sell" and quantity > (st["positions"].get(symbol) or {}).get("quantity", 0.0) + 1e-9:
        raise BrokerError(f"The paper account holds {(st['positions'].get(symbol) or {}).get('quantity', 0):g} {symbol}")
    cur = conn.execute("INSERT INTO paper_trades (at, symbol, side, quantity, price) VALUES (?, ?, ?, ?, ?)",
                       (_now(), symbol, side, round(quantity, 8), price))
    return {"id": f"paper-{cur.lastrowid}", "symbol": symbol, "side": side, "quantity": round(quantity, 8), "price": price}


def paper_reset(conn, start: float = PAPER_START) -> None:
    from . import db
    conn.executescript(PAPER_SCHEMA)
    conn.execute("DELETE FROM paper_trades")
    db.set_meta(conn, "paper_start", str(start))


# ------------------------------------------------------------------ Alpaca

def alpaca_configured() -> bool:
    return bool(os.environ.get("ALPACA_KEY_ID") and os.environ.get("ALPACA_SECRET_KEY"))


def alpaca_is_live() -> bool:
    return os.environ.get("ALPACA_LIVE", "") == "1"


def _alpaca(method: str, path: str, body: dict | None = None, params: dict | None = None, send=None, base: str | None = None):
    import httpx
    url = (base or (ALPACA_LIVE if alpaca_is_live() else ALPACA_PAPER)) + path
    headers = {"APCA-API-KEY-ID": os.environ.get("ALPACA_KEY_ID", ""), "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", "")}
    send = send or (lambda m, u, **kw: httpx.request(m, u, headers=headers, timeout=20, **kw))
    try:
        r = send(method, url, json=body, params=params)
    except httpx.HTTPError as exc:
        raise BrokerError(f"Alpaca: {exc}")
    if r.status_code >= 400:
        try:
            msg = (r.json() or {}).get("message") or r.text[:200]
        except ValueError:
            msg = r.text[:200]
        raise BrokerError(f"Alpaca said {r.status_code}: {msg}")
    return r.json() if r.content else {}


def alpaca_order_body(symbol: str, side: str, dollars: float | None, quantity: float | None, limit_price: float | None,
                      client_id: str) -> dict:
    """Fractional and dollar orders must be day orders (Alpaca's rule)."""
    body = {"symbol": symbol, "side": side, "type": "limit" if limit_price else "market", "time_in_force": "day",
            "client_order_id": client_id}
    if quantity:
        body["qty"] = f"{quantity:.9f}".rstrip("0").rstrip(".")
    else:
        body["notional"] = f"{dollars:.2f}"
    if limit_price:
        body["limit_price"] = f"{limit_price:.2f}" if limit_price >= 1 else f"{limit_price:.4f}"
    return body


def alpaca_place(symbol, side, dollars, quantity, limit_price, client_id, send=None) -> dict:
    return _alpaca("POST", "/v2/orders", alpaca_order_body(symbol, side, dollars, quantity, limit_price, client_id), send=send)


def alpaca_order(order_id: str, send=None) -> dict:
    return _alpaca("GET", f"/v2/orders/{order_id}", send=send)


def alpaca_account(send=None) -> dict:
    a = _alpaca("GET", "/v2/account", send=send)
    return {"cash": float(a.get("cash") or 0), "buying_power": float(a.get("buying_power") or 0),
            "value": float(a.get("portfolio_value") or a.get("equity") or 0), "status": a.get("status", "")}


def alpaca_positions(send=None) -> dict[str, float]:
    return {p["symbol"]: float(p.get("qty") or 0) for p in _alpaca("GET", "/v2/positions", send=send) or []}


def alpaca_price(symbol: str, send=None) -> float | None:
    """Latest trade on the free IEX feed."""
    try:
        d = _alpaca("GET", "/v2/stocks/trades/latest", params={"symbols": symbol, "feed": "iex"}, send=send, base=ALPACA_DATA)
        return float(((d.get("trades") or {}).get(symbol) or {}).get("p") or 0) or None
    except BrokerError:
        return None


def alpaca_fill(o: dict) -> dict | None:
    """A filled (or partly filled, then done) Alpaca order as {quantity, price, date}."""
    qty = float(o.get("filled_qty") or 0)
    if qty <= 0 or o.get("status") not in ("filled", "canceled", "expired", "done_for_day"):
        return None
    return {"quantity": qty, "price": float(o.get("filled_avg_price") or 0), "date": (o.get("filled_at") or o.get("updated_at") or _now())[:10]}


# ------------------------------------------------------------------ Public.com

_public_token: dict = {}


def public_configured() -> bool:
    return bool(os.environ.get("PUBLIC_API_SECRET"))


def _public(method: str, path: str, body: dict | None = None, send=None, auth: bool = True):
    import httpx
    send = send or (lambda m, u, headers, **kw: httpx.request(m, u, headers=headers, timeout=20, **kw))
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {public_token(send)}"
    try:
        r = send(method, PUBLIC_API + path, headers, json=body)
    except httpx.HTTPError as exc:
        raise BrokerError(f"Public: {exc}")
    if r.status_code >= 400:
        raise BrokerError(f"Public said {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


def public_token(send=None, now: float | None = None) -> str:
    """A short-lived access token from your API secret (Public: Settings → API)."""
    now = now or time.time()
    if _public_token.get("token") and _public_token.get("until", 0) > now:
        return _public_token["token"]
    d = _public("POST", "/userapiauthservice/personal/access-tokens",
                {"secret": os.environ.get("PUBLIC_API_SECRET", ""), "validityInMinutes": 60}, send, auth=False)
    tok = d.get("accessToken")
    if not tok:
        raise BrokerError("Public didn't return an access token: check the API secret")
    _public_token.update(token=tok, until=now + 55 * 60)
    return tok


def public_account_id(send=None) -> str:
    acct = os.environ.get("PUBLIC_ACCOUNT_ID")
    if acct:
        return acct
    accts = (_public("GET", "/userapigateway/trading/account", send=send) or {}).get("accounts") or []
    brokerage = [a for a in accts if a.get("accountType") == "BROKERAGE"] or accts
    if not brokerage:
        raise BrokerError("No Public brokerage account found")
    os.environ["PUBLIC_ACCOUNT_ID"] = brokerage[0]["accountId"]
    return brokerage[0]["accountId"]


def public_order_body(symbol: str, side: str, dollars: float | None, quantity: float | None, limit_price: float | None,
                      order_id: str) -> dict:
    body = {"orderId": order_id, "instrument": {"symbol": symbol, "type": "EQUITY"}, "orderSide": side.upper(),
            "orderType": "LIMIT" if limit_price else "MARKET", "expiration": {"timeInForce": "DAY"}}
    if quantity:
        body["quantity"] = f"{quantity:.5f}"
    else:
        body["amount"] = f"{dollars:.2f}"
    if limit_price:
        body["limitPrice"] = f"{limit_price:.2f}"
    return body


def public_preflight(symbol, side, dollars, quantity, limit_price, send=None) -> dict:
    acct = public_account_id(send)
    body = public_order_body(symbol, side, dollars, quantity, limit_price, str(uuid.uuid4()))
    body.pop("orderId")
    return _public("POST", f"/userapigateway/trading/{acct}/preflight/single-leg", body, send)


def public_place(symbol, side, dollars, quantity, limit_price, order_id, send=None) -> dict:
    acct = public_account_id(send)
    return _public("POST", f"/userapigateway/trading/{acct}/order",
                   public_order_body(symbol, side, dollars, quantity, limit_price, order_id), send)


def public_order(order_id: str, send=None) -> dict:
    return _public("GET", f"/userapigateway/trading/{public_account_id(send)}/order/{order_id}", send=send)


def public_fill(o: dict) -> dict | None:
    qty = float(o.get("filledQuantity") or 0)
    if qty <= 0 or o.get("status") not in ("FILLED", "CANCELLED", "EXPIRED"):
        return None
    return {"quantity": qty, "price": float(o.get("averagePrice") or 0), "date": (o.get("closedAt") or _now())[:10]}


# ------------------------------------------------------------------ what's set up

def overview(conn) -> list[dict]:
    st = paper_state(conn)
    out = [{"key": "paper", "name": "Paper account", "ready": True, "money": "pretend",
            "text": f"${st['cash']:,.2f} pretend cash, {len(st['positions'])} positions (started with ${st['start']:,.0f})"}]
    out.append({"key": "alpaca", "name": "Alpaca" + ("" if alpaca_is_live() else " (paper)"), "ready": alpaca_configured(),
                "money": "real" if alpaca_is_live() else "pretend",
                "text": ("Connected" + (": real money" if alpaca_is_live() else ": its paper mode, pretend money")) if alpaca_configured()
                else "Not connected: a free account at alpaca.markets, then an API key (paper keys to practise)"})
    out.append({"key": "public", "name": "Public.com", "ready": public_configured(), "money": "real",
                "text": "Connected: real money" if public_configured()
                else "Not connected: Public app → Settings → API → create a secret (real money; no paper mode)"})
    return out
