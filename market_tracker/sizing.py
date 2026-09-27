"""How much to put into an idea, in dollars.

Which stock you pick matters less to how your portfolio turns out than how much you put in it.
The limits:
- one stock from the core screens (quality/value/momentum, backlog, government contracts, your
  own idea): at most 5% of the portfolio;
- one speculative name (sleepers, chatter, insider buys, lagging suppliers, raised guidance,
  spin-offs): at most 2%, and all of them together inside the 10% speculative limit;
- a stock that typically moves more than 60% a year (annualized volatility): half those limits;
- one sector: at most 25% of the portfolio across the stocks you hold in it (funds don't count
  toward a sector);
- what you already own of it counts toward its limit.
The answer is the smallest of those, with the one that bound it named. Cash you have on record
is used first; anything beyond it would be new money.

Chatter names get a 48-hour wait: the first time you size one, the clock starts, and the amount
is only shown once it has run. Attention-driven buying is how individual investors tend to buy
at the top (Barber & Odean 2008); two days is enough for most of the heat to show itself.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

CORE_MAX = 0.05
SPEC_MAX = 0.02
SECTOR_MAX = 0.25
HIGH_VOL = 0.60
COOL_HOURS = 48
CORE = {"qvm", "backlog", "contract", "manual"}
SPECULATIVE = {"sleeper", "chatter", "insider", "supplier", "pead", "spinoff"}


def annual_vol(closes: list[float]) -> float | None:
    if len(closes) < 60:
        return None
    rets = [math.log(b / a) for a, b in zip(closes[-253:-1], closes[-252:]) if a > 0 and b > 0]
    if len(rets) < 50:
        return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1) * 252)


def size(symbol: str, source: str, price: float, positions: list[dict], cash: float, sector_of, spec_symbols: set[str],
         vol: float | None = None, cooling_started: datetime | None = None, now: datetime | None = None) -> dict:
    """positions: [{symbol, market_value}]; sector_of(symbol) -> sector or None (funds, coins);
    spec_symbols: what you hold that came from the speculative lists."""
    now = now or datetime.now(timezone.utc)
    invested = sum(p.get("market_value") or 0 for p in positions)
    total = invested + max(cash, 0)
    held = sum(p.get("market_value") or 0 for p in positions if p["symbol"] == symbol)
    speculative = source in SPECULATIVE
    lines = []
    if total <= 0:
        return {"symbol": symbol, "amount": 0.0, "shares": 0, "binding": "no portfolio",
                "lines": ["Add your holdings or cash first: limits are a share of your portfolio."], "locked_until": None}
    per_name = SPEC_MAX if speculative else CORE_MAX
    if vol is not None and vol > HIGH_VOL:
        per_name /= 2
        lines.append(f"It moves about {vol:.0%} a year, so the limit is halved.")
    limits = {f"{per_name:.1%} of your portfolio for one {'speculative' if speculative else 'core'} idea": per_name * total - held}
    if held:
        lines.append(f"You already own ${held:,.0f} of it.")
    if speculative:
        used = sum(p.get("market_value") or 0 for p in positions if p["symbol"] in spec_symbols)
        limits[f"the 10% speculative limit (${used:,.0f} used)"] = 0.10 * total - used
    sector = sector_of(symbol)
    if sector:
        in_sector = sum(p.get("market_value") or 0 for p in positions if sector_of(p["symbol"]) == sector)
        limits[f"25% in one sector ({sector}: ${in_sector:,.0f} now)"] = SECTOR_MAX * total - in_sector
    binding, room = min(limits.items(), key=lambda kv: kv[1])
    amount = round(max(room, 0.0), 2)
    shares = math.floor(amount / price) if price > 0 else 0
    from_cash = min(amount, max(cash, 0))
    if amount <= 0:
        lines.insert(0, f"Nothing more: you're at {binding}.")
    else:
        lines.insert(0, f"Up to ${amount:,.0f} ({amount / total:.1%} of ${total:,.0f}), set by {binding}.")
        lines.append(f"${from_cash:,.0f} from your cash on record" + (f", ${amount - from_cash:,.0f} would be new money." if amount > from_cash else "."))
        if shares == 0:
            lines.append("Less than one share: buy a fractional amount if your broker allows it.")
    locked = None
    if source == "chatter":
        start = cooling_started or now
        until = start + timedelta(hours=COOL_HOURS)
        if now < until:
            locked = until.isoformat(timespec="minutes")
            lines = [ln for ln in lines if not ln.startswith(("Up to", "$", "Less than"))]
            lines.insert(0, f"Chatter name: the amount shows after {until:%a %b %d, %H:%M} UTC. If it still looks good then, size it again.")
    return {"symbol": symbol, "source": source, "price": price, "amount": amount if not locked else None, "shares": shares if not locked else None,
            "binding": binding, "lines": lines, "locked_until": locked, "portfolio": round(total, 2)}
