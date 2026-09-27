"""Is the reason still there? Each idea came with what would prove it wrong; this checks it for
the ideas you bought (marked "I bought it") and for any logged idea whose stock you hold.

The expensive mistake in buy-and-hold isn't the day you buy, it's holding for years after the
reason you bought is gone. The checks are narrow on purpose: a stock falling is not by itself a
broken case, so the only price rule is a large gap against VOO (25 points), and the screen rules
use the weekly screen's own numbers.

Per kind of idea:
- quality/value/momentum: the screen grade fell below 50, or 12 months on it trails VOO by 15+.
- backlog: the backlog is no longer growing faster than revenue.
- sleepers and insider buys: insiders sold more than they bought since the idea, or the grade fell
  below 50.
- raised guidance: a later results release lowered the outlook, or 3 months on it trails VOO by 10+.
- spin-offs: 12 months on it trails VOO by 15+.
- any kind: it trails VOO by 25 points or more since the idea.
Found problems are pushed to your phone once each (checked weekly, after the Sunday screen).

Separately, any stock you hold that sits in the weekly screen's bottom 50 (companies worth $2B+)
is shown whether or not it came from an idea. It is shown, not pushed: once stock splits were
handled, the replay since 2012 found that group trailed SPY by under a point a quarter, within
luck, so a low grade is a prompt to reread why you own it, not a sell signal.
"""

from __future__ import annotations

from datetime import date

BIG_GAP = -25.0
SALE_MIN = 100_000.0


def reasons(idea: dict, today: date, lookup: dict | None, insider_trades=None, recap=None) -> list[str]:
    """idea: a scored idea (ideas.score item: day, source, so_far). insider_trades: [InsiderTrade]
    since the idea day; recap: earnings.recap of the latest release."""
    out: list[str] = []
    held_days = (today - date.fromisoformat(idea["day"])).days
    edge = (idea.get("so_far") or {}).get("edge")
    src = idea["source"]
    score = (lookup or {}).get("score")
    if src in ("qvm", "sleeper", "insider") and score is not None and score < 50:
        out.append(f"screen grade fell to {score:.0f}/100")
    if src == "qvm" and held_days >= 365 and edge is not None and edge <= -15:
        out.append(f"trails VOO by {-edge:.0f} points after a year")
    if src == "backlog" and lookup and lookup.get("rpo_growth") is not None and lookup.get("revenue_growth") is not None \
            and lookup["rpo_growth"] <= lookup["revenue_growth"]:
        out.append(f"backlog now growing slower than revenue ({lookup['rpo_growth']:+.0%} vs {lookup['revenue_growth']:+.0%})")
    if src in ("sleeper", "insider") and insider_trades:
        sold = sum(t.value for t in insider_trades if t.code == "S" and t.date >= idea["day"])
        bought = sum(t.value for t in insider_trades if t.code == "P" and t.date >= idea["day"])
        if sold >= SALE_MIN and sold > bought:
            out.append(f"insiders sold ${sold:,.0f} since the idea (bought ${bought:,.0f})")
    if src == "pead":
        if recap and recap["release"]["filed"] > idea["day"] and (recap.get("outlook") or {}).get("direction") == "lowered":
            out.append(f"outlook lowered in the {recap['release']['filed']} results release")
        if held_days >= 91 and edge is not None and edge <= -10:
            out.append(f"trails VOO by {-edge:.0f} points after 3 months")
    if src == "spinoff" and held_days >= 365 and edge is not None and edge <= -15:
        out.append(f"trails VOO by {-edge:.0f} points after a year")
    if edge is not None and edge <= BIG_GAP and not any("trails VOO" in r for r in out):
        out.append(f"trails VOO by {-edge:.0f} points since {idea['day']}")
    return out


def watched(items: list[dict], held: set[str]) -> list[dict]:
    """Ideas to check: bought, or logged and currently held (unless marked passed). One per symbol and
    kind: the latest."""
    seen, out = set(), []
    for it in sorted(items, key=lambda i: i["day"], reverse=True):
        if it.get("decision") == "passed" or not (it.get("decision") == "bought" or it["symbol"] in held):
            continue
        key = (it["symbol"], it["source"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def check(items: list[dict], held: set[str], today: date, lookup_fn, trades_fn=None, recap_fn=None) -> list[dict]:
    """[{id, symbol, source, day, reasons}] for watched ideas whose case has a problem."""
    out = []
    for it in watched(items, held):
        trades = recap = None
        try:
            if it["source"] in ("sleeper", "insider") and trades_fn:
                trades = trades_fn(it["symbol"], (today - date.fromisoformat(it["day"])).days + 5)
            if it["source"] == "pead" and recap_fn:
                recap = recap_fn(it["symbol"])
        except Exception:  # noqa: BLE001 - a missing source means that rule isn't checked this week
            pass
        rs = reasons(it, today, lookup_fn(it["symbol"]), trades, recap)
        if rs:
            out.append({"id": it.get("id"), "symbol": it["symbol"], "source": it["source"], "day": it["day"], "reasons": rs})
    return out


def bottom_held(held: set[str], screen_data: dict | None) -> list[dict]:
    """Stocks you hold that are in the screen's bottom 50 this week."""
    out = []
    for r in (screen_data or {}).get("bottom", []):
        if r["symbol"] in held:
            out.append({"id": None, "symbol": r["symbol"], "source": "bottom", "day": str((screen_data or {}).get("as_of", ""))[:10],
                        "reasons": [f"in the screen's bottom 50 of companies worth $2B+ (grade {r['score']:.0f}/100); "
                                    "in the replay since 2012 that group trailed SPY by under a point a quarter, within luck"]})
    return out
