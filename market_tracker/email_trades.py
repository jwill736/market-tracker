"""Trades from your brokers' confirmation emails: the free way to keep Robinhood (stocks) and
Stash current, since neither offers individuals an API.

Every executed order sends an email within a minute or two. The app checks your inbox over IMAP
(read-only; it never sends, moves or deletes mail) every two minutes, reads only messages from
the brokers' domains, and turns confirmations into ledger trades. For Gmail:

    1. Turn on 2-Step Verification for your Google account (required for app passwords).
    2. myaccount.google.com/apppasswords: create one named "Plumbline".
    3. In .env:   MAIL_USER="you@gmail.com"   MAIL_APP_PASSWORD="abcd efgh ijkl mnop"
       (other providers: also MAIL_IMAP_HOST, e.g. imap.mail.me.com for iCloud)

The app password can only read mail through IMAP; it can't change your Google password or
settings, and you can revoke it any time on the same page.

Emails the reader doesn't understand are listed (subject and date only) so the patterns can be
extended; nothing is guessed. A trade already in the ledger from a CSV import or another sync
(same account, symbol, side and quantity, within a day) isn't added twice.
"""

from __future__ import annotations

import email
import hashlib
import html
import imaplib
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

from .providers import market

SENDERS = {"robinhood.com": "Robinhood", "stash.com": "Stash", "stashinvest.com": "Stash", "coinbase.com": "Coinbase"}
LOOKBACK_DAYS = 30
NUM = r"([\d,]*\.?\d+)"
SYM = r"\(?([A-Z][A-Z0-9.\-]{0,9})\)?"

# (side from the email, quantity or dollars, symbol, price). Each pattern names what it captures.
PATTERNS = [
    # Robinhood: "Your order to buy 10 shares of AAPL ... was executed at an average price of $150.25"
    ("shares", re.compile(rf"order to (buy|sell) {NUM} shares? of {SYM}.{{0,200}}?(?:executed|filled)[^$]{{0,40}}\${NUM}", re.I | re.S)),
    # Robinhood dollar orders: "Your order to buy $50.00 of AAPL ... executed at an average price of $150.25"
    ("dollars", re.compile(rf"order to (buy|sell) \${NUM} (?:of|in|worth of) {SYM}.{{0,200}}?(?:executed|filled)[^$]{{0,40}}\${NUM}", re.I | re.S)),
    # Robinhood crypto: "Your order to buy 0.0015 BTC ... executed at an average price of $64,000.00"
    ("shares", re.compile(rf"order to (buy|sell) {NUM} {SYM}\b.{{0,200}}?(?:executed|filled)[^$]{{0,40}}\${NUM}", re.I | re.S)),
    # Generic: "You bought 0.0015 BTC at $64,000.00" / "You sold 3 shares of VOO at $500.00"
    ("shares", re.compile(rf"\byou (bought|sold) {NUM} (?:shares? of )?{SYM}\b[^$]{{0,40}}(?:at|@) (?:an average price of )?\${NUM}", re.I)),
    # Generic dollar buys: "You bought $100.00 of BTC at $64,000.00"
    ("dollars", re.compile(rf"\byou (bought|sold) \${NUM} (?:of|in|worth of) {SYM}\b[^$]{{0,40}}(?:at|@) (?:an average price of )?\${NUM}", re.I)),
    # Stash-style: "Your $20.00 investment in VOO ... at $500.00 per share"
    ("dollars_buy", re.compile(rf"\${NUM} (?:investment|purchase) (?:in|of) {SYM}\b.{{0,200}}?\${NUM} (?:per|a) share", re.I | re.S)),
]
NOT_TRADES = re.compile(r"\b(order (?:was |has been )?(?:placed|received|canceled|cancelled|rejected)|limit order placed|statement|"
                        r"tax document|deposit|withdrawal|transfer)\b", re.I)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


@dataclass
class Parsed:
    trades: list[dict] = field(default_factory=list)
    unread: list[dict] = field(default_factory=list)      # broker emails that look like trades but weren't understood
    skipped: int = 0


