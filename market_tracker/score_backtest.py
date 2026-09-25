"""Point-in-time historical backtest of the composite score.

Replays the score at each month-end using only information public on that date:

* trend and momentum from closing prices up to the date,
* smart money from each investor's latest 13F *filed* on or before the date (compared with
  the filing before it), never from the quarter-end it describes,
* insiders from Form 4 trades *filed* on or before the date.

News is left out: there is no free archive of past headlines. The composite renormalizes
over the parts it has, exactly as the live score does when a part is missing.

Evaluation is cross-sectional: on each date, do higher-scoring stocks go on to beat
lower-scoring ones over the next month? Averaging that across ~120 independent months gives
an answer with a real error bar, instead of waiting months for the live journal.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from .analytics import indicators, signals
from .investors import HIGH_CONVICTION, INVESTORS, Investor
from .journal import forward_return, spearman
from .providers import sec

COMPONENTS = ["trend", "momentum", "smart_money", "insider"]
ERAS = [("2016–2019", "2016-01-01", "2019-12-31"), ("2020–2022", "2020-01-01", "2022-12-31"),
        ("2023–now", "2023-01-01", "9999-12-31")]
LOOKBACK_BARS = 400  # enough for 12-1 momentum, the 200-day average and a settled EWMA vol
MIN_CROSS_SECTION = 10

# Large US companies across sectors, weighted toward names the tracked investors actually
# hold. Chosen today, so it contains only survivors; see `survivorship_note`.
DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "BRK-B", "JPM", "BAC", "WFC", "C", "AXP",
    "V", "MA", "GS", "KO", "PEP", "PG", "JNJ", "PFE", "MRK", "ABBV", "UNH", "LLY", "CVX", "XOM", "OXY",
    "COP", "HD", "LOW", "WMT", "COST", "TGT", "MCD", "SBUX", "NKE", "DIS", "NFLX", "CMCSA", "T", "VZ",
    "INTC", "AMD", "QCOM", "AVGO", "ORCL", "CRM", "ADBE", "IBM", "CSCO", "CAT", "DE", "BA", "HON",
    "UPS", "KHC", "GM", "MCO", "CHTR",
]


@dataclass
class HistoricalData:
    prices: dict[str, list[tuple[str, float]]]  # symbol -> [(date, close)] oldest first
    filings: dict[str, list[sec.Filing13F]] = field(default_factory=dict)  # investor key -> filings
    insiders: dict[str, list[sec.InsiderTrade]] = field(default_factory=dict)  # symbol -> trades


# ------------------------------------------------------------------ helpers

def month_ends(calendar: list[str], start: str, end: str) -> list[str]:
    """Last trading day of each month in [start, end], from a benchmark's trading calendar."""
    out = []
    for i, d in enumerate(calendar):
        if not (start <= d <= end):
            continue
        nxt = calendar[i + 1] if i + 1 < len(calendar) else None
        if nxt is None or nxt[:7] != d[:7]:
            out.append(d)
    return out


def closes_until(hist: list[tuple[str, float]], day: str) -> list[float]:
    """The last LOOKBACK_BARS closes on or before `day` (binary search on the date)."""
    lo, hi = 0, len(hist)
    while lo < hi:
        mid = (lo + hi) // 2
        if hist[mid][0] <= day:
            lo = mid + 1
        else:
            hi = mid
    return [c for _, c in hist[max(0, lo - LOOKBACK_BARS):lo]]


def filings_as_of(filings: list[sec.Filing13F], day: str) -> tuple[sec.Filing13F | None, sec.Filing13F | None]:
    """Latest filing public on `day` and the one for the quarter before it."""
    public = [f for f in filings if f.filed <= day]
    if not public:
        return None, None
    public.sort(key=lambda f: (f.period, f.filed))
    return public[-1], (public[-2] if len(public) > 1 else None)


def index_report(report: dict) -> dict[str, dict]:
    """ticker -> {'holding': weight or None, 'moves': [action, ...]} for fast lookups."""
    idx: dict[str, dict] = {}
    for h in report["holdings"]:
        if h["ticker"]:
            idx.setdefault(h["ticker"], {"holding": None, "moves": []})["holding"] = h
    for action, moves in report["moves"].items():
        for m in moves:
            if m["ticker"]:
                idx.setdefault(m["ticker"], {"holding": None, "moves": []})["moves"].append((action, m))
    return idx


