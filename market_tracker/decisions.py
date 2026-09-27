"""Decisions: what Plumbline thinks you should do this week, in dollars, ranked.

Everything else in the app describes; this decides. It reads the hold plan, the exit reviews,
the tax picture, idle cash, the data and connection checks and (last, smallest) the idea lists,
and turns them into a short list of actions, each with an amount, the reasons, and how strong the
evidence behind it is:

- rule: your own rule or a hard fact (your tripwire, your concentration cap, a delisting notice,
  tax arithmetic). Worth acting on.
- mixed: tested on history, or your own logged thesis has broken, but not proven.
- unproven: a list that hasn't earned a record yet, or a signal the replay couldn't tell from luck
  (a stock sitting in the screen's bottom 50). Optional; ideas are kept small (Size it limits).

"Hold: nothing needs you" is a real answer and the most common one for a buy-and-hold investor.

Nothing is ever executed. Approving a trade opens the trade ticket with the amount filled in;
you preview and confirm there. Each decision with a stock is also written to the advice track
record at that day's price, so whether the app's calls beat simply holding VOO gets scored.
Skipping a decision hides it for 30 days; "later" for 7.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    key TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    kind TEXT NOT NULL,
    symbol TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    amount REAL,
    status TEXT NOT NULL DEFAULT 'open',
    decided TEXT NOT NULL DEFAULT '',
    until TEXT NOT NULL DEFAULT ''
);
"""
PRIORITY = {"fix_sync": 95, "sell": 85, "fix_data": 70, "harvest": 60, "switch": 55, "trim": 50, "invest_cash": 45,
            "shelter": 40, "automate": 35, "wait_long_term": 30, "tax_setup": 25, "optional_idea": 10, "hold": 0}
EVIDENCE = {"rule": "Your rule or a hard fact", "mixed": "Tested, not proven", "unproven": "Unproven: optional, keep it small"}
WEAK_SWITCH_PRIORITY = 20       # a switch on the screen grade alone ranks below putting idle cash to work
IDLE_MIN = 100.0
SKIP_DAYS, LATER_DAYS = 30, 7


def _d(kind, title, lines, evidence, *, symbol="", amount=None, side=None, page=None, key=None, detail=None) -> dict:
    return {"key": key or f"{kind}:{symbol}", "kind": kind, "symbol": symbol, "title": title, "why": [x for x in lines if x],
            "evidence": {"level": evidence, "label": EVIDENCE[evidence]}, "amount": round(amount, 2) if amount else None,
            "action": ({"type": "trade", "symbol": symbol, "side": side, "dollars": round(amount, 2) if amount else None} if side
                       else {"type": "open", "page": page} if page else None),
            "priority": PRIORITY[kind], "detail": detail}