def text_of(msg: email.message.Message) -> str:
    """Plain text of an email: the text part, or the HTML part with tags removed."""
    plain, htm = [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        except (AttributeError, LookupError):
            continue
        (plain if ctype == "text/plain" else htm).append(body)
    if plain:
        return re.sub(r"\s+", " ", " ".join(plain))
    raw = " ".join(htm)
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def account_for(sender: str) -> str | None:
    s = sender.lower()
    for dom, acct in SENDERS.items():
        if re.search(rf"[@.]{re.escape(dom)}>?\s*$", s) or s.endswith(dom):
            return acct
    return None


def parse_message(raw: bytes) -> tuple[list[dict], dict | None]:
    """(trades found in one email, or an 'unread' note when it looks like a trade but no pattern matched)."""
    msg = email.message_from_bytes(raw)
    sender = email.utils.parseaddr(msg.get("From", ""))[1]
    acct = account_for(sender)
    subject = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
    try:
        when = parsedate_to_datetime(msg.get("Date")).date()
    except (TypeError, ValueError):
        when = date.today()
    if not acct:
        return [], None
    body = subject + " . " + text_of(msg)
    msg_id = msg.get("Message-ID") or hashlib.sha1(raw[:2000]).hexdigest()
    trades = []
    for kind, pat in PATTERNS:
        for m in pat.finditer(body):
            if kind == "dollars_buy":
                side, amount, sym, price = "buy", _num(m.group(1)), m.group(2), _num(m.group(3))
                qty = amount / price if price else 0
            elif kind == "dollars":
                side, amount, sym, price = m.group(1).lower(), _num(m.group(2)), m.group(3), _num(m.group(4))
                qty = amount / price if price else 0
            else:
                side, qty, sym, price = m.group(1).lower(), _num(m.group(2)), m.group(3), _num(m.group(4))
            side = {"bought": "buy", "sold": "sell"}.get(side, side)
            if not (qty > 0 and price > 0) or sym in ("USD", "US", "THE"):
                continue
            s = market.normalize_symbol(sym)
            if acct == "Coinbase" and "-" not in s:
                s += "-USD"
            key = "em:" + hashlib.sha1(f"{msg_id}|{s}|{side}|{qty:.8f}|{price}".encode()).hexdigest()[:20]
            if any(t["import_key"] == key for t in trades):
                continue
            trades.append({"symbol": s, "side": side, "quantity": round(qty, 8), "price": price, "fees": 0.0,
                           "date": when.isoformat(), "account": acct, "import_key": key,
                           "note": f"{acct} email: {subject[:80]}"})
        if trades:
            break
    note = None
    if not trades and re.search(r"\b(order|executed|filled|bought|sold|invest|purchase)", body[:600], re.I) and not NOT_TRADES.search(subject):
        note = {"account": acct, "subject": subject[:140], "date": when.isoformat()}
    return trades, note


# ------------------------------------------------------------------ IMAP

def configured() -> bool:
    return bool(os.environ.get("MAIL_USER") and os.environ.get("MAIL_APP_PASSWORD"))


def _host() -> str:
    if os.environ.get("MAIL_IMAP_HOST"):
        return os.environ["MAIL_IMAP_HOST"]
    user = os.environ.get("MAIL_USER", "").lower()
    if user.endswith(("@outlook.com", "@hotmail.com", "@live.com")):
        return "outlook.office365.com"
    if user.endswith(("@icloud.com", "@me.com", "@mac.com")):
        return "imap.mail.me.com"
    if user.endswith("@yahoo.com"):
        return "imap.mail.yahoo.com"
    return "imap.gmail.com"


def fetch(since: date, connect=None) -> list[bytes]:
    """Raw broker emails since a date, read-only (the mailbox is opened with readonly=True)."""
    imap = connect() if connect else imaplib.IMAP4_SSL(_host(), 993, timeout=30)
    try:
        imap.login(os.environ["MAIL_USER"], os.environ["MAIL_APP_PASSWORD"].replace(" ", ""))
        box = '"[Gmail]/All Mail"' if _host() == "imap.gmail.com" else "INBOX"
        typ, _ = imap.select(box, readonly=True)
        if typ != "OK":
            imap.select("INBOX", readonly=True)
        out, seen = [], set()
        for dom in SENDERS:
            typ, data = imap.search(None, "SINCE", since.strftime("%d-%b-%Y"), "FROM", dom)
            if typ != "OK":
                continue
            for num in (data[0] or b"").split():
                if num in seen:
                    continue
                seen.add(num)
                typ, parts = imap.fetch(num, "(BODY.PEEK[])")
                if typ == "OK" and parts and isinstance(parts[0], tuple):
                    out.append(parts[0][1])
        return out
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass


def read(since: date | None = None, connect=None) -> Parsed:
    since = since or date.today() - timedelta(days=LOOKBACK_DAYS)
    res = Parsed()
    for raw in fetch(since, connect):
        trades, note = parse_message(raw)
        res.trades += trades
        if note:
            res.unread.append(note)
        elif not trades:
            res.skipped += 1
    res.trades.sort(key=lambda t: t["date"])
    return res


def check_login(connect=None) -> str:
    """'' when the login works, else the reason (shown on the setup checklist)."""
    try:
        imap = connect() if connect else imaplib.IMAP4_SSL(_host(), 993, timeout=20)
        imap.login(os.environ.get("MAIL_USER", ""), os.environ.get("MAIL_APP_PASSWORD", "").replace(" ", ""))
        imap.logout()
        return ""
    except (imaplib.IMAP4.error, OSError) as exc:
        return f"{_host()}: {str(exc)[:160]}"


def age_of(day: str) -> int:
    return (date.today() - datetime.fromisoformat(day).date()).days
