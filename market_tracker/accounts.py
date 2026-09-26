"""Your accounts: what's in each, how it reaches this app, and how fresh it is.

Robinhood, Stash and Coinbase each hold part of the portfolio; the ledger tags every trade with
its account and with where it came from (its import key):

    rh:     Robinhood account-activity CSV     cb:     Coinbase transaction CSV
    rhc:    Robinhood Crypto API               em:     a broker's trade-confirmation email
    sched:  your auto-invest schedule (Stash)
    cbapi:  Coinbase read-only API sync        st:     SnapTrade sync (any broker)
    hl:     holdings typed in (Stash / other)  bk:     restored from a backup
    (none)  added by hand (Quick add / Trade → "It filled")

Syncs that can run without you run in the background: Coinbase (API key) and Robinhood Crypto
(API key) every 5 minutes, broker trade-confirmation emails every 2 minutes (Robinhood stocks,
Stash), SnapTrade twice a day. Each result is remembered so the
Accounts card can say when an account was last brought up to date, and anything new raises a
heads-up.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from . import coinbase_sync, db, email_trades, http, robinhood_crypto, service, snaptrade

SOURCES = {"rh": "Robinhood CSV", "cb": "Coinbase CSV", "cbapi": "Coinbase API sync", "st": "SnapTrade sync",
           "rhc": "Robinhood Crypto API", "em": "Trade emails", "sched": "Auto-invest schedule",
           "hl": "Typed in", "bk": "Backup restore", "": "Added by hand"}
AUTO_EVERY = {"coinbase": timedelta(minutes=5), "robinhood_crypto": timedelta(minutes=5), "email": timedelta(minutes=2),
              "snaptrade": timedelta(hours=12)}
STALE_DAYS = 30


class SyncError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def account_quantities(ledger: list[dict], account: str) -> dict[str, float]:
    """What the ledger (moves included) says one account holds."""
    qty: dict[str, float] = {}
    for t in ledger:
        if (t.get("account") or "") == account:
            qty[t["symbol"]] = qty.get(t["symbol"], 0.0) + (t["quantity"] if t["side"] == "buy" else -t["quantity"])
    return qty


def balance_differences() -> dict[str, list[dict]]:
    """The latest balance check per account ({account: [{symbol, difference}]}), from the last syncs."""
    out: dict[str, list[dict]] = {}
    with db.connect() as conn:
        for kind in SYNCS:
            raw = db.get_meta(conn, f"sync_diff:{kind}", "")
            try:
                out.update(json.loads(raw) if raw else {})
            except ValueError:
                continue
    return out


def source_of(import_key: str | None) -> str:
    return (import_key or "").split(":", 1)[0] if import_key and ":" in import_key else ""


# ------------------------------------------------------------------ syncs

def sync_coinbase() -> dict:
    """Import new Coinbase fills (read-only key) and compare balances with the ledger."""
    if not coinbase_sync.configured():
        raise SyncError(400, "Add COINBASE_API_KEY_NAME and COINBASE_API_PRIVATE_KEY (a View-only key) to .env first.")
    try:
        txs = coinbase_sync.fills_to_transactions(coinbase_sync.fills())
        bal = coinbase_sync.balances()
    except (http.DataUnavailable, ValueError) as exc:
        raise SyncError(502, f"Coinbase: {exc}")
    with db.connect() as conn:
        known = db.import_keys(conn)
        new = [t for t in txs if t["import_key"] not in known]
        try:
            positions = service.pf.build_positions(db.ledger(conn) + new)
        except ValueError as exc:
            raise SyncError(400, f"{exc}. Coins that arrived by transfer need pairing with where they came from, or their "
                                 "original cost (Portfolio → Transfers).")
        for t in new:
            db.add_transaction(conn, t["symbol"], t["side"], t["quantity"], t["price"], t["date"], t["fees"], t["note"],
                               import_key=t["import_key"], account="Coinbase")
        cb_qty = account_quantities(db.ledger(conn), "Coinbase")
        from . import cash
        cash.save(conn, "Coinbase", sum(bal.get(c, 0.0) for c in ("USD", "USDC")), None, date.today(), "Coinbase sync")
    diffs = coinbase_sync.reconcile(bal, cb_qty)
    return {"new": len(new), "duplicates": len(txs) - len(new), "differences": diffs,
            "by_account": {"Coinbase": [{"symbol": d["coin"] + "-USD", "difference": d["difference"]} for d in diffs]},
            "positions": sorted(p.symbol for p in positions.values() if p.quantity > 0 and p.symbol.endswith("-USD"))}


def sync_snaptrade() -> dict:
    """Trades, reinvested dividends, dividends and interest from every account connected through
    SnapTrade, then positions compared with the ledger."""
    if not snaptrade.configured():
        raise SyncError(400, "Add SNAPTRADE_CLIENT_ID and SNAPTRADE_CONSUMER_KEY (a personal SnapTrade key) to .env first.")
    try:
        got = snaptrade.fetch_all()
    except snaptrade.SnapTradeError as exc:
        raise SyncError(502, str(exc))
    if not got:
        return {"accounts": [], "new": 0, "duplicates": 0, "income_new": 0, "differences": [], "skipped": {},
                "note": "No broker connected yet: use Connect a broker first."}
    with db.connect() as conn:
        existing = db.list_transactions(conn)
        known = db.import_keys(conn)
        known_income = db.income_keys(conn)
        have_income = db.income(conn)
        new_tx, new_inc, skipped, drip, dup = [], [], {}, set(), 0
        per_account = []
        for a in got:
            acct = a["account"]
            name = snaptrade.account_name(acct)
            inst = acct.get("institution_name") or ""
            txs, inc, sk, dr = snaptrade.to_rows(a["activities"], name, inst)
            drip |= dr
            for k, n in sk.items():
                skipped[k] = skipped.get(k, 0) + n
            fresh, d = snaptrade.new_only(txs, known, existing, snaptrade.match)
            dup += d
            new_tx += fresh
            new_inc += snaptrade.new_only(inc, known_income, have_income, snaptrade.match_income)[0]
            per_account.append({"name": name, "institution": inst, "id": acct.get("id"), "trades": len(fresh),
                                "positions": (a["holdings"] or {}).get("positions") or []})
        try:
            service.pf.build_positions(db.ledger(conn) + new_tx)
        except ValueError as exc:
            raise SyncError(400, f"{exc}. Shares that arrived by transfer have no purchase in the broker's history: pair "
                                 "them with the account they came from, or enter their original cost (Portfolio → Transfers).")
        for t in sorted(new_tx, key=lambda t: t["date"]):
            db.add_transaction(conn, t["symbol"], t["side"], t["quantity"], t["price"], t["date"], t["fees"], t["note"],
                               import_key=t["import_key"], account=t["account"])
        db.add_income(conn, new_inc)
        if drip:
            old = set(json.loads(db.get_meta(conn, "drip_symbols", "[]") or "[]"))
            db.set_meta(conn, "drip_symbols", json.dumps(sorted(old | drip)))
        db.set_meta(conn, "snaptrade_last_sync", date.today().isoformat())
        ledger = db.ledger(conn)
    differences = []
    for a in per_account:
        differences += [dict(d, account=a["name"]) for d in
                        snaptrade.reconcile(a["positions"], account_quantities(ledger, a["name"]), a["institution"])]
    by_account: dict[str, list] = {}
    for d in differences:
        by_account.setdefault(d["account"], []).append({"symbol": d["symbol"], "difference": d["difference"]})
    return {"accounts": [{"name": a["name"], "new": a["trades"]} for a in per_account], "new": len(new_tx), "duplicates": dup,
            "income_new": len(new_inc), "differences": differences, "by_account": by_account, "skipped": skipped}


def _insert_new(txs: list[dict], label: str) -> tuple[list[dict], int]:
    """Add trades not already in the ledger (by key, or the same trade from a CSV or another sync)."""
    with db.connect() as conn:
        existing = db.list_transactions(conn)
        fresh, dup = snaptrade.new_only(txs, db.import_keys(conn), existing, snaptrade.match)
        try:
            service.pf.build_positions(db.ledger(conn) + fresh)
        except ValueError as exc:
            raise SyncError(400, f"{label}: {exc}. Add the missing earlier purchase (Stash / other), or pair a transfer "
                                 "(Portfolio → Transfers), then it syncs again.")
        for t in fresh:
            db.add_transaction(conn, t["symbol"], t["side"], t["quantity"], t["price"], t["date"], t.get("fees", 0.0),
                               t.get("note"), import_key=t["import_key"], account=t["account"])
    return fresh, dup


def sync_robinhood_crypto() -> dict:
    """Filled Robinhood crypto orders into the ledger; positions compared with Robinhood's."""
    if not robinhood_crypto.configured():
        raise SyncError(400, "Add ROBINHOOD_CRYPTO_API_KEY and ROBINHOOD_CRYPTO_PRIVATE_KEY to .env first.")
    try:
        txs = robinhood_crypto.orders_to_transactions(robinhood_crypto.orders())
        held = robinhood_crypto.holdings()
        try:
            buying_power = float(robinhood_crypto.account().get("buying_power") or 0)
        except (TypeError, ValueError, AttributeError):
            buying_power = None
    except robinhood_crypto.RobinhoodError as exc:
        raise SyncError(502, str(exc))
    fresh, dup = _insert_new(sorted(txs, key=lambda t: t["date"]), "Robinhood Crypto")
    with db.connect() as conn:
        qty = {s: q for s, q in account_quantities(db.ledger(conn), robinhood_crypto.ACCOUNT).items() if s.endswith("-USD")}
        if buying_power is not None:
            from . import cash
            if (cash.load(conn).get(robinhood_crypto.ACCOUNT) or {}).get("source", "Robinhood crypto API") == "Robinhood crypto API":
                cash.save(conn, robinhood_crypto.ACCOUNT, buying_power, None, date.today(), "Robinhood crypto API")
    diffs = [{"symbol": s, "broker": round(held.get(s, 0.0), 8), "ledger": round(qty.get(s, 0.0), 8),
              "difference": round(held.get(s, 0.0) - qty.get(s, 0.0), 8)}
             for s in sorted(set(held) | {k for k, v in qty.items() if abs(v) > 1e-9})
             if abs(held.get(s, 0.0) - qty.get(s, 0.0)) > 1e-6 * max(1.0, held.get(s, 0.0))]
    return {"new": len(fresh), "duplicates": dup, "differences": diffs,
            "by_account": {robinhood_crypto.ACCOUNT: [{"symbol": d["symbol"], "difference": d["difference"]} for d in diffs]}}


