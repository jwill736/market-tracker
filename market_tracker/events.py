"""Earnings and market events, in your dollars.

- Next earnings date for each stock you hold (Nasdaq's per-company page; "estimated" until the
  company confirms it).
- How big a move the options market is pricing in: the at-the-money straddle (call + put) at
  the first expiry on or after earnings, as a share of the price. That covers everything up to
  that expiry, earnings day included, so it is an upper-bound read of the earnings move. Turned
  into dollars on your position: "NVDA reports Nov 18: options price about ±8%, ±$640 on yours".
- Fed decisions (the Fed's own FOMC calendar) and the next two weeks of US releases that move
  the whole market: CPI, jobs, PCE, GDP, retail sales (Nasdaq's economic calendar).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from . import http
from .providers import market
from .reading import BROWSER_UA

EARNINGS_DATE = "https://api.nasdaq.com/api/analyst/{sym}/earnings-date"
OPTION_CHAIN = ("https://api.nasdaq.com/api/quote/{sym}/option-chain?assetclass=stocks&limit=400&fromdate=all"
                "&todate=undefined&excode=oprac&callput=callput&money=at&type=all")
ECON = "https://api.nasdaq.com/api/calendar/economicevents?date={day}"
FOMC = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
HEADERS = {"User-Agent": BROWSER_UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}

KEY_RELEASES = [
    (re.compile(r"\bCPI\b|consumer price", re.I), "Inflation (CPI)"),
    (re.compile(r"nonfarm payrolls|unemployment rate", re.I), "Jobs report"),
    (re.compile(r"\bPCE\b|personal consumption", re.I), "Inflation (PCE)"),
    (re.compile(r"\bGDP\b(?!now)", re.I), "GDP"),
    (re.compile(r"retail sales", re.I), "Retail sales"),
    (re.compile(r"fed interest rate decision", re.I), "Fed decision"),     # not "FOMC Member X Speaks"
    (re.compile(r"fomc (meeting )?minutes", re.I), "Fed minutes"),
    (re.compile(r"\bPPI\b|producer price", re.I), "Producer prices (PPI)"),
]
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ------------------------------------------------------------------ earnings

_ANNOUNCE = re.compile(r"for\s+[\w.\-]+:\s*([A-Z][a-z]{2})\s+(\d{1,2}),\s*(\d{4})")


def parse_earnings_date(data: dict) -> tuple[str, bool] | None:
    """(YYYY-MM-DD, estimated) from Nasdaq's earnings-date payload."""
    d = (data or {}).get("data") or {}
    m = _ANNOUNCE.search(d.get("announcement") or "")
    if not m:
        return None
    day = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date().isoformat()
    return day, "estimated" in (d.get("reportText") or "").lower()


