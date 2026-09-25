"""First-run setup, so nobody has to edit a hidden file: `mt setup` (the start scripts run it
with --if-needed before opening the app).

Asks for the contact email the SEC requires from every user of its data, and offers phone
alerts: it makes up a private ntfy topic name and says how to subscribe to it. Answers go into
.env next to the app; everything else keeps its default. Safe to run again at any time.
"""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Callable

EXAMPLE = ".env.example"
ENV = ".env"
PLACEHOLDER = "you@example.com"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def read_env(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def get(lines: list[str], key: str) -> str:
    for ln in lines:
        m = re.match(rf"^\s*{re.escape(key)}\s*=\s*(.*)$", ln)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def put(lines: list[str], key: str, value: str) -> list[str]:
    """Set KEY=value in place (quoted), or append it."""
    out, done = [], False
    for ln in lines:
        if re.match(rf"^\s*{re.escape(key)}\s*=", ln) and not done:
            out.append(f'{key}="{value}"')
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f'{key}="{value}"')
    return out


def email_missing(lines: list[str]) -> bool:
    ua = get(lines, "SEC_USER_AGENT")
    return not ua or PLACEHOLDER in ua or "@" not in ua


def needed(root: str = ".") -> bool:
    lines = read_env(os.path.join(root, ENV))
    return not lines or email_missing(lines)


def new_topic() -> str:
    return "plumbline-" + secrets.token_hex(8)


def run(root: str = ".", ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
        interactive: bool = True) -> dict:
    """Returns what was set: {"created", "email", "ntfy_topic"}."""
    env_path, example = os.path.join(root, ENV), os.path.join(root, EXAMPLE)
    created = not os.path.exists(env_path)
    lines = read_env(env_path) if not created else read_env(example)
    result = {"created": created, "email": None, "ntfy_topic": None}
    say("")
    say("Plumbline setup")
    say("---------------")
    if email_missing(lines):
        if not interactive:
            say(f"Open {ENV} and put your email in SEC_USER_AGENT (the SEC asks every user of its data for a contact).")
        else:
            while True:
                email = ask("Your email (the SEC asks every user of its filings data for a contact; it's only sent to the SEC): ").strip()
                if not email:
                    say("Skipped: filings still work with a placeholder for now; run `mt setup` to add it later.")
                    break
                if _EMAIL.match(email):
                    lines = put(lines, "SEC_USER_AGENT", f"plumbline {email}")
                    result["email"] = email
                    break
                say("That doesn't look like an email address; try again, or press Enter to skip.")
    if created and interactive and not get(lines, "NTFY_TOPIC"):
        yes = ask("Phone alerts (scary filings, the 8:30 morning brief, big news on what you own)? [y/N] ").strip().lower()
        if yes in ("y", "yes"):
            topic = new_topic()
            lines = put(lines, "NTFY_TOPIC", topic)
            result["ntfy_topic"] = topic
            say("")
            say("  1. Install the free ntfy app (App Store / Google Play).")
            say(f"  2. Tap +, subscribe to this topic:  {topic}")
            say("  3. That's it. Keep the name private: anyone who knows it can read the alerts.")
    with open(env_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    db = get(lines, "MT_DB_PATH") or "market_tracker.db"
    say("")
    say(f"Saved {os.path.abspath(env_path)}.")
    say(f"Your portfolio stays on this computer, in {os.path.abspath(os.path.join(root, db))}.")
    say("Next: in the app, Portfolio -> Import your accounts (Robinhood CSV, Coinbase CSV, Stash by hand).")
    say("")
    return result
