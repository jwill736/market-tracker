"""Taxes across all your accounts: wash sales, the short-to-long-term clock, and loss harvesting.

US rules, simplified, and not tax advice:
- A wash sale: selling at a loss and buying the same or a "substantially identical" security
  within 30 days before or after, in ANY of your accounts (the IRS also counts an IRA or a
  spouse's account; we only see what you've imported). The loss is disallowed and added to the
  replacement shares' cost. No single broker can see purchases at another broker, so Robinhood
  won't warn you about a Stash buy; this module does.
- What counts as substantially identical isn't fully defined. We treat the same ticker, share
  classes of one company (GOOG/GOOGL) and funds tracking the same index (VOO/SPY/IVV) as
  identical, which is the cautious reading.
- Crypto: the wash-sale rule applied to securities, and as of this writing US law did not
  extend it to crypto. That could change; the app notes it rather than assuming.
- Gains on shares held more than a year are long-term (lower rate). The clock is per lot.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .providers import market

WASH_DAYS = 30
ST_RATE = 0.24          # assumed federal rate on short-term gains (ordinary income); settable
LT_RATE = 0.15          # assumed long-term rate; settable
HARVEST_MIN_LOSS = 100.0
HARVEST_MIN_PCT = 5.0

# Groups treated as substantially identical (cautious). Tickers not listed stand alone.
IDENTICAL_GROUPS = [
    {"GOOG", "GOOGL"}, {"BRK-A", "BRK-B"}, {"FOX", "FOXA"}, {"NWS", "NWSA"}, {"UA", "UAA"},
    {"VOO", "SPY", "IVV", "SPLG", "SPYM", "RSP-NO"},              # S&P 500 funds
    {"VTI", "ITOT", "SCHB", "SPTM"},                               # CRSP/US total market (different indexes, but close)
    {"QQQ", "QQQM"},                                               # Nasdaq-100
    {"IWM", "VTWO"},                                               # Russell 2000
    {"VXUS", "IXUS"}, {"BND", "AGG"}, {"GLD", "IAU", "GLDM"}, {"IBIT", "FBTC", "BITB", "ARKB", "GBTC", "BTC"},
]
_GROUP = {t: frozenset(g) for g in IDENTICAL_GROUPS for t in g}

# A similar-but-not-identical fund to hold instead while a harvested loss's 30 days run, so you
# stay invested. Broad funds swap to a fund tracking a different index; single stocks swap to
# their sector fund (the most common way to keep the exposure).
REPLACEMENTS = {
    "VOO": "VTI", "SPY": "VTI", "IVV": "VTI", "SPLG": "SCHB", "VTI": "VOO", "ITOT": "VOO", "SCHB": "IVV",
    "QQQ": "VGT", "QQQM": "XLK", "IWM": "IJR", "VXUS": "VEA", "BND": "SCHZ", "AGG": "SCHZ",
    "ARKK": "QQQM", "SCHD": "VYM", "VYM": "SCHD", "VIG": "DGRO", "DGRO": "VIG",
}
SECTOR_FUNDS = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "ADBE", "CSCO", "INTC", "QCOM", "TXN", "MU", "AMAT",
            "NOW", "IBM", "PLTR", "ANET", "SMCI", "ARM", "DELL", "HPQ", "SNOW", "NET", "CRWD", "PANW", "SHOP"],
    "XLC": ["GOOG", "GOOGL", "META", "NFLX", "DIS", "T", "VZ", "TMUS", "SPOT", "RDDT", "SNAP", "PINS", "RBLX", "EA", "TTWO"],
    "XLY": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "BKNG", "CMG", "ABNB", "UBER", "LULU", "F", "GM", "RIVN",
            "GME", "DKS", "CVNA", "DASH"],
    "XLF": ["JPM", "BAC", "WFC", "GS", "MS", "C", "V", "MA", "AXP", "PYPL", "SCHW", "BLK", "HOOD", "SOFI", "COIN", "BRK-B",
            "AFRM", "UPST"],
    "XLV": ["UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "AMGN", "GILD", "MRNA", "CVS", "ISRG", "NVO", "HIMS"],
    "XLE": ["XOM", "CVX", "COP", "OXY", "SLB", "EOG", "PSX", "MPC", "KMI", "DVN"],
    "XLI": ["BA", "CAT", "GE", "HON", "UPS", "RTX", "LMT", "DE", "UNP", "FDX", "RKLB", "DY", "JOBY"],
    "XLP": ["PG", "KO", "PEP", "WMT", "COST", "PM", "MO", "CL", "KHC", "TGT"],
    "XLU": ["NEE", "DUK", "SO", "D", "AEP", "VST", "CEG"],
    "XLRE": ["PLD", "AMT", "O", "SPG", "EQIX", "CCI"],
    "XLB": ["LIN", "FCX", "NEM", "APD", "DOW", "NUE"],
}
_SECTOR = {t: etf for etf, ts in SECTOR_FUNDS.items() for t in ts}


def identical(a: str, b: str) -> bool:
    a, b = a.upper(), b.upper()
    return a == b or (a in _GROUP and b in _GROUP[a])


def group_of(sym: str) -> list[str]:
    sym = sym.upper()
    return sorted(_GROUP.get(sym, {sym}))


def is_crypto(sym: str) -> bool:
    return market.asset_class(sym) == "crypto"


def replacement(sym: str) -> tuple[str, str]:
    """(ticker, why) to hold while a harvested loss's 30 days run."""
    s = sym.upper()
    if s in REPLACEMENTS:
        return REPLACEMENTS[s], "a fund tracking a different index, so it isn't substantially identical"
    if s in _SECTOR:
        return _SECTOR[s], "its sector fund: similar exposure without being the same security"
    return "VTI", "a broad US market fund, to stay invested while you wait"


