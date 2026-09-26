"""Place orders from the app, with a preview and your confirmation every time.

Where orders can go (all free, official APIs):
- Coinbase: any coin, with a Coinbase key that has the Trade permission.
- Robinhood: crypto only, with a Robinhood Crypto API key.
- Stocks and funds (Robinhood, Stash): no broker offers individuals an API, so the app builds
  the order and opens it in the broker's app; the confirmation email then brings the trade in.

Safety, in order:
1. Trading is off until you switch it on (Settings), and each order is previewed first: the
   broker's own estimate, fees, what it does to your taxes (wash sales, short- vs long-term,
   the tax on a sale) and to your concentration.
2. Confirming sends exactly what was previewed: the preview is sealed with a one-time code that
   expires after 90 seconds, so a changed amount or a stale price needs a new preview.
3. Limits per order and per day (defaults $250 and $500; settable), and sells can't exceed what
   the account holds.
4. Every attempt is written to the trade log, sent or not.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from . import coinbase_sync, db, http, robinhood_crypto, taxes
from .providers import market

SEAL_SECONDS = 90
DEFAULT_MAX_ORDER = 250.0
DEFAULT_DAILY = 500.0
_SECRET = secrets.token_bytes(32)          # new every start: a preview never survives a restart
_used: set[str] = set()


class TradeError(Exception):
    pass


@dataclass
class Order:
    venue: str               # coinbase / robinhood / ticket
    symbol: str
    side: str                # buy / sell
    dollars: float | None = None
    quantity: float | None = None
    limit_price: float | None = None

    def canonical(self) -> str:
        return json.dumps({"v": self.venue, "s": self.symbol, "d": self.side, "$": round(self.dollars or 0, 2),
                           "q": round(self.quantity or 0, 8), "l": round(self.limit_price or 0, 8)}, sort_keys=True)


# ------------------------------------------------------------------ settings and log

def settings(conn) -> dict:
    def num(k, d):
        try:
            return float(db.get_meta(conn, k, "") or d)
        except ValueError:
            return d
    return {"enabled": db.get_meta(conn, "trading_enabled", "") == "1",
            "max_order": num("trading_max_order", DEFAULT_MAX_ORDER), "daily_limit": num("trading_daily_limit", DEFAULT_DAILY)}


def spent_today(conn, today: date | None = None) -> float:
    day = (today or date.today()).isoformat()
    row = conn.execute("SELECT COALESCE(SUM(usd), 0) AS s FROM trade_log WHERE substr(at, 1, 10) = ? AND status = 'placed'",
                       (day,)).fetchone()
    return float(row["s"] or 0)


def log(conn, order: Order, usd: float, status: str, broker_id: str = "", detail: dict | None = None) -> int:
    cur = conn.execute("INSERT INTO trade_log (at, venue, symbol, side, usd, quantity, limit_price, status, broker_order_id, detail) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (datetime.now(timezone.utc).isoformat(timespec="seconds"), order.venue, order.symbol, order.side,
                        round(usd, 2), order.quantity, order.limit_price, status, broker_id, json.dumps(detail or {})[:4000]))
    return cur.lastrowid


def venues(symbol: str) -> list[str]:
    """Where this symbol can be traded from the app."""
    if market.asset_class(symbol) != "crypto":
        return ["ticket"]
    out = []
    if coinbase_sync.configured():
        out.append("coinbase")
    if robinhood_crypto.configured():
        out.append("robinhood")
    return out + ["ticket"]


# ------------------------------------------------------------------ checks

def held_in(transactions: list[dict], symbol: str, account: str) -> float:
    q = 0.0
    for t in transactions:
        if t["symbol"] == symbol and (t.get("account") or "") == account:
            q += t["quantity"] if t["side"] == "buy" else -t["quantity"]
    return max(q, 0.0)


def checks(order: Order, usd: float, price: float, transactions: list[dict], plan: dict | None, today: date,
           st_rate: float = taxes.ST_RATE, lt_rate: float = taxes.LT_RATE,
           methods: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """(warnings, blockers) for an order about to be placed."""
    warn, block = [], []
    account = {"coinbase": "Coinbase", "robinhood": "Robinhood"}.get(order.venue, "")
    if order.side == "buy":
        for b in taxes.blackout(transactions, today):
            if order.symbol in b["avoid"] and not taxes.is_crypto(order.symbol):
                warn.append(f"You sold {b['symbol']} at a loss on {b['sold']}: buying before {b['until']} washes that loss")
        if plan and plan.get("base"):
            row = next((h for h in plan.get("holdings", []) if h["symbol"] == order.symbol), None)
            new_w = ((row["value"] if row else 0) + usd) / (plan["base"] + usd)
            if new_w > plan.get("cap", 1):
                warn.append(f"After this, {order.symbol} would be {new_w:.0%} of your portfolio, over your {plan['cap']:.0%} cap")
            if row and row["verdict"] in ("Sell?", "Trim"):
                warn.append(f"Your hold plan says {row['verdict']} for {order.symbol}")
    else:
        qty = order.quantity or (usd / price if price else 0)
        if account:
            have = held_in(transactions, order.symbol, account)
            if qty > have * 1.0001:
                block.append(f"Your ledger shows {have:g} {order.symbol} in {account}; this sells {qty:g}")
        lots, _ = taxes.lots_and_sales(transactions)
        method = (methods or {}).get(account, "fifo")
        left, gain_st, gain_lt = qty, 0.0, 0.0
        mine = [lt for lt in lots.get(order.symbol, []) if not account or lt.account == account]
        for lot in taxes.order_lots(mine, method, price, today.isoformat()):
            if left <= 0:
                break
            take = min(left, lot.quantity)
            g = (price - lot.cost) * take
            if (today - date.fromisoformat(lot.date)).days > 365:
                gain_lt += g
            else:
                gain_st += g
            left -= take
        tax = gain_st * st_rate + gain_lt * lt_rate
        cmp = taxes.compare_methods(lots, order.symbol, qty, price, today, account, st_rate, lt_rate)
        best = next((r for r in cmp["methods"] if r["method"] == cmp["best"]), None)
        if best and best["method"] != method and tax - best["tax"] >= 5:
            warn.append(f"Choosing lots {best['label'].split(' (')[0].lower()} at your broker would cut the tax on this sale by about "
                        f"${tax - best['tax']:,.0f} (Portfolio → Taxes → Which shares to sell)")
        if gain_st + gain_lt > 0:
            warn.append(f"Gain of about ${gain_st + gain_lt:,.0f} ({'short' if gain_st >= gain_lt else 'long'}-term): "
                        f"about ${tax:,.0f} in federal tax at your rates")
        elif gain_st + gain_lt < 0:
            recent = taxes.recent_buys(transactions, order.symbol, today)
            if recent and not taxes.is_crypto(order.symbol):
                warn.append(f"A loss, but you bought {order.symbol} in the last 30 days: part of the loss would be washed")
            else:
                warn.append(f"Loss of about ${-(gain_st + gain_lt):,.0f}, which can offset gains")
    return warn, block


# ------------------------------------------------------------------ preview / place

def _seal(order: Order, expires: int) -> str:
    nonce = secrets.token_hex(8)                     # each preview gets its own one-time code
    msg = f"{order.canonical()}|{expires}|{nonce}".encode()
    return f"{expires}.{nonce}.{hmac.new(_SECRET, msg, hashlib.sha256).hexdigest()}"


def _unseal(order: Order, token: str, now: float) -> None:
    try:
        exp_s, nonce, sig = token.split(".", 2)
        expires = int(exp_s)
    except ValueError:
        raise TradeError("Preview again: the confirmation code is invalid")
    good = hmac.new(_SECRET, f"{order.canonical()}|{expires}|{nonce}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        raise TradeError("Preview again: the order changed since it was previewed")
    if now > expires:
        raise TradeError("Preview again: the preview expired (prices move)")
    if token in _used:
        raise TradeError("This order was already sent")


def venue_quote(order: Order, cb_send=None, rh_send=None) -> dict:
    """The broker's own estimate: price, dollars, quantity, fees."""
    if order.venue == "coinbase":
        cfg = coinbase_sync.order_configuration(order.side, order.dollars, order.quantity, order.limit_price)
        if order.side == "sell" and not order.quantity and order.dollars:
            price = market.get_quote(order.symbol).price
            order.quantity = round(order.dollars / price, 8)
            cfg = coinbase_sync.order_configuration(order.side, order.dollars, order.quantity, order.limit_price)
        p = coinbase_sync.preview_order(order.symbol, order.side, cfg, cb_send)
        errs = [e.get("preview_failure_reason") or str(e) for e in p.get("errs") or [] if e]
        total = float(p.get("order_total") or 0)
        base = float(p.get("base_size") or order.quantity or 0)
        return {"price": (float(p.get("quote_size") or total) / base) if base else float(p.get("best_ask") or 0),
                "usd": total, "quantity": base, "fees": float(p.get("commission_total") or 0), "errors": errs,
                "broker": "Coinbase"}
    if order.venue == "robinhood":
        bp = robinhood_crypto.best_price(order.symbol, rh_send)
        price = order.limit_price or (bp["ask"] if order.side == "buy" else bp["bid"]) or bp["mid"]
        qty = order.quantity or ((order.dollars or 0) / price if price else 0)
        return {"price": price, "usd": qty * price, "quantity": round(qty, 8), "fees": 0.0, "errors": [], "broker": "Robinhood",
                "note": "Robinhood's price includes its spread; there's no separate fee"}
    q = market.get_live_quote(order.symbol)
    price = order.limit_price or q.price
    qty = order.quantity or ((order.dollars or 0) / price if price else 0)
    return {"price": price, "usd": qty * price, "quantity": round(qty, 6), "fees": 0.0, "errors": [], "broker": "your broker app"}


