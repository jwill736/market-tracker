"""Alert scorecard: did acting on an alert beat simply owning the market?

Every alert the watcher or the daily scan raises is written to alert_log.csv with its date.
The scorecard buys each one at the first close *after* the alert (so it never uses a price
the alert couldn't have known) and compares its return with SPY over the same days, at 1, 3
and 6 months (21, 63 and 126 trading days), plus a return to date for alerts still open.

It only claims an edge when enough alerts have a full holding period behind them and the
average excess return is at least twice its standard error. Until then it says so.
"""

from __future__ import annotations

import csv
import math
import os
import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import date

from . import alerts, http

HORIZONS = (21, 63, 126)
BENCHMARK = "SPY"
MIN_RESOLVED = 30
KINDS = {"cluster": "Insider clusters", "big": "Large insider buys", "13d": "Tracked-investor stakes"}


@dataclass
class AlertRecord:
    alerted: str      # date the alert went out (UTC)
    kind: str         # cluster / big / 13d
    symbol: str
    issuer_cik: str
    company: str
    detail: str
    url: str = ""


LOG_FIELDS = [f.name for f in fields(AlertRecord)]


def load_log(path: str) -> list[AlertRecord]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [AlertRecord(**{k: r.get(k, "") for k in LOG_FIELDS}) for r in csv.DictReader(fh)]


def save_log(records: list[AlertRecord], path: str) -> None:
    unique = {(r.kind, r.issuer_cik, r.alerted, r.detail): r for r in records}
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        w.writeheader()
        for r in sorted(unique.values(), key=lambda r: (r.alerted, r.kind, r.symbol)):
            w.writerow(asdict(r))


def seed_from_history(alerted: dict[str, str], buys: list[alerts.Buy]) -> list[AlertRecord]:
    """Cluster alerts raised before the log existed, rebuilt from the alert history (which
    records the company and date) and the stored purchases (which name the company)."""
    names = {}
    for b in buys:
        names[b.issuer_cik] = (b.symbol, b.issuer_name)
    out = []
    for key, when in alerted.items():
        if ":" in key or key not in names:
            continue
        symbol, company = names[key]
        out.append(AlertRecord(alerted=when, kind="cluster", symbol=symbol, issuer_cik=key, company=company,
                               detail=""))   # the history doesn't keep the cluster's size
    return out


def ensure_log(path: str, alerted: dict[str, str], buys: list[alerts.Buy]) -> list[AlertRecord]:
    """The log, created from the alert history the first time."""
    if os.path.exists(path):
        return load_log(path)
    records = seed_from_history(alerted, buys)
    save_log(records, path)
    return records


def from_cluster(c: alerts.Cluster, on: date) -> AlertRecord:
    return AlertRecord(alerted=on.isoformat(), kind="cluster", symbol=c.symbol, issuer_cik=c.issuer_cik,
                       company=c.issuer_name, detail=f"{len(c.insiders)} insiders, {alerts._money(c.total_value)}")


def from_big(b, on: date) -> AlertRecord:
    return AlertRecord(alerted=on.isoformat(), kind="big", symbol=b.symbol, issuer_cik=b.issuer_cik,
                       company=b.issuer_name, detail=f"{b.insider}: {alerts._money(b.value)}")


def from_stake(s, on: date, symbol: str) -> AlertRecord:
    return AlertRecord(alerted=on.isoformat(), kind="13d", symbol=symbol, issuer_cik=s.subject_cik,
                       company=s.subject_name, detail=f"{s.tracked or s.filer_name}: {s.form}", url=s.url)


def ticker_for_cik(cik: str) -> str:
    """The company's first listed ticker, from its EDGAR filing list ('' if none)."""
    from . import dilution
    try:
        tickers = dilution._submissions(cik).get("tickers") or []
    except http.DataUnavailable:
        return ""
    return tickers[0] if tickers else ""


# ------------------------------------------------------------------ evaluation

def _series(history: list[tuple[str, float]]) -> tuple[list[str], list[float]]:
    return [d for d, _ in history], [c for _, c in history]


def _close_on_or_before(dates: list[str], closes: list[float], day: str) -> float | None:
    lo, hi = 0, len(dates)
    while lo < hi:
        mid = (lo + hi) // 2
        if dates[mid] <= day:
            lo = mid + 1
        else:
            hi = mid
    return closes[lo - 1] if lo else None


