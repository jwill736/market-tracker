"""Which insider purchases carry information: routine versus opportunistic.

Cohen, Malloy & Pomorski (Journal of Finance 2012) sorted insiders by habit. An insider who
traded in the same calendar month in each of the three previous years is "routine": bonus
season, a standing plan. Everyone else is "opportunistic". Routine trades predicted nothing;
opportunistic ones were followed by about 0.8% a month more than the market (value-weighted) for
about six months, strongest in smaller companies. This separates the two for the purchases the
app already watches (cluster buys, $1M+ buys).

How: for the insider's company, the Form 4s filed in the same calendar month in each of the
three previous years, read for the same person's open-market trades.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import date

from . import http

CACHE_SECONDS = 7 * 86400
MAX_PER_MONTH = 25          # Form 4s read per company-month (big companies file hundreds a year)
_cache: dict[tuple, tuple[float, dict]] = {}


def _name_key(name: str) -> str:
    """Form 4 owner names are "LAST FIRST MIDDLE"; compare on the first two words, case-free."""
    words = re.findall(r"[a-z]+", name.lower())
    return " ".join(words[:2])


def routine_years(trade_month: str, history: dict[int, set[str]], insider: str) -> list[int]:
    """history: {year: set of name keys that traded in that month}. The previous three years in
    which this insider traded in the same calendar month."""
    y = int(trade_month[:4])
    key = _name_key(insider)
    return [yy for yy in (y - 1, y - 2, y - 3) if key in history.get(yy, set())]


def classify(issuer_cik: str, insider: str, trade_date: str, get=None, filings_fn=None) -> dict:
    """{"kind": "routine" | "opportunistic" | "unknown", "years": [...], "why": text}."""
    from .providers import sec
    key = (str(issuer_cik).lstrip("0"), _name_key(insider), trade_date[:7])
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    get = get or (lambda u: sec._sec_get(u, ttl=86400, as_json=False))
    try:
        _, rows = (filings_fn or (lambda c: sec.all_filings(c, {"4"})))(str(issuer_cik).zfill(10))
    except (http.DataUnavailable, KeyError):
        return {"kind": "unknown", "years": [], "why": "Couldn't read the company's past insider filings."}
    month = trade_date[5:7]
    y = int(trade_date[:4])
    history: dict[int, set[str]] = {}
    for yy in (y - 1, y - 2, y - 3):
        rows_m = [r for r in rows if r["filingDate"][:4] == str(yy) and r["filingDate"][5:7] == month][:MAX_PER_MONTH]
        names = set()
        for r in rows_m:
            doc = r["primaryDocument"].split("/")[-1]
            url = sec.ARCHIVE.format(cik=int(issuer_cik), acc=r["accessionNumber"].replace("-", "")) + "/" + doc
            try:
                for t in sec.parse_form4(get(url)):
                    if t.code in ("P", "S"):
                        names.add(_name_key(t.insider))
            except (http.DataUnavailable, ET.ParseError):
                continue
        history[yy] = names
    years = routine_years(trade_date, history, insider)
    if len(years) == 3:
        out = {"kind": "routine", "years": years,
               "why": f"Also traded in {date(y, int(month), 1):%B} of each of the last three years: a habit, not a signal."}
    else:
        out = {"kind": "opportunistic", "years": years,
               "why": "Not a yearly habit: purchases like this have been followed by better returns for about six months."}
    _cache[key] = (time.time(), out)
    return out