def smart_money_from_index(ticker: str, indexed: list[tuple[dict, dict[str, dict]]]) -> dict:
    """Same result as sec.smart_money_for_ticker, using pre-built indexes (see tests)."""
    holders, buyers, sellers = [], [], []
    for rep, idx in indexed:
        entry = idx.get(ticker)
        if not entry:
            continue
        weight = 1.0 if rep["investor"]["key"] in HIGH_CONVICTION else 0.35
        for action, move in entry["moves"]:
            e = {"investor": rep["investor"]["person"], "fund": rep["investor"]["fund"], "action": action,
                 "weight_pct": move["weight_now"], "period": rep["period"], "conviction_weight": weight}
            (buyers if action in ("new", "added") else sellers).append(e)
        pos = entry["holding"]
        if pos:
            holders.append({"investor": rep["investor"]["person"], "weight_pct": pos["weight_now"],
                            "value_usd": pos["value_now"], "period": rep["period"]})
    buy_w = sum(b["conviction_weight"] for b in buyers)
    sell_w = sum(s["conviction_weight"] for s in sellers)
    net = (buy_w - sell_w) / (buy_w + sell_w) if (buy_w + sell_w) else 0.0
    return {"ticker": ticker, "holders": holders, "buyers": buyers, "sellers": sellers,
            "net_flow": net, "investors_scanned": len(indexed)}


def _investor_by_key() -> dict[str, Investor]:
    return {i.key: i for i in INVESTORS}


# ------------------------------------------------------------------ scoring

def score_history(data: HistoricalData, dates: Iterable[str], horizons: tuple[int, ...] = (21, 63)) -> list[dict]:
    """One row per (date, symbol): component scores, composite and forward returns."""
    investors = _investor_by_key()
    report_cache: dict[tuple[str, str], tuple[dict, dict]] = {}
    rows: list[dict] = []
    for day in dates:
        as_of = date.fromisoformat(day)
        indexed = []
        staleness = []
        for key, filings in data.filings.items():
            current, previous = filings_as_of(filings, day)
            if not current or key not in investors:
                continue
            ck = (key, current.accession)
            if ck not in report_cache:
                rep = sec.build_report(investors[key], current, previous, as_of=as_of)
                report_cache[ck] = (rep, index_report(rep))
            rep, idx = report_cache[ck]
            indexed.append((rep, idx))
            staleness.append((as_of - date.fromisoformat(current.period)).days)
        sm_staleness = min(staleness) if staleness else None

        for sym, hist in data.prices.items():
            closes = closes_until(hist, day)
            if len(closes) < 260:
                continue
            ind = indicators.snapshot(closes)
            sm = smart_money_from_index(sym, indexed) if indexed else None
            trades = data.insiders.get(sym)
            ins = None
            if trades is not None:
                known = [t for t in trades if (t.filed or t.date) <= day]
                ins = sec.summarize_insiders(known, days=90, today=as_of)
            sig = signals.composite(ind, sm, ins, None, sm_staleness)
            row = {"date": day, "symbol": sym, "score": sig["score"]}
            for c in COMPONENTS:
                comp = sig["components"].get(c)
                row[c] = comp["score"] if comp else None
            for h in horizons:
                row[f"fwd_{h}"] = forward_return(hist, day, h)
            rows.append(row)
    return rows


# ------------------------------------------------------------------ evaluation

def _mean_t(values: list[float]) -> dict:
    n = len(values)
    if n < 2:
        return {"mean": values[0] if values else None, "t": None, "n": n}
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    return {"mean": mean, "t": mean / (sd / math.sqrt(n)) if sd else None, "n": n}


def _non_overlapping(dates: list[str], horizon: int) -> list[str]:
    """Month-end dates spaced so forward windows don't overlap (every date for 21 days,
    every third for 63), which keeps the t-statistics honest."""
    step = max(1, round(horizon / 21))
    return dates[::step]


