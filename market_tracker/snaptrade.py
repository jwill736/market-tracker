"""Automatic account sync through SnapTrade (Robinhood, Coinbase and other brokers), read-only.

SnapTrade is a paid service that connects to brokerages on your behalf. With a personal API
key (snaptrade.com → Dashboard → API keys) put two lines in .env:

    SNAPTRADE_CLIENT_ID="..."
    SNAPTRADE_CONSUMER_KEY="..."

(A commercial key also needs SNAPTRADE_USER_ID and SNAPTRADE_USER_SECRET for the user it
registered.) Then Portfolio → Import → Connect a broker opens SnapTrade's connection page,
asking only for read access: Plumbline can see trades and positions but can't place orders or
move money.

Sync reads every connected account's activity (buys, sells, reinvested dividends, dividends,
interest) into the ledger and the Income tab, and compares each position with SnapTrade's
holdings. A trade already in the ledger from a CSV import (same account, symbol, side, day and
quantity) is not added twice. Anything the ledger can't explain (transfers in, splits, trades
older than the broker shares) is listed as a difference, not guessed.

Requests are signed as SnapTrade's own SDK signs them: HMAC-SHA256 with the consumer key over
the JSON {"content": body, "path": path, "query": query-string}, base64, in a Signature header.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from base64 import b64encode
from datetime import date, timedelta
from urllib.parse import quote, urlencode

import httpx

from .providers import market

HOST = "https://api.snaptrade.com"
PAGE = 1000
HISTORY_YEARS = 10
TRADES = {"BUY", "SELL", "REI"}
INCOME = {"DIVIDEND": "dividend", "STOCK_DIVIDEND": "dividend", "INTEREST": "interest"}


class SnapTradeError(Exception):
    pass


def configured() -> bool:
    return bool(os.environ.get("SNAPTRADE_CLIENT_ID") and os.environ.get("SNAPTRADE_CONSUMER_KEY"))


def signature(path: str, query: str, body, consumer_key: str) -> str:
    content = None if body in (None, {}) else body
    raw = json.dumps({"content": content, "path": path, "query": query}, separators=(",", ":"), sort_keys=True)
    return b64encode(hmac.new(consumer_key.encode(), raw.encode(), hashlib.sha256).digest()).decode()


def signed(path: str, params: dict | None = None, body=None, *, now: float | None = None,
           client_id: str | None = None, consumer_key: str | None = None) -> tuple[str, dict]:
    """(full URL, headers) for a request. The query string is built once and sent exactly as
    signed: the order is the operation's parameters, then clientId, then timestamp."""
    client_id = client_id or os.environ.get("SNAPTRADE_CLIENT_ID", "")
    consumer_key = consumer_key or os.environ.get("SNAPTRADE_CONSUMER_KEY", "")
    q = [(k, v) for k, v in (params or {}).items() if v is not None]
    user, secret = os.environ.get("SNAPTRADE_USER_ID"), os.environ.get("SNAPTRADE_USER_SECRET")
    if user and secret:
        q += [("userId", user), ("userSecret", secret)]
    q += [("clientId", client_id), ("timestamp", str(int(now if now is not None else time.time())))]
    query = urlencode(q, quote_via=quote, safe=",")
    headers = {"Signature": signature(path, query, body, consumer_key), "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    return f"{HOST}{path}?{query}", headers


def request(method: str, path: str, params: dict | None = None, body=None, timeout: float = 30.0):
    if not configured():
        raise SnapTradeError("Add SNAPTRADE_CLIENT_ID and SNAPTRADE_CONSUMER_KEY to .env first.")
    url, headers = signed(path, params, body)
    content = json.dumps(body, separators=(",", ":"), sort_keys=True) if body is not None else None
    for attempt in range(3):
        try:
            resp = httpx.request(method, url, headers=headers, content=content, timeout=timeout)
        except httpx.HTTPError as exc:
            raise SnapTradeError(f"Couldn't reach SnapTrade: {exc}") from exc
        if resp.status_code == 429 and attempt < 2:
            time.sleep(min(float(resp.headers.get("Retry-After") or 6), 30))
            url, headers = signed(path, params, body)       # fresh timestamp
            continue
        break
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail") or resp.text[:200]
        except ValueError:
            detail = resp.text[:200]
        raise SnapTradeError(f"SnapTrade {resp.status_code}: {detail}")
    return resp.json()


# ------------------------------------------------------------------ endpoints

def connect_url(broker: str | None = None, reconnect: str | None = None) -> str:
    """The Connection Portal link where you sign in to a broker (read-only access)."""
    body = {"connectionType": "read"}
    if broker:
        body["broker"] = broker
    if reconnect:
        body["reconnect"] = reconnect
    got = request("POST", "/snapTrade/login", body=body)
    url = got.get("redirectURI") if isinstance(got, dict) else None
    if not url:
        raise SnapTradeError("SnapTrade didn't return a connection link.")
    return url


def accounts() -> list[dict]:
    return request("GET", "/accounts") or []


def holdings(account_id: str) -> dict:
    return request("GET", f"/accounts/{account_id}/holdings") or {}


def activities(account_id: str, start: str, end: str) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        got = request("GET", f"/accounts/{account_id}/activities",
                      {"startDate": start, "endDate": end, "offset": offset, "limit": PAGE})
        rows = got.get("data", []) if isinstance(got, dict) else (got or [])
        out += rows
        if len(rows) < PAGE:
            return out
        offset += PAGE


# ------------------------------------------------------------------ into the ledger

def account_name(acct: dict) -> str:
    """Names match the CSV imports ("Robinhood", "Coinbase", "Stash") so both land together."""
    inst = (acct.get("institution_name") or "").strip()
    for known in ("Robinhood", "Coinbase", "Stash"):
        if inst.lower().startswith(known.lower()):
            return known
    return (inst or acct.get("name") or "SnapTrade")[:40]


def _ticker(sym: dict | None, institution: str) -> str | None:
    if not isinstance(sym, dict):
        return None
    raw = sym.get("symbol") or sym.get("raw_symbol")
    if isinstance(raw, dict):               # holdings nest one level deeper
        return _ticker(raw, institution)
    if not raw:
        return None
    s = market.normalize_symbol(str(raw))
    kind = str(((sym.get("type") or {}).get("code") if isinstance(sym.get("type"), dict) else sym.get("type")) or "").lower()
    if ("crypto" in kind or "coinbase" in institution.lower()) and "-" not in s:
        s = f"{s}-USD"
    return s


def to_rows(acts: list[dict], account: str, institution: str = "") -> tuple[list[dict], list[dict], dict[str, int], set[str]]:
    """(ledger transactions, income rows, skipped counts by type, symbols with reinvested dividends)."""
    txs, income, skipped, drip = [], [], {}, set()
    for a in acts:
        kind = (a.get("type") or "").upper()
        day = (a.get("trade_date") or a.get("settlement_date") or "")[:10]
        sym = _ticker(a.get("symbol"), institution or a.get("institution") or "")
        key = f"st:{a.get('id')}"
        if kind in TRADES and sym and day:
            units, price = abs(float(a.get("units") or 0)), float(a.get("price") or 0)
            if units <= 0:
                skipped[kind] = skipped.get(kind, 0) + 1
                continue
            if kind == "REI":
                drip.add(sym)
            txs.append({"symbol": sym, "side": "sell" if kind == "SELL" else "buy", "quantity": units, "price": price,
                        "fees": abs(float(a.get("fee") or 0)), "date": day, "import_key": key, "account": account,
                        "note": "SnapTrade sync" + (": reinvested dividend" if kind == "REI" else "")})
        elif kind in INCOME and day and a.get("amount") is not None:
            income.append({"symbol": sym or "", "day": day, "amount": float(a["amount"]), "kind": INCOME[kind],
                           "account": account, "import_key": key, "note": (a.get("description") or "")[:120]})
        else:
            skipped[kind or "?"] = skipped.get(kind or "?", 0) + 1
    return txs, income, skipped, drip


def match(t: dict, pool: list[dict]) -> int | None:
    """Index in `pool` of the same trade imported earlier from a CSV (keys differ, so compare
    the trade itself: account, symbol, side, quantity, within a day). Callers remove the match
    from the pool, so two identical fills need two earlier ones."""
    d = date.fromisoformat(t["date"])
    for i, e in enumerate(pool):
        if (e["symbol"] == t["symbol"] and e["side"] == t["side"] and (e.get("account") or "") == t["account"]
                and abs(e["quantity"] - t["quantity"]) <= 1e-6 * max(1.0, t["quantity"])
                and abs((date.fromisoformat(e["date"][:10]) - d).days) <= 1):
            return i
    return None


def match_income(r: dict, pool: list[dict]) -> int | None:
    for i, e in enumerate(pool):
        if (e["symbol"] == r["symbol"] and (e.get("account") or "") == r["account"] and e["kind"] == r["kind"]
                and abs(e["amount"] - r["amount"]) < 0.015
                and abs((date.fromisoformat(e["day"]) - date.fromisoformat(r["day"])).days) <= 3):
            return i
    return None


def new_only(rows: list[dict], known_keys: set[str], pool: list[dict], matcher) -> tuple[list[dict], int]:
    """Rows not already imported (by key) and not matching an earlier CSV import; and how many were dropped."""
    pool = [p for p in pool if not (p.get("import_key") or "").startswith("st:")]
    fresh, dup = [], 0
    for r in rows:
        if r["import_key"] in known_keys:
            dup += 1
            continue
        i = matcher(r, pool)
        if i is not None:
            pool.pop(i)
            dup += 1
            continue
        fresh.append(r)
    return fresh, dup


def reconcile(positions: list[dict], ledger_qty: dict[str, float], institution: str = "") -> list[dict]:
    """Differences between the broker's positions and the ledger for one account."""
    broker: dict[str, float] = {}
    for p in positions or []:
        s = _ticker(p.get("symbol"), institution)
        if s:
            broker[s] = broker.get(s, 0.0) + float(p.get("units") or 0)
    out = []
    for s in sorted(set(broker) | {k for k, v in ledger_qty.items() if abs(v) > 1e-9}):
        have, led = broker.get(s, 0.0), ledger_qty.get(s, 0.0)
        if abs(have - led) > max(1e-6, 1e-6 * max(abs(have), abs(led))):
            out.append({"symbol": s, "broker": round(have, 8), "ledger": round(led, 8), "difference": round(have - led, 8)})
    return out


def fetch_all(today: date | None = None) -> list[dict]:
    """Every connected account with its activity and positions (network)."""
    today = today or date.today()
    start = (today - timedelta(days=366 * HISTORY_YEARS)).isoformat()
    out = []
    for acct in accounts():
        acct_id = acct.get("id")
        if not acct_id:
            continue
        out.append({"account": acct, "activities": activities(acct_id, start, today.isoformat()),
                    "holdings": holdings(acct_id)})
    return out
