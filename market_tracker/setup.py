"""The ten-minute setup: every connection, in order, each with a test that proves it works.

Steps are ordered by what they protect: the backup first (nothing else matters if the ledger
is lost), then phone alerts (so failures reach you), then each account's automatic feed, then
a statement check for the money no feed can see (Robinhood stocks, Stash), then live prices.
A step is done only when its test has passed, not when a key has merely been saved.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone


def steps(conn, today: date | None = None) -> list[dict]:
    from . import accounts, brokers, coinbase_sync, db, email_trades, livefeed, notify, offsite, robinhood_crypto
    today = today or date.today()
    now = datetime.now(timezone.utc)
    held = {t.get("account") or "" for t in db.list_transactions(conn)}
    try:
        stmts = json.loads(db.get_meta(conn, "statement_checks", "{}") or "{}")
    except ValueError:
        stmts = {}
    backup = json.loads(db.get_meta(conn, "offsite_last", "null") or "null")

    def sync_state(kind: str, configured: bool) -> tuple[bool, str]:
        if not configured:
            return False, "Not connected"
        last = accounts.last_sync(kind)
        if not last:
            return False, "Connected; run the test"
        if not last.get("ok"):
            return False, f"Last test failed: {last.get('error')}"
        return True, f"Working: last synced {last['at'][:16].replace('T', ' ')} UTC"

    def stmt_state(acct: str) -> tuple[bool, str]:
        s = stmts.get(acct)
        if not s:
            return False, f"No {acct} statement checked yet"
        age = (today - date.fromisoformat(s["day"])).days
        bad = len(s.get("differences") or [])
        if age > 35:
            return False, f"Last checked {age} days ago: check this month's"
        return bad == 0, (f"Checked {s['day']}: {len(s.get('match') or [])} match" + (f", {bad} differ: fix them" if bad else ""))

    out = []
    ok = bool(backup and backup.get("ok") and now - datetime.fromisoformat(backup["at"]) < timedelta(days=3))
    out.append({"key": "backup", "title": "Back up your ledger off this computer",
                "why": "Trades from emails and costs you type exist only here. A dead disk loses them.",
                "done": ok, "detail": ("Last copy " + backup["at"][:10]) if ok else
                ("Last try failed: " + backup.get("error", "")) if backup and not backup.get("ok") else
                "Set up" if offsite.configured() else "Not set up",
                "go": "offsite", "test": offsite.configured()})
    out.append({"key": "alerts", "title": "Get alerts on your phone",
                "why": "Failing syncs, confirmed serious news and price lines reach you even when the app is closed.",
                "done": notify.configured() and db.get_meta(conn, "ntfy_tested", "") == "1",
                "detail": ("Topic set" + (", test sent" if db.get_meta(conn, "ntfy_tested", "") == "1" else ": send a test"))
                if notify.configured() else "Not set up", "go": "setup-alerts", "test": notify.configured()})
    for kind, title, why, conf, go in [
            ("coinbase", "Connect Coinbase", "Every 5 minutes: trades, balances, and a check that the ledger matches.",
             coinbase_sync.configured(), "cx-coinbase"),
            ("robinhood_crypto", "Connect Robinhood crypto", "Every 5 minutes, and a balance check for your coins there.",
             robinhood_crypto.configured(), "cx-rh"),
            ("email", "Connect your email (Robinhood stocks, Stash)", "Neither has an API: their confirmation emails are how trades arrive.",
             email_trades.configured(), "cx-email")]:
        done, detail = sync_state(kind, conf)
        out.append({"key": kind, "title": title, "why": why, "done": done, "detail": detail, "go": go, "test": conf})
    for acct in ("Robinhood", "Stash"):
        if acct in held or acct == "Stash":
            done, detail = stmt_state(acct)
            out.append({"key": f"statement:{acct}", "title": f"Check a {acct} statement",
                        "why": ("Nothing automatic checks Robinhood stock balances." if acct == "Robinhood"
                                else "Stash has no API: a monthly statement is the only proof the ledger is right."),
                        "done": done, "detail": detail, "go": "statement", "test": False})
    key = livefeed.finnhub_key()
    out.append({"key": "finnhub", "title": "Live stock prices", "why": "Every trade as it happens instead of a price every few seconds.",
                "done": bool(key), "detail": "Streaming from Finnhub" if key else "Prices poll every few seconds", "go": "live",
                "test": bool(key)})
    out.append({"key": "paper", "title": "Practise a trade on the paper account",
                "why": "The full preview → confirm flow with pretend money, before any real order.",
                "done": bool(brokers.paper_state(conn)["trades"]), "detail": "Done" if brokers.paper_state(conn)["trades"] else
                "Open any stock → Trade → Send with: Paper account", "go": "brokers", "test": False})
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    out.append({"key": "ask", "title": "Talk to Plumbline (Ask, weekly letter)",
                "why": "Ask questions about your own money in plain words and get an advisor's answer from your data.",
                "done": has_key, "detail": "Anthropic API key found" if has_key else "Add ANTHROPIC_API_KEY to .env (console.anthropic.com), then restart",
                "go": "ask", "test": False})
    decided = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'").fetchone() and \
        conn.execute("SELECT COUNT(*) FROM decisions WHERE status != 'open'").fetchone()[0]
    out.append({"key": "decide", "title": "Make your first decision",
                "why": "Approve, skip or put off one of this week's decisions (Home → Decisions): that's how the app learns what you act on.",
                "done": bool(decided), "detail": "Done" if decided else "Home → Decisions", "go": "decisions", "test": False})
    return out


def suggest_topic() -> str:
    import secrets
    return "plumbline-" + secrets.token_urlsafe(12).replace("_", "").replace("-", "").lower()[:16]


def save_topic(topic: str) -> None:
    os.environ["NTFY_TOPIC"] = topic
