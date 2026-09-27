"""Going live safely: don't act on numbers that aren't checked, and don't score the first messy weeks.

Trust gate. Every money decision (sell, trim, harvest, switch, idle cash, automate, an optional
idea) rests on the ledger being right. Until it's checked against the brokers (confidence.py:
broker syncs and statements), Decisions shows only what fixes the data:
- more than 2% of the portfolio's value in holdings whose share count differs from a broker
  or statement, or
- less than half of the value checked against anything outside the ledger, or
- shares that arrived without a cost (a transfer in from another broker, a stock reward): until
  their original cost is entered, taxes, harvests and sale estimates would be wrong.
Tax-account, connection and data-freshness items still show; they don't depend on the numbers.

Settling in. The first imports are usually messy (a missing transfer, a double-counted buy), so for
14 days after the app first has holdings, decisions are shown but not pushed (connection and
data problems still are), and nothing decided in that window counts toward Plumbline's record.
"""

from __future__ import annotations

from datetime import date, timedelta

MISMATCH_MAX = 0.02
CHECKED_MIN = 0.50
SETTLE_DAYS = 14
MONEY_KINDS = {"sell", "trim", "harvest", "switch", "invest_cash", "automate", "optional_idea", "hold"}
ALWAYS_PUSH = {"fix_sync", "fix_data"}


def trust(conf: dict | None) -> dict:
    """{trusted, text, mismatch_pct, checked_pct} from confidence.current()."""
    if not conf or not conf.get("holdings"):
        return {"trusted": False, "text": "No holdings yet: import your accounts (Accounts → Setup) before the app decides anything.",
                "mismatch_pct": None, "checked_pct": None}
    if conf.get("needs_cost"):
        rows = conf["needs_cost"]
        return {"trusted": False, "mismatch_pct": None, "checked_pct": None,
                "text": f"{len(rows)} holding{'s' if len(rows) != 1 else ''} arrived without a cost ("
                        + ", ".join(f"{r['quantity']:g} {r['symbol']} on {r['date']}" for r in rows[:3])
                        + "): taxes, harvests and sale estimates would be wrong. Enter what you originally paid under "
                          "Portfolio → Accounts → Transfers; money decisions wait until then."}
    total = conf["total_value"]
    if not total:
        return {"trusted": False, "mismatch_pct": None, "checked_pct": None,
                "text": "Couldn't get prices for your holdings just now, so the app can't check them against your brokers; "
                        "money decisions wait until prices are back."}
    mismatch = sum(h["value"] for h in conf["holdings"] if h["state"] == "mismatch")
    m_pct, c_pct = mismatch / total, conf["checked_value"] / total
    if m_pct > MISMATCH_MAX:
        worst = sorted((h for h in conf["holdings"] if h["state"] == "mismatch"), key=lambda h: -h["value"])[:3]
        return {"trusted": False, "mismatch_pct": round(m_pct * 100, 1), "checked_pct": round(c_pct * 100),
                "text": f"{m_pct:.0%} of your portfolio (${mismatch:,.0f}) doesn't match your broker: "
                        + ", ".join(f"{h['account']} {h['symbol']} ({h['detail']})" for h in worst)
                        + ". Money decisions wait until it does."}
    if c_pct < CHECKED_MIN:
        return {"trusted": False, "mismatch_pct": round(m_pct * 100, 1), "checked_pct": round(c_pct * 100),
                "text": f"Only {c_pct:.0%} of your portfolio's value has been checked against a broker or a statement. Upload a recent "
                        "statement for each account (Portfolio → Holdings → Check a statement) or connect an automatic check; "
                        "money decisions wait until at least half is checked."}
    return {"trusted": True, "mismatch_pct": round(m_pct * 100, 1), "checked_pct": round(c_pct * 100),
            "text": f"{c_pct:.0%} of your portfolio checked against your brokers, {m_pct:.1%} off."}


def gate(items: list[dict], tr: dict, today: date) -> list[dict]:
    """Money decisions out, one 'check your numbers' decision in, when the numbers aren't trusted."""
    if tr["trusted"]:
        return items
    from .decisions import _d
    kept = [d for d in items if d["kind"] not in MONEY_KINDS]
    held = len(items) - len(kept)
    kept.append(_d("fix_data", "Check your numbers before acting on them",
                   [tr["text"]] + ([f"{held} money decision{'s are' if held != 1 else ' is'} waiting on this."] if held else []),
                   "rule", page="accounts", key=f"fix_data:trust:{today.isocalendar()[0]}-{today.isocalendar()[1]}"))
    return sorted(kept, key=lambda d: (-d["priority"], -(d["amount"] or 0)))


def live_since(conn, has_holdings: bool, today: date) -> str | None:
    """The day the app first had holdings (recorded the first time it's asked with some)."""
    from . import db
    day = db.get_meta(conn, "live_since", "")
    if not day and has_holdings:
        day = today.isoformat()
        db.set_meta(conn, "live_since", day)
    return day or None


def settling(since: str | None, today: date) -> dict:
    if not since:
        return {"settling": True, "until": None, "text": "Not live yet: no holdings imported."}
    until = date.fromisoformat(since) + timedelta(days=SETTLE_DAYS)
    if today < until:
        return {"settling": True, "until": until.isoformat(), "since": since,
                "text": f"Settling in until {until:%b} {until.day}: decisions show but aren't pushed (connection and data problems still are), "
                        "and what you decide now doesn't count toward Plumbline's record. First imports are usually messy."}
    return {"settling": False, "until": until.isoformat(), "since": since, "text": ""}


def record_start(since: str | None) -> str | None:
    """Decisions made before this day don't count toward the record."""
    return (date.fromisoformat(since) + timedelta(days=SETTLE_DAYS)).isoformat() if since else None
