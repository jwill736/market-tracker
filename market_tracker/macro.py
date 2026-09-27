"""The economy panel: a handful of well-studied gauges from the St. Louis Fed (FRED), free and
without a key, and what they mean for how fast to put new money in.

It never says "sell" or "go to cash". Over long periods, time in the market has beaten trying
to time it, and every gauge here has false alarms. What it can sensibly change is pacing: when
credit markets are stressed and a recession gauge has triggered, spreading new money over a few
months costs little and avoids putting it all in the week before a further fall.

Gauges:
- 10-year Treasury yield and the 10-year minus 3-month curve (an inverted curve has preceded
  most US recessions since the 1960s, with long and variable lags);
- the high-yield credit spread (what riskier companies pay over Treasuries; a fast widening is
  the market pricing defaults);
- the Sahm rule (the 3-month average unemployment rate 0.5 points above its 12-month low has
  marked the start of every recession since 1970, in real time, with a few false alarms);
- the Chicago Fed's financial conditions index (above zero: tighter than average);
- weekly jobless claims, Brent oil, and new factory orders for capital goods.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import date, timedelta

from . import http

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
UA = {"User-Agent": "Plumbline/1.0 (https://github.com/jwill736/market-tracker; personal portfolio app)"}
SERIES = {
    "DGS10": ("10-year Treasury yield", "%"),
    "T10Y3M": ("Yield curve: 10-year minus 3-month", "pts"),
    "BAMLH0A0HYM2": ("High-yield credit spread", "pts"),
    "SAHMREALTIME": ("Sahm recession gauge", "pts"),
    "NFCI": ("Financial conditions (Chicago Fed)", ""),
    "ICSA": ("Weekly jobless claims", ""),
    "DCOILBRENTEU": ("Brent oil", "$"),
    "NEWORDER": ("New orders, core capital goods", "$M"),
}
CACHE_SECONDS = 6 * 3600
_cache: dict[str, tuple[float, dict]] = {}


def parse(text: str) -> dict[str, list[tuple[str, float]]]:
    """{series: [(date, value)]} from FRED's multi-series CSV ('.' marks a missing value)."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}
    head = rows[0]
    out: dict[str, list[tuple[str, float]]] = {h: [] for h in head[1:]}
    for r in rows[1:]:
        for h, v in zip(head[1:], r[1:]):
            try:
                out[h].append((r[0], float(v)))
            except ValueError:
                continue
    return out


def load(get=None) -> dict[str, list[tuple[str, float]]]:
    """One request per series: asking for series of different frequencies at once returns a zip."""
    hit = _cache.get("fred")
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    start = (date.today() - timedelta(days=3 * 366)).isoformat()
    get = get or (lambda sid: http.get(FRED_CSV, params={"id": sid, "cosd": start}, ttl=0, as_json=False, headers=UA))
    data: dict[str, list[tuple[str, float]]] = {}
    for sid in SERIES:
        try:
            data.update({k: v for k, v in parse(get(sid)).items() if k == sid})
        except http.DataUnavailable:
            continue
    if not any(data.values()):
        raise http.DataUnavailable("FRED returned no data")
    _cache["fred"] = (time.time(), data)
    return data


def _ago(series: list[tuple[str, float]], days: int) -> float | None:
    if not series:
        return None
    cut = (date.fromisoformat(series[-1][0]) - timedelta(days=days)).isoformat()
    before = [v for d, v in series if d <= cut]
    return before[-1] if before else None


def gauges(data: dict[str, list[tuple[str, float]]]) -> list[dict]:
    out = []
    for sid, (name, unit) in SERIES.items():
        s = data.get(sid) or []
        if not s:
            continue
        last_d, last = s[-1]
        m3 = _ago(s, 91)
        y1 = _ago(s, 365)
        state, note = "normal", ""
        if sid == "T10Y3M" and last < 0:
            state, note = "watch", "Inverted: long rates below short ones, which has come before most recessions (lags of 6-24 months)."
        elif sid == "BAMLH0A0HYM2":
            if last >= 6 or (m3 is not None and last - m3 >= 1.5):
                state, note = "stress", "Credit markets are pricing more defaults."
            elif last >= 4.5 or (m3 is not None and last - m3 >= 0.75):
                state, note = "watch", "Credit spreads are widening."
        elif sid == "SAHMREALTIME":
            if last >= 0.5:
                state, note = "stress", "Triggered: unemployment has risen enough that past recessions were already under way."
            elif last >= 0.3:
                state, note = "watch", "Rising toward the 0.5 trigger."
        elif sid == "NFCI" and last > 0:
            state, note = "watch", "Financial conditions are tighter than average."
        elif sid == "ICSA" and y1 and last > y1 * 1.25:
            state, note = "watch", "Jobless claims are up a quarter on a year ago."
        elif sid == "DCOILBRENTEU" and m3 and last > m3 * 1.3:
            state, note = "watch", "Oil is up 30%+ in three months: a squeeze on consumers and margins."
        elif sid == "NEWORDER" and y1 and last < y1 * 0.95:
            state, note = "watch", "Businesses are ordering less equipment than a year ago."
        out.append({"id": sid, "name": name, "unit": unit, "value": last, "as_of": last_d, "three_months_ago": m3, "year_ago": y1,
                    "state": state, "note": note, "history": s[-260:] if sid not in ("ICSA", "NEWORDER") else s[-160:]})
    return out


def pace(g: list[dict]) -> dict:
    """How to put new money in. Changes pacing only; never timing in or out of the market."""
    stress = [x for x in g if x["state"] == "stress"]
    watch = [x for x in g if x["state"] == "watch"]
    if len(stress) >= 2 or (stress and len(watch) >= 2):
        return {"level": "stressed", "tranches": 6,
                "text": "Several gauges show stress. Keep investing, but split new money into 6 monthly pieces instead of all at once. Don't sell because of this."}
    if stress or len(watch) >= 3:
        return {"level": "cautious", "tranches": 3,
                "text": "Some gauges are flashing. Keep investing, but split new money into 3 monthly pieces. Don't sell because of this."}
    return {"level": "normal", "tranches": 1, "text": "Nothing unusual. Invest new money as planned."}


def build(get=None) -> dict:
    g = gauges(load(get))
    return {"gauges": g, "pace": pace(g), "as_of": max((x["as_of"] for x in g), default=None)}