def build(plan: dict, *, reviews: list[dict] | None = None, idle_cash: float = 0.0, health: list[dict] | None = None,
          freshness: list[dict] | None = None, optional: list[dict] | None = None, bottom_note: str = "", today: date | None = None,
          shelter: dict | None = None, automate: dict | None = None) -> list[dict]:
    """plan: holdplan.build(); reviews: exitreview.for_holdings(); health: health.problems(); freshness: freshness.check();
    optional: [{symbol, source, amount, why}] already sized (see sizing.py); shelter: shelter.decision();
    automate: recurring.plan()["suggest"]."""
    today = today or date.today()
    out: list[dict] = []
    for p in health or []:
        out.append(_d("fix_sync", f"Fix the {p['name']} connection", [p["text"], "Until it's fixed, trades there aren't coming in and every number here is off."],
                      "rule", key=f"fix_sync:{p['key'].split(':')[1] if ':' in p['key'] else p['key']}", page="accounts"))
    for f in freshness or []:
        if f.get("stale"):
            out.append(_d("fix_data", f"{f['name']} data is {f['age_days']} days old", [f["text"]], "rule", key=f"fix_data:{f['file']}", page="accounts"))
    rows = {h["symbol"]: h for h in plan.get("holdings") or []}
    for h in rows.values():
        trig = [t for t in h.get("triggers") or [] if t.get("level") in ("sell", "trim", "review")]
        if h.get("verdict") == "Sell?":
            wait = h.get("wait_until")
            lines = [t["text"] for t in trig][:3]
            if wait and h.get("wait_saves", 0) >= 50:
                lines.append(f"Selling after {wait} saves about ${h['wait_saves']:,.0f} in tax, if the reason can wait.")
            out.append(_d("sell", f"Decide on {h['symbol']}: sell ${h['value']:,.0f}?", lines, "rule", symbol=h["symbol"], amount=h["value"], side="sell"))
        elif h.get("verdict") == "Trim" and h.get("trim_value", 0) >= 25:
            out.append(_d("trim", f"Trim {h['symbol']} by ${h['trim_value']:,.0f}", [t["text"] for t in trig][:2]
                          + ["Put the money where the reinvest queue says (VOO if nothing else)."], "rule", symbol=h["symbol"], amount=h["trim_value"], side="sell"))
    sells = {d["symbol"] for d in out if d["kind"] in ("sell", "trim")}
    for r in reviews or []:
        if r["symbol"] in sells:
            continue
        cheap = r["tax_now"] <= max(0.01 * r["value"], 25)
        title = (f"Swap {r['symbol']} (${r['value']:,.0f}) for VOO" if cheap else f"Review {r['symbol']}: ${r['value']:,.0f}, ${r['tax_now']:,.0f} tax to switch")
        # A broken thesis you logged is your own tripwire; a low screen grade alone didn't lag by more than luck.
        strong = bool(r.get("thesis"))
        d = _d("switch", title, r["reasons"][:2] + [r["text"]] + ([] if strong or not bottom_note else [bottom_note]),
               "mixed" if strong else "unproven", symbol=r["symbol"], amount=r["value"], side="sell", detail=r)
        if not strong:
            d["priority"] = WEAK_SWITCH_PRIORITY
        out.append(d)
    for hv in (plan.get("tax") or {}).get("harvest") or []:
        if hv.get("blocked_by") or hv.get("tax_saved", 0) < 50 or hv["symbol"] in sells:
            continue
        px = (rows.get(hv["symbol"]) or {}).get("price")
        out.append(_d("harvest", f"Harvest a ${abs(hv['loss']):,.0f} loss in {hv['symbol']} (saves ~${hv['tax_saved']:,.0f})",
                      [f"Sell {hv['quantity']:g} shares in {hv['account'] or 'your account'} and buy {hv['replacement']} with the money: "
                       f"{hv['replacement_why']}", "Don't buy it back within 30 days (wash sale). " + (hv.get("note") or "")],
                      "rule", symbol=hv["symbol"], amount=hv["quantity"] * px if px else None, side="sell",
                      key=f"harvest:{hv['symbol']}:{hv.get('account', '')}"))
    if idle_cash >= IDLE_MIN:
        # Idle cash goes to a target you set, else the index: a single stock from a watchlist or a dip line is
        # mentioned as your alternative, not made the default.
        queue = [q for q in plan.get("reinvest") or [] if q.get("symbol")]
        dest = next((q for q in queue if q.get("source") in ("target", "default")), {"symbol": "VOO", "why": "The default: the whole US market for 0.03% a year."})
        other = next((q for q in queue if q.get("source") not in ("target", "default")), None)
        out.append(_d("invest_cash", f"Put ${idle_cash:,.0f} of idle cash into {dest['symbol']}",
                      [f"It has sat uninvested for two weeks or more. {dest.get('why', '')}",
                       "Cash is the one certain drag on a long-term portfolio; the index is the default, not a pick."]
                      + ([f"Your own list also has {other['symbol']} ({other.get('why', '')}): that's a pick, so size it with Size it if you want it."] if other else []),
                      "rule", symbol=dest["symbol"], amount=idle_cash, side="buy"))
    if shelter:
        out.append(_d(shelter["kind"], shelter["title"], shelter["why"], "rule", amount=shelter.get("amount"), page="accounts",
                      key=f"{shelter['kind']}:{today.year}"))
    if automate and automate.get("weekly"):
        out.append(_d("automate", f"Automate it: ${automate['weekly']:,} a week into {automate['symbol']}", [automate["text"]], "rule",
                      page="portfolio", key=f"automate:{today.year}-{(today.month - 1) // 3}"))
    for h in rows.values():
        if h.get("verdict") in ("Sell?", "Trim") or not h.get("wait_until") or h.get("wait_saves", 0) < 100:
            continue
        out.append(_d("wait_long_term", f"If you sell {h['symbol']}, wait until {h['wait_until']}",
                      [f"Selling before then is short-term: waiting saves about ${h['wait_saves']:,.0f} in tax."], "rule", symbol=h["symbol"]))
    for o in (optional or [])[:1]:
        out.append(_d("optional_idea", f"Optional: up to ${o['amount']:,.0f} in {o['symbol']}", o["why"][:3], "unproven",
                      symbol=o["symbol"], amount=o["amount"], side="buy", key=f"optional:{o['symbol']}:{today.isocalendar()[1]}"))
    if not [d for d in out if d["kind"] != "optional_idea"]:
        out.append(_d("hold", "Hold: nothing needs you this week",
                      ["No tripwire, filing, concentration, tax or cash issue. For a buy-and-hold portfolio, doing nothing is usually the best move."],
                      "rule", key=f"hold:{today.isocalendar()[0]}-{today.isocalendar()[1]}"))
    return sorted(out, key=lambda d: (-d["priority"], -(d["amount"] or 0)))


