"""How much of the portfolio the app has actually checked, and what to fix.

Every number in the app is only as good as the ledger. This scores how much of your money (by
value) has been checked against something outside the ledger recently:

- a broker's own balance, from the last successful sync (Coinbase and Robinhood crypto every
  5 minutes, SnapTrade twice a day): a holding counts when that account's balance matched the
  ledger within the last day;
- a statement: share counts read from a statement PDF that matched the ledger in the last
  35 days (Portfolio → Holdings → Check a statement).

Everything else (Robinhood stocks from trade emails, Stash typed in or scheduled) is
"unchecked": probably right, not proven. The fix list is what stands between the score and
100%: balances that don't match, transfers waiting for a decision, emails the reader couldn't
read, syncs that are failing, accounts nothing has checked.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

SYNC_FRESH = timedelta(days=1)
STATEMENT_FRESH_DAYS = 35
# Which sync checks which accounts' balances, and which of their holdings it can see.
CHECKS = {"coinbase": ("Coinbase", lambda s: s.endswith("-USD")),
          "robinhood_crypto": ("Robinhood", lambda s: s.endswith("-USD")),
          "snaptrade": (None, lambda s: True)}        # None: every account the sync reported


def holdings_by_account(ledger: list[dict]) -> dict[tuple[str, str], float]:
    q: dict[tuple[str, str], float] = {}
    for t in ledger:
        k = (t.get("account") or "Unlabeled", t["symbol"])
        q[k] = q.get(k, 0.0) + (t["quantity"] if t["side"] == "buy" else -t["quantity"])
    return {k: v for k, v in q.items() if v > 1e-9}


def status(ledger: list[dict], prices: dict[str, float], syncs: dict[str, dict], diffs: dict[str, dict[str, list[dict]]],
           statements: dict[str, dict], now: datetime) -> dict:
    """syncs: {kind: last_sync record}; diffs: {kind: {account: [{symbol, difference}]}};
    statements: {account: {"day", "match": [symbols], "differences": [symbols]}}."""
    rows = []
    for (acct, sym), qty in sorted(holdings_by_account(ledger).items()):
        value = qty * prices.get(sym, 0.0)
        how, state, detail = "", "unchecked", ""
        for kind, (only, visible) in CHECKS.items():
            rec, by_acct = syncs.get(kind), (diffs.get(kind) or {})
            if not rec or not rec.get("ok") or now - datetime.fromisoformat(rec["at"]) > SYNC_FRESH:
                continue
            if (only and acct != only) or (not only and acct not in by_acct) or not visible(sym):
                continue
            bad = next((d for d in by_acct.get(acct, []) if d["symbol"] == sym), None)
            if bad:
                state, how, detail = "mismatch", kind, f"broker differs by {bad['difference']:+g}"
            else:
                state, how = "broker", kind
            break
        if state == "unchecked":
            st = statements.get(acct)
            if st and (now.date() - date.fromisoformat(st["day"])).days <= STATEMENT_FRESH_DAYS:
                if sym in st.get("differences", []):
                    state, how, detail = "mismatch", "statement", "statement differs"
                elif sym in st.get("match", []):
                    state, how = "statement", "statement"
        rows.append({"account": acct, "symbol": sym, "quantity": round(qty, 8), "value": round(value, 2), "state": state,
                     "how": how, "detail": detail})
    total = sum(r["value"] for r in rows)
    checked = sum(r["value"] for r in rows if r["state"] in ("broker", "statement"))
    by_acct: dict[str, dict] = {}
    for r in rows:
        a = by_acct.setdefault(r["account"], {"account": r["account"], "value": 0.0, "checked": 0.0, "mismatch": 0})
        a["value"] += r["value"]
        a["checked"] += r["value"] if r["state"] in ("broker", "statement") else 0.0
        a["mismatch"] += r["state"] == "mismatch"
    return {"score": round(checked / total * 100) if total else None, "checked_value": round(checked, 2), "total_value": round(total, 2),
            "holdings": rows,
            "accounts": [dict(a, value=round(a["value"], 2), checked=round(a["checked"], 2),
                              pct=round(a["checked"] / a["value"] * 100) if a["value"] else None)
                         for a in sorted(by_acct.values(), key=lambda a: -a["value"])]}


def fix_list(st: dict, open_transfers: list[dict], unread: list[dict], problems: list[dict],
             needs_cost: list[dict] | None = None) -> list[dict]:
    """What to fix, most important first: {level: 2 red / 1 amber, text, where}."""
    out = []
    for r in needs_cost or []:
        out.append({"level": 2, "text": f"{r['account'] or 'Unlabeled'} {r['symbol']}: {r['quantity']:g} shares arrived on {r['date']} "
                                        "without a cost (a transfer in or a stock reward). Enter what you originally paid and when.",
                    "where": "transfers"})
    for r in st["holdings"]:
        if r["state"] == "mismatch":
            out.append({"level": 2, "text": f"{r['account']} {r['symbol']}: {r['detail']} (ledger {r['quantity']:g})",
                        "where": "transfers" if r["how"] != "statement" else "statement"})
    for t in open_transfers:
        out.append({"level": 2, "text": t["ask"], "where": "transfers"})
    for p in problems:
        out.append({"level": 2, "text": p["text"], "where": "health"})
    if unread:
        out.append({"level": 1, "text": f"{len(unread)} broker email{'s' if len(unread) != 1 else ''} the reader couldn't understand: "
                                        "check those trades are in the ledger", "where": "connections"})
    for a in st["accounts"]:
        if a["pct"] is not None and a["pct"] < 50 and a["value"] >= 100:
            out.append({"level": 1, "text": f"{a['account']}: {100 - a['pct']}% of its value (${a['value'] - a['checked']:,.0f}) isn't checked "
                                            "against the broker. Upload a recent statement, or connect an automatic check.",
                        "where": "statement"})
    return out


def load_inputs(conn) -> tuple[dict, dict, dict]:
    """(syncs, diffs, statements) from the app's stored state."""
    from . import accounts, db
    syncs = {k: accounts.last_sync(k) for k in CHECKS}
    diffs = {}
    for k in CHECKS:
        try:
            diffs[k] = json.loads(db.get_meta(conn, f"sync_diff:{k}", "") or "{}")
        except ValueError:
            diffs[k] = {}
    try:
        statements = json.loads(db.get_meta(conn, "statement_checks", "{}") or "{}")
    except ValueError:
        statements = {}
    return syncs, diffs, statements


