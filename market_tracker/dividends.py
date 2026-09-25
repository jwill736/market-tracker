"""Dividends and income: what you've been paid, what's coming, and the yield on what you paid.

- Received: dividend rows from your Robinhood CSV (and account sync), exactly as paid. For an
  account with no dividend rows for a stock (Stash, holdings typed in by hand) it's estimated:
  each ex-dividend date in the stock's history times the shares that account held the day
  before, marked "estimated".
- Forward income: the latest regular dividend times how many the company pays a year (a
  one-off special dividend isn't counted as if it would repeat), times your shares.
- Yield on cost: forward income over what you paid. Current yield: over today's value.
- Upcoming: the next ex-dividend date (own shares the day before it to get paid) and pay
  date. Nasdaq has the declared dates for Nasdaq-listed stocks; for the rest the next date is
  projected from the usual spacing and marked "estimated".
- The next 12 months, month by month, from the same projections.

Dividend history comes from Yahoo's chart data (ex-dates and amounts per share).
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from statistics import median

from . import http
from .providers import market
from .reading import BROWSER_UA

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
NASDAQ_DIVIDENDS = "https://api.nasdaq.com/api/quote/{sym}/dividends?assetclass={cls}"
NASDAQ_HEADERS = {"User-Agent": BROWSER_UA, "Accept": "application/json, text/plain, */*"}
SPECIAL_MULTIPLE = 2.0      # a payment over 2x the usual is treated as a one-off


# ------------------------------------------------------------------ sources

def parse_yahoo_dividends(result: dict) -> list[tuple[str, float]]:
    """[(ex-date, amount per share)] oldest first, from a Yahoo chart result with events=div."""
    divs = ((result or {}).get("events") or {}).get("dividends") or {}
    out = []
    for d in divs.values():
        try:
            out.append((datetime.fromtimestamp(int(d["date"]), tz=timezone.utc).date().isoformat(), float(d["amount"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(set(out))


def history(sym: str, get=None, years: int = 3) -> list[tuple[str, float]]:
    get = get or http.get
    now = int(datetime.now(timezone.utc).timestamp())
    data = get(YAHOO_CHART.format(symbol=sym), params={"period1": now - years * 366 * 86400, "period2": now,
                                                       "interval": "1mo", "events": "div"}, ttl=12 * 3600)
    try:
        result = data["chart"]["result"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise http.DataUnavailable(f"Yahoo returned no dividend data for {sym}") from exc
    return parse_yahoo_dividends(result)


def _us_date(text: str | None) -> str | None:
    try:
        return datetime.strptime((text or "").strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def parse_nasdaq(data: dict) -> dict | None:
    """{"ex_date", "pay_date", "amount"} of the latest declared dividend, or None."""
    d = (data or {}).get("data") or {}
    rows = ((d.get("dividends") or {}).get("rows")) or []
    for r in rows:
        ex = _us_date(r.get("exOrEffDate"))
        if not ex or (r.get("type") or "Cash") != "Cash":
            continue
        try:
            amount = float(str(r.get("amount") or "").replace("$", "").replace(",", ""))
        except ValueError:
            amount = None
        return {"ex_date": ex, "pay_date": _us_date(r.get("paymentDate")), "amount": amount}
    return None


def declared(sym: str, get=None) -> dict | None:
    """Nasdaq's latest declared dividend (Nasdaq-listed stocks and funds only)."""
    get = get or http.get
    for cls in ("stocks", "etf"):
        try:
            got = parse_nasdaq(get(NASDAQ_DIVIDENDS.format(sym=sym, cls=cls), headers=NASDAQ_HEADERS, ttl=12 * 3600))
        except (http.DataUnavailable, KeyError, TypeError):
            got = None
        if got:
            return got
    return None


# ------------------------------------------------------------------ the maths

def cadence(hist: list[tuple[str, float]], today: date) -> dict | None:
    """Regular amount, payments a year and typical spacing, from the last ~13 months."""
    recent = [(d, a) for d, a in hist if (today - date.fromisoformat(d)).days <= 400]
    if not recent:
        return None
    amounts = [a for _, a in recent]
    usual = median(amounts)
    regular = [(d, a) for d, a in recent if a <= usual * SPECIAL_MULTIPLE]
    last_day, last_amount = regular[-1]
    days = [(date.fromisoformat(b[0]) - date.fromisoformat(a[0])).days for a, b in zip(regular, regular[1:])]
    gap = median(days) if days else 365
    per_year = max(1, min(12, round(365 / gap))) if days else 1
    return {"amount": last_amount, "per_year": per_year, "gap_days": int(gap), "last_ex": last_day}


def project(c: dict, today: date, horizon_days: int = 365) -> list[str]:
    """Projected ex-dates from `today` over the horizon."""
    out = []
    d = date.fromisoformat(c["last_ex"]) + timedelta(days=c["gap_days"])
    while d <= today + timedelta(days=horizon_days):
        if d >= today:
            out.append(d.isoformat())
        d += timedelta(days=c["gap_days"])
    return out


def shares_on(transactions: list[dict], sym: str, day: str, account: str | None = None) -> float:
    """Shares held at the close of the day before `day` (who gets an ex-date dividend)."""
    q = 0.0
    for t in transactions:
        if t["symbol"] != sym or t["date"] >= day or (account is not None and (t.get("account") or "") != account):
            continue
        q += t["quantity"] if t["side"] == "buy" else -t["quantity"]
    return max(q, 0.0)


