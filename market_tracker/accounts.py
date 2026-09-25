"""Your accounts: what's in each, how it reaches this app, and how fresh it is.

Robinhood, Stash and Coinbase each hold part of the portfolio; the ledger tags every trade with
its account and with where it came from (its import key):

    rh:     Robinhood account-activity CSV     cb:     Coinbase transaction CSV
    cbapi:  Coinbase read-only API sync        st:     SnapTrade sync (any broker)
    hl:     holdings typed in (Stash / other)  bk:     restored from a backup
    (none)  added by hand (Quick add / Trade → "It filled")

Syncs that can run without you (a Coinbase View-only key, a SnapTrade key) run in the
background: Coinbase every 6 hours, SnapTrade twice a day. Each result is remembered so the
Accounts card can say when an account was last brought up to date, and anything new raises a
heads-up.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from . import coinbase_sync, db, http, service, snaptrade

SOURCES = {"rh": "Robinhood CSV", "cb": "Coinbase CSV", "cbapi": "Coinbase API sync", "st": "SnapTrade sync",
           "hl": "Typed in", "bk": "Backup restore", "": "Added by hand"}
AUTO_EVERY = {"coinbase": timedelta(hours=6), "snaptrade": timedelta(hours=12)}
STALE_DAYS = 30


class SyncError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


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
        existing = db.list_transactions(conn)
        try:
            positions = service.pf.build_positions(existing + new)
        except ValueError as exc:
            raise SyncError(400, f"{exc}. Coins that arrived by transfer or reward need adding first (Quick add).")
        for t in new:
            db.add_transaction(conn, t["symbol"], t["side"], t["quantity"], t["price"], t["date"], t["fees"], t["note"],
                               import_key=t["import_key"], account="Coinbase")
    cb_qty: dict[str, float] = {}
    for t in existing + new:
        if (t.get("account") or "") == "Coinbase":
            cb_qty[t["symbol"]] = cb_qty.get(t["symbol"], 0.0) + (t["quantity"] if t["side"] == "buy" else -t["quantity"])
    return {"new": len(new), "duplicates": len(txs) - len(new), "differences": coinbase_sync.reconcile(bal, cb_qty),
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
            service.pf.build_positions(existing + new_tx)
        except ValueError as exc:
            raise SyncError(400, f"{exc}. Shares that arrived by transfer have no purchase in the broker's history: "
                                 "add them with Stash / other (their original cost), then sync again.")
        for t in sorted(new_tx, key=lambda t: t["date"]):
            db.add_transaction(conn, t["symbol"], t["side"], t["quantity"], t["price"], t["date"], t["fees"], t["note"],
                               import_key=t["import_key"], account=t["account"])
        db.add_income(conn, new_inc)
        if drip:
            old = set(json.loads(db.get_meta(conn, "drip_symbols", "[]") or "[]"))
            db.set_meta(conn, "drip_symbols", json.dumps(sorted(old | drip)))
        db.set_meta(conn, "snaptrade_last_sync", date.today().isoformat())
        ledger = db.list_transactions(conn)
    differences = []
    for a in per_account:
        qty: dict[str, float] = {}
        for t in ledger:
            if (t.get("account") or "") == a["name"]:
                qty[t["symbol"]] = qty.get(t["symbol"], 0.0) + (t["quantity"] if t["side"] == "buy" else -t["quantity"])
        differences += [dict(d, account=a["name"]) for d in snaptrade.reconcile(a["positions"], qty, a["institution"])]
    return {"accounts": [{"name": a["name"], "new": a["trades"]} for a in per_account], "new": len(new_tx), "duplicates": dup,
            "income_new": len(new_inc), "differences": differences, "skipped": skipped}


SYNCS = {"coinbase": (coinbase_sync.configured, sync_coinbase), "snaptrade": (snaptrade.configured, sync_snaptrade)}


def record(kind: str, result: dict | None, error: str | None, now: datetime) -> None:
    with db.connect() as conn:
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
            label = "Coinbase" if kind == "coinbase" else "SnapTrade"
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
        if a["name"] == "Coinbase" and coinbase_sync.configured():
            synced = {"how": "Coinbase API, automatic every 6 hours", "last": cb}
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
        return "Kept up to date automatically."
    age = (today - date.fromisoformat(a["last_trade"])).days if a["last_trade"] else None
    srcs = set(a["sources"])
    if a["name"] == "Robinhood" or "Robinhood CSV" in srcs:
        return ("Updated by importing Robinhood's account-activity CSV: trades after your last import aren't here until you "
                "import again (or connect it through SnapTrade).")
    if a["name"] == "Coinbase":
        return "From a Coinbase CSV. Add a View-only API key to .env and it syncs itself every 6 hours."
    if "Typed in" in srcs or a["name"] == "Stash":
        stale = f" Last change {age} days ago." if age is not None and age > STALE_DAYS else ""
        return ("Typed in by hand (Stash has no export or API). Auto-invest and dividend reinvesting change it without "
                "telling this app: update it after each statement." + stale)
    return "Added by hand."
