"""Has the weekly screen beaten the S&P 500? A point-in-time replay from 2012 to now.

Every quarter, the screen is rebuilt the way it would have looked then:
- the SEC numbers for a calendar quarter are used only from 135 days after it ends (10-Qs are due
  within 40-45 days and 10-Ks within 60-90, so nearly every company has filed by then);
- market values are that day's price times the shares the company reported, not today's;
- momentum uses only prices up to that day.
Then the top picks are held for 3, 6 and 12 months and compared with SPY (dividends included on
both sides: Yahoo's adjusted closes).

What this can't fix, reported next to the result rather than hidden:
- Survivorship. The companies come from today's stock list, so ones that went bankrupt or were
  bought out since are missing, and they were more often losers. Results look better than a real
  investor would have got. The report counts how many large SEC filers from each date no longer
  have a ticker.
- Restatements. SEC "frames" hold the latest figure filed for a period, so a later correction
  leaks in. Small, but not zero.
- The rules were written in 2026 with the research in hand. A replay can't undo that hindsight.

The bottom-50 group is a check: if the screen means anything, it should lag. Once stock splits
were handled (company size from the price as traded, not today's split-adjusted price), it
mostly didn't: under a point a quarter behind SPY, within luck. bottom_sentence() words
whatever the latest run found, so nothing else in the app quotes a stale number.
"""

from __future__ import annotations

import math
import time
from array import array
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import http, screen

FILE = "screen_backtest.json"
LAG_DAYS = 135
HOLD = {"3m": 63, "6m": 126, "12m": 252}          # trading days
BENCH = "SPY"
START = (2011, 4)                                  # XBRL covers nearly every filer from 2011 on
GROUPS = {
    "large": "Top 30 worth $10B+",
    "all": "Top 50 worth $2B+",
    "small_mid": "Top 50 worth $300M-$10B, no flaws",
    "bottom": "Bottom 50 worth $2B+ (should lag)",
    "everyone": "Every company screened, equal weight",
}
# Checks on the bottom of the ranking: which grade carries it, and whether a wider cut still lags.
CHECKS = {
    "bottom_10pct": "Bottom 10% worth $2B+",
    "bottom_20pct": "Bottom 20% worth $2B+",
    "bottom_quality": "Bottom 50 worth $2B+ on quality alone",
    "bottom_value": "Bottom 50 worth $2B+ on value alone",
    "bottom_momentum": "Bottom 50 worth $2B+ on momentum alone",
    "bottom_low_issuance": "Bottom 50 worth $2B+ on dilution alone (heaviest issuers)",
}
ALL_GROUPS = {**GROUPS, **CHECKS}
MAX_CAP = 6e12                                     # a market value above this is a unit error in the filing


@dataclass
class Prices:
    calendar: list[str]                            # the benchmark's trading days
    series: dict[str, array]                       # symbol -> adjusted closes on those days (nan = no trade): for returns
    raw: dict[str, array] = field(default_factory=dict)   # the price as it traded that day: for market values

# Why two prices: Yahoo's history is adjusted for every later split, so NVIDIA's 2016 price shows as a
# fortieth of what it was. Times the share count NVIDIA reported in 2016, that makes a $30B company
# look like a $1B one, and pushes future winners (which are the ones that split) into the small and
# cheap buckets: look-ahead bias. Market values use the price as it traded, split factor undone.


def align(calendar: list[str], bars: list[tuple[str, float]]) -> array:
    pos = {d: i for i, d in enumerate(calendar)}
    out = array("d", [math.nan]) * len(calendar)
    for d, c in bars:
        i = pos.get(d)
        if i is not None and c and c > 0:
            out[i] = c
    return out


def price_at(p: Prices, sym: str, i: int, back: int = 5) -> float | None:
    s = p.series.get(sym)
    if s is None:
        return None
    for k in range(i, max(-1, i - back - 1), -1):
        if not math.isnan(s[k]):
            return s[k]
    return None


def price_traded(p: Prices, sym: str, i: int) -> float | None:
    """The price as it traded on day i (split-adjustment undone); the adjusted one if that's all there is."""
    if sym not in p.raw:
        return price_at(p, sym, i)
    s = p.raw[sym]
    for k in range(i, max(-1, i - 6), -1):
        if not math.isnan(s[k]):
            return s[k]
    return None


