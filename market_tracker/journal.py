"""Signal journal: log each day's composite scores, then measure them against what prices
actually did afterwards.

This is how the score earns (or loses) trust. The composite weights are judgment calls;
the journal answers the empirical question: do higher scores precede higher returns?

Storage is a plain CSV so it can live in git (the scheduled GitHub Actions job commits it
to the `journal-data` branch) and be inspected in any spreadsheet.
"""

from __future__ import annotations

import csv
import math
import os
from collections.abc import Callable
from datetime import date

from . import http
from .providers import market

FIELDS = ["date", "symbol", "asset_class", "price", "score", "label", "coverage",
          "trend", "momentum", "smart_money", "insider", "news", "version"]
COMPONENTS = ["trend", "momentum", "smart_money", "insider", "news"]
HORIZONS = (21, 63, 126)  # trading days; crypto is scaled to calendar days
# Bump whenever the composite score's definition changes, so the track record never mixes
# formulas. Rows written before versioning existed have no version and count as "1".
#   1: news mood averaged only headlines with sentiment words; journal ran without SEC data
#   2: news mood averages all headlines; journal records 13F and insider components
SCORE_VERSION = "2"

DEFAULT_UNIVERSE = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM",
                    "XOM", "BRK-B", "BTC-USD", "ETH-USD", "SOL-USD"]
REMOTE_URL = os.environ.get(
    "MT_JOURNAL_REMOTE",
    "https://raw.githubusercontent.com/jwill736/market-tracker/journal-data/signal_journal.csv")


def journal_path() -> str:
    return os.environ.get("MT_JOURNAL_PATH", "signal_journal.csv")


# ------------------------------------------------------------------ storage

def _num(value: str) -> float | None:
    return float(value) if value not in ("", None) else None


def load(path: str | None = None) -> list[dict]:
    path = path or journal_path()
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        rows = []
        for r in csv.DictReader(fh):
            row = {k: r.get(k, "") or "" for k in FIELDS}
            row["version"] = row["version"] or "1"
            for k in ("price", "score", "coverage", *COMPONENTS):
                row[k] = _num(row[k])
            rows.append(row)
        return rows


def save(rows: list[dict], path: str | None = None) -> None:
    path = path or journal_path()
    rows = sorted(rows, key=lambda r: (r["date"], r["symbol"]))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: "" if r.get(k) is None else r[k] for k in FIELDS})


def entry_from_analysis(analysis: dict, on: date | None = None) -> dict | None:
    sig, quote = analysis.get("signal"), analysis.get("quote")
    if not sig or not quote:
        return None
    comps = sig["components"]
    return {
        "date": (on or date.today()).isoformat(),
        "symbol": analysis["symbol"],
        "asset_class": analysis["asset_class"],
        "price": quote["price"],
        "score": sig["score"],
        "label": sig["label"],
        "coverage": sig["coverage"],
        **{c: (comps[c]["score"] if comps.get(c) else None) for c in COMPONENTS},
        "version": SCORE_VERSION,
    }


def record(entries: list[dict], path: str | None = None) -> int:
    """Upsert entries keyed by (date, symbol); returns how many were written."""
    existing = {(r["date"], r["symbol"]): r for r in load(path)}
    for e in entries:
        existing[(e["date"], e["symbol"])] = e
    save(list(existing.values()), path)
    return len(entries)


def merge_csv_text(text: str, path: str | None = None) -> int:
    """Merge a remote journal (e.g. from the journal-data branch) into the local one."""
    tmp = (path or journal_path()) + ".remote"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        remote = load(tmp)
    finally:
        os.remove(tmp)
    return record(remote, path)


def sync_from_remote(path: str | None = None) -> int:
    return merge_csv_text(http.get(REMOTE_URL, ttl=0, as_json=False), path)


# ------------------------------------------------------------------ evaluation

