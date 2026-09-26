"""Auto-invest schedules (Stash's Auto-Stash, Robinhood recurring investments): tell the app
"$20 into VOO every Monday" and it records those buys itself on each date, at that day's
closing price, so an account with no feed stays roughly right between statements.

A schedule's buy is skipped when the ledger already has a buy of that symbol in that account
within three days (from a trade email or an import), so it never double-counts. Buys are
marked as coming from the schedule; the statement check (or a CSV) corrects any drift.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from . import db

EVERY = ("week", "2weeks", "month")


@dataclass
class Schedule:
    id: int
    account: str
    symbol: str
    amount: float
    every: str          # week / 2weeks / month
    day: int            # weekday 0=Mon..6 for week/2weeks; day of month 1..28 for month
    start: str
    active: bool = True


def due_dates(s: Schedule, until: date) -> list[date]:
    start = date.fromisoformat(s.start)
    out: list[date] = []
    if s.every in ("week", "2weeks"):
        d = start + timedelta(days=(s.day - start.weekday()) % 7)
        step = 7 if s.every == "week" else 14
        while d <= until:
            out.append(d)
            d += timedelta(days=step)
    else:
        y, m = start.year, start.month
        while True:
            d = date(y, m, min(s.day, calendar.monthrange(y, m)[1]))
            if d > until:
                break
            if d >= start:
                out.append(d)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return [trading_day(d) for d in out if trading_day(d) <= until]


def trading_day(d: date) -> date:
    """Weekend dates move to the next Monday (brokers invest on market days)."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def pending(s: Schedule, transactions: list[dict], today: date) -> list[date]:
    """Due dates with no buy recorded yet (by this schedule, or any buy of it in that account ±3 days)."""
    keyed = {t.get("import_key") for t in transactions}
    buys = [date.fromisoformat(t["date"][:10]) for t in transactions
            if t["symbol"] == s.symbol and t["side"] == "buy" and (t.get("account") or "") == s.account
            and not (t.get("import_key") or "").startswith("sched:")]
    out = []
    for d in due_dates(s, today):
        if f"sched:{s.id}:{d.isoformat()}" in keyed:
            continue
        if any(abs((b - d).days) <= 3 for b in buys):
            continue
        out.append(d)
    return out


def apply(conn, today: date, close_fn) -> list[dict]:
    """Record every due buy that isn't in the ledger yet. close_fn(symbol, date) -> price or None."""
    added = []
    txs = db.list_transactions(conn)
    for s in load(conn):
        if not s.active:
            continue
        for d in pending(s, txs, today):
            price = close_fn(s.symbol, d)
            if not price:
                continue
            qty = round(s.amount / price, 6)
            db.add_transaction(conn, s.symbol, "buy", qty, price, d.isoformat(), 0.0,
                               f"Auto-invest schedule: ${s.amount:,.2f} every {s.every}", import_key=f"sched:{s.id}:{d.isoformat()}",
                               account=s.account)
            added.append({"symbol": s.symbol, "date": d.isoformat(), "quantity": qty, "price": price, "account": s.account})
    return added


def load(conn) -> list[Schedule]:
    return [Schedule(r["id"], r["account"], r["symbol"], r["amount"], r["every"], r["day"], r["start"], bool(r["active"]))
            for r in conn.execute("SELECT * FROM auto_invest ORDER BY id")]


def add(conn, account: str, symbol: str, amount: float, every: str, day: int, start: str) -> int:
    if every not in EVERY:
        raise ValueError("every must be week, 2weeks or month")
    return conn.execute("INSERT INTO auto_invest (account, symbol, amount, every, day, start, active) VALUES (?, ?, ?, ?, ?, ?, 1)",
                        (account, symbol, amount, every, day, start)).lastrowid


def remove(conn, sid: int) -> None:
    conn.execute("DELETE FROM auto_invest WHERE id = ?", (sid,))


def close_on(symbol: str, d: date) -> float | None:
    """The closing price on a date (or the last one before it)."""
    from .providers import market
    days = (date.today() - d).days + 10
    bars = market.get_history(symbol, max(days, 15))
    before = [b for b in bars if b.date <= d.isoformat()]
    return before[-1].close if before else None