def sync_email() -> dict:
    """Trades from broker confirmation emails (Robinhood stocks, Stash, Coinbase)."""
    if not email_trades.configured():
        raise SyncError(400, "Add MAIL_USER and MAIL_APP_PASSWORD (an app password) to .env first.")
    last = last_sync("email")
    since = date.today() - timedelta(days=email_trades.LOOKBACK_DAYS)
    if last and last.get("ok"):
        since = max(since, datetime.fromisoformat(last["at"]).date() - timedelta(days=2))
    try:
        parsed = email_trades.read(since)
    except Exception as exc:  # noqa: BLE001 - IMAP errors come in many shapes; report, don't crash the loop
        raise SyncError(502, f"Email: {str(exc)[:200]}")
    fresh, dup = _insert_new(parsed.trades, "Trade emails")
    with db.connect() as conn:
        old = json.loads(db.get_meta(conn, "email_unread", "[]") or "[]")
        seen = {(u["subject"], u["date"]) for u in old}
        merged = (parsed.unread + [u for u in old if (u["subject"], u["date"]) not in {(x["subject"], x["date"]) for x in parsed.unread}])
        db.set_meta(conn, "email_unread", json.dumps(merged[:30]))
    return {"new": len(fresh), "duplicates": dup, "differences": [], "unread": len([u for u in parsed.unread if (u["subject"], u["date"]) not in seen])}