def remember_statement(conn, account: str, result: dict, today: date | None = None) -> None:
    from . import db
    try:
        all_ = json.loads(db.get_meta(conn, "statement_checks", "{}") or "{}")
    except ValueError:
        all_ = {}
    all_[account] = {"day": (today or date.today()).isoformat(), "match": [m["symbol"] for m in result.get("match", [])],
                     "differences": [d["symbol"] for d in result.get("differences", [])]
                     + [m["symbol"] for m in result.get("not_on_statement", [])]}
    db.set_meta(conn, "statement_checks", json.dumps(all_))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def current() -> dict:
    """The score and fix list from the app's stored state (the Home card, the weekly recap)."""
    import json

    from . import db, health, http, service, transfers
    with db.connect() as conn:
        led = db.ledger(conn)
        syncs, diffs, stmts = load_inputs(conn)
        legs = transfers.from_rows(db.transfer_legs(conn))
        unread = json.loads(db.get_meta(conn, "email_unread", "[]") or "[]")
        missing_cost = db.needs_cost(conn)
    prices = {}
    if led:
        try:
            prices = {p["symbol"]: p["price"] for p in service.portfolio_summary(led, False)["positions"] if p.get("price")}
        except (ValueError, http.DataUnavailable):
            prices = {}
    st = status(led, prices, syncs, diffs, stmts, now_utc())
    arrivals = [{"symbol": r["symbol"], "quantity": r["quantity"], "date": r["date"], "account": r["account"]}
                for r in transfers.pending_arrivals(legs)]
    return dict(st, fixes=fix_list(st, transfers.open_items(legs)["open"], unread, health.problems(), missing_cost),
                needs_cost=missing_cost + arrivals)
