"""Cooling-off: a sell your own rules don't call for waits 48 hours, with the reason written down.

For a buy-and-hold investor the costliest trade is the one made in a hurry: selling after a bad
day, a scary headline or a hot tip, then missing the recovery (Barber & Odean; Dalbar's
investor-behavior studies put the gap between investors' returns and their funds' at a point or
more a year). So when you preview a sell that no rule backs (not a hold-plan Sell? or Trim, not an
open decision to sell, trim, switch or harvest), the ticket asks why and starts a 48-hour clock.
After it runs, the sell goes through with your reason shown back to you. "Sell anyway" skips the
wait and is logged as an override, so the record shows how often you overrode yourself and how
those sells did.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

HOURS = 48
BACKED_VERDICTS = ("Sell?", "Trim")
BACKED_KINDS = ("sell", "trim", "switch", "harvest")


def backed(symbol: str, plan: dict | None, open_decisions: list[dict]) -> str | None:
    """Why this sell is rule-backed, or None."""
    row = next((h for h in (plan or {}).get("holdings") or [] if h["symbol"] == symbol), None)
    if row and row.get("verdict") in BACKED_VERDICTS:
        return f"Your hold plan says {row['verdict']}"
    if any(h["symbol"] == symbol for h in ((plan or {}).get("tax") or {}).get("harvest") or []):
        return "A tax-loss harvest in your plan"
    d = next((d for d in open_decisions if d["symbol"] == symbol and d["kind"] in BACKED_KINDS), None)
    return f"Plumbline's decision: {d['title']}" if d else None


def load(conn) -> dict:
    from . import db
    try:
        return json.loads(db.get_meta(conn, "cooloff", "{}") or "{}")
    except ValueError:
        return {}


def start(conn, symbol: str, reason: str, now: datetime, override: bool = False) -> dict:
    """Write the reason down and start the clock (or, with override, end it now and log that)."""
    from . import db
    st = load(conn)
    started = now - timedelta(hours=HOURS) if override else now
    st[symbol] = {"reason": reason.strip()[:300], "started": started.isoformat(timespec="seconds"), "override": override}
    db.set_meta(conn, "cooloff", json.dumps(st))
    log = json.loads(db.get_meta(conn, "cooloff_log", "[]") or "[]")
    log.append({"symbol": symbol, "reason": reason.strip()[:300], "at": now.isoformat(timespec="seconds"), "override": override})
    db.set_meta(conn, "cooloff_log", json.dumps(log[-200:]))
    return st[symbol]


def check(entry: dict | None, now: datetime) -> dict:
    """{"state": "ask" | "waiting" | "done", "until", "reason", ...} for a sell that no rule backs."""
    if not entry:
        return {"state": "ask", "hours": HOURS}
    started = datetime.fromisoformat(entry["started"])
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    until = started + timedelta(hours=HOURS)
    if now < until:
        from .weekly import ET
        et = until.astimezone(ET)
        return {"state": "waiting", "hours": HOURS, "until": until.isoformat(timespec="minutes"), "reason": entry["reason"],
                "until_text": f"{et:%a %b} {et.day}, {et.hour % 12 or 12}:{et:%M}{'am' if et.hour < 12 else 'pm'} ET",
                "left_hours": round((until - now).total_seconds() / 3600, 1)}
    if now > until + timedelta(days=14):                  # a reason from weeks ago doesn't cover today's sell
        return {"state": "ask", "hours": HOURS, "expired": True}
    return {"state": "done", "reason": entry["reason"], "override": entry.get("override", False)}


def gate(symbol: str, plan: dict | None, open_decisions: list[dict], entry: dict | None, now: datetime) -> tuple[list[str], list[str], dict | None]:
    """(warnings, blockers, cooling) to add to a sell's preview."""
    why = backed(symbol, plan, open_decisions)
    if why:
        return [f"Backed by a rule: {why}."], [], None
    c = check(entry, now)
    if c["state"] == "ask":
        return [], [f"No rule of yours calls for selling {symbol}: write down why, and it can go through in {HOURS} hours (cooling-off)."], c
    if c["state"] == "waiting":
        return [], [f"Cooling off until {c['until_text']} ({c['left_hours']:g} hours left). Your reason: “{c['reason']}”"], c
    return [f"Your reason, {'written when you overrode the wait' if c['override'] else 'written 48+ hours ago'}: “{c['reason']}”. Still true?"], [], None
