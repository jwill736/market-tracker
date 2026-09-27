"""Tax-sheltered accounts: which of yours are, how much room is left this year, and what belongs where.

For most people the biggest free return isn't a stock: it's money that grows without tax. A Roth
IRA's growth comes out tax-free in retirement; a traditional IRA or 401(k) defers the tax and
lowers this year's bill; an HSA does both for medical costs. None of that shows up in a screen.

You tell the app what each account is (Robinhood, Coinbase and Stash are taxable unless you opened
an IRA there) and what you've put into each sheltered one this year. It then shows the room left
under this year's IRS limits, the deadline, and which holdings would be better off sheltered (the
ones paying more than 3% in dividends each year, taxed every year in a taxable account). It never
suggests selling to move something: that would realize the gains. Only where new money goes.

Limits are the IRS's published figures for the year; the Roth has an income limit and the
traditional IRA's deduction can phase out with a workplace plan. When those apply, it's a question
for an accountant.
"""

from __future__ import annotations

import json
from datetime import date

TYPES = {"taxable": "Taxable", "roth_ira": "Roth IRA", "trad_ira": "Traditional IRA", "401k": "401(k) / 403(b)", "hsa": "HSA"}
SHELTERED = {"roth_ira", "trad_ira", "401k", "hsa"}
# IRS limits by tax year (dollars): IRAs share one limit; catch-up from age 50 (HSA from 55).
LIMITS = {2026: {"ira": 7500, "ira_catchup": 1100, "401k": 24500, "401k_catchup": 8000, "hsa_self": 4400, "hsa_family": 8750, "hsa_catchup": 1000}}
HIGH_YIELD = 0.03


def settings(conn) -> dict:
    from . import db
    try:
        return json.loads(db.get_meta(conn, "account_tax", "{}") or "{}")
    except ValueError:
        return {}


def save(conn, account: str, kind: str, contributed: float | None, year: int) -> dict:
    from . import db
    if kind not in TYPES:
        raise ValueError(f"type: one of {', '.join(TYPES)}")
    st = settings(conn)
    accts = st.setdefault("accounts", {})
    cur = accts.get(account) or {}
    accts[account] = {"type": kind, "contributed": round(contributed if contributed is not None else
                                                          (cur.get("contributed", 0.0) if cur.get("year") == year else 0.0), 2), "year": year}
    db.set_meta(conn, "account_tax", json.dumps(st))
    return st


def save_person(conn, age50: bool, hsa_family: bool) -> dict:
    from . import db
    st = settings(conn)
    st["age50"], st["hsa_family"] = bool(age50), bool(hsa_family)
    db.set_meta(conn, "account_tax", json.dumps(st))
    return st


def limits_for(year: int) -> tuple[dict, int]:
    """This year's limits, or the latest published year's (said so) if this one isn't in the table."""
    y = year if year in LIMITS else max(LIMITS)
    return LIMITS[y], y


