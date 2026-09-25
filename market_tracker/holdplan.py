"""Hold plan: for people who buy and hold, when to sell and where the money goes next.

The default for every holding is Hold. Price wiggles, a stock being "extended" or below its
200-day average are not reasons here: for long-term investors, trading on those mostly costs
money (the stocks people sell tend to do better afterwards than the ones they buy instead).
A holding is only raised for a decision when one of these fires:

1. Your own tripwire. When you record a holding's thesis you can set the lines that would
   prove you wrong: a price floor, a loss from your cost, a take-profit price, a review date.
2. A serious filing: the SEC radar's "Act today" (delisting, bankruptcy) or "Serious"
   (going-concern doubt, restatement, auditor change, late report) for the company.
3. Concentration: one position grown past your cap or past the target weight you gave it.
   The cap is 20% (settable), loosened for small portfolios to 1.5x an equal share, so three
   holdings can each be up to 50%. Trimming back is the one sale buy-and-hold research supports.
4. Taxes: a loss worth harvesting (with a replacement so you stay invested), or a sale that
   would be cheaper if it waited until the shares turn long-term.

Freed money goes to a reinvest queue: holdings below the target you set for them, watchlist
names with no serious filings, and, if nothing else qualifies, a broad market fund.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from . import taxes

CAP = 0.20
RADAR_DAYS = 90
FALLBACK_FUND = ("VTI", "a broad US market fund: the default home for money with no better idea")
ORDER = {"Sell?": 0, "Trim": 1, "Review": 2, "Hold": 3}


@dataclass
class Thesis:
    symbol: str
    thesis: str = ""
    wrong_if: str = ""              # in your words: what would prove the reason wrong
    price_below: float | None = None
    price_above: float | None = None
    max_loss_pct: float | None = None
    review_on: str | None = None
    target_weight: float | None = None   # fraction, e.g. 0.10
    updated: str = ""


@dataclass
class Trigger:
    kind: str        # tripwire / filing / size / tax / thesis
    level: str       # sell / trim / review / info
    text: str


@dataclass
class HoldRow:
    symbol: str
    verdict: str
    quantity: float
    price: float | None
    value: float
    weight: float
    cost_pct: float | None
    triggers: list[Trigger] = field(default_factory=list)
    trim_value: float = 0.0
    wait_until: str | None = None
    wait_saves: float = 0.0
    thesis: dict | None = None
    earnings: dict | None = None


def effective_cap(cap: float, holdings: int) -> float:
    return max(cap, 1.5 / holdings) if holdings else cap


def _verdict(triggers: list[Trigger]) -> str:
    levels = {t.level for t in triggers}
    if "sell" in levels:
        return "Sell?"
    if "trim" in levels:
        return "Trim"
    if "review" in levels:
        return "Review"
    return "Hold"


def check_holding(pos: dict, base: float, thesis: Thesis | None, radar: list[dict], today: date,
                  cap: float = CAP) -> HoldRow:
    """pos: a valued position (symbol, quantity, price, market_value, unrealized_pct)."""
    sym, price = pos["symbol"], pos.get("price")
    value = pos.get("market_value") or 0.0
    w = value / base if base else 0.0
    cost_pct = pos.get("unrealized_pct")
    trig: list[Trigger] = []
    trim_value = 0.0

    if thesis is None or not (thesis.thesis or thesis.wrong_if):
        trig.append(Trigger("thesis", "info", "No reason written down yet: note why you own it and what would make you sell"))
    if thesis and price:
        if thesis.price_below is not None and price <= thesis.price_below:
            trig.append(Trigger("tripwire", "sell", f"Your line: sell below ${thesis.price_below:,.2f}. It's ${price:,.2f}"))
        if thesis.max_loss_pct is not None and cost_pct is not None and cost_pct <= -abs(thesis.max_loss_pct):
            trig.append(Trigger("tripwire", "review", f"Down {abs(cost_pct):.0f}% from your cost, past the {abs(thesis.max_loss_pct):.0f}% "
                                                      "you said you'd re-think at"))
        if thesis.price_above is not None and price >= thesis.price_above:
            trig.append(Trigger("tripwire", "trim", f"Reached your ${thesis.price_above:,.2f} target (now ${price:,.2f}): "
                                                    "take some off or raise the target"))
    if thesis and thesis.review_on and thesis.review_on <= today.isoformat():
        trig.append(Trigger("thesis", "review", f"Review date {thesis.review_on}: re-read your reason"
                                                + (f" (\"{thesis.wrong_if}\")" if thesis.wrong_if else "")))

    since = (today - timedelta(days=RADAR_DAYS)).isoformat()
    for a in radar:
        if (a.get("filed") or a.get("when") or "")[:10] < since or a.get("level", 0) < 2:
            continue
        lvl = "sell" if a["level"] >= 3 else "review"
        trig.append(Trigger("filing", lvl, f"{a.get('headline') or a.get('label')} ({(a.get('filed') or a.get('when') or '')[:10]})"))

    limit = cap
    if thesis and thesis.target_weight:
        limit = min(cap, thesis.target_weight * 1.25)
    if base and w > limit:
        target = min(cap, thesis.target_weight) if thesis and thesis.target_weight else cap
        trim_value = round((w - target) * base, 2)
        trig.append(Trigger("size", "trim", f"{w:.0%} of your portfolio, over your {limit:.0%} line: trimming ${trim_value:,.0f} "
                                            f"brings it back to {target:.0%}"))
    verdict = _verdict(trig)
    return HoldRow(sym, verdict, pos["quantity"], price, round(value, 2), round(w, 4), cost_pct, trig, trim_value,
                   thesis=asdict(thesis) if thesis else None)


def add_tax_notes(rows: list[HoldRow], tax: dict) -> None:
    """Attach the tax clock (wait to sell) and harvest opportunities to the rows."""
    clock = {}
    for c in tax.get("clock", []):
        cur = clock.get(c["symbol"])
        if not cur or c["long_term_on"] > cur["long_term_on"]:
            clock[c["symbol"]] = dict(c, saving=(cur["saving"] if cur else 0) + c["saving"])
    harvest = {h["symbol"]: h for h in tax.get("harvest", [])}
    blackout = {b["symbol"]: b for b in tax.get("blackout", [])}
    for r in rows:
        c = clock.get(r.symbol)
        if c and r.verdict in ("Trim", "Sell?", "Review"):
            r.wait_until, r.wait_saves = c["long_term_on"], c["saving"]
            if r.verdict != "Sell?":
                r.triggers.append(Trigger("tax", "info", f"If you sell, waiting until {c['long_term_on']} ({c['days']} days) turns "
                                                         f"the gain long-term and saves about ${c['saving']:,.0f}"))
        h = harvest.get(r.symbol)
        if h:
            text = (f"Down ${h['loss']:,.0f} ({h['loss_pct']:.0f}%): selling and holding {h['replacement']} for 31 days "
                    f"would save about ${h['tax_saved']:,.0f} in tax while staying invested")
            if h["blocked_by"]:
                b = h["blocked_by"][0]
                text += f". But you bought {b['symbol']} on {b['date']}{' in ' + b['account'] if b['account'] else ''}: selling now would wash part of the loss"
            r.triggers.append(Trigger("tax", "info", text))
        bo = blackout.get(r.symbol)
        if bo:
            r.triggers.append(Trigger("tax", "info", f"Sold at a loss on {bo['sold']}: don't buy more before {bo['until']} "
                                                     "or the loss is washed"))


def reinvest_queue(rows: list[HoldRow], theses: dict[str, Thesis], watch: list[str], radar_by_symbol: dict[str, list[dict]],
                   blackout: list[dict], base: float, freed: float, today: date) -> list[dict]:
    """Where freed-up money could go, best first. Nothing here is a buy signal; it's the order
    in which your own plan says money should be put back to work."""
    blocked = {s for b in blackout for s in b["avoid"]}
    out = []
    for r in sorted(rows, key=lambda r: r.weight):
        t = theses.get(r.symbol)
        if not t or not t.target_weight or r.verdict != "Hold" or r.symbol in blocked:
            continue
        gap = (t.target_weight - r.weight) * base
        if gap >= 25:
            out.append({"symbol": r.symbol, "why": f"Below the {t.target_weight:.0%} you set ({r.weight:.1%} now)",
                        "amount": round(gap, 2), "source": "target"})
    held = {r.symbol for r in rows}
    since = (today - timedelta(days=RADAR_DAYS)).isoformat()
    for sym in watch:
        if sym in held or sym in blocked:
            continue
        serious = [a for a in radar_by_symbol.get(sym, []) if a.get("level", 0) >= 2 and (a.get("filed") or a.get("when") or "") >= since]
        if serious:
            continue
        out.append({"symbol": sym, "why": "On your watchlist, no serious filings in 90 days", "amount": None, "source": "watchlist"})
    if FALLBACK_FUND[0] not in held or not out:
        out.append({"symbol": FALLBACK_FUND[0], "why": FALLBACK_FUND[1], "amount": None, "source": "default"})
    if freed > 0 and out:
        left = freed
        for q in out:
            if q["amount"]:
                q["amount"] = round(min(q["amount"], left), 2)
                left -= q["amount"]
        if left >= 25:
            out[-1]["amount"] = round((out[-1]["amount"] or 0) + left, 2)
    return out


def build(positions: list[dict], transactions: list[dict], theses: dict[str, Thesis], radar_by_symbol: dict[str, list[dict]],
          today: date, *, cash: float = 0.0, cap: float = CAP, watch: list[str] | None = None,
          st_rate: float = taxes.ST_RATE, lt_rate: float = taxes.LT_RATE, earnings: dict[str, dict] | None = None) -> dict:
    positions = [p for p in positions if p.get("quantity")]
    cap = effective_cap(cap, len(positions))
    base = sum(p.get("market_value") or 0 for p in positions) + max(cash, 0.0)
    prices = {p["symbol"]: p["price"] for p in positions if p.get("price")}
    tax = taxes.summary(transactions, prices, today, st_rate, lt_rate)
    rows = [check_holding(p, base, theses.get(p["symbol"]), radar_by_symbol.get(p["symbol"], []), today, cap)
            for p in positions]
    add_tax_notes(rows, tax)
    for r in rows:
        e = (earnings or {}).get(r.symbol)
        if e:
            r.earnings = e
    rows.sort(key=lambda r: (ORDER[r.verdict], -r.value))
    freed = sum(r.trim_value for r in rows if r.verdict == "Trim") + max(cash, 0.0)
    queue = reinvest_queue(rows, theses, watch or [], radar_by_symbol, tax["blackout"], base, freed, today)
    counts = {v: sum(1 for r in rows if r.verdict == v) for v in ORDER}
    return {"as_of": today.isoformat(), "base": round(base, 2), "cash": cash, "cap": cap, "counts": counts,
            "holdings": [asdict(r) for r in rows], "reinvest": queue, "freed": round(freed, 2), "tax": tax,
            "rules": {"cap": cap, "radar_days": RADAR_DAYS, "short_term_rate": st_rate, "long_term_rate": lt_rate}}


# ------------------------------------------------------------------ from the app's own data

def settings(conn) -> dict:
    from . import db
    return {"cap": float(db.get_meta(conn, "hold_cap", str(CAP))),
            "st_rate": float(db.get_meta(conn, "tax_st_rate", str(taxes.ST_RATE))),
            "lt_rate": float(db.get_meta(conn, "tax_lt_rate", str(taxes.LT_RATE)))}


def theses_from(raw: dict[str, dict]) -> dict[str, Thesis]:
    keys = set(Thesis.__dataclass_fields__)
    return {s: Thesis(**{k: v for k, v in d.items() if k in keys}) for s, d in raw.items()}


def gather(today: date | None = None) -> dict:
    """The hold plan from the ledger, your theses and settings, the radar for your companies and
    upcoming earnings. Used by the Hold tab and the morning brief."""
    from . import db, events, http, sentinel, service
    today = today or date.today()
    with db.connect() as conn:
        txs = db.list_transactions(conn)
        watch = db.watchlist(conn)
        raw = db.theses(conn)
        cash = float(db.get_meta(conn, "cash", "0") or 0)
        cfg = settings(conn)
    positions = service.portfolio_summary(txs, False)["positions"] if txs else []
    positions = [p for p in positions if p.get("quantity")]
    held = [p["symbol"] for p in positions]
    radar_by: dict[str, list[dict]] = {}
    radar_errors: list[str] = []
    try:
        mine, radar_errors = sentinel.radar_cache.get(tuple(held + watch), lambda: sentinel.sentinel.mine(held + watch))
        for a in mine:
            radar_by.setdefault(a.symbol, []).append(a.to_dict())
    except (http.DataUnavailable, ValueError) as exc:
        radar_errors = [str(exc)]
    try:
        cal = events.build(positions, today)
    except (http.DataUnavailable, ValueError, KeyError) as exc:
        cal = {"earnings": [], "macro": [], "errors": [str(exc)]}
    plan = build(positions, txs, theses_from(raw), radar_by, today, cash=cash, cap=cfg["cap"], watch=watch,
                 st_rate=cfg["st_rate"], lt_rate=cfg["lt_rate"], earnings={e["symbol"]: e for e in cal["earnings"]})
    plan["events"] = cal
    plan["radar"] = {s: v for s, v in radar_by.items()}
    plan["errors"] = radar_errors + cal.get("errors", [])
    return plan