def parse_history(result: dict) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """(adjusted closes, closes as traded) from a Yahoo chart result requested with events=split.
    Yahoo's plain close is split-adjusted but not dividend-adjusted; multiplying by every split after
    the day gives the price that day."""
    stamps = result.get("timestamp") or []
    ind = result.get("indicators") or {}
    close = ((ind.get("quote") or [{}])[0].get("close")) or []
    adj = ((ind.get("adjclose") or [{}])[0].get("adjclose")) or close
    splits = sorted(((int(v.get("date", 0)), float(v.get("numerator") or 1) / float(v.get("denominator") or 1))
                     for v in (((result.get("events") or {}).get("splits")) or {}).values() if v.get("denominator")), reverse=True)
    out_adj, out_raw = [], []
    factor, k = 1.0, 0
    rows = sorted(zip(stamps, close, adj), key=lambda r: r[0], reverse=True)
    for ts, c, a in rows:
        while k < len(splits) and splits[k][0] > ts:
            factor *= splits[k][1]
            k += 1
        day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        if a is not None:
            out_adj.append((day, float(a)))
        if c is not None:
            out_raw.append((day, float(c) * factor))
    return out_adj[::-1], out_raw[::-1]


def closes_to(p: Prices, sym: str, i: int, n: int = 260) -> list[float]:
    s = p.series.get(sym)
    if s is None:
        return []
    return [c for c in s[max(0, i - n + 1): i + 1] if not math.isnan(c)]


def rebalance_days(calendar: list[str], start: tuple[int, int] = START) -> list[tuple[int, int, int]]:
    """(year, quarter, index into the calendar) for every quarter whose numbers were public by a day
    the calendar covers."""
    import bisect
    out = []
    y, q = start
    while True:
        end = date(y, 3 * q, 1) + timedelta(days=31)
        end = end.replace(day=1) - timedelta(days=1)           # last day of the quarter
        day = (end + timedelta(days=LAG_DAYS)).isoformat()
        i = bisect.bisect_left(calendar, day)
        if i >= len(calendar):
            break
        out.append((y, q, i))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def period_rows(F: dict, universe: dict[str, dict], p: Prices, i: int) -> dict[str, dict]:
    rows = {}
    for sym, u in universe.items():
        f = screen.company(F, u["cik"])
        if f["assets"] is None or f["net_income"] is None or not f["shares"]:
            continue
        px = price_traded(p, sym, i)
        if not px:
            continue
        cap = f["shares"] * px
        if cap < screen.MIN_CAP or cap > MAX_CAP:
            continue
        m, r12, above = screen.momentum(closes_to(p, sym, i))
        rows[sym] = dict(sector=u["sector"], cap=cap, momentum=m, return_12m=r12, **screen.metrics(f, cap, u["sector"]))
    return screen.score(rows)


