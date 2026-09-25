"""Single-user login for the hosted app.

Set APP_PASSWORD and every page and API call needs a session cookie, which the /login page
issues after the password is entered. Without APP_PASSWORD (running on your own computer)
nothing changes. The cookie is an expiry time signed with HMAC-SHA256, so the server keeps no
session state; changing APP_PASSWORD or APP_SECRET signs everyone out.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import defaultdict, deque

COOKIE = "plumbline_session"
SESSION_DAYS = 30
MAX_ATTEMPTS = 10                 # failed logins per client per window
ATTEMPT_WINDOW = 15 * 60
OPEN_PATHS = ("/login", "/logout", "/healthz")


def password() -> str:
    return os.environ.get("APP_PASSWORD", "")


def enabled() -> bool:
    return bool(password())


def misconfigured() -> bool:
    """Hosted configs set REQUIRE_LOGIN=1; without a password the app then refuses to serve
    rather than putting a portfolio on the open internet."""
    return os.environ.get("REQUIRE_LOGIN", "").lower() in ("1", "true", "yes") and not enabled()


def _secret() -> bytes:
    # APP_SECRET is optional; deriving from the password still ties sessions to it.
    explicit = os.environ.get("APP_SECRET", "")
    return hashlib.sha256(("plumbline:" + (explicit or password())).encode()).digest()


def make_token(now: float | None = None, days: int = SESSION_DAYS) -> str:
    expires = int((now or time.time()) + days * 86400)
    sig = hmac.new(_secret(), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def valid_token(token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expires, _, sig = token.partition(".")
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    good = hmac.new(_secret(), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good)


def check_password(given: str) -> bool:
    return enabled() and hmac.compare_digest(given.encode(), password().encode())


class Throttle:
    """Failed-login counter per client address, so the password can't be guessed quickly."""

    def __init__(self, limit: int = MAX_ATTEMPTS, window: float = ATTEMPT_WINDOW):
        self.limit, self.window = limit, window
        self.failures: dict[str, deque] = defaultdict(deque)

    def _trim(self, key: str, now: float) -> deque:
        q = self.failures[key]
        while q and q[0] < now - self.window:
            q.popleft()
        return q

    def blocked(self, key: str, now: float | None = None) -> bool:
        return len(self._trim(key, now or time.time())) >= self.limit

    def fail(self, key: str, now: float | None = None) -> None:
        self._trim(key, now or time.time()).append(now or time.time())

    def reset(self, key: str) -> None:
        self.failures.pop(key, None)


def is_open(path: str) -> bool:
    return path in OPEN_PATHS or path.startswith("/static/login")


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plumbline · Sign in</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=Instrument+Serif&display=swap">
<style>
:root {{ color-scheme: light; --page:#eef1f4; --surface:#fff; --ink:#1e2328; --muted:#6b7580; --accent:#2743d6; --bad:#c23b32; --border:rgba(30,35,40,.12); }}
@media (prefers-color-scheme: dark) {{ :root {{ color-scheme: dark; --page:#15191d; --surface:#1e2328; --ink:#eef1f4; --muted:#8a949f; --accent:#6f86f4; --bad:#e26a5f; --border:rgba(255,255,255,.1); }} }}
body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:var(--page); color:var(--ink);
  font:15px/1.5 "Instrument Sans", system-ui, sans-serif; padding:16px; box-sizing:border-box; }}
form {{ background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:28px; width:min(360px,100%);
  display:grid; gap:14px; box-sizing:border-box; }}
h1 {{ font:400 32px/1 "Instrument Serif", Georgia, serif; margin:0; display:flex; gap:10px; align-items:center; }}
input {{ font:inherit; padding:10px 12px; border-radius:8px; border:1px solid var(--border); background:var(--page); color:var(--ink); }}
button {{ font:inherit; font-weight:600; padding:10px; border-radius:8px; border:0; background:var(--accent); color:#fff; cursor:pointer; }}
input:focus-visible, button:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
.err {{ color:var(--bad); font-size:14px; margin:0; }} .muted {{ color:var(--muted); font-size:13px; margin:0; }}
</style></head><body>
<form method="post" action="/login">
  <h1><svg width="20" height="28" viewBox="0 0 26 36" aria-hidden="true"><path d="M3 2H23M13 2V22" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/><path d="M13 21L18 27L13 34L8 27Z" fill="var(--accent)"/></svg>Plumbline</h1>
  <label for="password" class="muted">Password</label>
  <input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
  {error}
  <button type="submit">Sign in</button>
  <p class="muted">Private dashboard. Not financial advice.</p>
</form></body></html>"""


def login_page(error: str = "") -> str:
    return LOGIN_PAGE.format(error=f'<p class="err" role="alert">{error}</p>' if error else "")
