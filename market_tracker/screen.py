"""What to buy: a quality, value and momentum screen of every US-listed company, and the
order-backlog screen, rebuilt weekly by a GitHub job (`mt screen`) into screen.json on the
journal-data branch.

The evidence (rated in README): what has held up best after publication is
- quality: operating profit over total assets (Ball, Gerakos, Linnainmaa & Nikolaev 2016 found it
  a better measure than Novy-Marx's gross profit, and far more companies tag it in SEC data);
  banks and insurers are compared on return on equity instead, and companies that report no
  operating profit (miners, some conglomerates) on net income over assets;
- value: earnings yield and book value against the market value;
- momentum: the last 12 months' return, skipping the most recent month (Jegadeesh & Titman);
- not diluting shareholders: companies issuing lots of new shares tend to lag (Pontiff & Woodgate).
Each is ranked within the company's own sector (a bank's balance sheet isn't a chip maker's),
averaged, and any single very bad score (bottom 10% of its sector) caps the total at 50, the
way Seeking Alpha's quant ratings rule out a stock with one fatal flaw.

Backlog: companies must report "remaining performance obligations", contracted revenue not yet
delivered. The screen looks for backlog growing faster than revenue, and only when the stock
isn't among the most expensive: research found investors over-pay for big backlogs on their own
(Rajgopal, Shevlin & Venkatachalam 2003).

Published signals lose about half their edge once known (McLean & Pontiff 2016). Every top name
goes into the idea log and is scored against VOO, so this screen has to earn its place.

Data: SEC XBRL "frames" (one request returns one number for every filer), Nasdaq's list of
listed stocks (price, market value, sector), and a year of Yahoo daily prices per company.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from . import http

FRAMES = "https://data.sec.gov/api/xbrl/frames/{tax}/{tag}/{unit}/{period}.json"
NASDAQ_LIST = "https://api.nasdaq.com/api/screener/stocks"
FILE = "screen.json"
MIN_CAP = 3e8               # $300M: below this, prices are too easy to push around
SLEEPER_MAX_CAP = 1e10
DISQUALIFY = 10             # a component in the bottom 10% of its sector caps the total
REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]
NET_INCOME = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]
SHARES_Q = "WeightedAverageNumberOfDilutedSharesOutstanding"
BOTTOM_N = 50
MIN_RPO_BASE = 0.10         # last year's backlog must be at least 10% of revenue, or growth "from nothing" dominates


# ------------------------------------------------------------------ sources

ENTITY_NAMES: dict[int, str] = {}      # cik -> the name the filer used, from every frame read


def frame(tag: str, period: str, unit: str = "USD", tax: str = "us-gaap", get=None) -> dict[int, float]:
    """{cik: value} for every filer (a missing frame is an empty dict)."""
    from .providers import sec
    try:
        d = (get or (lambda u: sec._sec_get(u, ttl=0)))(FRAMES.format(tax=tax, tag=tag, unit=unit, period=period))
    except http.DataUnavailable:
        return {}
    out = {}
    for r in d.get("data", []):
        if "val" in r:
            out[int(r["cik"])] = float(r["val"])
            if r.get("entityName"):
                ENTITY_NAMES.setdefault(int(r["cik"]), r["entityName"])
    return out


def frame_any(tags: list[str], period: str, get=None) -> dict[int, float]:
    out: dict[int, float] = {}
    for t in tags:
        for cik, v in frame(t, period, get=get).items():
            out.setdefault(cik, v)
    return out


def latest_quarter(today: date, get=None) -> tuple[int, int]:
    """The latest calendar quarter most companies have reported (balance sheets: 4,000+ filers)."""
    y, q = today.year, (today.month - 1) // 3 + 1
    for _ in range(6):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        if len(frame("Assets", f"CY{y}Q{q}I", get=get)) >= 4000:
            return y, q
    raise http.DataUnavailable("no recent quarter with enough SEC balance sheets")


def listed(get=None) -> dict[str, dict]:
    """{symbol: {name, price, cap, sector, industry}} for US-listed common stocks."""
    from .reading import BROWSER_UA
    d = (get or (lambda: http.get(NASDAQ_LIST, params={"tableonly": "true", "limit": "10000", "download": "true"}, ttl=0,
                                  headers={"User-Agent": BROWSER_UA, "Accept": "application/json, text/plain, */*"})))()
    out = {}
    for r in ((d.get("data") or {}).get("rows") or []):
        sym = (r.get("symbol") or "").strip().upper().replace("/", "-").replace("^", "-P")
        try:
            price = float(str(r.get("lastsale") or "").replace("$", "").replace(",", ""))
            cap = float(r.get("marketCap") or 0)
        except ValueError:
            continue
        if sym and price > 0 and cap > 0:
            out[sym] = {"name": r.get("name", ""), "price": price, "cap": cap, "sector": r.get("sector") or "Other",
                        "industry": r.get("industry") or ""}
    return out


# ------------------------------------------------------------------ scoring

def pct_ranks(values: dict[str, float], higher_better: bool = True) -> dict[str, float]:
    """Percentile 0-100 of each value within the group (ties share a rank)."""
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    out = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        p = ((i + j) / 2) / (n - 1) * 100 if n > 1 else 50.0
        for k in range(i, j + 1):
            out[items[k][0]] = p if higher_better else 100 - p
        i = j + 1
    return out


def momentum(closes: list[float]) -> tuple[float | None, float | None, float | None]:
    """(12-1 month return, 12-month return, % above the 200-day average)."""
    if len(closes) < 230:
        return None, None, None
    r121 = closes[-22] / closes[-min(len(closes), 253)] - 1
    r12 = closes[-1] / closes[-min(len(closes), 253)] - 1
    ma = sum(closes[-200:]) / 200
    return r121, r12, closes[-1] / ma - 1


FINANCE = "Finance"          # Nasdaq's sector name for banks, insurers, brokers and REITs


def quality_of(f: dict, sector: str | None) -> tuple[float | None, str | None]:
    """(quality, basis). Banks and insurers report no operating income and carry huge balance sheets,
    so the finance sector is compared on return on equity; anyone else without an operating-income
    figure (miners and some conglomerates) falls back to net income on assets."""
    assets, op, ni, eq = f.get("assets"), f.get("operating_income"), f.get("net_income"), f.get("equity")
    if sector == FINANCE:
        return (ni / eq, "return on equity") if ni is not None and eq and eq > 0 else (None, None)
    if op is not None and assets:
        return op / assets, "operating profit on assets"
    if ni is not None and assets:
        return ni / assets, "net income on assets"
    return None, None


def metrics(f: dict, cap: float, sector: str | None = None) -> dict:
    """f: the company's SEC numbers. Returns the raw inputs to the score."""
    assets = f.get("assets")
    q, basis = quality_of(f, sector)
    ey = f["net_income"] / cap if f.get("net_income") is not None and cap else None
    bm = f["equity"] / cap if f.get("equity") is not None and cap else None
    iss = (f["shares"] / f["shares_ya"] - 1) if f.get("shares") and f.get("shares_ya") else None
    ag = (assets / f["assets_ya"] - 1) if assets and f.get("assets_ya") else None
    rg = (f["rev_q"] / f["rev_q_ya"] - 1) if f.get("rev_q") and f.get("rev_q_ya") and f["rev_q_ya"] > 0 else None
    base_ok = f.get("rpo_ya") and f.get("revenue") and f["rpo_ya"] >= MIN_RPO_BASE * f["revenue"]
    rpo_g = (f["rpo"] / f["rpo_ya"] - 1) if f.get("rpo") and base_ok else None
    cover = f["rpo"] / f["revenue"] if f.get("rpo") and f.get("revenue") else None
    return {"quality": q, "quality_basis": basis, "earnings_yield": ey,
            "book_to_market": bm, "issuance": iss, "asset_growth": ag, "revenue_growth": rg, "rpo_growth": rpo_g, "rpo_cover": cover}