# ------------------------------------------------------------------ lots with accounts

@dataclass
class Lot:
    symbol: str
    date: str
    quantity: float
    cost: float                # per share, fees included
    account: str = ""
    tx: int = 0                # the purchase this lot came from


@dataclass
class Realized:
    symbol: str
    date: str
    quantity: float
    proceeds: float
    cost: float
    gain: float
    long_term: bool
    account: str = ""
    lot_tx: int = 0


def _tx_id(t: dict, i: int) -> int:
    return int(t["id"]) if t.get("id") is not None else -(i + 1)


def lots_and_sales(transactions: list[dict]) -> tuple[dict[str, list[Lot]], list[Realized]]:
    """Open lots per symbol and every realized sale, oldest lots first (FIFO within an account,
    falling back to any account; brokers use FIFO by default)."""
    lots: dict[str, list[Lot]] = {}
    sales: list[Realized] = []
    indexed = [(t, _tx_id(t, i)) for i, t in enumerate(transactions)]
    for tx, tid in sorted(indexed, key=lambda p: (p[0]["date"], p[0].get("id", 0))):
        sym = tx["symbol"].upper()
        qty = float(tx["quantity"])
        acct = tx.get("account") or ""
        fees = float(tx.get("fees") or 0)
        if tx["side"] == "buy":
            lots.setdefault(sym, []).append(Lot(sym, tx["date"][:10], qty, float(tx["price"]) + fees / qty, acct, tid))
            continue
        price = float(tx["price"]) - fees / qty
        held = lots.get(sym, [])
        order = [lt for lt in held if lt.account == acct] + [lt for lt in held if lt.account != acct]
        left = qty
        for lot in order:
            if left <= 1e-9:
                break
            take = min(left, lot.quantity)
            if take <= 0:
                continue
            lot.quantity -= take
            left -= take
            long_term = (date.fromisoformat(tx["date"][:10]) - date.fromisoformat(lot.date)).days > 365
            sales.append(Realized(sym, tx["date"][:10], take, take * price, take * lot.cost,
                                  take * (price - lot.cost), long_term, acct, lot.tx))
        lots[sym] = [lt for lt in held if lt.quantity > 1e-9]
    return lots, sales


# ------------------------------------------------------------------ wash sales

@dataclass
class WashSale:
    symbol: str
    sold: str
    loss: float
    replacement_symbol: str
    replacement_date: str
    replacement_account: str
    sold_account: str
    disallowed: float
    note: str


