"""Connection health: the quiet failures that turn into months of wrong numbers.

Checked every loop (every couple of minutes), pushed to your phone once per problem:

- a sync (Coinbase, Robinhood crypto, trade emails, SnapTrade) that has kept failing for three
  hours: an expired key, a changed password, a broker outage that didn't clear;
- an auto-invest buy the schedule recorded that no confirmation email or import has matched
  within five days (the broker may have skipped it, or changed the amount): only when the
  email connection is on, since that's what would confirm it;
- the off-site backup failing, or not having succeeded for three days.

Everything else (a sync that failed once and recovered) shows on the Accounts page but doesn't
push.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

FAIL_HOURS = 3
CONFIRM_DAYS = 5
BACKUP_DAYS = 3


def _ago(then: datetime, now: datetime) -> str:
    m = (now - then).total_seconds() / 60
    return f"{m / 1440:.0f} days" if m >= 2880 else f"{m / 60:.0f} hours" if m >= 120 else f"{max(1, m):.0f} minutes"


def status(now: datetime | None = None) -> list[dict]:
    """One line per connection or check: {key, name, ok, text, push, title, body}."""
    from . import accounts, db, email_trades, offsite, schedules
    now = now or datetime.now(timezone.utc)
    out = []
    for kind, (configured, _) in accounts.SYNCS.items():
        if not configured():
            continue
        name = accounts.LABELS[kind]
        last = accounts.last_sync(kind)
        if not last:
            out.append({"key": f"health:{kind}:new", "name": name, "ok": True, "text": f"{name}: connected, first sync pending",
                        "push": False})
            continue
        at = datetime.fromisoformat(last["at"])
        if last.get("ok"):
            out.append({"key": f"health:{kind}:ok", "name": name, "ok": True, "text": f"{name}: fine, last synced {_ago(at, now)} ago",
                        "push": False})
            continue
        since = datetime.fromisoformat(last.get("fail_since") or last["at"])
        long = now - since >= timedelta(hours=FAIL_HOURS)
        out.append({"key": f"health:{kind}:fail:{since.isoformat()}", "name": name, "ok": False, "push": long,
                    "text": f"{name}: failing for {_ago(since, now)}: {last.get('error')}",
                    "title": f"{name} sync has been failing for {_ago(since, now)}",
                    "body": f"{last.get('error')}. Until it's fixed, new trades there aren't coming in (Portfolio → Accounts)."})
    with db.connect() as conn:
        txs = db.list_transactions(conn)
        scheds = [s for s in schedules.load(conn) if s.active]
        backup = json.loads(db.get_meta(conn, "offsite_last", "null") or "null")
    if email_trades.configured():
        today = now.date()
        for s in scheds:
            confirmed = [date.fromisoformat(t["date"][:10]) for t in txs if t["symbol"] == s.symbol and t["side"] == "buy"
                         and (t.get("account") or "") == s.account and not (t.get("import_key") or "").startswith("sched:")]
            for t in txs:
                key = t.get("import_key") or ""
                if not key.startswith(f"sched:{s.id}:"):
                    continue
                d = date.fromisoformat(t["date"][:10])
                if (today - d).days < CONFIRM_DAYS or (today - d).days > 45 or any(abs((c - d).days) <= 3 for c in confirmed):
                    continue
                out.append({"key": f"health:sched:{s.id}:{d.isoformat()}", "name": "Auto-invest", "ok": False, "push": True,
                            "text": f"{s.account} {s.symbol} buy of {d.isoformat()}: no confirmation email or import yet",
                            "title": f"No confirmation for {s.account}'s {s.symbol} buy on {d.isoformat()}",
                            "body": f"The schedule recorded ${s.amount:,.0f} of {s.symbol}; no email or import has matched it in "
                                    f"{(today - d).days} days. Check {s.account}: it may have been skipped or changed."})
    if offsite.configured():
        if not backup:
            out.append({"key": "health:offsite:new", "name": "Off-site backup", "ok": True, "text": "Off-site backup: first copy tonight",
                        "push": False})
        else:
            at = datetime.fromisoformat(backup["at"])
            if backup.get("ok"):
                out.append({"key": "health:offsite:ok", "name": "Off-site backup", "ok": True,
                            "text": f"Off-site backup: fine, last copy {_ago(at, now)} ago", "push": False})
            else:
                out.append({"key": f"health:offsite:fail:{at.date().isoformat()}", "name": "Off-site backup", "ok": False, "push": True,
                            "text": f"Off-site backup failed: {backup.get('error')}",
                            "title": "The off-site backup failed", "body": f"{backup.get('error')} (Portfolio → Accounts → Off-site backup)."})
            if backup.get("ok") and now - at > timedelta(days=BACKUP_DAYS):
                out.append({"key": f"health:offsite:stale:{at.date().isoformat()}", "name": "Off-site backup", "ok": False, "push": True,
                            "text": f"Off-site backup: nothing for {_ago(at, now)}",
                            "title": f"No off-site backup for {_ago(at, now)}",
                            "body": "Is the app running overnight? (Portfolio → Accounts → Off-site backup)"})
    return out


def problems(now: datetime | None = None) -> list[dict]:
    return [s for s in status(now) if not s["ok"]]
