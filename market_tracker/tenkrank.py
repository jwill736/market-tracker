"""10-K changes ranked against the S&P 500.

A single company's "similarity to last year" means little on its own: every long report scores
close to 1. The Lazy Prices paper (Cohen, Malloy & Nguyen 2020) ranks companies against each
other, and the ones whose reports changed most did worse over the following year. So a weekly
GitHub job reads every S&P 500 company's last two annual reports (only the ones with a new 10-K
since the last run) and stores, per company, how much of Risk Factors is new. The app then says
where your holding stands: "more new text than 92% of S&P 500 companies this year".

The list of the most-changed reports is a look-before-you-buy list, not a sell list: a big change
can be a new business line as well as new trouble. Read the new sentences.

The data lives in this repository's journal-data branch (tenk_rank.json) next to the journal.
"""

from __future__ import annotations

import html as htmllib
import re
import time
from datetime import date, datetime, timezone

from . import filings, http

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
# Wikimedia asks for a descriptive User-Agent with a way to reach the operator; the project page, never an email.
WIKI_UA = "Plumbline/1.0 (https://github.com/jwill736/market-tracker; personal portfolio app) python-httpx"
FILE = "tenk_rank.json"
FRESH_DAYS = 400            # a 10-K filed within this many days counts as this year's
MIN_UNIVERSE = 450
METHOD = 3                  # bump when the comparison changes: entries from an older method are recomputed


def parse_universe(page: str) -> list[dict]:
    """[{symbol, name, sector, cik}] from Wikipedia's constituents table."""
    m = re.search(r'<table[^>]*id="constituents".*?</table>', page, re.S)
    if not m:
        return []
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S):
        cells = [htmllib.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) >= 7 and re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", cells[0]):
            cik = next((c for c in cells if re.fullmatch(r"\d{6,10}", c)), "")
            out.append({"symbol": cells[0].replace(".", "-"), "name": cells[1], "sector": cells[2], "cik": cik.zfill(10) if cik else ""})
    return out


def universe_from_nport(get=None) -> list[dict]:
    """The S&P 500 from iShares Core S&P 500's (IVV) latest SEC holdings report, names matched to tickers."""
    from . import lookthrough
    from .providers import sec
    series = lookthrough.fund_series(get).get("IVV")
    url = lookthrough.latest_filing(series, get) if series else None
    if not url:
        return []
    _, rows = lookthrough.parse_nport((get or http.get)(url, headers=lookthrough._sec_headers(), ttl=0, as_json=False))
    tmap = sec.ticker_map()
    out, seen = [], set()
    for r in rows:
        if r["category"] not in ("EC", ""):
            continue
        t = tmap.ticker_for_issuer(r["name"])
        if t and t not in seen:
            seen.add(t)
            out.append({"symbol": t, "name": r["name"].title(), "sector": "", "cik": filings.cik_of(t) or ""})
    return out


def universe(get=None, previous: dict | None = None) -> list[dict]:
    """Wikipedia's constituents table (it has the sectors and CIKs); SEC's IVV holdings if that fails."""
    why = []
    try:
        page = (get or (lambda u: http.get(u, ttl=0, as_json=False, headers={"User-Agent": WIKI_UA})))(WIKI_URL)
        rows = parse_universe(page)
        why.append(f"Wikipedia: {len(rows)} rows")
    except http.DataUnavailable as exc:
        rows = []
        why.append(f"Wikipedia: {str(exc)[-80:]}")
    if len(rows) >= MIN_UNIVERSE:
        return rows
    try:
        rows = universe_from_nport(None if get is None else get)
        why.append(f"IVV holdings: {len(rows)} matched")
    except (http.DataUnavailable, KeyError, ValueError) as exc:
        rows = []
        why.append(f"IVV holdings: {str(exc)[-80:]}")
    if len(rows) >= 400:
        return rows
    old = [{k: c.get(k, "") for k in ("symbol", "name", "sector", "cik")} for c in (previous or {}).get("companies", [])]
    if len(old) >= MIN_UNIVERSE:
        return old
    raise http.DataUnavailable("S&P 500 list: " + "; ".join(why) + "; no earlier list to fall back on")


