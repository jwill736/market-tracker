"""The weekly recap: one push on Sunday evening instead of a push every morning.

Checking a portfolio less often leads to fewer fear-driven sales (Benartzi & Thaler's "myopic
loss aversion", 1995, and the experiments that followed: people shown their returns less often
take more long-term risk and trade less). Urgent things still push the moment they happen
(serious filings, big moves, failed syncs); this collects the rest into one read:

1. The week in dollars: your holdings' change since the previous week's close, biggest first.
2. What needs a decision (the hold plan's Sell?/Trim/Review and why).
3. News confirmed by several outlets this week, per holding.
4. Big moves and new annual reports (with how much of them is new).
5. Next week: your earnings dates and the Fed and inflation releases.
6. Your data: how much of the portfolio was checked against a broker, and what to fix.

The morning brief is still there in the app every day; the weekly recap replaces its push
unless you choose "daily" or "both" (Home → This week).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SEND_DAY, SEND_AT = 6, time(17, 0)          # Sunday, 5pm Eastern
CADENCES = ("weekly", "daily", "both")


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"


def week_change(positions: list[dict], closes: dict[str, list[tuple[str, float]]], today: date) -> dict:
    """Each holding's change over the last 7 calendar days, on the shares you hold now."""
    start = (today - timedelta(days=7)).isoformat()
    rows = []
    for p in positions:
        bars = closes.get(p["symbol"]) or []
        q = p.get("quantity") or 0
        if not bars or not q:
            continue
        before = [c for d, c in bars if d <= start]
        if not before:
            continue
        now = p.get("price") or bars[-1][1]
        change = q * (now - before[-1])
        rows.append({"symbol": p["symbol"], "change": round(change, 2), "change_pct": round((now / before[-1] - 1) * 100, 2),
                     "value": round(q * now, 2)})
    rows.sort(key=lambda r: -abs(r["change"]))
    total = sum(r["change"] for r in rows)
    base = sum(r["value"] for r in rows) - total
    return {"total": round(total, 2), "pct": round(total / base * 100, 2) if base > 0 else None, "rows": rows}


