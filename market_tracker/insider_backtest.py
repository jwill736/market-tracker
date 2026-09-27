"""Has insider buying beaten the market? A replay of open-market purchases since 2013.

The sleepers page leans on "opportunistic" insider buying (Cohen, Malloy & Pomorski 2012). This
checks it on the SEC's own Form 4 data sets before any money follows it:

- events: a company's officers and directors buying their own stock in the open market (code P,
  not under a 10b5-1 plan), $100,000 or more in a month, dated by the day the Form 4 was *filed*;
- routine vs opportunistic: an insider who also bought in the same calendar month in each of the
  three previous years is routine (in this replay only purchases count toward the habit; the app's
  live filter also counts sales, so it calls a few more buyers routine);
- cluster: two or more insiders buying within the month;
- size: market value at the time (price times the shares on the company's latest cover page);
- returns: bought at the close of the first trading day after the filing, held 3, 6 and 12
  months, against SPY over the same days.

Averaging events would count one bad month many times, so the statistics are "calendar time":
each month's events are averaged into one number, and the t-statistic is over months (with
Newey-West errors for the overlapping 6- and 12-month holds).

Only companies listed today are covered (their CIKs map to tickers), so insiders who bought
into a company that later went bust are missing: the same survivorship problem as the screen,
and it flatters these numbers.
"""

from __future__ import annotations

import bisect
import csv
import io
import math
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone

from . import http

FILE = "insider_backtest.json"
MIN_VALUE = 100_000.0
HOLD = {"3m": 63, "6m": 126, "12m": 252}
START_QUARTER = "2013q1"
SIZE_BUCKETS = [("under $300M", 0, 3e8), ("$300M-$2B", 3e8, 2e9), ("$2B-$10B", 2e9, 1e10), ("$10B+", 1e10, float("inf"))]


@dataclass
class Buy:
    cik: str
    insider: str        # name key: first two words, lower case
    role: str
    trade_date: str
    filed: str
    value: float


def name_key(name: str) -> str:
    from .insiders import _name_key
    return _name_key(name)


def parse_zip(blob: bytes, ciks: set[str]) -> list[Buy]:
    """Open-market purchases (not 10b5-1) by the issuers in `ciks` from one quarterly data set."""
    from .history import _rows, _sec_date
    z = zipfile.ZipFile(io.BytesIO(blob))
    subs: dict[str, tuple[str, str]] = {}
    for r in _rows(z, "SUBMISSION.TSV"):
        cik = (r.get("ISSUERCIK") or "").zfill(10)
        plan = (r.get("AFF10B5ONE") or "").strip().lower() in ("1", "true")
        if r.get("DOCUMENT_TYPE") == "4" and cik in ciks and not plan:
            subs[r["ACCESSION_NUMBER"]] = (cik, _sec_date(r.get("FILING_DATE", "")))
    owners: dict[str, tuple[str, str]] = {}
    for r in _rows(z, "REPORTINGOWNER.TSV"):
        acc = r["ACCESSION_NUMBER"]
        if acc in subs and acc not in owners:
            owners[acc] = (r.get("RPTOWNERNAME") or "", " ".join((r.get("RPTOWNER_RELATIONSHIP") or "", r.get("RPTOWNER_TITLE") or "")).lower())
    out = []
    for r in _rows(z, "NONDERIV_TRANS.TSV"):
        acc = r["ACCESSION_NUMBER"]
        if acc not in subs or (r.get("TRANS_CODE") or "").strip() != "P":
            continue
        name, role = owners.get(acc, ("", ""))
        if role and not any(w in role for w in ("director", "officer")):
            continue                      # 10% owners (often funds) buy for other reasons
        try:
            v = float(r.get("TRANS_SHARES") or 0) * float(r.get("TRANS_PRICEPERSHARE") or 0)
        except ValueError:
            continue
        if v <= 0:
            continue
        cik, filed = subs[acc]
        out.append(Buy(cik, name_key(name), role, _sec_date(r.get("TRANS_DATE", "")), filed, v))
    return out


