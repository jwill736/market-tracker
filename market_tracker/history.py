"""Historical data loaders for the score backtest (network-heavy; run on GitHub Actions).

* Prices: Yahoo daily adjusted closes, full history.
* 13F: every 13F-HR each tracked investor filed since `since`, from EDGAR, with CUSIPs used
  to carry tickers back through renames (Facebook's 2015 filings map to META).
* Insider trades: the SEC's quarterly bulk Form 3/4/5 data sets, matched to the universe by
  issuer CIK (stable across ticker changes) and dated by *filing* date.
"""

from __future__ import annotations

import csv
import io
import re
import sys
import zipfile
from datetime import datetime

from . import http
from .config import settings
from .investors import INVESTORS
from .providers import market, sec
from .score_backtest import HistoricalData

INSIDER_INDEX = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
SEC_BASE = "https://www.sec.gov"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _sec_headers() -> dict:
    return {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def _sec_date(txt: str) -> str:
    """'15-NOV-2022' -> '2022-11-15'; '' on anything unparseable."""
    try:
        return datetime.strptime(txt.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return ""


# ------------------------------------------------------------------ prices

def load_prices(symbols: list[str], bars: int = 3200) -> dict[str, list[tuple[str, float]]]:
    out = {}
    for sym in symbols:
        try:
            out[sym] = [(b.date, b.close) for b in market.get_history(sym, bars)]
        except http.DataUnavailable as exc:
            log(f"  prices {sym}: {exc}")
    log(f"prices: {len(out)}/{len(symbols)} symbols")
    return out


# ------------------------------------------------------------------ 13F

def load_13f_history(since: str, keys: list[str] | None = None) -> dict[str, list[sec.Filing13F]]:
    tmap = sec.ticker_map()
    result: dict[str, list[sec.Filing13F]] = {}
    for inv in INVESTORS:
        if keys and inv.key not in keys:
            continue
        name, rows = sec.all_filings(inv.cik, {"13F-HR"})
        seen: set[str] = set()
        filings = []
        # Oldest first so the original filing for a period wins over later re-filings.
        for row in sorted(rows, key=lambda r: r["filingDate"]):
            if row["filingDate"] < since or row["reportDate"] in seen:
                continue
            seen.add(row["reportDate"])
            try:
                xml_text = sec._sec_get(sec._infotable_url(inv.cik, row["accessionNumber"]), ttl=0, as_json=False)
                holdings = sec.parse_13f_infotable(xml_text, row["reportDate"])
            except Exception as exc:  # noqa: BLE001 - one bad filing shouldn't sink the run
                log(f"  13F {inv.key} {row['accessionNumber']}: {exc}")
                continue
            for h in holdings:
                h.ticker = tmap.ticker_for_issuer(h.issuer)
            filings.append(sec.Filing13F(investor=inv.key, filer_name=name, period=row["reportDate"],
                                         filed=row["filingDate"], accession=row["accessionNumber"],
                                         holdings=holdings))
        result[inv.key] = filings
        log(f"13F {inv.key}: {len(filings)} filings")
    propagate_tickers_by_cusip(result)
    return result


def propagate_tickers_by_cusip(filings: dict[str, list[sec.Filing13F]]) -> int:
    """A company keeps its CUSIP through a rename, so a CUSIP mapped to a ticker in any filing
    (usually a recent one, under today's name) maps it in every filing. Returns how many
    holdings gained a ticker."""
    cusip_ticker: dict[str, str] = {}
    all_filings = sorted((f for fs in filings.values() for f in fs), key=lambda f: f.period, reverse=True)
    for f in all_filings:
        for h in f.holdings:
            if h.ticker:
                cusip_ticker.setdefault(h.cusip, h.ticker)
    gained = 0
    for f in all_filings:
        for h in f.holdings:
            if not h.ticker and h.cusip in cusip_ticker:
                h.ticker = cusip_ticker[h.cusip]
                gained += 1
    return gained


# ------------------------------------------------------------------ insider trades

def insider_zip_urls(since_quarter: str) -> list[str]:
    """Bulk data set links from the SEC's index page. Read from the page rather than built
    from a pattern: the newest quarter lives under a different path from the rest."""
    page = http.get(INSIDER_INDEX, headers=_sec_headers(), ttl=3600, as_json=False)
    links = sorted(set(re.findall(r'href="([^"]*?(\d{4}q[1-4])_form345\.zip)"', page)), key=lambda x: x[1])
    return [SEC_BASE + href if href.startswith("/") else href for href, q in links if q >= since_quarter]


def _rows(z: zipfile.ZipFile, name: str):
    member = next(n for n in z.namelist() if n.upper().endswith(name))
    with z.open(member) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"),
                                  delimiter="\t", quoting=csv.QUOTE_NONE)


def parse_insider_zip(blob: bytes, cik_to_symbol: dict[str, str]) -> dict[str, list[sec.InsiderTrade]]:
    """Form 4 non-derivative transactions for the issuers in `cik_to_symbol`."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    subs: dict[str, tuple[str, str, bool]] = {}
    for r in _rows(z, "SUBMISSION.TSV"):
        cik = (r.get("ISSUERCIK") or "").zfill(10)
        if r.get("DOCUMENT_TYPE") == "4" and cik in cik_to_symbol:
            subs[r["ACCESSION_NUMBER"]] = (_sec_date(r.get("FILING_DATE", "")), cik_to_symbol[cik],
                                           (r.get("AFF10B5ONE") or "").strip().lower() in ("1", "true"))
    owners: dict[str, tuple[str, str]] = {}
    for r in _rows(z, "REPORTINGOWNER.TSV"):
        acc = r["ACCESSION_NUMBER"]
        if acc in subs and acc not in owners:
            role = ", ".join(x for x in (r.get("RPTOWNER_TITLE") or "", r.get("RPTOWNER_RELATIONSHIP") or "") if x)
            owners[acc] = (r.get("RPTOWNERNAME") or "", role or "Insider")
    out: dict[str, list[sec.InsiderTrade]] = {}
    for r in _rows(z, "NONDERIV_TRANS.TSV"):
        acc = r["ACCESSION_NUMBER"]
        if acc not in subs:
            continue
        filed, sym, plan = subs[acc]
        name, role = owners.get(acc, ("", "Insider"))

        def num(k: str) -> float:
            try:
                return float(r.get(k) or 0)
            except ValueError:
                return 0.0

        out.setdefault(sym, []).append(sec.InsiderTrade(
            insider=name, role=role, date=_sec_date(r.get("TRANS_DATE", "")), code=(r.get("TRANS_CODE") or "").strip(),
            shares=num("TRANS_SHARES"), price=num("TRANS_PRICEPERSHARE"),
            acquired=(r.get("TRANS_ACQUIRED_DISP_CD") or "").strip() == "A",
            shares_after=num("SHRS_OWND_FOLWNG_TRANS"), ten_b5_1=plan, filed=filed))
    return out


def load_insider_history(symbols: list[str], since_quarter: str) -> dict[str, list[sec.InsiderTrade]]:
    tmap = sec.ticker_map()
    cik_to_symbol = {cik: s for s in symbols if (cik := tmap.cik_for(s))}
    trades: dict[str, list[sec.InsiderTrade]] = {s: [] for s in cik_to_symbol.values()}
    urls = insider_zip_urls(since_quarter)
    for url in urls:
        try:
            part = parse_insider_zip(http.get_bytes(url, headers=_sec_headers()), cik_to_symbol)
        except (http.DataUnavailable, zipfile.BadZipFile, KeyError, StopIteration) as exc:
            log(f"  insider {url}: {exc}")
            continue
        for sym, ts in part.items():
            trades[sym].extend(ts)
    log(f"insiders: {len(urls)} quarterly files, {sum(map(len, trades.values()))} transactions, "
        f"{len(cik_to_symbol)}/{len(symbols)} symbols mapped")
    return trades


def load_all(symbols: list[str], start: str, investor_keys: list[str] | None = None) -> HistoricalData:
    # 13F and insider history start a year before the first scored month so the first
    # dates already have "previous filing" and 90-day insider windows to compare against.
    lead = f"{int(start[:4]) - 1}{start[4:]}"
    since_quarter = f"{lead[:4]}q{(int(lead[5:7]) - 1) // 3 + 1}"
    return HistoricalData(prices=load_prices(symbols),
                          filings=load_13f_history(lead, investor_keys),
                          insiders=load_insider_history(symbols, since_quarter))