def preview(order: Order, transactions: list[dict], plan: dict | None, cfg: dict, spent: float, *,
            now: float | None = None, today: date | None = None, quote_fn=None, methods: dict[str, str] | None = None) -> dict:
    now = now or time.time()
    today = today or date.today()
    if order.side not in ("buy", "sell"):
        raise TradeError("Side must be buy or sell")
    if not (order.dollars or order.quantity):
        raise TradeError("Give an amount in dollars or a quantity")
    try:
        q = (quote_fn or venue_quote)(order)
    except (http.DataUnavailable, robinhood_crypto.RobinhoodError, KeyError, ValueError) as exc:
        raise TradeError(f"Couldn't get a price from {order.venue}: {exc}")
    usd = q["usd"] or (order.dollars or 0)
    warn, block = checks(order, usd, q["price"], transactions, plan, today, methods=methods)
    block += q.get("errors") or []
    if order.venue != "ticket":
        if not cfg["enabled"]:
            block.append("Trading from the app is off: switch it on in Settings → Trading first")
        if usd > cfg["max_order"]:
            block.append(f"${usd:,.2f} is over your ${cfg['max_order']:,.0f} per-order limit")
        if spent + usd > cfg["daily_limit"]:
            block.append(f"Today's orders would reach ${spent + usd:,.2f}, over your ${cfg['daily_limit']:,.0f} daily limit")
    token = _seal(order, int(now) + SEAL_SECONDS) if not block and order.venue != "ticket" else None
    return {"order": order.__dict__, "estimate": q, "warnings": warn, "blockers": block, "token": token,
            "expires_in": SEAL_SECONDS if token else None, "spent_today": round(spent, 2), "limits": cfg}