def estimate_received(transactions: list[dict], hist: list[tuple[str, float]], sym: str, account: str,
                      since: str, until: str) -> list[dict]:
    out = []
    for ex, amount in hist:
        if since <= ex <= until:
            n = shares_on(transactions, sym, ex, account)
            if n > 0:
                out.append({"symbol": sym, "day": ex, "amount": round(n * amount, 2), "account": account,
                            "kind": "dividend", "estimated": True, "per_share": amount, "shares": n})
    return out


def build(positions: list[dict], transactions: list[dict], income_rows: list[dict], today: date, get=None) -> dict:
    """positions: valued positions (symbol, quantity, market_value, cost_basis)."""
    errors: list[str] = []
    held = [p for p in positions if p.get("quantity") and market.asset_class(p["symbol"]) == "stock"]
    symbols = sorted({p["symbol"] for p in held} | {r["symbol"] for r in income_rows if r.get("symbol")})

    def one(sym):
        try:
            return sym, history(sym, get), declared(sym, get)
        except (http.DataUnavailable, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{sym}: {str(exc)[:80]}")
            return sym, [], None
    with ThreadPoolExecutor(max_workers=4) as pool:
        got = {s: (h, d) for s, h, d in pool.map(one, symbols)}

    since_12m = (today - timedelta(days=365)).isoformat()
    ytd = f"{today.year}-01-01"
    recorded = [dict(r, estimated=False) for r in income_rows]
    have = {(r["symbol"], r.get("account") or "") for r in income_rows if r.get("kind") in ("dividend", "reinvested")}
    accounts = defaultdict(set)
    for t in transactions:
        accounts[t["symbol"]].add(t.get("account") or "")
    estimated = []
    for sym in symbols:
        hist = got.get(sym, ([], None))[0]
        for acct in accounts.get(sym, set()):
            if (sym, acct) not in have:
                estimated += estimate_received(transactions, hist, sym, acct, f"{today.year - 1}-01-01", today.isoformat())
    received = sorted(recorded + estimated, key=lambda r: r["day"], reverse=True)

    def total(rows, since):
        return round(sum(r["amount"] for r in rows if r["day"] >= since), 2)

    rows, upcoming = [], []
    # This month through the month a year from today: the same window the projections cover.
    monthly: dict[str, float] = {}
    m = date(today.year, today.month, 1)
    while m <= today + timedelta(days=365):
        monthly[m.strftime("%Y-%m")] = 0.0
        m = (m + timedelta(days=32)).replace(day=1)
    for p in held:
        sym = p["symbol"]
        hist, decl = got.get(sym, ([], None))
        c = cadence(hist, today)
        paid_12m = total([r for r in received if r["symbol"] == sym], since_12m)
        if not c:
            if paid_12m:
                rows.append({"symbol": sym, "annual_income": 0.0, "paid_12m": paid_12m, "note": "No regular dividend in the last year"})
            continue
        forward = c["amount"] * c["per_year"] * p["quantity"]
        cost = p.get("cost_basis") or 0.0
        value = p.get("market_value") or 0.0
        dates = project(c, today)
        nxt = None
        if decl and decl["ex_date"] >= today.isoformat():
            nxt = {"ex_date": decl["ex_date"], "pay_date": decl["pay_date"], "per_share": decl["amount"] or c["amount"], "estimated": False}
            # The declared date replaces the projection it stands for.
            cut = (date.fromisoformat(decl["ex_date"]) + timedelta(days=c["gap_days"] // 2)).isoformat()
            dates = [decl["ex_date"]] + [d for d in dates if d > cut]
        elif dates:
            nxt = {"ex_date": dates[0], "pay_date": None, "per_share": c["amount"], "estimated": True}
        if nxt:
            upcoming.append({"symbol": sym, **nxt, "amount": round(nxt["per_share"] * p["quantity"], 2)})
        for ex in dates:
            if ex[:7] in monthly:
                monthly[ex[:7]] += c["amount"] * p["quantity"]
        rows.append({"symbol": sym, "per_share": c["amount"], "per_year": c["per_year"], "annual_income": round(forward, 2),
                     "yield_on_cost": round(forward / cost * 100, 2) if cost > 0 else None,
                     "current_yield": round(forward / value * 100, 2) if value > 0 else None,
                     "paid_12m": paid_12m, "next": nxt})
    rows.sort(key=lambda r: -r["annual_income"])
    annual = round(sum(r["annual_income"] for r in rows), 2)
    cost_all = sum(p.get("cost_basis") or 0 for p in held)
    value_all = sum(p.get("market_value") or 0 for p in positions if p.get("quantity"))
    return {
        "as_of": today.isoformat(),
        "received_12m": total(received, since_12m), "received_ytd": total(received, ytd),
        "estimated_share": round(sum(r["amount"] for r in estimated if r["day"] >= since_12m) / max(total(received, since_12m), 0.01), 2)
        if estimated else 0.0,
        "annual_income": annual, "monthly_avg": round(annual / 12, 2),
        "yield_on_cost": round(annual / cost_all * 100, 2) if cost_all else None,
        "portfolio_yield": round(annual / value_all * 100, 2) if value_all else None,
        "holdings": rows, "upcoming": sorted(upcoming, key=lambda u: u["ex_date"])[:20],
        "months": [{"month": k, "amount": round(v, 2)} for k, v in monthly.items()],
        "received": received[:60], "errors": errors,
    }
