"""Strategy plan: for each holding, sell / trim / hold / add, and a few new buys, each with a share
count, the reasons, and what it does to your taxes.

The rules are deliberately few and visible, and every plan is logged so the plan itself gets
a track record (see `plan_log` in db.py). They are not validated: the backtest found the
composite score has no reliable edge, so treat a "Sell" as "decide this week", not as a signal.

- Each position gets a size limit: the volatility-based maximum from the signal (a 1-sigma month
  costs at most 2% of the portfolio), never above 20%, 10% when volatility is unknown.
- Sell: the sell-watch flags add up to 4+ and the composite signal is -15 or worse.
- Trim to the limit: the position is more than 1.25x its limit.
- Trim by half: the flags add up to 3+ (the sell watch's "Review").
- Add (at most 4 points of weight per plan, never past the limit): no serious flag, signal +15
  or better, above the 200-day average, and the position is under 60% of its limit. Funded only
  by cash and this plan's sales.
- Buy a starter position (4%, or the limit if lower): watchlist names and sleepers you don't
  own with signal +25 or better, above the 200-day average and no serious flag, best first,
  while money is left.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .providers import market
from .pulse import Flag, sell_flags

MAX_WEIGHT = 0.20
DEFAULT_CAP = 0.10
STARTER_WEIGHT = 0.04
OVERSIZE = 1.25
SELL_FLAGS = 4
TRIM_FLAGS = 3
SELL_SCORE = -15
ADD_SCORE = 15
BUY_SCORE = 25
ADD_ROOM = 0.6
MIN_TRADE = 25.0          # dollars: smaller moves aren't worth a decision


@dataclass
class Lot:
    date: str
    quantity: float
    price: float


@dataclass
class Action:
    symbol: str
    action: str                  # Sell / Trim / Hold / Add / Buy
    shares: float
    price: float | None
    value: float
    current_weight: float        # fraction of the portfolio including cash
    target_weight: float
    limit: float
    reasons: list[str]
    tax: str | None = None
    score: float | None = None
    flags: list[dict] = field(default_factory=list)
    source: str = ""             # for buys: watchlist / sleeper


# ------------------------------------------------------------------ lots and taxes

def open_lots(transactions: list[dict], symbol: str) -> list[Lot]:
    """Shares still held, oldest first (sales use up the oldest lots, as brokers do by default)."""
    lots: list[Lot] = []
    for tx in sorted((t for t in transactions if t["symbol"].upper() == symbol),
                     key=lambda t: (t["date"], t.get("id", 0))):
        qty = float(tx["quantity"])
        if tx["side"] == "buy":
            lots.append(Lot(tx["date"], qty, float(tx["price"]) + float(tx.get("fees") or 0) / qty))
            continue
        while qty > 1e-9 and lots:
            take = min(qty, lots[0].quantity)
            lots[0].quantity -= take
            qty -= take
            if lots[0].quantity < 1e-9:
                lots.pop(0)
    return lots


def tax_note(lots: list[Lot], shares: float, price: float, today: date, crypto: bool) -> str | None:
    """What selling `shares` now would realize, oldest lots first (US rules: over a year is
    long-term)."""
    short = long_ = 0.0
    turns_long: str | None = None
    left = shares
    for lot in lots:
        if left <= 1e-9:
            break
        take = min(left, lot.quantity)
        gain = take * (price - lot.price)
        bought = date.fromisoformat(lot.date[:10])
        if (today - bought).days > 365:
            long_ += gain
        else:
            short += gain
            if gain > 0:
                later = (bought + timedelta(days=366)).isoformat()
                turns_long = max(turns_long or later, later)
        left -= take
    total = short + long_
    if abs(total) < 1:
        return None
    if total < 0:
        note = f"Realizes a loss of about ${-total:,.0f}, which offsets gains or up to $3,000 of income"
        if not crypto:
            note += "; buying it back within 30 days would cancel the deduction (wash sale)"
        return note
    if short > 1:
        part = "The whole gain" if long_ <= 1 else f"About ${short:,.0f} of the ${total:,.0f} gain"
        return (f"{part} is short-term (taxed as income{f', about ${short:,.0f}' if long_ <= 1 else ''})"
                + (f"; those shares turn long-term on {turns_long}" if turns_long else ""))
    return f"Long-term gain of about ${total:,.0f}"


# ------------------------------------------------------------------ rules

def size_limit(analysis: dict | None) -> float:
    sig = (analysis or {}).get("signal") or {}
    return min(sig.get("suggested_max_weight") or DEFAULT_CAP, MAX_WEIGHT)


def _shares(value: float, price: float) -> float:
    return round(value / price, 4) if price else 0.0


def plan_holding(pos: dict, analysis: dict | None, flags: list[Flag], base: float) -> Action:
    """pos: a valued position (symbol, quantity, price, market_value). base: portfolio value
    including cash."""
    sym, price, qty = pos["symbol"], pos.get("price"), pos["quantity"]
    w = (pos.get("market_value") or 0) / base if base else 0.0
    limit = size_limit(analysis)
    sig = (analysis or {}).get("signal") or {}
    ind = (analysis or {}).get("indicators") or {}
    score = sig.get("score")
    weight = sum(f.severity for f in flags)
    serious = any(f.severity >= 2 for f in flags)
    texts = [f.text for f in flags]

    def act(kind, target, reasons, shares=None):
        if shares is None:
            shares = _shares(max(w - target, 0) * base, price) if price else 0.0
        shares = min(shares, qty)
        return Action(sym, kind, shares, price, round(shares * (price or 0), 2), w, target, limit, reasons,
                      score=score, flags=[asdict(f) for f in flags])

    if not analysis or not price:
        return act("Hold", w, ["Couldn't fetch its data, so no change is suggested"], 0.0)
    if weight >= SELL_FLAGS and score is not None and score <= SELL_SCORE:
        return act("Sell", 0.0, texts + [f"Composite signal {score:+.0f}"], qty)
    if w > limit * OVERSIZE:
        return act("Trim", limit, [f"{w:.0%} of your portfolio; its volatility supports about {limit:.0%}"] + texts)
    if weight >= TRIM_FLAGS:
        return act("Trim", w / 2, ["Several warning signs at once: halve it until they clear"] + texts)
    if (not serious and score is not None and score >= ADD_SCORE and ind.get("above_sma200")
            and w < limit * ADD_ROOM):
        return act("Add", min(limit, w + STARTER_WEIGHT),
                   [f"Signal {score:+.0f}, above its 200-day average, and only {w:.1%} of the portfolio "
                    f"against a limit of {limit:.0%}"], 0.0)
    if w > limit:
        texts = [f"Above its {limit:.0%} limit, but not by enough to trim (trims start at {OVERSIZE}x)"] + texts
    return act("Hold", w, texts or ["No rule fired"], 0.0)


def buy_candidate(sym: str, analysis: dict | None, why: str) -> tuple[float, str] | None:
    """(score, reason) when a symbol you don't own qualifies as a new buy."""
    if not analysis or not analysis.get("quote"):
        return None
    sig = analysis.get("signal") or {}
    ind = analysis.get("indicators") or {}
    score = sig.get("score")
    flags = sell_flags(analysis, None, None, None)
    if score is None or score < BUY_SCORE or not ind.get("above_sma200") or any(f.severity >= 2 for f in flags):
        return None
    return score, f"{why}; signal {score:+.0f} ({sig.get('label', '').lower()}), above its 200-day average"