def events(buys: list[Buy]) -> list[dict]:
    """One event per company per month of filing: total bought, how many insiders, whether any buyer
    was opportunistic (not a same-month habit in each of the 3 years before)."""
    habit: dict[tuple[str, str], set[tuple[int, int]]] = defaultdict(set)
    for b in buys:
        if len(b.trade_date) >= 7:
            habit[(b.cik, b.insider)].add((int(b.trade_date[:4]), int(b.trade_date[5:7])))
    groups: dict[tuple[str, str], list[Buy]] = defaultdict(list)
    for b in buys:
        if b.filed:
            groups[(b.cik, b.filed[:7])].append(b)
    out = []
    for (cik, month), bs in groups.items():
        total = sum(b.value for b in bs)
        if total < MIN_VALUE:
            continue

        def routine(b: Buy) -> bool:
            y, m = int(b.trade_date[:4]), int(b.trade_date[5:7])
            seen = habit[(b.cik, b.insider)]
            return all((y - k, m) in seen for k in (1, 2, 3))
        people = {b.insider for b in bs}
        opp = [b for b in bs if not routine(b)]
        out.append({"cik": cik, "month": month, "filed": max(b.filed for b in bs), "value": round(total),
                    "insiders": len(people), "cluster": len(people) >= 2, "opportunistic": bool(opp),
                    "ceo_cfo": any(any(w in b.role for w in ("ceo", "cfo", "chief executive", "chief financial")) for b in bs)})
    return sorted(out, key=lambda e: e["filed"])


def _ret(days: list[str], closes: list[float], start: str, n: int) -> tuple[float | None, int | None]:
    """Return from the first close after `start` to n trading days later; and the entry index."""
    i = bisect.bisect_right(days, start)
    if i + n >= len(days):
        return None, (i if i < len(days) else None)
    a, b = closes[i], closes[i + n]
    return (b / a - 1 if a > 0 else None), i


def score(evts: list[dict], prices: dict[str, list[tuple[str, float]]], sym_of: dict[str, str], spy: list[tuple[str, float]],
          shares_at=None, traded: dict[str, list[tuple[str, float]]] | None = None) -> list[dict]:
    """Adds each horizon's return and SPY's, and the size at the time (from the price as it traded
    that day, never the split-adjusted one: see screen_backtest.Prices)."""
    traded_at = {s: dict(v) for s, v in (traded or {}).items()}
    split = {s: ([d for d, _ in v], [c for _, c in v]) for s, v in prices.items() if v}
    sd, sc = [d for d, _ in spy], [c for _, c in spy]
    out = []
    for e in evts:
        sym = sym_of.get(e["cik"])
        if sym not in split:
            continue
        days, closes = split[sym]
        row = dict(e, symbol=sym)
        entry = None
        for h, n in HOLD.items():
            r, i = _ret(days, closes, e["filed"], n)
            sp, _ = _ret(sd, sc, e["filed"], n)
            row[h] = r
            row[f"{h}_spy"] = sp
            entry = entry if entry is not None else i
        if entry is None:
            continue
        row["entry"] = days[entry]
        px = traded_at[sym].get(days[entry]) if sym in traded_at else (closes[entry] if traded is None else None)
        sh = shares_at(e["cik"], e["filed"]) if shares_at else None
        row["cap"] = px * sh if px and sh else None
        out.append(row)
    return out