def place(order: Order, token: str, conn, *, now: float | None = None, cb_send=None, rh_send=None) -> dict:
    """Send a previewed order. Raises TradeError without sending when anything doesn't match."""
    now = now or time.time()
    _unseal(order, token, now)
    cfg = settings(conn)
    if not cfg["enabled"]:
        raise TradeError("Trading from the app is off")
    est = order.dollars or 0
    if order.quantity and not est:
        est = order.quantity * (order.limit_price or market.get_quote(order.symbol).price)
    if est > cfg["max_order"] * 1.02 or spent_today(conn) + est > cfg["daily_limit"] * 1.02:
        raise TradeError("Over your trading limits")
    _used.add(token)
    client_id = str(uuid.uuid4())
    try:
        if order.venue == "coinbase":
            res = coinbase_sync.create_order(order.symbol, order.side,
                                             coinbase_sync.order_configuration(order.side, order.dollars, order.quantity, order.limit_price),
                                             client_id, cb_send)
            if not res.get("success"):
                reason = (res.get("error_response") or {}).get("message") or res.get("failure_reason") or res
                log(conn, order, est, "failed", "", res)
                raise TradeError(f"Coinbase rejected it: {reason}")
            broker_id = (res.get("success_response") or {}).get("order_id") or res.get("order_id") or ""
        elif order.venue == "robinhood":
            qty = order.quantity
            if not qty:
                price = robinhood_crypto.best_price(order.symbol, rh_send)["ask"]
                qty = round((order.dollars or 0) / price, 8) if price else 0
            res = robinhood_crypto.place(robinhood_crypto.order_body(order.symbol, order.side, qty, order.limit_price, client_id), rh_send)
            broker_id = res.get("id", "")
        else:
            raise TradeError("Stocks are placed in your broker's app; use the order ticket")
    except (http.DataUnavailable, robinhood_crypto.RobinhoodError, ValueError, TypeError) as exc:
        # ValueError: a key that doesn't load (wrong format, or missing): nothing was sent.
        log(conn, order, est, "failed", "", {"error": str(exc)[:300]})
        raise TradeError(f"Not sent: {str(exc)[:200]}")
    log(conn, order, est, "placed", broker_id, res)
    return {"placed": True, "broker_order_id": broker_id, "venue": order.venue, "usd": round(est, 2),
            "note": "Sent. The trade shows up in your ledger at the next sync (within about 5 minutes)."}


def recent(conn, days: int = 30) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [dict(r) for r in conn.execute("SELECT * FROM trade_log WHERE at >= ? ORDER BY id DESC LIMIT 100", (since,))]
