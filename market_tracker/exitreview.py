"""Exit review: what it would cost to swap a weak holding for VOO, and what it must do to be worth keeping.

A warning ("in the screen's bottom 50", "the reason you bought is gone") only helps if it turns into
a decision, and for a buy-and-hold investor the real price of selling is often the tax. So for each
flagged holding, from your own tax lots:
- the tax on selling all of it today (a loss shows as tax saved), at your rates;
- lots turning long-term within 90 days, and what waiting for them saves;
- how much a year the stock would have to beat VOO, over 3 and 5 years, to leave you better off
  than paying the tax now and holding VOO. Below that, keeping it is a bet that it will.
Estimates, not tax advice: federal rates only, and you still owe tax on the kept shares when you
eventually sell them (this ignores that, which favours keeping slightly).
"""

from __future__ import annotations

from datetime import date

from . import taxes

HORIZONS = (3, 5)
SOON_DAYS = 90


def review(symbol: str, lots: list[taxes.Lot], price: float, today: date, st_rate: float = taxes.ST_RATE,
           lt_rate: float = taxes.LT_RATE, reasons: list[str] | None = None) -> dict | None:
    views = taxes.lot_views({symbol: lots}, {symbol: price}, today, st_rate, lt_rate)
    if not views:
        return None
    qty = sum(v.quantity for v in views)
    value = qty * price
    gain = sum(v.gain for v in views)
    tax_now = sum(v.tax_if_sold_now for v in views)
    soon = [v for v in views if not v.long_term and v.days_to_long_term is not None and v.days_to_long_term <= SOON_DAYS and v.gain > 0]
    wait_saves = sum(v.tax_if_sold_now - (v.tax_if_sold_long_term or 0) for v in soon)
    need = {}
    if tax_now > 0 and value > tax_now:
        for n in HORIZONS:
            need[f"{n}y"] = round(((value / (value - tax_now)) ** (1 / n) - 1) * 100, 2)
    out = {"symbol": symbol, "quantity": round(qty, 6), "price": price, "value": round(value, 2), "gain": round(gain, 2),
           "tax_now": round(tax_now, 2), "need_edge_pct_per_year": need, "reasons": reasons or [],
           "long_term_soon": [{"shares": v.quantity, "on": v.long_term_on, "saves": round(v.tax_if_sold_now - (v.tax_if_sold_long_term or 0), 2)}
                              for v in soon],
           "wait_saves": round(wait_saves, 2)}
    out["text"] = _text(out)
    return out


def _text(r: dict) -> str:
    v, t = r["value"], r["tax_now"]
    if t <= 0:
        line = (f"Selling all ${v:,.0f} of {r['symbol']} realizes a ${-r['gain']:,.0f} loss, about ${-t:,.0f} of tax saved: "
                "switching to VOO costs nothing in tax and the loss offsets other gains (VOO isn't a wash-sale match for a single stock).")
    else:
        need = r["need_edge_pct_per_year"]
        line = (f"Selling all ${v:,.0f} of {r['symbol']} costs about ${t:,.0f} in tax now. To be worth keeping instead of VOO it has to beat VOO "
                f"by about {need.get('3y', 0):.1f}% a year over 3 years ({need.get('5y', 0):.1f}% over 5).")
    if r["wait_saves"] >= 25:
        first = min(x["on"] for x in r["long_term_soon"])
        line += f" Waiting until {first} for lots to turn long-term would save about ${r['wait_saves']:,.0f}."
    return line


def for_holdings(flagged: dict[str, list[str]], transactions: list[dict], prices: dict[str, float], today: date,
                 st_rate: float = taxes.ST_RATE, lt_rate: float = taxes.LT_RATE) -> list[dict]:
    """flagged: {symbol: [reasons]}. Reviews for the ones you hold, biggest tax-free switch first."""
    lots, _ = taxes.lots_and_sales(transactions)
    out = []
    for sym, why in flagged.items():
        if sym in lots and prices.get(sym):
            r = review(sym, lots[sym], prices[sym], today, st_rate, lt_rate, why)
            if r and r["value"] > 0:
                out.append(r)
    return sorted(out, key=lambda r: (r["tax_now"] > 0, -r["value"]))