SYNCS = {"coinbase": (coinbase_sync.configured, sync_coinbase), "robinhood_crypto": (robinhood_crypto.configured, sync_robinhood_crypto),
         "email": (email_trades.configured, sync_email), "snaptrade": (snaptrade.configured, sync_snaptrade)}
LABELS = {"coinbase": "Coinbase", "robinhood_crypto": "Robinhood Crypto", "email": "Trade emails", "snaptrade": "SnapTrade"}


def record(kind: str, result: dict | None, error: str | None, now: datetime) -> None:
    with db.connect() as conn:
        if result is not None and "by_account" in result:
            db.set_meta(conn, f"sync_diff:{kind}", json.dumps(result["by_account"]))
        db.set_meta(conn, f"sync:{kind}", json.dumps({"at": now.isoformat(timespec="seconds"), "ok": error is None,
                                                      "new": (result or {}).get("new", 0),
                                                      "income_new": (result or {}).get("income_new", 0),
                                                      "differences": len((result or {}).get("differences") or []),
                                                      "error": error}))


def last_sync(kind: str) -> dict | None:
    with db.connect() as conn:
        raw = db.get_meta(conn, f"sync:{kind}", "")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def run_sync(kind: str, now: datetime | None = None) -> dict:
    """Run one sync and remember how it went (the endpoints and the background loop both use this)."""
    now = now or datetime.now(timezone.utc)
    try:
        result = SYNCS[kind][1]()
    except SyncError as exc:
        record(kind, None, str(exc), now)
        raise
    record(kind, result, None, now)
    return result