def wash_sales(transactions: list[dict]) -> list[WashSale]:
    """Loss sales with a purchase of the same (or an identical) security within 30 days either
    side, in any account, other than the purchase of the shares that were sold. The disallowed
    loss is capped by the shares bought back."""
    _, sales = lots_and_sales(transactions)
    buys = [(t, _tx_id(t, i)) for i, t in enumerate(transactions) if t["side"] == "buy"]
    sold_lots = {s.lot_tx for s in sales}
    out = []
    used: set[int] = set()
    for s in sales:
        if s.gain >= 0 or is_crypto(s.symbol):
            continue
        d = date.fromisoformat(s.date)
        for b, tid in buys:
            bd = date.fromisoformat(b["date"][:10])
            if tid == s.lot_tx or tid in used or not identical(b["symbol"], s.symbol) or abs((bd - d).days) > WASH_DAYS:
                continue
            if bd < d and tid in sold_lots and not any(x.lot_tx == tid and x.date > s.date for x in sales):
                continue      # an earlier purchase that was itself sold before this sale isn't a replacement
            used.add(tid)
            share = min(1.0, float(b["quantity"]) / s.quantity) if s.quantity else 1.0
            other = b.get("account") and b.get("account") != s.account
            out.append(WashSale(s.symbol, s.date, round(-s.gain, 2), b["symbol"].upper(), b["date"][:10],
                                b.get("account") or "", s.account, round(-s.gain * share, 2),
                                f"Bought {b['symbol'].upper()} {'before' if bd < d else 'after'} selling at a loss"
                                + (f", in {b.get('account')}" if other else "")
                                + ": the loss moves into the new shares' cost instead of counting this year"))
            break
    return out


def blackout(transactions: list[dict], today: date) -> list[dict]:
    """Symbols you sold at a loss in the last 30 days: buying them (or an identical fund) before
    the date shown would wash the loss."""
    _, sales = lots_and_sales(transactions)
    out: dict[str, dict] = {}
    for s in sales:
        if s.gain >= 0 or is_crypto(s.symbol):
            continue
        until = date.fromisoformat(s.date) + timedelta(days=WASH_DAYS + 1)
        if until <= today:
            continue
        cur = out.get(s.symbol)
        if not cur or cur["until"] < until.isoformat():
            out[s.symbol] = {"symbol": s.symbol, "avoid": group_of(s.symbol), "sold": s.date, "until": until.isoformat(),
                             "loss": round(-s.gain + (cur["loss"] if cur else 0), 2)}
    return sorted(out.values(), key=lambda x: x["until"])


def recent_buys(transactions: list[dict], symbol: str, today: date) -> list[tuple[dict, int]]:
    """Purchases of the symbol or an identical one in the last 30 days, any account."""
    since = (today - timedelta(days=WASH_DAYS)).isoformat()
    return [(t, _tx_id(t, i)) for i, t in enumerate(transactions)
            if t["side"] == "buy" and identical(t["symbol"], symbol) and t["date"][:10] >= since]


# ------------------------------------------------------------------ the clock and harvesting

def rates(st: float | None = None, lt: float | None = None) -> tuple[float, float]:
    return (ST_RATE if st is None else st), (LT_RATE if lt is None else lt)


@dataclass
class LotView:
    symbol: str
    account: str
    bought: str
    quantity: float
    cost: float
    price: float
    gain: float
    gain_pct: float
    long_term: bool
    long_term_on: str | None
    days_to_long_term: int | None
    tax_if_sold_now: float
    tax_if_sold_long_term: float | None
    tx: int = 0


def lot_views(lots: dict[str, list[Lot]], prices: dict[str, float], today: date,
              st_rate: float = ST_RATE, lt_rate: float = LT_RATE) -> list[LotView]:
    out = []
    for sym, ls in lots.items():
        price = prices.get(sym)
        if price is None:
            continue
        for lt in ls:
            bought = date.fromisoformat(lt.date)
            long_on = bought + timedelta(days=366)
            is_long = today >= long_on
            gain = (price - lt.cost) * lt.quantity
            tax_now = gain * (lt_rate if is_long else st_rate)
            out.append(LotView(sym, lt.account, lt.date, lt.quantity, lt.cost, price, round(gain, 2),
                               round((price / lt.cost - 1) * 100, 2) if lt.cost else 0.0, is_long,
                               None if is_long else long_on.isoformat(), None if is_long else (long_on - today).days,
                               round(tax_now, 2), None if is_long else round(gain * lt_rate, 2) if gain > 0 else None,
                               lt.tx))
    return out


