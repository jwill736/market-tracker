"""Is the data behind the app still being refreshed?

The screen, the sealed idea log, the backtests, the 10-K ranking and the filing watcher are all
built by GitHub jobs and saved to the journal-data branch. When a job fails quietly (a changed
website, an expired secret, GitHub disabling schedules on a quiet repository), nothing looks
broken: the pages just keep showing old numbers, and the idea log grows a hole that can't be filled
later. This checks the age of each file against how often its job runs and says which are stale;
the background loop pushes each stale file to your phone once, and again only after it has
recovered and gone stale a second time.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from . import http

# name, file, how to read its date, the most days it may be old, what runs it
CHECKS = [
    ("Stock screen", "screen.json", "as_of", 8, "the Sunday 'Stock screen' job"),
    ("Idea log (sealed)", "ideas_chain.jsonl", "chain", 5, "the weekday 'Idea log' job"),
    ("Filing watcher", "insider_buys.csv", "watcher", 5, "the 'Filing watcher' job (every 5 minutes)"),
    ("10-K ranking", "tenk_rank.json", "as_of", 10, "the weekly '10-K ranking' job"),
    ("Screen backtest", "screen_backtest.json", "as_of", 40, "the monthly 'Screen backtest' job"),
    ("Signal backtests", "insider_backtest.json", "as_of", 40, "the monthly 'Signal backtests' job"),
]


def _day(s: str | None) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def read_date(kind: str, name: str, get) -> date | None:
    """The date a file was last built, or None if it isn't there."""
    from .pulse import DATA_URL
    url = f"{DATA_URL}/{name}"
    if kind == "as_of":
        return _day((get(url, as_json=True) or {}).get("as_of"))
    text = get(url, as_json=False) or ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if kind == "chain":
        return _day(json.loads(lines[-1]).get("sealed_at")) if lines else None
    if kind == "watcher":                     # CSV: the latest filing date in the file
        from . import alerts
        buys = alerts.parse_buys(text)
        return max((_day(b.filed) for b in buys if _day(b.filed)), default=None)
    return None


def check(today: date | None = None, get=None) -> list[dict]:
    today = today or date.today()
    get = get or (lambda u, as_json=True: http.get(u, ttl=1800, as_json=as_json))
    out = []
    for label, name, kind, max_days, job in CHECKS:
        try:
            d = read_date(kind, name, get)
            missing = d is None
        except http.DataUnavailable:
            d, missing = None, True
        except (ValueError, KeyError, TypeError):
            d, missing = None, True
        age = (today - d).days if d else None
        # The watcher and the idea log only move on weekdays: allow for a long weekend.
        stale = age is not None and age > max_days
        out.append({"name": label, "file": name, "as_of": d.isoformat() if d else None, "age_days": age, "max_days": max_days,
                    "missing": missing, "stale": stale, "job": job,
                    "text": (f"{label}: last built {d.isoformat()} ({age} days ago; it should be at most {max_days}). "
                             f"Check {job} on GitHub (Actions tab)." if stale else
                             f"{label}: not built yet ({job})." if missing else f"{label}: {d.isoformat()}")})
    return out


def alerts_due(results: list[dict], already: set[str]) -> tuple[list[dict], set[str]]:
    """Stale files not yet pushed; and the new 'already pushed' set (recovered files drop out)."""
    stale = {r["file"] for r in results if r["stale"]}
    return [r for r in results if r["stale"] and r["file"] not in already], (already & stale) | stale


def summary(results: list[dict]) -> dict:
    stale = [r for r in results if r["stale"]]
    missing = [r for r in results if r["missing"]]
    return {"checks": results, "stale": len(stale), "missing": len(missing), "ok": not stale,
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "text": ("All data files are current." if not stale and not missing else
                     f"{len(stale)} data file{'s' if len(stale) != 1 else ''} stale: " + "; ".join(r["name"] for r in stale) if stale else
                     "Some data hasn't been built yet: " + ", ".join(r["name"] for r in missing) + ".")}