def compose(*, today: date, week: dict, plan: dict | None = None, desk: dict | None = None, headsups: list[dict] | None = None,
            tenk: list[dict] | None = None, confidence: dict | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    lines: list[dict] = []

    def add(section, text, level=1, symbol=""):
        lines.append({"section": section, "text": text, "level": level, "symbol": symbol})

    if week["rows"]:
        pct = f" ({week['pct']:+.1f}%)" if week["pct"] is not None and abs(week["pct"]) >= 0.05 else ""
        head = f"Your holdings {_money(week['total'])}{pct} this week"
        add("The week", head, 0)
        ups = [r for r in week["rows"] if r["change"] > 0][:3]
        downs = [r for r in week["rows"] if r["change"] < 0][:3]
        for r in ups + downs:
            add("The week", f"{r['symbol']} {_money(r['change'])} ({r['change_pct']:+.1f}%)", 0, r["symbol"])

    for h in (plan or {}).get("holdings", []):
        if h.get("verdict") and h["verdict"] != "Hold":
            why = next((t["text"] for t in h.get("triggers", []) if t.get("level") != "info"), "")
            add("Needs a decision", f"{h['symbol']}: {h['verdict']}{' - ' + why if why else ''}", 3 if h["verdict"] == "Sell?" else 2, h["symbol"])

    since = (now - timedelta(days=7)).isoformat()
    for sym, d in sorted((desk or {}).items()):
        for st in d.get("stories", []):
            if st.get("tier") in ("A", "B") and st.get("first", "") >= since:
                add("Confirmed news", f"{sym}: {st['title']} ({st['sources']} outlet{'s' if st['sources'] != 1 else ''})",
                    2 if st["tier"] == "A" else 1, sym)
    for hu in headsups or []:
        if hu.get("key", "").startswith("bigmove:") and hu.get("at", "") >= since:
            add("Big moves", hu["title"], 1, hu.get("symbol", ""))
    for t in tenk or []:
        risk = (t.get("sections") or {}).get("risk") or {}
        if (t.get("current") or {}).get("filed", "") >= (today - timedelta(days=7)).isoformat():
            rank = f"; {t['rank']['text'].lower()}" if t.get("rank") else ""
            add("Annual reports", f"{t['symbol']} filed its 10-K: {round((risk.get('new_share') or 0) * 100)}% of Risk Factors is new{rank}",
                2 if t.get("level") == "big" else 1, t["symbol"])

    nxt = {(today + timedelta(days=i)).isoformat() for i in range(1, 8)}
    ev = (plan or {}).get("events") or {}
    for e in ev.get("earnings", []):
        if e["date"] in nxt:
            move = f", options price about ±{e['move_pct']:.1f}%" if e.get("move_pct") is not None else ""
            add("Next week", f"{e['symbol']} reports {date.fromisoformat(e['date']).strftime('%a %b %-d')}{move}", 1, e["symbol"])
    seen = set()
    for m in ev.get("macro", []):
        if m["date"] in nxt and (m["kind"], m["date"]) not in seen:
            seen.add((m["kind"], m["date"]))
            add("Next week", f"{m['kind']} {date.fromisoformat(m['date']).strftime('%a %b %-d')}", 0)

    if confidence and confidence.get("total_value"):
        add("Your data", f"{confidence['score']:.0f}% of your money was checked against a broker or statement lately", 1 if confidence["score"] < 80 else 0)
        for f in (confidence.get("fixes") or [])[:3]:
            add("Your data", f["text"], f.get("level", 1))

    decisions = sum(1 for ln in lines if ln["section"] == "Needs a decision")
    wk = f"{_money(week['total'])} " if week["rows"] else ""
    title = (f"Your week: {wk}· {decisions} holding{'s' if decisions != 1 else ''} need{'s' if decisions == 1 else ''} a decision" if decisions
             else f"Your week: {wk}· nothing needs you")
    return {"title": title.replace("  ", " "), "week_ending": today.isoformat(), "generated_at": now.isoformat(timespec="seconds"),
            "lines": lines, "week": week}


def push_text(r: dict, limit: int = 9) -> str:
    keep = [ln for ln in r["lines"] if ln["level"] >= 1 or ln["section"] == "The week"][:limit]
    more = len(r["lines"]) - len(keep)
    return "\n".join(f"- {ln['text']}" for ln in keep) + (f"\n+{more} more in the app" if more > 0 else "")


def due(now: datetime, last_sent: str) -> bool:
    """True once a week, Sunday at or after 5pm Eastern."""
    et = now.astimezone(ET)
    return et.weekday() == SEND_DAY and et.time() >= SEND_AT and last_sent != et.date().isoformat()


def gather(now: datetime | None = None) -> dict:
    """Build the recap from the app's own data."""
    import json
    from concurrent.futures import ThreadPoolExecutor

    from . import confidence as conf, db, holdplan, http, sentinel, service
    from .providers import market
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ET).date()
    with db.connect() as conn:
        led = db.ledger(conn)
        heads = [dict(r) for r in conn.execute("SELECT key, kind, level, symbol, title, at FROM headsup WHERE at >= ? ORDER BY at DESC",
                                               ((now - timedelta(days=8)).isoformat(),))]
    positions = service.portfolio_summary(led, False)["positions"] if led else []
    positions = [p for p in positions if p.get("quantity")]

    def hist(p):
        try:
            return p["symbol"], [(b.date, b.close) for b in market.get_history(p["symbol"], 20)]
        except (http.DataUnavailable, KeyError, ValueError):
            return p["symbol"], []
    with ThreadPoolExecutor(max_workers=6) as pool:
        closes = dict(pool.map(hist, positions))
    try:
        plan = holdplan.cached()
    except Exception:  # noqa: BLE001 - the recap goes out without the plan rather than not at all
        plan = None
    held = sorted(p["symbol"] for p in positions)
    desk = (sentinel.newsdesk_cache.peek(tuple(held), 86400) or {}).get("desk") if held else None
    tenk = _recent_tenk(held, today)
    try:
        c = conf.current()
    except Exception:  # noqa: BLE001
        c = None
    out = compose(today=today, week=week_change(positions, closes, today), plan=plan, desk=desk, headsups=heads, tenk=tenk,
                  confidence=c, now=now)
    json.dumps(out)     # the recap is stored as JSON: fail here, not after the push
    return out


def _recent_tenk(held: list[str], today: date) -> list[dict]:
    """Annual reports filed this week by companies you hold, compared with last year's (and ranked)."""
    from . import filings, http, tenkrank
    from .providers import market
    out = []
    data = None
    for sym in held:
        if market.asset_class(sym) != "stock":
            continue
        try:
            docs = filings.filings_for(sym, {"10-K", "10-K405", "20-F"}, 1)
            if not docs or docs[0]["filed"] < (today - timedelta(days=7)).isoformat():
                continue
            r = filings.tenk_changes(sym)
        except (http.DataUnavailable, KeyError, ValueError):
            continue
        if r and not r.get("error"):
            data = data or tenkrank.load()
            r["rank"] = tenkrank.percentile(data, ((r.get("sections") or {}).get("risk") or {}).get("new_share"), today)
            out.append(r)
    return out