def calendar_stats(rows: list[dict], h: str) -> dict | None:
    """Average edge over SPY per calendar month of events, then mean, share of months ahead, and a
    t-statistic across months."""
    from .screen_backtest import newey_west_t
    by_month: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get(h) is not None and r.get(f"{h}_spy") is not None:
            by_month[r["month"]].append(r[h] - r[f"{h}_spy"])
    months = sorted(by_month)
    if len(months) < 12:
        return None
    edges = [sum(by_month[m]) / len(by_month[m]) for m in months]
    n = len(edges)
    mean = sum(edges) / n
    return {"events": sum(len(v) for v in by_month.values()), "months": n, "avg_edge": round(mean * 100, 2),
            "beat_pct": round(sum(e > 0 for e in edges) / n * 100), "worst_month": round(min(edges) * 100, 1),
            "t": newey_west_t(edges, max(0, HOLD[h] // 21 - 1))}


def summarize(rows: list[dict]) -> dict:
    groups = {
        "all": ("Every insider purchase month ($100k+)", lambda r: True),
        "opportunistic": ("Opportunistic (not a yearly habit)", lambda r: r["opportunistic"]),
        "routine": ("Routine only (same month 3 years running)", lambda r: not r["opportunistic"]),
        "cluster": ("Opportunistic, 2+ insiders", lambda r: r["opportunistic"] and r["cluster"]),
        "big": ("Opportunistic, $1M+", lambda r: r["opportunistic"] and r["value"] >= 1_000_000),
    }
    for label, lo, hi in SIZE_BUCKETS:
        groups[f"size {label}"] = (f"Opportunistic, company worth {label}", lambda r, lo=lo, hi=hi: r["opportunistic"] and r.get("cap") and lo <= r["cap"] < hi)
    out = {}
    for key, (label, keep) in groups.items():
        sel = [r for r in rows if keep(r)]
        out[key] = {"label": label, **{h: calendar_stats(sel, h) for h in HOLD}}
    return out


def verdict(summary: dict) -> str:
    o, r = summary["opportunistic"].get("6m"), summary["routine"].get("6m")
    if not o:
        return "Not enough history to judge."
    line = (f"Opportunistic insider buying: {o['avg_edge']:+.1f} points against SPY over 6 months on average, ahead in {o['beat_pct']}% of "
            f"months ({o['events']:,} purchase months)" + (f", t = {o['t']:.1f}" if o["t"] is not None else "") +
            (": statistically solid." if o["t"] is not None and abs(o["t"]) >= 2 else ": could be luck."))
    if r:
        line += f" Routine buying: {r['avg_edge']:+.1f}."
    sizes = [(k, v["6m"]) for k, v in summary.items() if k.startswith("size ") and v.get("6m")]
    if sizes:
        k, v = max(sizes, key=lambda kv: kv[1]["avg_edge"])
        line += f" Strongest in companies worth {k[5:]} ({v['avg_edge']:+.1f})."
    return line + " Only companies listed today are covered, which flatters these numbers."


# ------------------------------------------------------------------ the job

def run(start_quarter: str = START_QUARTER, log=print, listed_fn=None, fetch=None, prices=None, shares_fn=None) -> dict:
    from . import screen, screen_backtest
    from .history import _sec_headers, insider_zip_urls
    from .providers import sec
    tmap = sec.ticker_map()
    lst = (listed_fn or screen.listed)()
    sym_of: dict[str, str] = {}
    for sym, info in sorted(lst.items(), key=lambda kv: (-kv[1]["cap"], len(kv[0]))):
        cik = tmap.cik_for(sym)
        if cik and cik not in sym_of:
            sym_of[cik] = sym
    buys: list[Buy] = []
    urls = insider_zip_urls(start_quarter) if fetch is None else list(fetch)
    for u in urls:
        try:
            blob = http.get_bytes(u, headers=_sec_headers()) if fetch is None else fetch[u]
            part = parse_zip(blob, set(sym_of))
        except (http.DataUnavailable, zipfile.BadZipFile, KeyError, StopIteration, csv.Error) as exc:
            log(f"  {u}: {exc}")
            continue
        buys.extend(part)
        log(f"  {u.rsplit('/', 1)[-1]}: {len(part)} purchases")
    evts = events(buys)
    log(f"{len(buys):,} open-market purchases, {len(evts):,} company-months of $100k+")
    need = sorted({sym_of[e["cik"]] for e in evts} | {"SPY"})
    if prices is None:
        p = screen_backtest.load_prices([s for s in need if s != "SPY"], f"{start_quarter[:4]}-01-01", log=log)
        prices = {s: [(p.calendar[i], c) for i, c in enumerate(arr) if not math.isnan(c)] for s, arr in p.series.items()}
        traded = {s: [(p.calendar[i], c) for i, c in enumerate(arr) if not math.isnan(c)] for s, arr in p.raw.items()}
    else:
        traded = None
    shares_at = shares_fn or _shares_lookup(start_quarter, log)
    rows = score(evts, prices, sym_of, prices.get("SPY", []), shares_at, traded)
    summary = summarize(rows)
    return {"as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"), "since": start_quarter, "events": len(rows),
            "summary": summary, "verdict": verdict(summary),
            "recent": [{k: r.get(k) for k in ("symbol", "filed", "value", "insiders", "opportunistic", "3m", "3m_spy")} for r in rows[-40:]]}


def _shares_lookup(start_quarter: str, log=print):
    """shares(cik, day): cover-page shares outstanding from the latest quarter before that day."""
    from . import screen
    y0 = int(start_quarter[:4])
    frames: list[tuple[str, dict[int, float]]] = []
    for y in range(y0 - 1, date.today().year + 1):
        for q in range(1, 5):
            end = date(y, 3 * q, 28).isoformat()
            if end > date.today().isoformat():
                continue
            frames.append((end, screen.frame("EntityCommonStockSharesOutstanding", f"CY{y}Q{q}I", "shares", "dei")))
    log(f"share counts from {len(frames)} quarters")

    def shares(cik: str, day: str) -> float | None:
        c = int(cik)
        for end, f in reversed(frames):
            if end <= day and c in f:
                return f[c]
        return None
    return shares


def load(get=None) -> dict | None:
    from .pulse import DATA_URL
    try:
        return (get or (lambda u: http.get(u, ttl=6 * 3600)))(f"{DATA_URL}/{FILE}")
    except http.DataUnavailable:
        return None