def tax_clock(views: list[LotView], within_days: int = 90, min_saving: float = 25.0) -> list[dict]:
    """Short-term lots with a gain that turn long-term soon: waiting saves the difference."""
    rows = []
    for v in views:
        if v.long_term or v.gain <= 0 or v.days_to_long_term is None or v.days_to_long_term > within_days:
            continue
        saving = v.tax_if_sold_now - (v.tax_if_sold_long_term or 0)
        if saving < min_saving:
            continue
        rows.append({"symbol": v.symbol, "account": v.account, "bought": v.bought, "quantity": v.quantity,
                     "gain": v.gain, "long_term_on": v.long_term_on, "days": v.days_to_long_term,
                     "saving": round(saving, 2)})
    return sorted(rows, key=lambda r: r["days"])


@dataclass
class Harvest:
    symbol: str
    account: str
    quantity: float
    loss: float
    loss_pct: float
    long_term: bool
    tax_saved: float
    replacement: str
    replacement_why: str
    blocked_by: list[dict] = field(default_factory=list)   # buys in the last 30 days that would wash it
    note: str = ""


def harvest_candidates(views: list[LotView], transactions: list[dict], today: date,
                       st_rate: float = ST_RATE, lt_rate: float = LT_RATE,
                       min_loss: float = HARVEST_MIN_LOSS, min_pct: float = HARVEST_MIN_PCT) -> list[Harvest]:
    """Lots at a loss big enough to be worth selling for the deduction, with a replacement to
    hold for 31 days. Recent purchases of the same thing (any account) are listed as blockers:
    selling now would wash the loss on those shares."""
    by_sym: dict[tuple, list[LotView]] = {}
    for v in views:
        if v.gain < 0:
            by_sym.setdefault((v.symbol, v.account), []).append(v)
    out = []
    for (sym, acct), vs in by_sym.items():
        loss = -sum(v.gain for v in vs)
        cost = sum(v.cost * v.quantity for v in vs)
        pct = loss / cost * 100 if cost else 0.0
        if loss < min_loss or pct < min_pct:
            continue
        st_loss = -sum(v.gain for v in vs if not v.long_term)
        saved = st_loss * st_rate + (loss - st_loss) * lt_rate
        rep, why = replacement(sym)
        crypto = is_crypto(sym)
        selling = {v.tx for v in vs}
        blockers = [] if crypto else [{"date": t["date"][:10], "symbol": t["symbol"].upper(), "account": t.get("account") or "",
                                       "quantity": float(t["quantity"])}
                                      for t, tid in recent_buys(transactions, sym, today) if tid not in selling]
        note = ("Crypto: the wash-sale rule hasn't applied to crypto, so you could buy it straight back; check current rules"
                if crypto else f"Don't buy {', '.join(group_of(sym))} in any account for 31 days after selling")
        out.append(Harvest(sym, acct, round(sum(v.quantity for v in vs), 6), round(loss, 2), round(pct, 1),
                           all(v.long_term for v in vs), round(saved, 2), rep if not crypto else sym, why if not crypto else
                           "no wash-sale wait for crypto (check current rules)", blockers, note))
    return sorted(out, key=lambda h: -h.tax_saved)


def summary(transactions: list[dict], prices: dict[str, float], today: date,
            st_rate: float = ST_RATE, lt_rate: float = LT_RATE) -> dict:
    lots, sales = lots_and_sales(transactions)
    views = lot_views(lots, prices, today, st_rate, lt_rate)
    year = str(today.year)
    realized = [s for s in sales if s.date.startswith(year)]
    washes = wash_sales(transactions)
    disallowed = sum(w.disallowed for w in washes if w.sold.startswith(year))
    st = sum(s.gain for s in realized if not s.long_term)
    lt = sum(s.gain for s in realized if s.long_term)
    return {
        "as_of": today.isoformat(), "rates": {"short_term": st_rate, "long_term": lt_rate},
        "realized": {"short_term": round(st, 2), "long_term": round(lt, 2), "wash_disallowed": round(disallowed, 2),
                     "net": round(st + lt + disallowed, 2)},
        "wash_sales": [asdict(w) for w in washes],
        "blackout": blackout(transactions, today),
        "clock": tax_clock(views),
        "harvest": [asdict(h) for h in harvest_candidates(views, transactions, today, st_rate, lt_rate)],
        "lots": [asdict(v) for v in sorted(views, key=lambda v: (v.symbol, v.bought))],
    }