def auto_sync(now: datetime | None = None, raise_headsup=None) -> list[str]:
    """Run the syncs that are set up and due. Returns the ones that ran."""
    now = now or datetime.now(timezone.utc)
    ran = []
    for kind, (configured, _) in SYNCS.items():
        if not configured():
            continue
        last = last_sync(kind)
        if last and now - datetime.fromisoformat(last["at"]) < AUTO_EVERY[kind]:
            continue
        ran.append(kind)
        try:
            r = run_sync(kind, now)
        except SyncError:
            continue
        if raise_headsup and (r.get("new") or r.get("income_new")):
            label = LABELS[kind]
            raise_headsup(f"sync:{kind}:{now.date().isoformat()}:{r.get('new')}:{r.get('income_new')}", "sync", 1,
                          f"{label} sync: {r.get('new', 0)} new trades"
                          + (f", {r['income_new']} payments" if r.get("income_new") else ""),
                          "Brought in automatically; the hold plan and taxes now include them.", "", "")
    return ran


# ------------------------------------------------------------------ the Accounts card

def overview(transactions: list[dict], income_rows: list[dict], today: date | None = None) -> list[dict]:
    """Per account: positions (shares and cost), where its data came from, and how fresh it is."""
    today = today or date.today()
    accts: dict[str, dict] = {}
    for t in transactions:
        name = t.get("account") or "Unlabeled"
        a = accts.setdefault(name, {"name": name, "positions": {}, "sources": {}, "last_trade": "", "trades": 0})
        pos = a["positions"].setdefault(t["symbol"], {"quantity": 0.0, "cost": 0.0})
        if t["side"] == "buy":
            pos["cost"] += t["quantity"] * t["price"] + (t.get("fees") or 0)
            pos["quantity"] += t["quantity"]
        else:
            if pos["quantity"] > 0:
                pos["cost"] *= max(0.0, 1 - t["quantity"] / pos["quantity"])
            pos["quantity"] -= t["quantity"]
        src = SOURCES.get(source_of(t.get("import_key")), "Other")
        a["sources"][src] = a["sources"].get(src, 0) + 1
        a["last_trade"] = max(a["last_trade"], t["date"][:10])
        a["trades"] += 1
    for r in income_rows:
        name = r.get("account") or "Unlabeled"
        if name in accts:
            accts[name]["income_12m"] = accts[name].get("income_12m", 0.0) + (
                r["amount"] if r["day"] >= (today - timedelta(days=365)).isoformat() else 0.0)
    cb, st = last_sync("coinbase"), last_sync("snaptrade")
    out = []
    for a in accts.values():
        a["positions"] = [{"symbol": s, "quantity": round(p["quantity"], 8), "cost": round(p["cost"], 2)}
                          for s, p in sorted(a["positions"].items()) if p["quantity"] > 1e-9]
        a["income_12m"] = round(a.get("income_12m", 0.0), 2)
        synced = None
        mail = email_trades.configured()
        if a["name"] == "Coinbase" and coinbase_sync.configured():
            synced = {"how": "Coinbase API, every 5 minutes", "last": cb}
        elif a["name"] == "Robinhood" and (mail or robinhood_crypto.configured()):
            parts = (["stocks from trade emails every 2 minutes"] if mail else []) + \
                    (["crypto from the Robinhood Crypto API every 5 minutes"] if robinhood_crypto.configured() else [])
            synced = {"how": "Automatic: " + ", ".join(parts), "last": last_sync("email") if mail else last_sync("robinhood_crypto"),
                      "partial": not mail}
        elif a["name"] == "Stash" and mail:
            synced = {"how": "Trade emails every 2 minutes, plus your auto-invest schedule", "last": last_sync("email")}
        elif "SnapTrade sync" in a["sources"] and snaptrade.configured():
            synced = {"how": "SnapTrade, automatic twice a day", "last": st}
        a["auto"] = synced
        a["advice"] = advice(a, today)
        out.append(a)
    return sorted(out, key=lambda a: -len(a["positions"]))