# ------------------------------------------------------------------ assembled

def build_plan(summary: dict, analyses: dict[str, dict | None], flags: dict[str, list[Flag]],
               transactions: list[dict], cash: float = 0.0,
               candidates: list[tuple[str, str]] | None = None, today: date | None = None) -> dict:
    """summary: service.portfolio_summary(...). candidates: [(symbol, why)] not currently held."""
    today = today or date.today()
    cash = max(cash, 0.0)
    positions = [p for p in summary.get("positions", []) if p.get("quantity")]
    base = (summary.get("total_value") or 0) + cash
    actions = [plan_holding(p, analyses.get(p["symbol"]), flags.get(p["symbol"], []), base) for p in positions]

    for a in actions:
        if a.action in ("Sell", "Trim"):
            crypto = market.asset_class(a.symbol) == "crypto"
            a.tax = tax_note(open_lots(transactions, a.symbol), a.shares, a.price or 0, today, crypto)
            if a.value < MIN_TRADE:
                a.action, a.shares, a.value, a.target_weight = "Hold", 0.0, 0.0, a.current_weight
                a.reasons.append("The trim would be under $25")

    budget = cash + sum(a.value for a in actions if a.action in ("Sell", "Trim"))
    for a in sorted((a for a in actions if a.action == "Add"), key=lambda a: -(a.score or 0)):
        spend = min((a.target_weight - a.current_weight) * base, budget)
        if spend < MIN_TRADE:
            a.action, a.target_weight = "Hold", a.current_weight
            a.reasons.append("Qualifies for adding, but there is no cash or sale proceeds to fund it")
            continue
        a.shares, a.value = _shares(spend, a.price or 0), round(spend, 2)
        a.target_weight = a.current_weight + spend / base
        budget -= spend

    held = {p["symbol"] for p in positions}
    buys: list[Action] = []
    ranked = []
    for sym, why in candidates or []:
        if sym in held:
            continue
        got = buy_candidate(sym, analyses.get(sym), why)
        if got:
            ranked.append((got[0], sym, got[1], why))
    for score, sym, reason, why in sorted(ranked, reverse=True):
        a = analyses[sym]
        price = a["quote"]["price"]
        limit = size_limit(a)
        spend = min(min(STARTER_WEIGHT, limit) * base, budget)
        if spend < MIN_TRADE or not price:
            break
        buys.append(Action(sym, "Buy", _shares(spend, price), price, round(spend, 2), 0.0, spend / base, limit,
                           [reason], score=score, source=why.split(":")[0].lower()))
        budget -= spend

    order = {"Sell": 0, "Trim": 1, "Add": 2, "Buy": 3, "Hold": 4}
    all_actions = sorted(actions + buys, key=lambda a: (order[a.action], -a.value))
    sold = sum(a.value for a in all_actions if a.action in ("Sell", "Trim"))
    bought = sum(a.value for a in all_actions if a.action in ("Add", "Buy"))
    return {
        "as_of": today.isoformat(),
        "actions": [asdict(a) for a in all_actions],
        "totals": {"invested": summary.get("total_value") or 0, "cash": cash, "base": base, "sell_value": sold,
                   "buy_value": bought, "cash_after": cash + sold - bought},
        "rules": {"max_weight": MAX_WEIGHT, "default_cap": DEFAULT_CAP, "starter_weight": STARTER_WEIGHT,
                  "oversize": OVERSIZE, "sell_flags": SELL_FLAGS, "trim_flags": TRIM_FLAGS,
                  "sell_score": SELL_SCORE, "add_score": ADD_SCORE, "buy_score": BUY_SCORE},
    }
