"""The year's tax paperwork from every account in one place.

Sales: one row per lot sold, laid out like IRS Form 8949 (description, date acquired, date sold,
proceeds, cost basis, adjustment code and amount, gain or loss), split short-term (held a year
or less) and long-term. Wash-sale losses carry code W with the disallowed amount added back,
across accounts (a Robinhood loss washed by a Stash purchase: no single broker's 1099 shows
that). Moves between your own accounts aren't sales and don't appear.

Income: dividends, interest and foreign tax withheld from the Income tab, plus crypto rewards
and staking (ordinary income at their value on the day received, which is also their cost).

This is a worksheet to check each broker's 1099-B / 1099-DA / 1099-DIV against, not a filing.
Brokers report the cost of shares they can't see (moved in from elsewhere) as unknown; this
file has it, which is exactly where it helps.
"""

from __future__ import annotations

import csv
import io

from . import taxes

SALE_FIELDS = ["term", "description", "date_acquired", "date_sold", "proceeds", "cost_basis", "adjustment_code",
               "adjustment", "gain_or_loss", "account", "asset"]
INCOME_FIELDS = ["date", "kind", "symbol", "amount", "account", "note"]
REWARD_WORDS = ("rewards income", "staking income", "learning reward", "coinbase earn", "inflation reward", "reward", "staking")


def _desc(sym: str, qty: float) -> str:
    q = f"{qty:,.8f}".rstrip("0").rstrip(".")
    return f"{q} {sym.removesuffix('-USD')}" if sym.endswith("-USD") else f"{q} sh {sym}"


def sales_rows(ledger: list[dict], year: int) -> list[dict]:
    _, sales = taxes.lots_and_sales(ledger)
    washes = [w for w in taxes.wash_sales(ledger) if w.sold.startswith(str(year))]
    rows = []
    for s in sales:
        if not s.date.startswith(str(year)):
            continue
        rows.append({"term": "long" if s.long_term else "short", "description": _desc(s.symbol, s.quantity),
                     "date_acquired": s.bought, "date_sold": s.date, "proceeds": round(s.proceeds, 2),
                     "cost_basis": round(s.cost, 2), "adjustment_code": "", "adjustment": 0.0,
                     "gain_or_loss": round(s.gain, 2), "account": s.account, "asset": "crypto" if taxes.is_crypto(s.symbol) else "security"})
    # Spread each wash sale's disallowed loss over that sale's losing lots, in proportion to their loss.
    for w in washes:
        hit = [r for r in rows if r["date_sold"] == w.sold and r["account"] == w.sold_account and r["gain_or_loss"] < 0
               and r["description"].endswith(" " + w.symbol)]
        total = -sum(r["gain_or_loss"] for r in hit)
        for r in hit:
            add = round(w.disallowed * (-r["gain_or_loss"] / total), 2) if total else 0.0
            r["adjustment_code"] = "W"
            r["adjustment"] = round(r["adjustment"] + add, 2)
            r["gain_or_loss"] = round(r["gain_or_loss"] + add, 2)
    return sorted(rows, key=lambda r: (r["term"] != "short", r["date_sold"], r["description"]))


def income_rows(income: list[dict], ledger: list[dict], year: int) -> list[dict]:
    out = [{"date": r["day"], "kind": r["kind"], "symbol": r.get("symbol") or "", "amount": round(r["amount"], 2),
            "account": r.get("account") or "", "note": r.get("note") or ""}
           for r in income if r["day"].startswith(str(year)) and r["kind"] in ("dividend", "interest", "tax_withheld")]
    for t in ledger:
        note = (t.get("note") or "").lower()
        if t["side"] != "buy" or t.get("transfer") is not None or not t["date"].startswith(str(year)):
            continue
        if ":" in note and any(w in note.split(":", 1)[1] for w in REWARD_WORDS):
            out.append({"date": t["date"][:10], "kind": "crypto_reward", "symbol": t["symbol"],
                        "amount": round(t["quantity"] * t["price"], 2), "account": t.get("account") or "",
                        "note": t.get("note") or ""})
    return sorted(out, key=lambda r: (r["date"], r["kind"]))


def summary(sales: list[dict], income: list[dict]) -> dict:
    def tot(term, key):
        return round(sum(r[key] for r in sales if r["term"] == term), 2)
    by_kind: dict[str, float] = {}
    for r in income:
        by_kind[r["kind"]] = round(by_kind.get(r["kind"], 0.0) + r["amount"], 2)
    return {"short": {"proceeds": tot("short", "proceeds"), "cost": tot("short", "cost_basis"),
                      "adjustment": tot("short", "adjustment"), "gain": tot("short", "gain_or_loss")},
            "long": {"proceeds": tot("long", "proceeds"), "cost": tot("long", "cost_basis"),
                     "adjustment": tot("long", "adjustment"), "gain": tot("long", "gain_or_loss")},
            "sales": len(sales), "wash_rows": sum(1 for r in sales if r["adjustment_code"] == "W"), "income": by_kind}


def to_csv(rows: list[dict], fields: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()