def score(rows: dict[str, dict]) -> dict[str, dict]:
    """rows: {symbol: {sector, cap, quality, earnings_yield, book_to_market, momentum, issuance, ...}}.
    Adds sector-relative percentiles, the composite and any disqualifier."""
    by_sector: dict[str, list[str]] = {}
    for s, r in rows.items():
        by_sector.setdefault(r["sector"], []).append(s)
    for sector, syms in by_sector.items():
        group = syms if len(syms) >= 15 else list(rows)          # tiny sectors are ranked against everyone

        def ranks(key, higher=True):
            return pct_ranks({s: rows[s][key] for s in group if rows[s].get(key) is not None}, higher)
        q, ey, bm, mo, iss = ranks("quality"), ranks("earnings_yield"), ranks("book_to_market"), ranks("momentum"), ranks("issuance", False)
        for s in syms:
            r = rows[s]
            value = [x for x in (ey.get(s), bm.get(s)) if x is not None]
            parts = {"quality": q.get(s), "value": sum(value) / len(value) if value else None, "momentum": mo.get(s), "low_issuance": iss.get(s)}
            have = {k: v for k, v in parts.items() if v is not None}
            comp = sum(have.values()) / len(have) if len(have) >= 3 else None
            bad = [k for k, v in have.items() if v < DISQUALIFY]
            if comp is not None and bad:
                comp = min(comp, 50.0)
            r.update({"grades": {k: round(v) if v is not None else None for k, v in parts.items()},
                      "score": round(comp, 1) if comp is not None else None, "flaws": bad})
    return rows


