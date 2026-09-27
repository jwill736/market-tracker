"""Paper portfolios: each idea list run as a real (pretend-money) portfolio, month by month.

The idea log scores single ideas; this answers the question you'd actually act on: "if I had put
equal money in this list every month, where would I be against VOO?". On the first weekday run of
each month the GitHub idea-log job records a book per list (the members and their prices at that
close) in paper_books.jsonl on journal-data, never edited afterwards. Each book is held until the
next month's book, and the months are chained into one line per list.

Lists: the screen's top 20 worth $2B+, its top 20 small and mid caps, sleepers with 2+ pieces of
evidence, spin-offs in their first year, and the screen's bottom 20 worth $2B+ (the avoid list:
it should trail VOO if the warning means anything).
"""

from __future__ import annotations

import bisect
import json
import os
from datetime import date

from . import http

FILE = "paper_books.jsonl"
BENCH = "VOO"
BOOK_SIZE = 20
LISTS = {
    "screen_2b": "Screen: top 20 worth $2B+",
    "screen_small_mid": "Screen: top 20 small and mid",
    "sleepers": "Sleepers (2+ signals)",
    "spinoffs": "Spin-offs in their first year",
    "bottom": "Screen: bottom 20 worth $2B+ (avoid list)",
}


def due(books: list[dict], today: date) -> bool:
    """A new set of books is due when this month has none yet."""
    return not any(b["month"] == today.isoformat()[:7] for b in books)


def members(screen_data: dict | None, sleepers: list[dict], spinoffs: list[dict]) -> dict[str, list[str]]:
    d = screen_data or {}
    return {"screen_2b": [r["symbol"] for r in d.get("top_all", [])[:BOOK_SIZE]],
            "screen_small_mid": [r["symbol"] for r in d.get("small_mid", [])[:BOOK_SIZE]],
            "sleepers": [r["symbol"] for r in sleepers if r.get("evidence", 0) >= 2][:BOOK_SIZE],
            "spinoffs": [r["ticker"] for r in spinoffs if r.get("stage") == "trading" and r.get("days_trading", 0) <= 250][:BOOK_SIZE],
            "bottom": [r["symbol"] for r in d.get("bottom", [])[:BOOK_SIZE]]}


def make_books(today: date, lists: dict[str, list[str]], price_fn) -> list[dict]:
    """One book per non-empty list, priced now; names without a price are left out (and say so)."""
    out = []
    cache: dict[str, float | None] = {}
    for key, syms in lists.items():
        prices, skipped = {}, []
        for s in syms:
            if s not in cache:
                try:
                    cache[s] = price_fn(s)
                except Exception:  # noqa: BLE001 - no price today: not in this month's book
                    cache[s] = None
            if cache[s]:
                prices[s] = round(float(cache[s]), 4)
            else:
                skipped.append(s)
        if prices:
            out.append({"month": today.isoformat()[:7], "day": today.isoformat(), "list": key, "prices": prices, "skipped": skipped})
    return out


def _close_on_or_after(days: list[str], closes: list[float], day: str) -> float | None:
    i = bisect.bisect_left(days, day)
    return closes[i] if i < len(days) else None


def performance(books: list[dict], history_fn, today: date) -> list[dict]:
    """history_fn(symbol) -> [(date, close)]. Per list: each month's equal-weight return against VOO,
    chained into growth of $10,000."""
    cache: dict[str, tuple[list[str], list[float]]] = {}

    def series(sym):
        if sym not in cache:
            try:
                bars = history_fn(sym)
            except Exception:  # noqa: BLE001
                bars = []
            cache[sym] = ([d for d, _ in bars], [c for _, c in bars])
        return cache[sym]
    bd, bc = series(BENCH)
    out = []
    for key, label in LISTS.items():
        mine = sorted((b for b in books if b["list"] == key), key=lambda b: b["day"])
        if not mine:
            continue
        months, grow, bench = [], 10000.0, 10000.0
        for i, b in enumerate(mine):
            end = mine[i + 1]["day"] if i + 1 < len(mine) else today.isoformat()
            rets = []
            for s, p0 in b["prices"].items():
                d, c = series(s)
                p1 = _close_on_or_after(d, c, end) if i + 1 < len(mine) else (c[-1] if c else None)
                if p1 and p0:
                    rets.append(p1 / p0 - 1)
            v0, v1 = _close_on_or_after(bd, bc, b["day"]), (_close_on_or_after(bd, bc, end) if i + 1 < len(mine) else (bc[-1] if bc else None))
            if not rets or not (v0 and v1):
                continue
            r, vr = sum(rets) / len(rets), v1 / v0 - 1
            grow *= 1 + r
            bench *= 1 + vr
            months.append({"from": b["day"], "to": end, "names": len(rets), "return": round(r * 100, 2), "voo": round(vr * 100, 2)})
        if months:
            out.append({"list": key, "label": label, "months": months, "value": round(grow), "voo_value": round(bench),
                        "ahead": round(grow - bench), "open": mine[-1]["day"], "holding": sorted(mine[-1]["prices"])})
    return out


def run_job(folder: str, today: date, lists_fn, price_fn, log=print) -> int:
    """For the GitHub job: add this month's books if due. Returns how many books were added."""
    path = os.path.join(folder, FILE)
    books = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            books = [json.loads(ln) for ln in fh if ln.strip()]
    if not due(books, today):
        log(f"paper books for {today.isoformat()[:7]} already recorded")
        return 0
    new = make_books(today, lists_fn(), price_fn)
    with open(path, "a", encoding="utf-8") as fh:
        for b in new:
            fh.write(json.dumps(b, sort_keys=True, separators=(",", ":")) + "\n")
    for b in new:
        log(f"  paper book {b['list']}: {len(b['prices'])} names")
    return len(new)


def load(get=None) -> list[dict]:
    from .pulse import DATA_URL
    try:
        text = (get or (lambda u: http.get(u, ttl=3600, as_json=False)))(f"{DATA_URL}/{FILE}")
    except http.DataUnavailable:
        return []
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]