def view(st: dict, accounts: list[str], positions: list[dict], income_12m: dict[tuple[str, str], float], today: date) -> dict:
    """positions: [{account, symbol, value}]; income_12m: {(account, symbol): dividends in the last year}."""
    year = today.year
    lim, lim_year = limits_for(year)
    tags = st.get("accounts") or {}
    rows = []
    for a in accounts:
        t = tags.get(a) or {}
        rows.append({"account": a, "type": t.get("type"), "label": TYPES.get(t.get("type"), "Not set"),
                     "contributed": t.get("contributed", 0.0) if t.get("year") == year else 0.0})
    kinds = {r["type"] for r in rows}
    untagged = [r["account"] for r in rows if not r["type"]]
    age50 = bool(st.get("age50"))
    ira_cap = lim["ira"] + (lim["ira_catchup"] if age50 else 0)
    k_cap = lim["401k"] + (lim["401k_catchup"] if age50 else 0)
    hsa_cap = lim["hsa_family" if st.get("hsa_family") else "hsa_self"]
    put = lambda k: sum(r["contributed"] for r in rows if r["type"] in k)  # noqa: E731
    room = []
    ira_in = put({"roth_ira", "trad_ira"})
    room.append({"kind": "ira", "label": "IRAs (Roth and traditional together)", "limit": ira_cap, "contributed": ira_in,
                 "left": max(0.0, ira_cap - ira_in), "deadline": f"April 15, {year + 1}", "have": bool(kinds & {"roth_ira", "trad_ira"})})
    if "401k" in kinds:
        room.append({"kind": "401k", "label": "401(k) / 403(b), your own contributions", "limit": k_cap, "contributed": put({"401k"}),
                     "left": max(0.0, k_cap - put({"401k"})), "deadline": f"December 31, {year} (through payroll)", "have": True})
    if "hsa" in kinds:
        room.append({"kind": "hsa", "label": "HSA", "limit": hsa_cap, "contributed": put({"hsa"}), "left": max(0.0, hsa_cap - put({"hsa"})),
                     "deadline": f"April 15, {year + 1}", "have": True})
    # Holdings paying 3%+ a year sitting in a taxable account: their dividends are taxed every year.
    taxable = {r["account"] for r in rows if r["type"] == "taxable"}
    moves = []
    for p in positions:
        if p["account"] in taxable and p.get("value", 0) > 0:
            y = income_12m.get((p["account"], p["symbol"]), 0.0) / p["value"]
            if y >= HIGH_YIELD:
                moves.append({"symbol": p["symbol"], "account": p["account"], "yield_pct": round(y * 100, 1),
                              "text": f"{p['symbol']} pays about {y * 100:.1f}% a year, taxed every year in {p['account']}: buy more of it inside an IRA "
                                      "rather than here (don't sell to move it; that would realize the gain)."})
    ira = room[0]
    if untagged and len(untagged) == len(rows):
        text = "Tell Plumbline which of your accounts are tax-sheltered (below): for most people that matters more than any stock pick."
    elif untagged and not kinds & SHELTERED:
        text = f"Still to say what {', '.join(untagged)} {'is' if len(untagged) == 1 else 'are'}: then the app can tell whether you have any tax-sheltered room."
    elif not kinds & SHELTERED and not untagged:
        text = (f"None of your accounts is tax-sheltered. A Roth IRA lets up to ${ira_cap:,} a year ({lim_year} limit) grow and come out "
                "tax-free in retirement; if your income is under the Roth limit, that beats anything the screen can find. "
                "Robinhood, and most brokers, open one in minutes.")
    elif ira["have"] and ira["left"] > 0:
        text = f"${ira['left']:,.0f} of IRA room left for {year} (limit ${ira_cap:,}), until {ira['deadline']}."
    else:
        text = f"Your {year} IRA room is used." if ira["have"] else "Tagged. No IRA yet: a Roth IRA is the usual next step."
    if lim_year != year:
        text += f" (Using the {lim_year} limits: {year}'s aren't in the app yet.)"
    return {"year": year, "limits_year": lim_year, "types": TYPES, "accounts": rows, "untagged": untagged, "room": room,
            "location": moves, "age50": age50, "hsa_family": bool(st.get("hsa_family")), "text": text,
            "sheltered": bool(kinds & SHELTERED)}


def decision(v: dict) -> dict | None:
    """The one tax-shelter decision worth making now, or None."""
    ira = v["room"][0]
    if v["untagged"] and not v["sheltered"]:
        return {"kind": "tax_setup", "title": "Tell Plumbline which accounts are tax-sheltered", "why": [v["text"]], "amount": None}
    if not v["sheltered"] and not v["untagged"]:
        return {"kind": "shelter", "title": f"Open a Roth IRA: up to ${ira['limit']:,} a year grows tax-free", "why": [v["text"]], "amount": float(ira["limit"])}
    if ira["have"] and ira["left"] >= 100:
        return {"kind": "shelter", "title": f"Fund your IRA: ${ira['left']:,.0f} of {v['year']} room left",
                "why": [v["text"], "Money put in an IRA and invested in the index grows without yearly tax; the room doesn't carry over."],
                "amount": ira["left"]}
    return None