def picks(rows: dict[str, dict]) -> dict[str, list[str]]:
    ranked = sorted((s for s in rows if rows[s]["score"] is not None), key=lambda s: (-rows[s]["score"], s))
    big = [s for s in ranked if rows[s]["cap"] >= 2e9]
    out = {"large": [s for s in ranked if rows[s]["cap"] >= 1e10][:30], "all": big[:50],
           "small_mid": [s for s in ranked if rows[s]["cap"] < screen.SLEEPER_MAX_CAP and not rows[s]["flaws"]][:50],
           "bottom": big[::-1][:50], "everyone": ranked,
           "bottom_10pct": big[::-1][:max(1, len(big) // 10)], "bottom_20pct": big[::-1][:max(1, len(big) // 5)]}
    bigs = [s for s in rows if rows[s]["cap"] >= 2e9]
    for comp in ("quality", "value", "momentum", "low_issuance"):
        have = [s for s in bigs if (rows[s].get("grades") or {}).get(comp) is not None]
        out[f"bottom_{comp}"] = sorted(have, key=lambda s: (rows[s]["grades"][comp], s))[:50]
    return out


def forward(p: Prices, syms: list[str], i: int, h: int) -> tuple[float | None, int]:
    """Equal-weighted return from day i to day i+h, and how many of the names had both prices."""
    if i + h >= len(p.calendar):
        return None, 0
    rets = []
    for s in syms:
        a, b = price_at(p, s, i), price_at(p, s, i + h)
        if a and b:
            rets.append(b / a - 1)
    return (sum(rets) / len(rets) if rets else None), len(rets)


def newey_west_t(xs: list[float], lags: int) -> float | None:
    """t-statistic of the mean with Newey-West standard errors: overlapping 6- and 12-month holds
    started every quarter share returns, which a plain t-test would count as independent."""
    n = len(xs)
    if n < 8:
        return None
    m = sum(xs) / n
    d = [x - m for x in xs]
    var = sum(e * e for e in d) / n
    for lag in range(1, lags + 1):
        cov = sum(d[i] * d[i - lag] for i in range(lag, n)) / n
        var += 2 * (1 - lag / (lags + 1)) * cov
    return round(m / math.sqrt(var / n), 2) if var > 0 else None


def breakeven_missing(edge: float | None, gone_share: float | None) -> float | None:
    """How much worse per holding period the missing companies (bankrupt, bought out, delisted since)
    would have to have done than the visible ones, if they'd been picked in proportion, to erase
    this group's edge over SPY. A positive edge that survives only small numbers is fragile."""
    if edge is None or not gone_share:
        return None
    return round(edge / gone_share, 2)


def summarize(periods: list[dict]) -> dict:
    out = {}
    surv = [pr["large_filers_gone"] / pr["large_filers"] for pr in periods if pr.get("large_filers")]
    gone = sum(surv) / len(surv) if surv else None
    for g in ALL_GROUPS:
        if not any(g in pr["groups"] for pr in periods):
            continue
        row = {"label": ALL_GROUPS[g], "check": g in CHECKS}
        for h in HOLD:
            pairs = [(pr["groups"][g][h], pr["spy"][h]) for pr in periods
                     if g in pr["groups"] and pr["groups"][g].get(h) is not None and pr["spy"].get(h) is not None]
            if not pairs:
                row[h] = None
                continue
            edges = [a - b for a, b in pairs]
            n = len(edges)
            mean = sum(edges) / n
            sd = math.sqrt(sum((e - mean) ** 2 for e in edges) / (n - 1)) if n > 1 else 0.0
            row[h] = {"periods": n, "avg": round(sum(a for a, _ in pairs) / n * 100, 2), "avg_spy": round(sum(b for _, b in pairs) / n * 100, 2),
                      "avg_edge": round(mean * 100, 2), "beat_pct": round(sum(e > 0 for e in edges) / n * 100),
                      "worst_edge": round(min(edges) * 100, 1), "best_edge": round(max(edges) * 100, 1),
                      "t": (round(mean / (sd / math.sqrt(n)), 2) if sd > 0 else None) if h == "3m"
                      else newey_west_t(edges, HOLD[h] // 63 - 1),
                      "breakeven_missing": breakeven_missing(round(mean * 100, 2), gone)}
        # $10,000 rebalanced every quarter (3-month holds back to back) against SPY
        chain = [(pr["groups"][g]["3m"], pr["spy"]["3m"]) for pr in periods
                 if g in pr["groups"] and pr["groups"][g].get("3m") is not None and pr["spy"].get("3m") is not None]
        grow, spy = 10000.0, 10000.0
        for a, b in chain:
            grow *= 1 + a
            spy *= 1 + b
        row["growth"] = {"quarters": len(chain), "screen": round(grow), "spy": round(spy),
                         "cagr": round(((grow / 10000) ** (4 / len(chain)) - 1) * 100, 2) if chain else None,
                         "cagr_spy": round(((spy / 10000) ** (4 / len(chain)) - 1) * 100, 2) if chain else None}
        out[g] = row
    return out


def _t(x: float | None) -> str:
    if x is None:
        return ""
    return f" (t = {x:.1f}: {'statistically solid' if abs(x) >= 2 else 'could be luck'})"


def bottom_sentence(bt: dict | None) -> str:
    """What the latest replay says about the screen's bottom 50, in one sentence ("" before a run)."""
    q = (((bt or {}).get("summary") or {}).get("bottom") or {}).get("3m")
    if not q:
        return ""
    edge, t = q["avg_edge"], q.get("t")
    if edge < 0 and t is not None and t <= -2:
        return f"In the replay since 2012 the screen's bottom 50 trailed SPY by {-edge:.1f} points a quarter (t = {t:.1f}: statistically solid)."
    if edge < 0:
        return (f"In the replay since 2012 the screen's bottom 50 trailed SPY by only {-edge:.1f} points a quarter"
                + (f" (t = {t:.1f})" if t is not None else "") + ": within luck, so a low grade alone is not a reason to sell.")
    return f"In the replay since 2012 the screen's bottom 50 did not trail SPY ({edge:+.1f} points a quarter): a low grade alone is not a reason to sell."


def verdict(summary: dict, survivorship: float | None) -> str:
    """Every group against SPY in one paragraph, weakest claims labelled as such."""
    if not summary["large"].get("3m"):
        return "Not enough history to judge."
    parts = []
    for g in ("large", "all", "small_mid"):
        row = summary[g]
        gr, q = row["growth"], row["3m"]
        if not q or gr["cagr"] is None:
            continue
        ahead = gr["cagr"] - gr["cagr_spy"]
        parts.append(f"{row['label']}: {gr['cagr']:+.1f}% a year against SPY's {gr['cagr_spy']:+.1f}% "
                     f"({'ahead' if ahead >= 0 else 'behind'} by {abs(ahead):.1f}), beat SPY in {q['beat_pct']}% of quarters{_t(q.get('t'))}.")
    years = summary["large"]["growth"]["quarters"] / 4
    line = f"Rebalanced every quarter for {years:.0f} years. " + " ".join(parts)
    ev, b = summary["everyone"]["growth"], summary["bottom"]["3m"]
    if ev["cagr"] is not None and b:
        line += (f" The average screened stock, equal-weighted, made {ev['cagr']:+.1f}% a year, so beating SPY at all took picking well;"
                 f" the bottom 50 {'trailed' if b['avg_edge'] < 0 else 'led'} SPY by {abs(b['avg_edge']):.1f} points a quarter{_t(b.get('t'))}.")
    checks = [(g, summary[g]["3m"]) for g in CHECKS if g in summary and summary[g].get("3m")]
    comps = [(g, q) for g, q in checks if g.startswith("bottom_") and not g.endswith("pct")]
    if comps:
        g, q = min(comps, key=lambda x: x[1]["avg_edge"])
        line += f" Of the four grades alone, the worst on {g.replace('bottom_', '').replace('_', ' ')} lagged most ({q['avg_edge']:+.1f} points a quarter)."
        g2, q2 = max(comps, key=lambda x: x[1]["avg_edge"])
        if q2["avg_edge"] > 0:
            line += (f" The worst on {g2.replace('bottom_', '').replace('_', ' ')} alone led SPY ({q2['avg_edge']:+.1f} a quarter):"
                     " in this period that grade pointed the wrong way.")
    if survivorship:
        line += (f" Survivorship: up to {survivorship:.0f}% of large SEC filers from those dates have no ticker today and are missing,"
                 " which flatters the buy lists and, since the missing are more often failures, understates how badly the bottom did.")
    return line


# ------------------------------------------------------------------ the job

def load_prices(symbols: list[str], start: str, fetch=None, workers: int = 8, log=print) -> Prices:
    from .providers import market
    days = (date.today() - date.fromisoformat(start)).days + 10

    now = int(datetime.now(timezone.utc).timestamp())

    def one(sym):
        try:
            if fetch:
                bars = fetch(sym)
                return sym, bars, bars
            d = http.get(market.YAHOO_CHART.format(symbol=sym), ttl=0,
                         params={"period1": now - days * 86400, "period2": now, "interval": "1d", "events": "split"})
            res = d["chart"]["result"][0]
            if (res.get("meta") or {}).get("dataGranularity", "1d") != "1d":
                return sym, [], []
            adj, raw = parse_history(res)
            return sym, adj, raw
        except (http.DataUnavailable, KeyError, IndexError, ValueError, TypeError):
            return sym, [], []
    _, bench, _ = one(BENCH)
    if len(bench) < 1000:
        raise http.DataUnavailable(f"no {BENCH} history")
    calendar = [d for d, _ in bench]
    series = {BENCH: align(calendar, bench)}
    raw_series: dict[str, array] = {}
    t = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for k, (sym, bars, raw) in enumerate(ex.map(one, symbols)):
            if bars:
                series[sym] = align(calendar, bars)
                if raw and not fetch:
                    raw_series[sym] = array("f", align(calendar, raw))
            if k and k % 1000 == 0:
                log(f"  prices: {k}/{len(symbols)} in {time.monotonic() - t:.0f}s")
    log(f"prices for {len(series) - 1} of {len(symbols)} companies, {calendar[0]} to {calendar[-1]}")
    return Prices(calendar, series, raw_series)


def run(get=None, listed_fn=None, prices: Prices | None = None, start: tuple[int, int] = START, last: int | None = None,
        log=print, gather_fn=None) -> dict:
    from .providers import sec
    tmap = sec.ticker_map()
    lst = (listed_fn or screen.listed)()
    universe: dict[str, dict] = {}
    seen: set[int] = set()
    for sym, info in sorted(lst.items(), key=lambda kv: (-kv[1]["cap"], len(kv[0]), kv[0])):
        cik = tmap.cik_for(sym)
        if cik and int(cik) not in seen:
            seen.add(int(cik))
            universe[sym] = {"cik": int(cik), "sector": info["sector"]}
    listed_ciks = {int(r["cik_str"]) for r in tmap.by_ticker.values()}
    log(f"{len(universe)} listed companies with SEC filings")
    p = prices or load_prices(sorted(universe), f"{start[0] - 1}-01-01", log=log)
    days = rebalance_days(p.calendar, start)
    if last:
        days = days[-last:]
    periods = []
    gather_fn = gather_fn or (lambda y, q: screen.gather(y, q, get)[0])
    for y, q, i in days:
        F = gather_fn(y, q)
        rows = period_rows(F, universe, p, i)
        if len(rows) < 200:
            log(f"CY{y}Q{q}: only {len(rows)} companies, skipped")
            continue
        big_filers = [c for c, v in F["assets"].items() if v >= 1e9]
        gone = sum(1 for c in big_filers if c not in listed_ciks)
        pk = picks(rows)
        spy = {h: forward(p, [BENCH], i, n)[0] for h, n in HOLD.items()}
        groups = {}
        for g, syms in pk.items():
            groups[g] = {"n": len(syms)}
            for h, n in HOLD.items():
                r, k = forward(p, syms, i, n)
                groups[g][h] = r
                groups[g][f"{h}_priced"] = k
            if g != "everyone":
                groups[g]["top"] = syms[:10]
        periods.append({"quarter": f"CY{y}Q{q}", "day": p.calendar[i], "companies": len(rows), "spy": spy, "groups": groups,
                        "large_filers": len(big_filers), "large_filers_gone": gone})
        big = groups["large"]
        log(f"CY{y}Q{q} on {p.calendar[i]}: {len(rows)} companies; top 30 large 3m "
            + (f"{big['3m'] * 100:+.1f}% vs SPY {spy['3m'] * 100:+.1f}%" if big["3m"] is not None and spy["3m"] is not None else "pending"))
    summary = summarize(periods)
    surv = [pr["large_filers_gone"] / pr["large_filers"] * 100 for pr in periods if pr["large_filers"]]
    survivorship = round(sum(surv) / len(surv), 1) if surv else None
    return {"as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"), "benchmark": BENCH, "lag_days": LAG_DAYS,
            "first": periods[0]["day"] if periods else None, "last": periods[-1]["day"] if periods else None,
            "summary": summary, "survivorship_pct": survivorship, "verdict": verdict(summary, survivorship),
            "periods": [dict(pr, spy={h: _r(v) for h, v in pr["spy"].items()},
                             groups={g: {k: (_r(v) if isinstance(v, float) else v) for k, v in d.items()} for g, d in pr["groups"].items()})
                        for pr in periods]}


def _r(x: float | None) -> float | None:
    return round(x, 5) if x is not None else None


def load(get=None) -> dict | None:
    from .pulse import DATA_URL
    try:
        return (get or (lambda u: http.get(u, ttl=6 * 3600)))(f"{DATA_URL}/{FILE}")
    except http.DataUnavailable:
        return None
