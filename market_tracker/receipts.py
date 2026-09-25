"""Receipts: a public, tamper-evident record of every call Plumbline makes.

Two kinds of call are recorded:
- insider alerts (alert_log.csv, written by the watcher and the daily scan), and
- early-wire sightings (early_calls.csv): the strongest tickers the early wire flags, logged
  once per day with the price when first seen.

When a day is over it is sealed: its calls are written as canonical JSON, hashed with SHA-256,
and chained to the previous day's seal (receipts.jsonl). Changing, adding or dropping any past
call changes that day's digest and breaks every later seal, and the chain lives in git on the
journal-data branch, where each commit carries GitHub's own timestamp. `mt receipts verify`
recomputes everything from the raw files.

Nothing is left out for looking bad: misses stay in the record, which is the point.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timezone

from . import http, scorecard
from .providers import market

GENESIS = "0" * 64
EARLY_FILE = "early_calls.csv"
CHAIN_FILE = "receipts.jsonl"
SNAPSHOT_TOP = 12           # strongest sightings recorded per snapshot
MIN_STRENGTH = 30


@dataclass
class EarlyCall:
    day: str
    time: str        # HH:MM UTC when first seen
    symbol: str
    kinds: str
    strength: float
    early: int       # 1 = two or fewer mainstream articles in 24 h
    price: float
    headline: str


EARLY_FIELDS = [f.name for f in fields(EarlyCall)]


def load_early(path: str) -> list[EarlyCall]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [EarlyCall(r["day"], r["time"], r["symbol"], r["kinds"], float(r["strength"]), int(r["early"]),
                          float(r["price"]), r["headline"]) for r in csv.DictReader(fh)]


def save_early(calls: list[EarlyCall], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=EARLY_FIELDS)
        w.writeheader()
        for c in sorted(calls, key=lambda c: (c.day, c.time, c.symbol)):
            w.writerow(asdict(c))


def record_sightings(calls: list[EarlyCall], signals: list[dict], now: datetime,
                     quote_fn: Callable[[str], market.Quote] = market.get_quote,
                     top: int = SNAPSHOT_TOP, min_strength: float = MIN_STRENGTH) -> list[EarlyCall]:
    """Add today's first sighting of each of the strongest signals (early.build's output),
    priced now. Returns the new calls; `calls` is extended in place."""
    day = now.date().isoformat()
    have = {(c.day, c.symbol) for c in calls}
    new = []
    ranked = sorted((s for s in signals if s.get("strength", 0) >= min_strength and s.get("kinds") != ["depeg"]),
                    key=lambda s: (not s.get("early"), -s["strength"]))
    for s in ranked:
        if len(new) >= top:
            break
        if (day, s["symbol"]) in have:
            continue
        try:
            price = quote_fn(s["symbol"]).price
        except (http.DataUnavailable, KeyError, ValueError):
            continue
        if not price:
            continue
        headline = ((s.get("signals") or [{}])[0].get("headline") or "")
        c = EarlyCall(day, now.strftime("%H:%M"), s["symbol"], ",".join(s.get("kinds", [])), round(float(s["strength"]), 1),
                      int(bool(s.get("early"))), round(float(price), 6), headline[:200])
        calls.append(c)
        have.add((day, c.symbol))
        new.append(c)
    return new


# ------------------------------------------------------------------ the chain

def day_items(day: str, early_calls: list[EarlyCall], alerts_log: list[scorecard.AlertRecord]) -> list[dict]:
    """Every call made on `day`, in a fixed order, as the dicts that get hashed."""
    items = [{"type": "early", "symbol": c.symbol, "time": c.time, "kinds": c.kinds, "strength": c.strength,
              "early": c.early, "price": c.price, "headline": c.headline}
             for c in early_calls if c.day == day]
    items += [{"type": "alert", "kind": r.kind, "symbol": r.symbol, "company": r.company, "detail": r.detail,
               "url": r.url} for r in alerts_log if r.alerted == day]
    return sorted(items, key=lambda i: json.dumps(i, sort_keys=True))


def canonical(items: list[dict]) -> bytes:
    return json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(items: list[dict]) -> str:
    return hashlib.sha256(canonical(items)).hexdigest()


def link(prev: str, day: str, dig: str) -> str:
    return hashlib.sha256(f"{prev}|{day}|{dig}".encode()).hexdigest()


def load_chain(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def save_chain(chain: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for s in chain:
            fh.write(json.dumps(s, sort_keys=True, separators=(",", ":")) + "\n")


def seal(chain: list[dict], early_calls: list[EarlyCall], alerts_log: list[scorecard.AlertRecord],
         today: date, now: datetime | None = None) -> list[dict]:
    """Seal every finished day (before `today`) that has calls and isn't sealed yet, oldest
    first. Days are never re-sealed: a call that shows up late for a sealed day is left out of
    the chain and reported by verify. Returns the new seals; `chain` is extended in place."""
    now = now or datetime.now(timezone.utc)
    sealed = {s["day"] for s in chain}
    last = chain[-1]["day"] if chain else ""
    days = sorted({c.day for c in early_calls} | {r.alerted for r in alerts_log})
    new = []
    for day in days:
        if day >= today.isoformat() or day in sealed or day <= last:
            continue
        items = day_items(day, early_calls, alerts_log)
        prev = chain[-1]["hash"] if chain else GENESIS
        dig = digest(items)
        s = {"day": day, "calls": len(items), "digest": dig, "prev": prev, "hash": link(prev, day, dig),
             "sealed_at": now.isoformat(timespec="seconds")}
        chain.append(s)
        new.append(s)
    return new


def verify(chain: list[dict], early_calls: list[EarlyCall], alerts_log: list[scorecard.AlertRecord]) -> list[str]:
    """Problems found recomputing the chain from the raw files (empty = intact)."""
    problems = []
    prev = GENESIS
    for s in chain:
        items = day_items(s["day"], early_calls, alerts_log)
        if digest(items) != s["digest"]:
            problems.append(f"{s['day']}: the recorded calls don't match the seal ({len(items)} now, {s['calls']} sealed)")
        if s["prev"] != prev:
            problems.append(f"{s['day']}: chained to {s['prev'][:12]}, expected {prev[:12]}")
        if link(s["prev"], s["day"], s["digest"]) != s["hash"]:
            problems.append(f"{s['day']}: seal hash doesn't match its contents")
        prev = s["hash"]
    return problems


# ------------------------------------------------------------------ the public view

def public_calls(early_calls: list[EarlyCall], chain: list[dict], now: datetime, *, delay_hours: int = 24,
                 horizon: int = 5, history_fn=None) -> dict:
    """Early-wire calls old enough to publish, scored `horizon` closes later against SPY, with
    each call's seal. Misses included."""
    from . import early
    cutoff = now.timestamp() - delay_hours * 3600
    seals = {s["day"]: s for s in chain}

    def ts(c):
        return datetime.fromisoformat(f"{c.day}T{c.time}:00+00:00").timestamp()
    shown = [c for c in early_calls if ts(c) <= cutoff]
    logged = [{"day": c.day, "symbol": c.symbol, "kinds": c.kinds, "early": c.early, "price": c.price} for c in shown]
    sc = early.scorecard(logged, horizon=horizon, history_fn=history_fn) if logged else None
    scored = {(r["day"], r["symbol"]): r for r in (sc or {}).get("rows", [])}
    rows = []
    for c in sorted(shown, key=lambda c: (c.day, c.time), reverse=True)[:120]:
        r = scored.get((c.day, c.symbol), {})
        seal_ = seals.get(c.day)
        rows.append({"day": c.day, "time": c.time, "symbol": c.symbol, "kinds": c.kinds, "early": bool(c.early),
                     "price": c.price, "headline": c.headline, "return_pct": r.get("return_pct"),
                     "benchmark_pct": r.get("benchmark_pct"), "excess_pct": r.get("excess_pct"),
                     "seal": seal_["hash"][:16] if seal_ else None})
    return {"horizon": horizon, "delay_hours": delay_hours, "summary": {k: (sc or {}).get(k) for k in ("all", "early", "pending", "by_kind")},
            "calls": rows, "chain": [{k: s[k] for k in ("day", "calls", "hash", "sealed_at")} for s in chain[-60:]],
            "chain_length": len(chain)}