# ------------------------------------------------------------------ what you did with them

def sync(conn, items: list[dict], today: date) -> list[dict]:
    """Record new decisions and attach each one's status; skipped and 'later' ones are hidden until
    their date. Returns the visible list."""
    conn.executescript(SCHEMA)
    day = today.isoformat()
    out = []
    for d in items:
        row = conn.execute("SELECT * FROM decisions WHERE key = ?", (d["key"],)).fetchone()
        if row is None:
            conn.execute("INSERT INTO decisions (key, first_seen, kind, symbol, title, amount) VALUES (?, ?, ?, ?, ?, ?)",
                         (d["key"], day, d["kind"], d["symbol"], d["title"], d["amount"]))
            status, first = "open", day
        else:
            status, first = row["status"], row["first_seen"]
            if status in ("skipped", "later") and row["until"] and row["until"] <= day:
                conn.execute("UPDATE decisions SET status = 'open', until = '' WHERE key = ?", (d["key"],))
                status = "open"
        if status in ("skipped", "later"):
            continue
        out.append(dict(d, status=status, first_seen=first, new=first == day))
    return out


def decide(conn, key: str, status: str, today: date) -> dict:
    if status not in ("approved", "skipped", "later", "done"):
        raise ValueError("status: approved, skipped, later or done")
    conn.executescript(SCHEMA)
    until = {"skipped": today + timedelta(days=SKIP_DAYS), "later": today + timedelta(days=LATER_DAYS)}.get(status)
    cur = conn.execute("UPDATE decisions SET status = ?, decided = ?, until = ? WHERE key = ?",
                       (status, today.isoformat(), until.isoformat() if until else "", key))
    if cur.rowcount != 1:
        raise KeyError(key)
    return {"key": key, "status": status, "until": until.isoformat() if until else None}


def history(conn, limit: int = 60) -> list[dict]:
    conn.executescript(SCHEMA)
    return [dict(r) for r in conn.execute("SELECT * FROM decisions WHERE status != 'open' ORDER BY decided DESC LIMIT ?", (limit,))]


# ------------------------------------------------------------------ did following them help?

# What "doing nothing" means for each kind of call, so each is scored against the choice you actually had:
# a sell or trim against keeping the stock (the money goes to VOO), idle cash against leaving it in cash
# (at the T-bill yield), an optional idea against putting the same money in VOO. Harvests are about tax,
# not return, and are counted by the tax they saved instead.
SCORE_KINDS = {"sell": "sell", "trim": "sell", "switch": "sell", "invest_cash": "cash", "optional_idea": "buy"}
SCORE_DAYS = (30, 91, 365)
SCORE_MIN = 20
FOLLOWED = ("approved", "done")


def _close_on_or_after(bars: list[tuple[str, float]], day: str) -> float | None:
    for d, c in bars:
        if d >= day:
            return c
    return None