def evaluate(rows: list[dict], horizons: tuple[int, ...] = (21, 63)) -> dict:
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date)
    out: dict = {"dates": len(dates), "first_date": dates[0] if dates else None,
                 "last_date": dates[-1] if dates else None,
                 "symbols": len({r["symbol"] for r in rows}), "horizons": {}}
    for h in horizons:
        key = f"fwd_{h}"
        use_dates = _non_overlapping(dates, h)
        signals_ic: dict[str, list[tuple[str, float]]] = {s: [] for s in ["score", *COMPONENTS]}
        spreads, sm_excess, cluster_excess = [], [], []
        for d in use_dates:
            cross = [r for r in by_date[d] if r[key] is not None]
            if len(cross) < MIN_CROSS_SECTION:
                continue
            universe_ret = sum(r[key] for r in cross) / len(cross)
            for s in signals_ic:
                pairs = [(r[s], r[key]) for r in cross if r[s] is not None]
                # A component that is constant across stocks on a date (e.g. no insider
                # activity anywhere) carries no ranking information that month.
                if len(pairs) >= MIN_CROSS_SECTION and len({p[0] for p in pairs}) > 1:
                    ic = spearman([p[0] for p in pairs], [p[1] for p in pairs])
                    if ic is not None:
                        signals_ic[s].append((d, ic))
            ranked = sorted(cross, key=lambda r: r["score"])
            q = max(1, len(ranked) // 5)
            spreads.append(sum(r[key] for r in ranked[-q:]) / q - sum(r[key] for r in ranked[:q]) / q)
            bought = [r[key] for r in cross if r["smart_money"] is not None and r["smart_money"] > 0]
            if bought:
                sm_excess.append(sum(bought) / len(bought) - universe_ret)
            clusters = [r[key] for r in cross if r["insider"] == 100.0]
            cluster_excess.extend(c - universe_ret for c in clusters)
        res = {
            "dates_used": len(use_dates),
            "ic": {s: _mean_t([v for _, v in vals]) for s, vals in signals_ic.items()},
            "ic_by_era": {s: {name: _mean_t([v for d, v in vals if lo <= d <= hi])["mean"]
                              for name, lo, hi in ERAS} for s, vals in signals_ic.items()},
            "top_minus_bottom_quintile": _mean_t(spreads),
            "copy_billionaires_excess": _mean_t(sm_excess),
            "insider_cluster_excess": _mean_t(cluster_excess),
        }
        out["horizons"][h] = res
    out["verdicts"] = verdicts(out)
    out["survivorship_note"] = survivorship_note()
    return out


def verdict_for(stat: dict) -> str:
    t, mean, n = stat.get("t"), stat.get("mean"), stat.get("n", 0)
    if mean is None or n < 12:
        return "not enough data"
    if t is None:
        return "no variation"
    if t >= 2:
        return "works (statistically significant)"
    if t >= 1:
        return "weak positive, not significant"
    if t > -1:
        return "no detectable edge"
    if t > -2:
        return "weak negative, not significant"
    return "works backwards (significant)"


def verdicts(result: dict) -> dict[str, str]:
    h = result["horizons"].get(21) or next(iter(result["horizons"].values()), None)
    if not h:
        return {}
    v = {s: verdict_for(h["ic"][s]) for s in ["score", *COMPONENTS]}
    v["copy_billionaires"] = verdict_for(h["copy_billionaires_excess"])
    v["insider_clusters"] = verdict_for(h["insider_cluster_excess"])
    return v


def survivorship_note() -> str:
    return ("The universe is today's large companies, so it excludes firms that failed or shrank "
            "since 2016. That flatters average returns but affects rankings between survivors less; "
            "treat the IC as the main result, not the absolute returns.")


NAMES = {"score": "Composite score (without news)", "trend": "Trend", "momentum": "Momentum",
         "smart_money": "Billionaire moves (13F)", "insider": "Insider trades"}


def _fmt(x: float | None, spec: str) -> str:
    return "—" if x is None else format(x, spec)


def report_markdown(result: dict) -> str:
    lines = ["## Score backtest", "",
             f"{result['symbols']} stocks, {result['dates']} month-ends ({result['first_date']} → {result['last_date']}). "
             "Each month, stocks are ranked by score using only data public that day; the IC is the rank "
             "correlation between score and next-period return (0 = no edge, +0.05 = small real edge).", ""]
    for h, res in result["horizons"].items():
        lines += [f"### {h}-day forward returns ({res['dates_used']} non-overlapping dates)", "",
                  "| Signal | Mean IC | t-stat | Months | " + " | ".join(n for n, _, _ in ERAS) + " | Verdict |",
                  "|---|---:|---:|---:|" + "---:|" * len(ERAS) + "---|"]
        for s in ["score", *COMPONENTS]:
            st = res["ic"][s]
            eras = " | ".join(_fmt(res["ic_by_era"][s][n], "+.3f") for n, _, _ in ERAS)
            lines.append(f"| {NAMES[s]} | {_fmt(st['mean'], '+.3f')} | {_fmt(st['t'], '+.2f')} | {st['n']} | "
                         f"{eras} | {verdict_for(st)} |")
        lines.append("")
        for label, key in [("Top minus bottom fifth by score", "top_minus_bottom_quintile"),
                           ("Copy the billionaires (stocks they bought vs. all)", "copy_billionaires_excess"),
                           ("Insider cluster buys vs. all", "insider_cluster_excess")]:
            st = res[key]
            if st["mean"] is None:
                lines.append(f"- **{label}:** no data")
            else:
                lines.append(f"- **{label}:** {st['mean'] * 100:+.2f}% per period, t = {_fmt(st['t'], '+.2f')}, "
                             f"n = {st['n']} ({verdict_for(st)})")
        lines.append("")
    lines += [f"_{result['survivorship_note']}_", ""]
    return "\n".join(lines)