def _num(x) -> float | None:
    try:
        return float(str(x).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def _mid(bid, ask, last) -> float | None:
    b, a, lst = _num(bid), _num(ask), _num(last)
    if b is not None and a is not None and a >= b > 0:
        return (a + b) / 2
    return lst


def parse_chain(data: dict) -> tuple[float | None, dict[str, list[dict]]]:
    """(last price, {expiry ISO date: [{strike, call, put}]}) from Nasdaq's option chain."""
    d = (data or {}).get("data") or {}
    m = re.search(r"\$([\d,.]+)", d.get("lastTrade") or "")
    last = _num(m.group(1)) if m else None
    out: dict[str, list[dict]] = {}
    group = None
    for r in ((d.get("table") or {}).get("rows") or []):
        if r.get("expirygroup"):
            try:
                group = datetime.strptime(r["expirygroup"], "%B %d, %Y").date().isoformat()
            except ValueError:
                group = None
            continue
        strike = _num(r.get("strike"))
        if group is None or strike is None:
            continue
        out.setdefault(group, []).append({"strike": strike, "call": _mid(r.get("c_Bid"), r.get("c_Ask"), r.get("c_Last")),
                                          "put": _mid(r.get("p_Bid"), r.get("p_Ask"), r.get("p_Last"))})
    return last, out


def implied_move(last: float | None, chain: dict[str, list[dict]], on_or_after: str) -> tuple[float, str] | None:
    """(move as a fraction of price, expiry) from the at-the-money straddle."""
    if not last:
        return None
    for expiry in sorted(chain):
        if expiry < on_or_after:
            continue
        rows = [r for r in chain[expiry] if r["call"] and r["put"]]
        if not rows:
            continue
        atm = min(rows, key=lambda r: abs(r["strike"] - last))
        return (atm["call"] + atm["put"]) / last, expiry
    return None


def earnings_for(sym: str, value: float, today: date, get=None, within_days: int = 100) -> dict | None:
    get = get or http.get
    got = parse_earnings_date(get(EARNINGS_DATE.format(sym=sym), headers=HEADERS, ttl=6 * 3600))
    if not got:
        return None
    day, estimated = got
    days = (date.fromisoformat(day) - today).days
    if days < 0 or days > within_days:
        return None
    row = {"symbol": sym, "date": day, "estimated": estimated, "days": days, "move_pct": None, "move_dollars": None,
           "expiry": None, "position_value": round(value, 2)}
    try:
        last, chain = parse_chain(get(OPTION_CHAIN.format(sym=sym), headers=HEADERS, ttl=3600))
        im = implied_move(last, chain, day)
        if im:
            row["move_pct"] = round(im[0] * 100, 1)
            row["move_dollars"] = round(im[0] * value, 2)
            row["expiry"] = im[1]
    except (http.DataUnavailable, KeyError, TypeError, ValueError):
        pass
    return row


# ------------------------------------------------------------------ macro

def parse_fomc(html: str) -> list[str]:
    """Decision days (the last day of each meeting) from the Fed's FOMC calendar page."""
    out = []
    parts = re.split(r"(\d{4})\s+FOMC Meetings", html)
    for i in range(1, len(parts) - 1, 2):
        year, body = int(parts[i]), parts[i + 1]
        for mon, days in re.findall(r"fomc-meeting__month[^>]*>\s*(?:<strong>)?\s*([A-Za-z/]+)\s*(?:</strong>)?.*?"
                                    r"fomc-meeting__date[^>]*>\s*([\d\-\s*]+)", body, re.S):
            months = [m.strip().lower()[:3] for m in mon.split("/")]
            nums = [int(n) for n in re.findall(r"\d+", days)]
            if not nums or months[-1] not in _MONTHS:
                continue
            try:
                out.append(date(year, _MONTHS[months[-1]], nums[-1]).isoformat())
            except ValueError:
                continue
    return sorted(set(out))


def parse_econ(data: dict, day: str) -> list[dict]:
    rows = ((data or {}).get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        if r.get("country") != "United States":
            continue
        for pat, label in KEY_RELEASES:
            if pat.search(r.get("eventName") or ""):
                out.append({"date": day, "time_gmt": r.get("gmt"), "name": r.get("eventName"), "kind": label,
                            "consensus": (r.get("consensus") or "").replace("&nbsp;", "").strip() or None,
                            "previous": (r.get("previous") or "").replace("&nbsp;", "").strip() or None})
                break
    return out


def macro(today: date, days: int = 14, get=None) -> tuple[list[dict], list[str]]:
    get = get or http.get
    errors: list[str] = []
    out: list[dict] = []
    try:
        for d in parse_fomc(get(FOMC, headers={"User-Agent": BROWSER_UA}, ttl=24 * 3600, as_json=False)):
            if today.isoformat() <= d <= (today + timedelta(days=60)).isoformat():
                out.append({"date": d, "time_gmt": "18:00", "name": "FOMC rate decision", "kind": "Fed decision",
                            "consensus": None, "previous": None})
    except http.DataUnavailable as exc:
        errors.append(f"Fed calendar: {exc}")

    def one(i):
        day = (today + timedelta(days=i)).isoformat()
        try:
            return parse_econ(get(ECON.format(day=day), headers=HEADERS, ttl=6 * 3600), day)
        except (http.DataUnavailable, KeyError, TypeError):
            return []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for rows in pool.map(one, range(days)):
            out += rows
    seen: set[tuple] = set()
    unique = []
    for r in sorted(out, key=lambda r: (r["date"], r["time_gmt"] or "")):
        k = (r["date"], r["kind"]) if r["kind"] == "Fed decision" else (r["date"], r["name"])
        if k not in seen:
            seen.add(k)
            unique.append(r)
    return unique, errors


def build(positions: list[dict], today: date, get=None) -> dict:
    """Earnings for held stocks (with the options-implied move in dollars) and market events."""
    errors: list[str] = []
    stocks = [p for p in positions if p.get("quantity") and market.asset_class(p["symbol"]) == "stock"]

    def one(p):
        try:
            return earnings_for(p["symbol"], p.get("market_value") or 0.0, today, get)
        except (http.DataUnavailable, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{p['symbol']}: {str(exc)[:80]}")
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        earnings = [e for e in pool.map(one, stocks) if e]
    mac, merr = macro(today, get=get)
    return {"as_of": today.isoformat(), "earnings": sorted(earnings, key=lambda e: e["date"]), "macro": mac,
            "errors": errors + merr}
