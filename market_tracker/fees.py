"""What your funds cost a year, in dollars, and a cheaper twin where one exists.

A fund's expense ratio comes out of your balance every day. 0.6% sounds small; on $10,000 over
30 years at 7% it leaves about $11,200 less than a 0.03% fund would (about 15% of the ending
balance). Expense ratios are read from each fund's public page on stockanalysis.com (checked
monthly); the cheaper twins are funds tracking the same or a near-identical index.
"""

from __future__ import annotations

import json
import os
import re
import time

from . import config, http
from .reading import BROWSER_UA

PAGE = "https://stockanalysis.com/etf/{sym}/"
CACHE_DAYS = 30
TWINS = {
    "SPY": ["VOO", "IVV", "SPLG"], "IVV": ["VOO", "SPLG"], "VOO": ["IVV", "SPLG"],
    "QQQ": ["QQQM"], "IWM": ["VTWO"], "DIA": [], "VTI": ["ITOT", "SCHB"], "ITOT": ["VTI", "SCHB"],
    "GLD": ["GLDM", "IAU"], "IAU": ["GLDM"], "EFA": ["IEFA", "VEA"], "EEM": ["IEMG", "VWO"],
    "VYM": ["SCHD"], "DVY": ["SCHD", "VYM"], "XLK": ["VGT"], "ARKK": [], "VUG": ["SCHG"], "VTV": ["SCHV"],
    "AGG": ["BND", "SCHZ"], "BND": ["AGG", "SCHZ"], "LQD": ["VCIT"], "TLT": ["VGLT"], "SHY": ["VGSH"],
}
_RE = re.compile(r"Expense Ratio\s*([\d.]+)%", re.I)


def _cache() -> str:
    d = os.path.join(os.path.dirname(os.path.abspath(config.settings.db_path)) or ".", "logo_cache", "funds")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "expense_ratios.json")


def expense_ratio(sym: str, get=None, now: float | None = None) -> float | None:
    """As a fraction (0.0003 for 0.03%), or None when the page has none (a stock, or unknown)."""
    now = now or time.time()
    try:
        with open(_cache()) as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(sym)
    if hit and now - hit["at"] < CACHE_DAYS * 86400:
        return hit["er"]
    er = None
    try:
        html = (get or http.get)(PAGE.format(sym=sym.lower()), headers={"User-Agent": BROWSER_UA}, ttl=86400, as_json=False)
        m = _RE.search(re.sub(r"<[^>]+>", " ", html))
        er = float(m.group(1)) / 100 if m else None
    except (http.DataUnavailable, ValueError):
        return hit["er"] if hit else None
    cache[sym] = {"er": er, "at": now}
    with open(_cache(), "w") as fh:
        json.dump(cache, fh)
    return er


def drag(value: float, er: float, years: int = 30, growth: float = 0.07) -> float:
    """Dollars less after `years` than with no fee at all (growth before fees)."""
    return value * ((1 + growth) ** years - (1 + growth - er) ** years)


def build(positions: list[dict], get=None) -> dict:
    from .providers import market
    rows = []
    for p in positions:
        sym, v = p["symbol"], p.get("market_value") or 0
        if market.asset_class(sym) != "stock" or not v:
            continue
        er = expense_ratio(sym, get)
        if er is None:
            continue
        twins = []
        for t in TWINS.get(sym, []):
            ter = expense_ratio(t, get)
            if ter is not None and ter < er:
                twins.append({"symbol": t, "expense_ratio": ter, "saves_per_year": round(v * (er - ter), 2),
                              "saves_30y": round(drag(v, er) - drag(v, ter), 0)})
        rows.append({"symbol": sym, "value": round(v, 2), "expense_ratio": er, "per_year": round(v * er, 2),
                     "drag_30y": round(drag(v, er), 0), "cheaper": sorted(twins, key=lambda t: t["expense_ratio"])})
    rows.sort(key=lambda r: -r["per_year"])
    return {"funds": rows, "per_year": round(sum(r["per_year"] for r in rows), 2)}
