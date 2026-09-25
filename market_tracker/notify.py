"""Phone notifications through ntfy (https://ntfy.sh).

ntfy needs no account: install the app, subscribe to a topic, and anything posted to that
topic arrives as a push notification. Topics are public to whoever knows the name, so the
name should be long and random, and it is read from the NTFY_TOPIC environment variable
(a repository secret in GitHub Actions) and never printed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

DEFAULT_SERVER = "https://ntfy.sh"


@dataclass
class Message:
    title: str
    body: str
    url: str = ""                 # opened when the notification is tapped
    priority: int = 3             # 1 (min) to 5 (max)
    tags: tuple[str, ...] = ()    # ntfy emoji short codes shown next to the title


def configured() -> bool:
    return bool(os.environ.get("NTFY_TOPIC", "").strip())


def send(msg: Message, client: httpx.Client | None = None) -> bool:
    """Post one notification. Returns False (and never raises) when ntfy isn't configured
    or the post fails: a lost notification must not break the scan that produced it."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    server = os.environ.get("NTFY_SERVER", DEFAULT_SERVER).rstrip("/")
    headers = {"Title": _header(msg.title), "Priority": str(msg.priority)}
    if msg.url:
        headers["Click"] = msg.url
    if msg.tags:
        headers["Tags"] = ",".join(msg.tags)
    try:
        c = client or httpx.Client(timeout=15)
        r = c.post(f"{server}/{topic}", content=msg.body.encode("utf-8"), headers=headers)
        return r.status_code < 300
    except httpx.HTTPError:
        return False


def _header(text: str) -> str:
    # HTTP headers must be latin-1; ntfy also accepts RFC 2047, but plain ASCII is simplest.
    return text.encode("ascii", "replace").decode("ascii")[:250]