def scorecard(conn, history_fn, today: date, cash_yield: float = 0.04, since: str | None = None) -> dict:
    """history_fn(symbol) -> [(date, close)] oldest first. Every call you approved or skipped, scored
    from the day you decided at 1, 3 and 12 months: points and dollars against doing nothing.
    since: calls decided before this day (the settling-in weeks, golive.py) don't count."""
    from .advice import BENCH
    conn.executescript(SCHEMA)
    rows = [dict(r) for r in conn.execute("SELECT * FROM decisions WHERE status IN ('approved', 'done', 'skipped') AND decided != ''")
            if not since or r["decided"] >= since]
    cache: dict[str, list] = {}

    def bars(sym):
        if sym not in cache:
            try:
                cache[sym] = history_fn(sym)
            except Exception:  # noqa: BLE001 - no history leaves the call unscored, not the page broken
                cache[sym] = []
        return cache[sym]
    groups = {g: {h: [] for h in SCORE_DAYS} for g in ("followed", "skipped")}
    items, saved = [], 0.0
    for r in rows:
        group = "followed" if r["status"] in FOLLOWED else "skipped"
        if r["kind"] == "harvest":
            m = re.search(r"saves ~\$([\d,]+)", r["title"])
            if m and group == "followed":
                saved += float(m.group(1).replace(",", ""))
            continue
        how = SCORE_KINDS.get(r["kind"])
        if not how or not r["symbol"]:
            continue
        item = {"key": r["key"], "title": r["title"], "symbol": r["symbol"], "decided": r["decided"], "you": group,
                "amount": r["amount"], "results": {}}
        s0, b0 = _close_on_or_after(bars(r["symbol"]), r["decided"]), _close_on_or_after(bars(BENCH), r["decided"])
        for h in SCORE_DAYS:
            end = (date.fromisoformat(r["decided"]) + timedelta(days=h)).isoformat()
            if end > today.isoformat() or not (s0 and b0):
                continue
            s1, b1 = _close_on_or_after(bars(r["symbol"]), end), _close_on_or_after(bars(BENCH), end)
            if not (s1 and b1):
                continue
            stock, voo = s1 / s0 - 1, b1 / b0 - 1
            edge = {"sell": voo - stock, "cash": stock - cash_yield * h / 365, "buy": stock - voo}[how]
            dollars = edge * (r["amount"] or 0)
            item["results"][h] = {"edge": round(edge * 100, 2), "dollars": round(dollars, 2)}
            groups[group][h].append((edge, dollars))
        items.append(item)
    summary = {}
    for g, by_h in groups.items():
        summary[g] = {h: {"n": len(v), "avg_edge": round(sum(e for e, _ in v) / len(v) * 100, 2) if v else None,
                          "helped_pct": round(sum(e > 0 for e, _ in v) / len(v) * 100) if v else None,
                          "dollars": round(sum(d for _, d in v), 2), "enough": len(v) >= SCORE_MIN} for h, v in by_h.items()}
    out = {"summary": summary, "items": sorted(items, key=lambda x: x["decided"], reverse=True), "harvest_saved": round(saved, 2), "since": since,
           "followed": sum(1 for r in rows if r["status"] in FOLLOWED), "skipped": sum(1 for r in rows if r["status"] == "skipped"),
           "min": SCORE_MIN}
    out["text"] = scorecard_text(out)
    return out


def scorecard_text(card: dict) -> str:
    f, s = card["summary"]["followed"][91], card["summary"]["skipped"][91]
    if not card["followed"] and not card["skipped"]:
        return "No decisions made yet: approve or skip a few and this starts scoring the app's calls against doing nothing."
    parts = [f"You followed {card['followed']} call{'s' if card['followed'] != 1 else ''} and skipped {card['skipped']}."]
    if f["n"]:
        parts.append(f"The ones you followed: {f['avg_edge']:+.1f} points ({'+' if f['dollars'] >= 0 else '-'}${abs(f['dollars']):,.0f}) against "
                     f"doing nothing over 3 months, {f['n']} with a result.")
    if s["n"]:
        parts.append(f"The ones you skipped would have made {s['avg_edge']:+.1f} points: "
                     + ("skipping them cost you." if s["avg_edge"] > 0 else "skipping them was right."))
    if card["harvest_saved"]:
        parts.append(f"Harvests you did saved about ${card['harvest_saved']:,.0f} in tax.")
    if not f["enough"]:
        parts.append(f"Too early to judge the app: that takes {card['min']} results at 3 months, and a few lucky ones prove nothing.")
    elif f["avg_edge"] < -1:
        parts.append("So far its calls have cost you: weigh them less and lean on the index.")
    return " ".join(parts)