def score_one(rec: AlertRecord, history: list[tuple[str, float]], bench: list[tuple[str, float]]) -> dict:
    """Entry at the first close after the alert date; returns at each horizon and to date."""
    dates, closes = _series(history)
    bdates, bcloses = _series(bench)
    after = [i for i, d in enumerate(dates) if d > rec.alerted]
    row = {**asdict(rec), "status": "pending", "entry_date": None, "entry": None,
           "to_date": None, "to_date_excess": None, "days_held": 0}
    if not after:
        return row
    i0 = after[0]
    entry, entry_date = closes[i0], dates[i0]
    b0 = _close_on_or_before(bdates, bcloses, entry_date)
    last = len(dates) - 1
    row.update(status="open", entry_date=entry_date, entry=entry, days_held=last - i0)

    def excess(i: int) -> tuple[float, float | None]:
        r = closes[i] / entry - 1
        b1 = _close_on_or_before(bdates, bcloses, dates[i])
        return r, (r - (b1 / b0 - 1)) if (b0 and b1) else None

    row["to_date"], row["to_date_excess"] = excess(last)
    for h in HORIZONS:
        if i0 + h <= last:
            row[f"ret_{h}"], row[f"excess_{h}"] = excess(i0 + h)
    if i0 + HORIZONS[-1] <= last:
        row["status"] = "closed"
    return row


def summarize(rows: list[dict], horizon: int) -> dict:
    vals = [r[f"excess_{horizon}"] for r in rows if r.get(f"excess_{horizon}") is not None]
    if not vals:
        return {"horizon_days": horizon, "n": 0}
    mean = statistics.fmean(vals)
    se = statistics.stdev(vals) / math.sqrt(len(vals)) if len(vals) > 1 else None
    return {"horizon_days": horizon, "n": len(vals), "mean_excess": mean, "median_excess": statistics.median(vals),
            "hit_rate": sum(v > 0 for v in vals) / len(vals), "se": se}


def verdict(summary: dict) -> str:
    n = summary.get("n", 0)
    if n < MIN_RESOLVED:
        return (f"Too early to judge: {n} alert{'s' if n != 1 else ''} with a full 3 months behind "
                f"{'them' if n != 1 else 'it'}; about {MIN_RESOLVED} are needed. Returns to date are noise.")
    mean, se = summary["mean_excess"], summary["se"]
    if se and abs(mean) >= 2 * se:
        return (f"{'Beating' if mean > 0 else 'Trailing'} SPY by {mean:+.1%} on average over 3 months "
                f"(±{2 * se:.1%}, {n} alerts). That clears the bar for 'probably not luck'.")
    return (f"No reliable difference from SPY yet: {mean:+.1%} on average over 3 months (±{2 * se:.1%}, "
            f"{n} alerts).")


def evaluate(records: list[AlertRecord], history_fn: Callable[[str], list[tuple[str, float]]],
             benchmark: str = BENCHMARK) -> dict:
    try:
        bench = history_fn(benchmark)
    except (http.DataUnavailable, KeyError, ValueError) as exc:
        return {"error": f"{benchmark}: {exc}", "alerts": [], "by_kind": {}}
    rows, errors = [], []
    cache: dict[str, list[tuple[str, float]]] = {}
    for rec in records:
        if not rec.symbol or rec.symbol.upper() in alerts.NO_TICKER:
            continue
        try:
            if rec.symbol not in cache:
                cache[rec.symbol] = history_fn(rec.symbol)
            rows.append(score_one(rec, cache[rec.symbol], bench))
        except (http.DataUnavailable, KeyError, ValueError) as exc:
            errors.append(f"{rec.symbol}: {exc}")
            rows.append({**asdict(rec), "status": "no price"})
    by_kind = {}
    for kind in KINDS:
        sel = [r for r in rows if r["kind"] == kind]
        if not sel:
            continue
        sums = [summarize(sel, h) for h in HORIZONS]
        by_kind[kind] = {"label": KINDS[kind], "alerts": len(sel), "horizons": sums,
                         "verdict": verdict(sums[1])}
    rows.sort(key=lambda r: (r["alerted"], r["symbol"]), reverse=True)
    return {"benchmark": benchmark, "alerts": rows, "by_kind": by_kind, "errors": errors}


def history_closes(symbol: str) -> list[tuple[str, float]]:
    from .providers import market
    return [(b.date, b.close) for b in market.get_history(symbol, 400)]