def backlog_picks(rows: dict[str, dict], n: int = 25) -> list[dict]:
    out = []
    for s, r in rows.items():
        g, rg, cover = r.get("rpo_growth"), r.get("revenue_growth"), r.get("rpo_cover")
        val = (r.get("grades") or {}).get("value")
        if g is None or rg is None or cover is None or val is None:
            continue
        if g - rg >= 0.10 and cover >= 0.5 and val >= 20 and g > 0:
            out.append(dict(_public(s, r), why=f"Backlog {g:+.0%} vs revenue {rg:+.0%}; backlog covers {cover:.1f} years of revenue"))
    return sorted(out, key=lambda x: -(x["rpo_growth"] - (x["revenue_growth"] or 0)))[:n]


def _public(s: str, r: dict) -> dict:
    keep = ("name", "sector", "industry", "cap", "price", "score", "grades", "flaws", "momentum", "return_12m", "above_200d", "issuance", "quality_basis",
            "asset_growth", "revenue_growth", "rpo_growth", "rpo_cover", "earnings_yield", "book_to_market")
    return dict({k: r.get(k) for k in keep}, symbol=s)


# ------------------------------------------------------------------ the weekly job

def gather(y: int, q: int, get=None) -> tuple[dict[str, dict[int, float]], str, int]:
    """Every number the screen uses for calendar quarter (y, q), for every filer at once."""
    per, per_ya, per_i, per_i_ya = f"CY{y}Q{q}", f"CY{y - 1}Q{q}", f"CY{y}Q{q}I", f"CY{y - 1}Q{q}I"
    yr = y if q == 4 else y - 1
    # Nobody files a fourth-quarter 10-Q, so fourth-quarter share counts and sales exist only for the few
    # companies that report the quarter separately: use the full year instead.
    dur, dur_ya = (f"CY{y}", f"CY{y - 1}") if q == 4 else (per, per_ya)
    F = {
        "assets": frame("Assets", per_i, get=get), "assets_ya": frame("Assets", per_i_ya, get=get),
        "equity": frame_any(["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], per_i, get),
        "shares": frame(SHARES_Q, dur, "shares", get=get), "shares_ya": frame(SHARES_Q, dur_ya, "shares", get=get),
        "shares_dei": frame("EntityCommonStockSharesOutstanding", per_i, "shares", "dei", get),
        "shares_dei_ya": frame("EntityCommonStockSharesOutstanding", per_i_ya, "shares", "dei", get),
        "operating_income": frame("OperatingIncomeLoss", f"CY{yr}", get=get), "net_income": frame_any(NET_INCOME, f"CY{yr}", get),
        "revenue": frame_any(REVENUE, f"CY{yr}", get), "rev_q": frame_any(REVENUE, dur, get), "rev_q_ya": frame_any(REVENUE, dur_ya, get),
        "rpo": frame("RevenueRemainingPerformanceObligation", per_i, get=get),
        "rpo_ya": frame("RevenueRemainingPerformanceObligation", per_i_ya, get=get),
    }
    return F, per, yr


NAME_NOISE = {"COMMON", "STOCK", "CLASS", "ORDINARY", "SHARES", "SHARE", "INC", "CORP", "CORPORATION", "HOLDINGS", "HOLDING", "CO",
              "COMPANY", "LTD", "LIMITED", "PLC", "GROUP", "THE", "NEW", "DE", "NV", "SA", "AG", "LLC", "LP", "A", "B", "C"}


def name_key(name: str) -> str:
    import re
    words = [w for w in re.findall(r"[A-Z0-9]+", name.upper().replace("&", " AND ")) if w not in NAME_NOISE]
    return "".join(words)


def predecessors(F: dict[str, dict[int, float]], missing: dict[int, str]) -> dict[int, int]:
    """{new cik: old cik} for companies that recently moved their listing to a new holding company
    (ExxonMobil in 2026): the new filer has no prior-year numbers, the old one has them under the same
    name. Only an exact, unique name match counts."""
    by_key: dict[str, list[int]] = {}
    for c in F["net_income"]:
        k = name_key(ENTITY_NAMES.get(c, ""))
        if k:
            by_key.setdefault(k, []).append(c)
    out = {}
    for c, name in missing.items():
        cands = [o for o in by_key.get(name_key(name), []) if o != c]
        if len(cands) == 1:
            out[c] = cands[0]
    return out


def company(F: dict[str, dict[int, float]], c: int, pred: int | None = None) -> dict:
    """One company's numbers; gaps filled from its predecessor filer, if any. Share counts stay
    like-for-like (diluted average with diluted average, cover-page count with cover-page count)."""
    f = {k: v.get(c) for k, v in F.items()}
    if pred:
        for k, v in F.items():
            if f[k] is None:
                f[k] = v.get(pred)
    if not (f["shares"] and f["shares_ya"]) and f["shares_dei"] and f["shares_dei_ya"]:
        f["shares"], f["shares_ya"] = f["shares_dei"], f["shares_dei_ya"]
    elif not f["shares"]:
        f["shares"] = f["shares_dei"]
    return f


def build(today: date | None = None, get=None, history_fn=None, listed_fn=None, sp500: set[str] | None = None, log=print,
          workers: int = 6) -> dict:
    from .providers import market, sec
    today = today or date.today()
    y, q = latest_quarter(today, get)
    F, per, yr = gather(y, q, get)
    log(f"latest quarter {per}; annual {yr}")
    log(", ".join(f"{k} {len(v)}" for k, v in F.items()))
    tmap = sec.ticker_map()
    lst = (listed_fn or listed)()
    rows: dict[str, dict] = {}
    seen: set[int] = set()
    firsts: list[tuple[str, int]] = []
    # One listing per company (Alphabet trades as GOOGL and GOOG): the most valuable line, then the shortest ticker.
    aliases: dict[int, list[str]] = {}
    for sym, info in sorted(lst.items(), key=lambda kv: (-kv[1]["cap"], len(kv[0]), kv[0])):
        if info["cap"] < MIN_CAP:
            continue
        cik = tmap.cik_for(sym)
        if not cik:
            continue
        if int(cik) in seen:
            aliases.setdefault(int(cik), []).append(sym)
            continue
        seen.add(int(cik))
        firsts.append((sym, int(cik)))
    missing = {c: lst[s]["name"] for s, c in firsts if F["assets"].get(c) is not None and F["net_income"].get(c) is None}
    preds = predecessors(F, missing)
    for new, old in preds.items():
        log(f"  {missing[new]}: prior-year numbers from its earlier SEC filer, {ENTITY_NAMES.get(old, old)} (CIK {old})")
    for sym, c in firsts:
        info = lst[sym]
        f = company(F, c, preds.get(c))
        if f["assets"] is None or f["net_income"] is None:
            continue
        rows[sym] = dict(info, cik=c, **metrics(f, info["cap"], info["sector"]))
    log(f"{len(rows)} companies with SEC numbers and a market value of $300M+")
    hist = history_fn or (lambda s: [b.close for b in market.get_history(s, 260)])

    def mom(sym):
        try:
            return sym, momentum(hist(sym))
        except Exception:  # noqa: BLE001 - no history: scored without momentum
            return sym, (None, None, None)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for sym, (m, r12, above) in pool.map(mom, list(rows)):
            rows[sym].update(momentum=m, return_12m=r12, above_200d=above)
    log(f"prices in {time.monotonic() - started:.0f}s; {sum(1 for r in rows.values() if r['momentum'] is not None)} with a year of history")
    score(rows)
    r12 = pct_ranks({s: r["return_12m"] for s, r in rows.items() if r.get("return_12m") is not None})
    for s, r in rows.items():
        r["return_12m_pct"] = round(r12[s]) if s in r12 else None
    ranked = sorted((s for s in rows if rows[s]["score"] is not None), key=lambda s: -rows[s]["score"])
    large = [s for s in ranked if (sp500 and s in sp500) or (not sp500 and rows[s]["cap"] >= 1e10)]
    mid = [s for s in ranked if SLEEPER_MAX_CAP > rows[s]["cap"] >= MIN_CAP and not rows[s]["flaws"]]
    lookup = {s: _lookup_row(r) for s, r in rows.items()}
    for s, r in rows.items():
        for a in aliases.get(r["cik"], []):          # BRK-B and GOOG share BRK-A's and GOOGL's grades, at their own price
            lookup[a] = _lookup_row(dict(r, price=lst[a]["price"]))
    return {"as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"), "quarter": per, "annual": yr, "universe": len(rows),
            "top_large": [_public(s, rows[s]) for s in large[:30]],
            "top_all": [_public(s, rows[s]) for s in ranked if rows[s]["cap"] >= 2e9][:50],
            "small_mid": [_public(s, rows[s]) for s in mid[:150]],
            "backlog": backlog_picks(rows),
            # The replay (screen_backtest) found the bottom of the ranking far more telling than the top:
            # the 50 lowest-graded companies worth $2B+ trailed SPY by about 5 points a quarter.
            "bottom": [_public(s, rows[s]) for s in [x for x in ranked if rows[x]["cap"] >= 2e9][::-1][:BOTTOM_N]],
            "lookup": lookup}


def _lookup_row(r: dict) -> list:
    g = r.get("grades") or {}
    return [r["score"], g.get("quality"), g.get("value"), g.get("momentum"), _r(r.get("issuance")), _r(r.get("asset_growth")),
            r.get("return_12m_pct"), _r(r.get("above_200d")), round(r["cap"]), r["sector"], _r(r.get("rpo_growth")),
            _r(r.get("revenue_growth")), r.get("price")]


LOOKUP_FIELDS = ["score", "quality", "value", "momentum", "issuance", "asset_growth", "return_12m_pct", "above_200d", "cap", "sector",
                 "rpo_growth", "revenue_growth", "price"]


def _r(x: float | None) -> float | None:
    return round(x, 4) if x is not None else None


# ------------------------------------------------------------------ the app side

def load(get=None) -> dict | None:
    from .pulse import DATA_URL
    try:
        return (get or (lambda u: http.get(u, ttl=6 * 3600)))(f"{DATA_URL}/{FILE}")
    except http.DataUnavailable:
        return None


def lookup(data: dict | None, symbol: str) -> dict | None:
    row = ((data or {}).get("lookup") or {}).get(symbol)
    return dict(zip(LOOKUP_FIELDS, row)) if row else None