def advice_items(items: list[dict]) -> list[dict]:
    """The stock decisions, as advice-track-record rows (scored against VOO later)."""
    act = {"sell": "Sell?", "trim": "Trim", "switch": "Sell?", "harvest": "Sell?", "invest_cash": "Buy", "optional_idea": "Buy"}
    return [{"symbol": d["symbol"], "action": act[d["kind"]], "source": "decision", "reason": d["title"][:200]}
            for d in items if d["kind"] in act and d["symbol"]]


def push_actions(items: list[dict], base: str, sign) -> tuple[str, ...]:
    """Buttons for the decision push: with one new decision, Do it (opens it in the app, trade ticket
    filled in), Later and Skip (recorded straight from the notification through a signed link);
    with several, one button that opens the Decisions page. No base URL, no buttons."""
    from urllib.parse import quote
    new = [d for d in items if d.get("new") and d["priority"] >= 45]
    if not base or not new:
        return ()
    base = base.rstrip("/")
    if len(new) > 1:
        return (f"view, Open decisions, {base}/#decisions, clear=true",)
    d = new[0]
    return (f"view, {'Do it' if d.get('action') else 'Open'}, {base}/#decisions?do={quote(d['key'], safe='')}, clear=true",
            f"http, Later, {base}/act/{sign('decide|later|' + d['key'])}, method=POST, clear=true",
            f"http, Skip, {base}/act/{sign('decide|skipped|' + d['key'])}, method=POST, clear=true")


def push_text(items: list[dict]) -> tuple[str, str] | None:
    """One push for new decisions that matter (priority 45+), or None."""
    new = [d for d in items if d.get("new") and d["priority"] >= 45]
    if not new:
        return None
    title = new[0]["title"] if len(new) == 1 else f"{len(new)} decisions for you"
    body = "\n".join(f"• {d['title']}" for d in new[:5]) + "\nOpen Plumbline → Decisions to approve, skip or ask why."
    return title, body


# ------------------------------------------------------------------ from the app's own data

