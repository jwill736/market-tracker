"""Which pages you actually use, counted on this app only (never sent anywhere).

Every page you don't open still costs attention: it adds a tab to scan and a warning to half-read.
After the app has been in use for 30 days, pages opened on fewer than two days in the last 30
are suggested for hiding. Hiding only takes the page off the menu; everything keeps running and
it can be turned back on in one tap.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

CORE = {"home", "decisions", "hold", "portfolio", "accounts"}      # never suggested for hiding
WINDOW = 30
KEEP_DAYS = 60


def _load(conn) -> dict:
    from . import db
    try:
        return json.loads(db.get_meta(conn, "usage", "{}") or "{}")
    except ValueError:
        return {}


def record(conn, page: str, today: date) -> None:
    from . import db
    u = _load(conn)
    u.setdefault("_since", today.isoformat())
    p = u.setdefault(page, {"n": 0, "days": []})
    p["n"] += 1
    d = today.isoformat()
    if d not in p["days"]:
        p["days"] = sorted(p["days"] + [d])[-KEEP_DAYS:]
    db.set_meta(conn, "usage", json.dumps(u))


def hidden(conn) -> list[str]:
    from . import db
    try:
        return json.loads(db.get_meta(conn, "hidden_pages", "[]") or "[]")
    except ValueError:
        return []


def set_hidden(conn, page: str, hide: bool) -> list[str]:
    from . import db
    h = set(hidden(conn))
    if page in CORE:
        hide = False
    (h.add if hide else h.discard)(page)
    db.set_meta(conn, "hidden_pages", json.dumps(sorted(h)))
    return sorted(h)


def view(conn, pages: list[str], today: date) -> dict:
    u = _load(conn)
    since = u.get("_since")
    long_enough = bool(since) and (today - date.fromisoformat(since)).days >= WINDOW
    cutoff = (today - timedelta(days=WINDOW)).isoformat()
    hid = set(hidden(conn))
    rows = []
    for p in pages:
        rec = u.get(p) or {"n": 0, "days": []}
        recent = sum(1 for d in rec["days"] if d >= cutoff)
        rows.append({"page": p, "opens": rec["n"], "days_last_30": recent, "last": rec["days"][-1] if rec["days"] else None,
                     "hidden": p in hid, "core": p in CORE,
                     "suggest_hide": long_enough and p not in CORE and p not in hid and recent < 2})
    rows.sort(key=lambda r: (-r["days_last_30"], -r["opens"], r["page"]))
    sugg = [r["page"] for r in rows if r["suggest_hide"]]
    return {"since": since, "pages": rows, "hidden": sorted(hid), "suggest": sugg,
            "text": (f"{len(sugg)} page{'s' if len(sugg) != 1 else ''} opened on fewer than 2 days in the last 30: hiding them keeps the menu to what you use."
                     if sugg else "Suggestions start after 30 days of use." if not long_enough else "You use every page you haven't hidden.")}