def score_one(co: dict, prev_entry: dict | None, get=None, text_fn=None) -> dict:
    """One company's entry; reuses the previous one when there's no new 10-K."""
    docs = filings.filings_for(co["symbol"], {"10-K", "10-K405"}, 2, get, cik=co.get("cik") or None)
    if len(docs) < 2:
        return dict(co, error="fewer than two 10-Ks on file")
    cur, prev = docs
    if (prev_entry and prev_entry.get("acc") == cur["accession"] and prev_entry.get("prev_acc") == prev["accession"]
            and not prev_entry.get("error") and prev_entry.get("method") == METHOD):
        return dict(prev_entry, **{k: co[k] for k in ("name", "sector")})
    text_fn = text_fn or (lambda u: filings.document_text(u, get))
    a, b = text_fn(prev["url"]), text_fn(cur["url"])
    risk = filings.compare(filings.section(a, "risk"), filings.section(b, "risk"))
    legal = filings.compare(filings.section(a, "legal"), filings.section(b, "legal"))
    if not risk["words"] or risk["words"] < 300:
        seen = [re.sub(r"\s+", " ", b[max(0, m.start() - 10): m.end() + 40]) for m in re.finditer(r"item\s*1a", b, re.I)][:3]
        return dict(co, acc=cur["accession"], prev_acc=prev["accession"], filed=cur["filed"],
                    error=f"Risk Factors not found ({risk['words']} words; 'Item 1A' appears as: {seen})")
    return dict(co, acc=cur["accession"], prev_acc=prev["accession"], filed=cur["filed"], prev_filed=prev["filed"],
                url=cur["url"], risk_new=risk["new_share"], risk_sim=risk["similarity"], risk_words=risk["words"],
                new_count=risk["new_count"], legal_new=legal["new_share"], sample=risk["new"][:2], method=METHOD,
                computed=datetime.now(timezone.utc).date().isoformat())


def run(previous: dict | None = None, get=None, text_fn=None, budget_seconds: float = 4 * 3600, limit: int | None = None,
        log=print) -> dict:
    """Refresh the ranking. Companies not reached within the time budget keep their earlier entry."""
    prev_by = {c["symbol"]: c for c in (previous or {}).get("companies", [])}
    cos = universe(get, previous)[: limit or None]
    if get is None:
        from .providers import sec

        def get(url, ttl=0, as_json=True):      # 500 companies' files: don't keep them in memory
            return sec._sec_get(url, ttl=0, as_json=as_json)
    started = time.monotonic()
    out, fresh, reused, failed = [], 0, 0, 0
    for i, co in enumerate(cos):
        old = prev_by.get(co["symbol"])
        if time.monotonic() - started > budget_seconds:
            if old:
                out.append(old)
            continue
        try:
            e = score_one(co, old, get, text_fn)
        except (http.DataUnavailable, KeyError, ValueError, TypeError) as exc:
            e = dict(old or co, error=str(exc)[:160])
        if e.get("error"):
            failed += 1
        elif old and e.get("acc") == old.get("acc"):
            reused += 1
        else:
            fresh += 1
        out.append(e)
        if (i + 1) % 50 == 0:
            log(f"{i + 1}/{len(cos)}: {fresh} new, {reused} unchanged, {failed} failed")
    if limit:       # a partial run keeps everyone it didn't reach
        seen = {c["symbol"] for c in out}
        out += [c for sym, c in prev_by.items() if sym not in seen]
    log(f"done: {fresh} new, {reused} unchanged, {failed} failed of {len(cos)}")
    return {"as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"), "universe": max(len(cos), len(out)), "companies": out}


# ------------------------------------------------------------------ the app side

def load(get=None) -> dict | None:
    from .pulse import DATA_URL
    try:
        return (get or (lambda u: http.get(u, ttl=6 * 3600)))(f"{DATA_URL}/{FILE}")
    except http.DataUnavailable:
        return None


def _current(data: dict, today: date | None = None) -> list[dict]:
    today = today or date.today()
    return [c for c in (data or {}).get("companies", []) if c.get("risk_new") is not None and c.get("filed")
            and (today - date.fromisoformat(c["filed"])).days <= FRESH_DAYS]


def percentile(data: dict | None, new_share: float | None, today: date | None = None) -> dict | None:
    """Where a Risk Factors new-text share falls among this year's S&P 500 reports."""
    if not data or new_share is None:
        return None
    vals = sorted(c["risk_new"] for c in _current(data, today))
    if len(vals) < 50:
        return None
    below = sum(1 for v in vals if v < new_share)
    ties = sum(1 for v in vals if v == new_share)
    pct = round((below + ties / 2) / len(vals) * 100)
    return {"pct": pct, "of": len(vals), "median": vals[len(vals) // 2],
            "text": f"More new text than {pct}% of S&P 500 companies' latest reports" if pct >= 50
            else f"Less new text than {100 - pct}% of S&P 500 companies' latest reports"}


def most_changed(data: dict | None, n: int = 20, today: date | None = None) -> list[dict]:
    cur = sorted(_current(data or {}, today), key=lambda c: -c["risk_new"])[:n]
    return [{k: c.get(k) for k in ("symbol", "name", "sector", "filed", "risk_new", "risk_sim", "new_count", "sample", "url")} for c in cur]