def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation (the 'information coefficient' when x is a signal, y a forward return)."""
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return cov / math.sqrt(vx * vy) if vx and vy else None


def bars_for(horizon: int, asset_class: str) -> int:
    return round(horizon * 365 / 252) if asset_class == "crypto" else horizon


def forward_return(history: list[tuple[str, float]], start: str, bars: int) -> float | None:
    """Return from the first close on/after `start` to `bars` closes later."""
    idx = next((i for i, (d, _) in enumerate(history) if d >= start), None)
    if idx is None or idx + bars >= len(history):
        return None
    p0 = history[idx][1]
    return history[idx + bars][1] / p0 - 1 if p0 else None


def evaluate(rows: list[dict], history_fn: Callable[[str], list[tuple[str, float]]],
             horizons: tuple[int, ...] = HORIZONS, version: str | None = SCORE_VERSION) -> dict:
    """Score the journal. Only rows from `version` count (None = all), so a formula change
    never blends two different scores into one track record."""
    all_rows = rows
    rows = [r for r in rows if version is None or r.get("version", "1") == version]
    histories: dict[str, list[tuple[str, float]]] = {}
    errors: list[str] = []
    for sym in sorted({r["symbol"] for r in rows}):
        try:
            histories[sym] = history_fn(sym)
        except (http.DataUnavailable, KeyError, ValueError) as exc:
            errors.append(f"{sym}: {exc}")

    dates = sorted({r["date"] for r in rows})
    report = {
        "entries": len(rows),
        "symbols": len({r["symbol"] for r in rows}),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "horizons": [],
        "errors": errors,
        "score_version": version,
        "excluded_other_versions": len(all_rows) - len(rows),
    }
    for h in horizons:
        obs = []
        for r in rows:
            hist = histories.get(r["symbol"])
            if not hist or r["score"] is None:
                continue
            fwd = forward_return(hist, r["date"], bars_for(h, r["asset_class"]))
            if fwd is not None:
                obs.append((r, fwd))
        scores = [r["score"] for r, _ in obs]
        rets = [f for _, f in obs]
        distinct_dates = len({r["date"] for r, _ in obs})
        # Daily entries with a 21-day horizon overlap ~21x; independent periods are far fewer.
        effective_n = len({r["symbol"] for r, _ in obs}) * max(1, math.ceil(distinct_dates / h)) if obs else 0
        buckets = []
        for label in ("Strong bullish", "Bullish", "Neutral", "Bearish", "Strong bearish"):
            sel = [f for r, f in obs if r["label"] == label]
            if sel:
                buckets.append({"label": label, "n": len(sel), "avg_return": sum(sel) / len(sel),
                                "hit_rate": sum(1 for f in sel if f > 0) / len(sel)})
        bull = [f for r, f in obs if r["score"] >= 15]
        bear = [f for r, f in obs if r["score"] <= -15]
        comp_ic = {}
        for c in COMPONENTS:
            pairs = [(r[c], f) for r, f in obs if r[c] is not None]
            comp_ic[c] = spearman([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 10 else None
        report["horizons"].append({
            "horizon_days": h,
            "n": len(obs),
            "effective_n": effective_n,
            "ic": spearman(scores, rets),
            "ic_se": ic_standard_error(effective_n),
            "component_ic": comp_ic,
            "buckets": buckets,
            "bull_minus_bear": (sum(bull) / len(bull) - sum(bear) / len(bear)) if bull and bear else None,
            "avg_return_all": sum(rets) / len(rets) if rets else None,
        })
    report["verdict"] = verdict(report)
    return report


def ic_standard_error(effective_n: int) -> float | None:
    """Approximate sampling error of a rank correlation measured on n independent pairs."""
    return 1 / math.sqrt(effective_n - 1) if effective_n > 1 else None


def verdict(report: dict) -> str:
    first = report["horizons"][0] if report["horizons"] else None
    if not first or first["effective_n"] < 50:
        n = first["effective_n"] if first else 0
        return (f"Too early to judge: ~{n} independent observations with known outcomes. "
                "Roughly 50+ are needed before the numbers mean anything; until then treat the "
                "score as unproven.")
    ic, se = first["ic"], first["ic_se"]
    if ic is None:
        return "Not enough variation in scores to measure."
    band = f"IC {ic:+.2f} ± {se:.2f}"
    # Only call an edge when it is at least ~2 standard errors from zero; smaller values are
    # indistinguishable from luck at this sample size.
    if ic > 2 * se:
        if ic >= 0.10:
            return f"Encouraging: 1-month {band}. Higher scores have preceded higher returns."
        return f"Small but real edge: 1-month {band}. Useful for ranking; don't size up on it alone."
    if ic < -2 * se:
        return f"Wrong-way: 1-month {band}. Higher scores have preceded lower returns — revisit the weights."
    return (f"No detectable edge yet: 1-month {band}. That's within what luck produces at this "
            "sample size; keep recording.")


def report_markdown(report: dict) -> str:
    excluded = report.get("excluded_other_versions")
    lines = ["## Signal track record", "",
             f"{report['entries']} journal entries across {report['symbols']} symbols "
             f"({report['first_date']} → {report['last_date']}), score version {report.get('score_version')}."
             + (f" {excluded} entries from older score versions excluded." if excluded else ""), "",
             f"**Verdict:** {report['verdict']}", ""]
    for h in report["horizons"]:
        ic = "—" if h["ic"] is None else f"{h['ic']:+.3f}" + (f" ± {h['ic_se']:.3f}" if h["ic_se"] else "")
        lines += [f"### {h['horizon_days']}-day forward returns",
                  f"{h['n']} observations (~{h['effective_n']} independent) · IC {ic}", ""]
        if h["buckets"]:
            lines += ["| Label | n | Avg return | Hit rate |", "|---|---:|---:|---:|"]
            lines += [f"| {b['label']} | {b['n']} | {b['avg_return'] * 100:+.2f}% | {b['hit_rate'] * 100:.0f}% |"
                      for b in h["buckets"]]
            lines.append("")
    return "\n".join(lines)


def history_closes(symbol: str) -> list[tuple[str, float]]:
    return [(b.date, b.close) for b in market.get_history(symbol, 1300)]
