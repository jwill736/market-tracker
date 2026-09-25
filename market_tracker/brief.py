"""Morning brief: one push and one page before the open (8:30 ET on weekdays).

Only what needs you, most important first:
1. Holdings that need a decision (the hold plan's Sell?/Trim/Review and why).
2. New serious filings on your companies since yesterday.
3. Today and tomorrow: your earnings (with the options-implied move in dollars) and the Fed,
   CPI and jobs releases.
4. Pre-market moves in your holdings of 2% or more, and what the whole portfolio is doing.
5. Early-wire hits on your holdings; crypto radar items for your coins.
6. Taxes: shares turning long-term this week, and don't-buy windows ending.

When nothing needs you, it says so: holding is the plan.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import http
from .providers import market

ET = ZoneInfo("America/New_York")
SEND_AT = time(8, 30)
MOVE_PCT = 2.0


def _money(x: float) -> str:
    return f"${abs(x):,.0f}"


def _when(day: str, today: date) -> str:
    d = date.fromisoformat(day)
    return "today" if d == today else "tomorrow" if d == today + timedelta(days=1) else d.strftime("%a %b %-d")


def compose(*, today: date, plan: dict, quotes: dict[str, market.Quote], early_data: dict | None = None,
            crypto: dict | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    lines: list[dict] = []

    def add(section, text, level=1, symbol=""):
        lines.append({"section": section, "text": text, "level": level, "symbol": symbol})

    for h in plan.get("holdings", []):
        if h["verdict"] == "Hold":
            continue
        why = next((t["text"] for t in h["triggers"] if t["level"] != "info"), "")
        add("Needs a decision", f"{h['symbol']}: {h['verdict']} {('- ' + why) if why else ''}".strip(),
            3 if h["verdict"] == "Sell?" else 2, h["symbol"])

    since = (today - timedelta(days=1)).isoformat()
    for sym, alerts in (plan.get("radar") or {}).items():
        for a in alerts:
            if (a.get("filed") or "") >= since and a.get("level", 0) >= 2:
                add("New filings", f"{sym}: {a.get('headline')} ({a.get('level_name') or 'serious'})", a["level"], sym)

    ev = plan.get("events") or {}
    soon = {today.isoformat(), (today + timedelta(days=1)).isoformat()}
    for e in ev.get("earnings", []):
        if e["date"] in soon:
            move = (f": options price about ±{e['move_pct']:.1f}%, ±{_money(e['move_dollars'])} on yours"
                    if e.get("move_pct") is not None else "")
            add("Today and tomorrow", f"{e['symbol']} reports {_when(e['date'], today)}{' (estimated date)' if e.get('estimated') else ''}{move}",
                2, e["symbol"])
    for m in ev.get("macro", []):
        if m["date"] in soon:
            extra = f", forecast {m['consensus']}" if m.get("consensus") else ""
            add("Today and tomorrow", f"{m['kind']} {_when(m['date'], today)}{extra}", 1)

    total_move = 0.0
    for h in plan.get("holdings", []):
        q = quotes.get(h["symbol"])
        if not q or q.change_pct is None or not h.get("value"):
            continue
        move = h["value"] * q.change_pct / (100 + q.change_pct)
        total_move += move
        if abs(q.change_pct) >= MOVE_PCT:
            label = {"pre": "pre-market", "post": "after hours", "24h": "in 24 h"}.get(q.session, "today")
            add("Moving", f"{h['symbol']} {q.change_pct:+.1f}% {label} ({'+' if move >= 0 else '-'}{_money(move)} on yours)",
                2 if abs(q.change_pct) >= 5 else 1, h["symbol"])
    if quotes:
        base = sum(h.get("value") or 0 for h in plan.get("holdings", []))
        if base:
            lines.insert(0, {"section": "Portfolio", "level": 0, "symbol": "",
                             "text": f"Portfolio {'+' if total_move >= 0 else '-'}{_money(total_move)} "
                                     f"({total_move / base * 100:+.2f}%) since yesterday's close"})

    held = {h["symbol"] for h in plan.get("holdings", [])}
    for s in (early_data or {}).get("signals", []):
        if s.get("symbol") in held and s.get("strength", 0) >= 30:
            head = (s.get("signals") or [{}])[0].get("headline", "")
            add("Early wire", f"{s['symbol']}: {head}{' (not in the mainstream yet)' if s.get('early') else ''}", 2, s["symbol"])
    for a in (crypto or {}).get("alerts", []):
        if a["level"] >= 2 and (not a.get("date") or a["date"] >= since):
            add("Crypto", a["text"], a["level"], ",".join(a.get("coins") or []))

    tax = plan.get("tax") or {}
    for c in tax.get("clock", []):
        if c["days"] <= 7:
            add("Taxes", f"{c['symbol']} shares bought {c['bought']} turn long-term {_when(c['long_term_on'], today)}: "
                         f"selling after that saves about {_money(c['saving'])}", 1, c["symbol"])
    for b in tax.get("blackout", []):
        if (date.fromisoformat(b["until"]) - today).days <= 3:
            add("Taxes", f"From {_when(b['until'], today)} you can buy {b['symbol']} again without washing the loss", 1, b["symbol"])

    decisions = sum(1 for ln in lines if ln["section"] == "Needs a decision")
    urgent = [ln for ln in lines if ln["level"] >= 2]
    if decisions:
        title = f"Morning brief: {decisions} holding{'s' if decisions != 1 else ''} need{'s' if decisions == 1 else ''} a decision"
    elif urgent:
        title = f"Morning brief: {len(urgent)} thing{'s' if len(urgent) != 1 else ''} to know"
    else:
        title = "Morning brief: nothing needs you today"
    if len(lines) <= (1 if lines and lines[0]["section"] == "Portfolio" else 0):
        lines.append({"section": "All quiet", "text": "No tripwires, filings, earnings or tax dates. Holding is the plan.",
                      "level": 0, "symbol": ""})
    return {"title": title, "date": today.isoformat(), "generated_at": now.isoformat(timespec="seconds"), "lines": lines}


def push_text(brief: dict, limit: int = 7) -> str:
    return "\n".join(f"- {ln['text']}" for ln in brief["lines"][:limit]) + \
        (f"\n+{len(brief['lines']) - limit} more in the app" if len(brief["lines"]) > limit else "")


def gather(today: date | None = None, now: datetime | None = None) -> dict:
    """Build today's brief from the app's own data."""
    from . import cryptoradar, holdplan, sentinel
    now = now or datetime.now(timezone.utc)
    today = today or now.astimezone(ET).date()
    plan = holdplan.cached()
    syms = [h["symbol"] for h in plan["holdings"]]

    def q(sym):
        try:
            return sym, market.get_live_quote(sym)
        except (http.DataUnavailable, KeyError, ValueError):
            return sym, None
    with ThreadPoolExecutor(max_workers=6) as pool:
        quotes = {s: v for s, v in pool.map(q, syms) if v}
    # The early wire's last scan (the background loop refreshes it every few minutes); running a
    # fresh one here would hold the brief up by half a minute.
    early_data = sentinel.early_cache.peek("early", 1800)
    crypto = None
    coins = tuple(s for s in syms if market.asset_class(s) == "crypto")
    if coins:
        try:
            crypto = sentinel.crypto_cache.get(coins, lambda: cryptoradar.build(list(coins), now))
        except (http.DataUnavailable, ValueError, KeyError):
            crypto = None
    return compose(today=today, plan=plan, quotes=quotes, early_data=early_data, crypto=crypto, now=now)


def due(now: datetime, last_sent: str) -> bool:
    """True once per weekday, at or after 8:30 ET."""
    et = now.astimezone(ET)
    return et.weekday() < 5 and et.time() >= SEND_AT and last_sent != et.date().isoformat()
