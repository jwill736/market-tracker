"""Moves between your own accounts: coins sent from Coinbase to Robinhood, shares moved from one
broker to another, crypto withdrawn to your own wallet.

A move isn't a sale. The coins keep what you paid for them and the date you bought them, so a
later sale is taxed on the original cost and counts as long-term if the first purchase was more
than a year earlier. Without this the app would either lose track of them (the sending account
still "holds" them, the receiving one sells coins it never bought) or treat them as bought again
on the day they arrived.

Each side of a move is a leg: "out" from one account, "in" to another. Legs come from Coinbase's
transaction CSV (Send / Receive), from the balance checks after each sync (an account holding
less than the ledger says next to one holding more of the same coin), or from the form. Legs
are paired when the symbol matches, the accounts differ, the "in" lands within a few days of
the "out", and the amount received is the amount sent less a network fee of at most 3%. The fee
coins are folded into the cost of what arrived (the common treatment; not tax advice).

A leg with no partner needs a decision: an "in" from outside the tracked accounts needs its
original cost and purchase date; an "out" either went to a wallet of yours (track it as an
account) or was spent (record it as a sale).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

MAX_DAYS = 5            # an "in" arrives within this many days of its "out"
MAX_FEE = 0.03          # network fee: at most 3% of the amount sent
SYNTH = 10 ** 12        # beyond any real row id: orders the rows a move adds within a day


@dataclass
class Leg:
    id: int
    symbol: str
    direction: str          # "out" | "in"
    quantity: float
    day: str
    account: str
    pair: int | None = None
    resolved: str = ""      # "", "paired", "bought" (cost given), "sold" (spent), "wallet" (moved to own wallet)
    import_key: str | None = None
    note: str = ""


def from_rows(rows: list[dict]) -> list[Leg]:
    return [Leg(r["id"], r["symbol"], r["direction"], float(r["quantity"]), r["day"], r["account"], r.get("pair"),
                r.get("resolved") or "", r.get("import_key"), r.get("note") or "") for r in rows]


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def fits(out: Leg, inn: Leg) -> bool:
    if out.symbol != inn.symbol or out.account == inn.account:
        return False
    if not -1 <= _days(out.day, inn.day) <= MAX_DAYS:     # a day early: time zones between brokers
        return False
    return out.quantity * (1 - MAX_FEE) - 1e-12 <= inn.quantity <= out.quantity * (1 + 1e-9)


def match(legs: list[Leg]) -> list[tuple[int, int]]:
    """Pairs (out id, in id) among legs not yet paired or resolved: closest in time, then in amount."""
    outs = sorted((lg for lg in legs if lg.direction == "out" and lg.pair is None and not lg.resolved), key=lambda lg: lg.day)
    ins = [lg for lg in legs if lg.direction == "in" and lg.pair is None and not lg.resolved]
    used: set[int] = set()
    pairs = []
    for o in outs:
        cands = [i for i in ins if i.id not in used and fits(o, i)]
        if not cands:
            continue
        best = min(cands, key=lambda i: (abs(_days(o.day, i.day)), o.quantity - i.quantity))
        used.add(best.id)
        pairs.append((o.id, best.id))
    return pairs


def moves(legs: list[Leg]) -> list[dict]:
    """Paired legs as moves: {id, symbol, sent_on, arrived_on, from, to, sent, received}."""
    by_id = {lg.id: lg for lg in legs}
    out = []
    for lg in legs:
        if lg.direction != "out" or lg.pair is None or lg.pair not in by_id:
            continue
        inn = by_id[lg.pair]
        out.append({"id": lg.id, "symbol": lg.symbol, "sent_on": lg.day[:10], "arrived_on": max(lg.day[:10], inn.day[:10]),
                    "from": lg.account, "to": inn.account, "sent": lg.quantity, "received": inn.quantity})
    return sorted(out, key=lambda m: (m["sent_on"], m["id"]))


def with_moves(transactions: list[dict], mv: list[dict]) -> list[dict]:
    """The ledger plus two rows per move (flagged "transfer") that take the coins out of one
    account and put them into the other. Code that sums quantities per account sees the move;
    lot and cost code (taxes.lots_and_sales, portfolio.build_positions) carries the original
    lots across instead of treating them as a sale and a purchase. The "in" row's price is the
    carried cost per unit."""
    if not mv:
        return transactions
    rows = list(transactions)
    for k, m in enumerate(mv):
        # Same-day order: the coins leave after that day's buys in the sending account and arrive
        # before that day's sales in the receiving one (ids order rows within a date).
        buys = [float(t.get("id") or 0) for t in transactions if t["side"] == "buy" and t["date"][:10] == m["sent_on"]
                and (t.get("account") or "") == m["from"] and t["symbol"].upper() == m["symbol"]]
        sells = [float(t.get("id") or 0) for t in transactions if t["side"] == "sell" and t["date"][:10] == m["arrived_on"]
                 and (t.get("account") or "") == m["to"] and t["symbol"].upper() == m["symbol"]]
        out_id = (max(buys) + 0.5 if buys else -SYNTH) + k * 1e-6
        if m["arrived_on"] == m["sent_on"]:
            in_id = out_id + 1e-7
        else:
            in_id = (min(sells) - 0.5 if sells else -SYNTH) + k * 1e-6
        rows.append({"id": out_id, "symbol": m["symbol"], "side": "sell", "quantity": m["sent"], "price": 0.0,
                     "fees": 0.0, "date": m["sent_on"], "account": m["from"], "transfer": m["id"], "import_key": None,
                     "note": f"Moved to {m['to']}"})
        rows.append({"id": in_id, "symbol": m["symbol"], "side": "buy", "quantity": m["received"], "price": 0.0, "fees": 0.0,
                     "date": m["arrived_on"], "account": m["to"], "transfer": m["id"], "import_key": None,
                     "note": f"Moved from {m['from']}"})
    from . import taxes
    carried: dict[int, float] = {}
    taxes.lots_and_sales(rows, carried=carried)
    for r in rows:
        if r.get("transfer") is not None and r["side"] == "buy":
            r["price"] = round(carried.get(r["transfer"], 0.0) / r["quantity"], 8) if r["quantity"] else 0.0
    return rows


def pending_arrivals(legs: list[Leg]) -> list[dict]:
    """Coins or shares that arrived from outside the tracked accounts and have no decision yet: they're
    held (so a later sale of them isn't "selling what you never had"), at no cost until you pair them
    or enter what you paid. Rows are flagged as moves, so they aren't counted as purchases."""
    out = []
    for lg in legs:
        if lg.direction != "in" or lg.pair is not None or lg.resolved:
            continue
        out.append({"id": -SYNTH + lg.id * 1e-6, "symbol": lg.symbol, "side": "buy", "quantity": lg.quantity, "price": 0.0,
                    "fees": 0.0, "date": lg.day[:10], "account": lg.account, "transfer": f"pending:{lg.id}", "import_key": None,
                    "note": "Arrived from outside: original cost needed"})
    return out


def real(transactions: list[dict]) -> list[dict]:
    """The ledger without move rows (purchases and sales you actually made)."""
    return [t for t in transactions if t.get("transfer") is None]


def suggest_from_differences(diffs: dict[str, list[dict]], legs: list[Leg], today: str) -> list[dict]:
    """Balance checks after syncs, per account: [{symbol, broker, ledger, difference}]. An account
    holding less than the ledger says (coins left) next to one holding more (coins arrived) looks
    like a move between them; suggest it, unless a leg already explains it."""
    lows = [(a, d) for a, ds in diffs.items() for d in ds if d.get("difference", 0) < -1e-9]
    highs = [(a, d) for a, ds in diffs.items() for d in ds if d.get("difference", 0) > 1e-9]
    open_legs = {(lg.symbol, lg.account) for lg in legs if not lg.resolved}
    out = []
    for a, lo in lows:
        sent = -lo["difference"]
        for b, hi in highs:
            if hi["symbol"] != lo["symbol"] or b == a or (lo["symbol"], a) in open_legs:
                continue
            got = hi["difference"]
            if sent * (1 - MAX_FEE) - 1e-12 <= got <= sent * (1 + 1e-9):
                out.append({"symbol": lo["symbol"], "from": a, "to": b, "sent": round(sent, 8), "received": round(got, 8),
                            "day": today})
    return out


def open_items(legs: list[Leg]) -> dict:
    """Legs still waiting for a decision, with what each needs."""
    need = []
    for lg in legs:
        if lg.pair is not None or lg.resolved:
            continue
        if lg.direction == "in":
            ask = (f"{lg.quantity:g} {lg.symbol} arrived in {lg.account} on {lg.day[:10]} from outside the accounts here. "
                   "Enter what you originally paid and when, so a later sale is taxed right.")
        else:
            ask = (f"{lg.quantity:g} {lg.symbol} left {lg.account} on {lg.day[:10]}. Moved to a wallet of yours, or spent / "
                   "sold elsewhere?")
        need.append(dict(asdict(lg), ask=ask))
    return {"open": need}