def advice(a: dict, today: date) -> str:
    if a["auto"]:
        last = a["auto"]["last"]
        if last and not last["ok"]:
            return f"The last automatic sync failed: {last['error']}"
        if a["auto"].get("partial"):
            return ("Crypto syncs automatically; stock trades still need the CSV, or connect your email "
                    "(Portfolio → Accounts) so confirmations bring them in.")
        return "Kept up to date automatically."
    age = (today - date.fromisoformat(a["last_trade"])).days if a["last_trade"] else None
    srcs = set(a["sources"])
    if a["name"] == "Robinhood" or "Robinhood CSV" in srcs:
        return ("New trades aren't here until you import the CSV again. Connect your email (Portfolio → Accounts) and "
                "each trade's confirmation brings it in within minutes.")
    if a["name"] == "Coinbase":
        return "From a CSV. Connect Coinbase (Portfolio → Accounts, free View key) and it syncs every 5 minutes."
    if "Typed in" in srcs or a["name"] == "Stash":
        stale = f" Last change {age} days ago." if age is not None and age > STALE_DAYS else ""
        return ("Typed in by hand (Stash has no export or API). Connect your email so Stash's confirmations come in, or add your "
                "auto-invest schedule; check it against each statement." + stale)
    return "Added by hand."


def stale_notes(overview_rows: list[dict], today: date | None = None, days: int = STALE_DAYS) -> list[dict]:
    """Accounts whose data has gone stale: nothing automatic keeps them current and nothing new
    has come in for `days` days. Shown in the morning brief with where to fix it."""
    today = today or date.today()
    out = []
    for a in overview_rows:
        if a.get("auto") and not (a["auto"].get("partial") and a["name"] == "Robinhood"):
            last = a["auto"].get("last")
            if last and not last.get("ok"):
                out.append({"account": a["name"], "text": f"{a['name']}: the automatic sync is failing ({last.get('error')})"})
            continue
        if not a.get("last_trade") or not a.get("positions"):
            continue
        age = (today - date.fromisoformat(a["last_trade"])).days
        if age < days:
            continue
        how = ("import the Robinhood CSV again, or connect your email" if a["name"] == "Robinhood" else
               "check it against your latest statement, or connect your email / set its auto-invest schedule" if a["name"] == "Stash" else
               "update it")
        out.append({"account": a["name"], "text": f"{a['name']} hasn't changed in {age} days: {how} (Portfolio → Accounts)"})
    return out
