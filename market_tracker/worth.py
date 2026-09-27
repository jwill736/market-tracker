"""Is Plumbline worth it? What it has saved you, in dollars, against what it costs.

Saved (only what can be measured, counted from the end of the settling-in weeks):
- calls you followed, against doing nothing (decisions.scorecard: each at its longest result so far);
- tax that harvests you did saved;
- sells the cooling-off talked you out of: you wrote a reason, then didn't sell within a week of the
  wait ending; counted as what the shares you kept have done since (negative if they fell: holding
  off cost you).
Cost: hosting (about $5 a month on Fly.io; set your own figure) and the estimated Claude spend.

Not counted, because the app can't see them: fees you cut after the fee check, taxes saved by
waiting for long-term rates, money you'd have left idle anyway. So this undercounts a little. After
six months, if it's still negative, the honest move is to switch Plumbline off and hold VOO.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

FLY_MONTHLY = 5.0
VERDICT_MONTHS = 6
KEPT_DAYS = 9              # 48 hours of cooling plus a week: no sell by then means you held off


def _close_on_or_after(bars, day):
    for d, c in bars:
        if d >= day:
            return c
    return None


def _held_on(txs: list[dict], symbol: str, day: str) -> float:
    q = 0.0
    for t in txs:
        if t["symbol"] == symbol and t["date"][:10] <= day:
            q += t["quantity"] if t["side"] == "buy" else -t["quantity"]
    return max(0.0, q)


def held_off(log: list[dict], txs: list[dict], history_fn, today: date, since: str | None) -> list[dict]:
    """Cooling-off entries you didn't follow with a sell, and what keeping the shares has done since."""
    out = []
    for e in log:
        day = e["at"][:10]
        if e.get("override") or (since and day < since) or (today - date.fromisoformat(day)).days < KEPT_DAYS:
            continue
        end = (date.fromisoformat(day) + timedelta(days=KEPT_DAYS)).isoformat()
        if any(t["symbol"] == e["symbol"] and t["side"] == "sell" and day <= t["date"][:10] <= end for t in txs):
            continue                                   # you sold after all
        qty = _held_on(txs, e["symbol"], day)
        try:
            bars = history_fn(e["symbol"])
        except Exception:  # noqa: BLE001 - no prices, not counted
            continue
        p0 = _close_on_or_after(bars, day)
        if not (qty and p0 and bars):
            continue
        out.append({"symbol": e["symbol"], "day": day, "reason": e["reason"], "shares": round(qty, 6),
                    "dollars": round(qty * (bars[-1][1] - p0), 2)})
    return out


def view(card: dict, kept: list[dict], spend: dict[str, float], live_since: str | None, today: date, hosting: float) -> dict:
    followed = 0.0
    for it in card.get("items") or []:
        if it["you"] == "followed" and it["results"]:
            followed += it["results"][max(it["results"])]["dollars"]
    saved = {"followed": round(followed, 2), "harvests": card.get("harvest_saved", 0.0), "held_off": round(sum(k["dollars"] for k in kept), 2)}
    months = max(0.0, (today - date.fromisoformat(live_since)).days / 30.44) if live_since else 0.0
    cost = {"hosting": round(hosting * months, 2), "claude": round(sum(spend.values()), 2)}
    net = round(sum(saved.values()) - sum(cost.values()), 2)
    if not live_since:
        text = "Not live yet: this starts counting once your holdings are in."
    elif months < 1:
        text = f"Counting since {live_since}: nothing to judge yet. The verdict comes at {VERDICT_MONTHS} months."
    elif months < VERDICT_MONTHS:
        text = (f"So far: {'+' if net >= 0 else '−'}${abs(net):,.0f} net after {months:.1f} months "
                f"(saved ${sum(saved.values()):,.0f}, cost ${sum(cost.values()):,.0f}). Too early to judge; the verdict comes at {VERDICT_MONTHS} months.")
    elif net < 0:
        text = (f"After {months:.0f} months Plumbline has cost you ${-net:,.0f} more than it saved. The honest move is to switch it off "
                "and hold VOO.")
    else:
        text = f"After {months:.0f} months Plumbline has saved you ${net:,.0f} more than it cost."
    return {"saved": saved, "cost": cost, "net": net, "months": round(months, 1), "held_off": kept, "text": text,
            "verdict_months": VERDICT_MONTHS}


def hosting_monthly(conn, url: str) -> float:
    from . import db
    raw = db.get_meta(conn, "hosting_monthly", "")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return FLY_MONTHLY if "fly.dev" in (url or "") else 0.0


def spend(conn) -> dict[str, float]:
    from . import db
    try:
        return json.loads(db.get_meta(conn, "claude_spend", "{}") or "{}")
    except ValueError:
        return {}