def gather(today: date | None = None) -> list[dict]:
    """Build today's decisions from the ledger, the hold plan and the GitHub-built data."""
    from . import cash, db, exitreview, freshness, health, holdplan, http, ideas, screen, screen_backtest, service, sizing, spinoffs, thesis
    today = today or date.today()
    plan = holdplan.cached()
    with db.connect() as conn:
        txs = db.ledger(conn)
        cfg = holdplan.settings(conn)
        accts = cash.load(conn)
        logged = ideas.logged(conn)
    positions = service.portfolio_summary(txs, False)["positions"] if txs else []
    held = {p["symbol"] for p in positions if p.get("quantity")}
    prices = {p["symbol"]: p["price"] for p in positions if p.get("price")}
    data = screen.load()
    flagged: dict[str, list[str]] = {}
    broken: set[str] = set()
    for b in thesis.bottom_held(held, data):
        flagged.setdefault(b["symbol"], []).extend(b["reasons"])
    try:
        for b in thesis.check([dict(r, so_far=None) for r in logged], held, today, lambda s: screen.lookup(data, s)):
            flagged.setdefault(b["symbol"], []).extend(b["reasons"])
            broken.add(b["symbol"])
    except (http.DataUnavailable, KeyError, ValueError):
        pass
    reviews = [dict(r, thesis=r["symbol"] in broken)
               for r in exitreview.for_holdings(flagged, txs, prices, today, cfg["st_rate"], cfg["lt_rate"])]
    idle = sum(r["amount"] for r in cash.view(accts, today, cash.FALLBACK_YIELD, False)["accounts"] if r["idle"])
    note = screen_backtest.bottom_sentence(screen_backtest.load())
    optional = []
    try:
        from . import evidence, paper
        books = paper.load()
        spin_books = [b for b in books if b["list"] == "spinoffs"]
        st = evidence.status(paper.performance(spin_books, lambda s: [(b.date, b.close) for b in market_history(s)], today),
                             [], {"spinoff": "Spin-offs"})
        spin_earned = st["spinoff"]["earned"]
    except Exception:  # noqa: BLE001 - no record, no optional idea
        spin_earned = False
    try:
        # Spin-offs are research only (evidence.py) until their paper portfolio beats VOO; until then no optional idea.
        spec = {r["symbol"] for r in logged if r["source"] in sizing.SPECULATIVE and r["decision"] == "bought"}
        cands = [r for r in spinoffs.build(today, lookup_fn=lambda s: screen.lookup(data, s))
                 if r["loggable"] and r["ticker"] not in held] if spin_earned else []
        for c in cands[:1]:
            sz = sizing.size(c["ticker"], "spinoff", market_price(c["ticker"]), positions, float(sum(a["amount"] for a in accts.values())),
                             lambda s: (screen.lookup(data, s) or {}).get("sector"), spec)
            if sz.get("amount"):
                optional.append({"symbol": c["ticker"], "source": "spinoff", "amount": sz["amount"],
                                 "why": [f"Spin-off trading since {c['trading_since']} ({c['name'][:60]}).", spin_note(), sz["lines"][0]]})
    except (http.DataUnavailable, KeyError, ValueError):
        pass
    shelter_d = automate = None
    try:
        from . import accounts, recurring, schedules, shelter
        with db.connect() as conn:
            st, inc, scheds = shelter.settings(conn), db.income(conn), schedules.load(conn)
        names = sorted({t.get("account") or "Unlabeled" for t in txs} | set(accts))
        pos = [{"account": a["name"], "symbol": p["symbol"], "value": p["quantity"] * prices.get(p["symbol"], 0.0)}
               for a in accounts.overview(txs, inc, today) for p in a["positions"]]
        year_ago = (today - timedelta(days=365)).isoformat()
        div: dict[tuple[str, str], float] = {}
        for r in inc:
            if r["kind"] in ("dividend", "reinvested") and r["day"] >= year_ago:
                div[(r["account"], r["symbol"])] = div.get((r["account"], r["symbol"]), 0.0) + r["amount"]
        if names:
            shelter_d = shelter.decision(shelter.view(st, names, pos, div, today))
        automate = recurring.plan(txs, scheds, today, idle)["suggest"]
    except Exception:  # noqa: BLE001 - the rest of the decisions don't wait on these
        pass
    items = build(plan, reviews=reviews, idle_cash=idle, health=health.problems(), freshness=freshness.check(today),
                  optional=optional, bottom_note=note, today=today, shelter=shelter_d, automate=automate)
    from . import confidence, golive
    try:
        tr = golive.trust(confidence.current())
    except Exception:  # noqa: BLE001 - can't check the numbers: don't act on them
        tr = {"trusted": False, "text": "Couldn't check your holdings against your brokers just now; money decisions wait until it can."}
    return golive.gate(items, tr, today)


def spin_note() -> str:
    """What the spin-off replay says, in one sentence (or a caution if it hasn't run)."""
    from . import spinoff_backtest
    bt = spinoff_backtest.load()
    s = (((bt or {}).get("summary") or {}).get("from_day20") or {}).get("12m")
    if not s:
        return "Spin-offs have beaten the market in older studies; this app hasn't replayed them yet: small and optional at most."
    st = spinoff_backtest.stress(bt)
    return (f"In the replay since 2005, spin-offs bought after their first 20 days led SPY by {s['avg_edge']:+.0f} points over 12 months on "
            f"average but {s['median_edge']:+.0f} for the typical one. " + (st["text"] if st else "Half the spin-offs are missing (no ticker today).")
            + " Small and optional at most.")


def market_history(sym: str):
    from .providers import market
    return market.get_history(sym, 800)


def market_price(sym: str) -> float:
    from .providers import market
    return market.get_quote(sym).price
