"""Market-wide insider cluster-buy alerts.

Every business day the SEC publishes an index of all filings. This module reads that index,
fetches every Form 4, keeps open-market purchases (transaction code P), and looks for
clusters: several different insiders at the same company buying within a short window.

Why clusters: insiders sell for many reasons (taxes, diversification, planned 10b5-1 sales)
but buy with their own money for essentially one. Several of them buying at once is the
insider signal with the most research behind it, and it shows up mostly at small and
mid-sized companies, which is why this scans every filer rather than a watchlist.
"""

from __future__ import annotations

import csv
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, fields
from datetime import date, timedelta

from . import http
from .config import settings
from .providers import sec

DAILY_INDEX = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{ymd}.idx"
ARCHIVES = "https://www.sec.gov/Archives/"

MIN_BUY_VALUE = 10_000        # ignore token purchases
CLUSTER_WINDOW_DAYS = 30
CLUSTER_MIN_INSIDERS = 3
CLUSTER_MIN_TOTAL = 100_000
REALERT_AFTER_DAYS = 30
KEEP_DAYS = 120               # rolling window of buys kept in the CSV


@dataclass
class Buy:
    filed: str
    accession: str
    issuer_cik: str
    issuer_name: str
    symbol: str
    insider: str
    role: str
    trade_date: str
    shares: float
    price: float
    value: float


BUY_FIELDS = [f.name for f in fields(Buy)]


def _headers() -> dict:
    return {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


# ------------------------------------------------------------------ SEC daily index

def index_url(day: date) -> str:
    return DAILY_INDEX.format(y=day.year, q=(day.month - 1) // 3 + 1, ymd=day.strftime("%Y%m%d"))


def parse_form_index(text: str, forms: Iterable[str] = ("4",)) -> list[dict]:
    """Rows of an EDGAR form.YYYYMMDD.idx for the given form types, one per accession.

    The file is fixed-width text: a header, a line of dashes, then
    'Form Type  Company Name  CIK  Date Filed  File Name' columns separated by 2+ spaces.
    A Form 4 is listed once per filer (issuer and each reporting owner), so rows are
    deduplicated on the accession number in the file name."""
    wanted = set(forms)
    body = text.split("\n")
    start = next((i + 1 for i, line in enumerate(body) if line.startswith("----")), 0)
    seen: set[str] = set()
    out = []
    for line in body[start:]:
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 5 or parts[0] not in wanted:
            continue
        filename = parts[-1]
        accession = filename.rsplit("/", 1)[-1].removesuffix(".txt")
        if accession in seen:
            continue
        seen.add(accession)
        out.append({"form": parts[0], "company": parts[1], "cik": parts[2], "date": parts[3],
                    "filename": filename, "accession": accession})
    return out


def extract_xml(submission_text: str) -> str | None:
    """The ownershipDocument XML embedded in a full-submission .txt file."""
    m = re.search(r"<XML>\s*(.*?)\s*</XML>", submission_text, re.S | re.I)
    return m.group(1) if m else None


def parse_issuer(xml_text: str) -> tuple[str, str, str]:
    root = ET.fromstring(xml_text)
    issuer = sec._find(root, "issuer")
    return (sec._text(issuer, "issuerCik").zfill(10), sec._text(issuer, "issuerName"),
            sec._text(issuer, "issuerTradingSymbol").upper())


def buys_from_submission(submission_text: str, accession: str, filed: str) -> list[Buy]:
    xml = extract_xml(submission_text)
    if not xml:
        return []
    try:
        cik, name, symbol = parse_issuer(xml)
        trades = sec.parse_form4(xml)
    except ET.ParseError:
        return []
    out = []
    for t in trades:
        if t.code != "P" or not t.acquired or t.value < MIN_BUY_VALUE:
            continue
        out.append(Buy(filed=filed, accession=accession, issuer_cik=cik, issuer_name=name, symbol=symbol,
                       insider=t.insider, role=t.role, trade_date=t.date, shares=t.shares, price=t.price,
                       value=t.value))
    return out


def scan_day(day: date, log: Callable[[str], None] = lambda m: None) -> list[Buy] | None:
    """All qualifying open-market buys filed on `day`; None if the SEC has no index for it
    (weekends, holidays, or not published yet)."""
    try:
        index_text = http.get(index_url(day), headers=_headers(), ttl=0, as_json=False)
    except http.DataUnavailable as exc:
        log(f"{day}: no daily index ({exc})")
        return None
    filings = parse_form_index(index_text)
    buys: list[Buy] = []
    failed = 0
    for f in filings:
        try:
            text = http.get(ARCHIVES + f["filename"], headers=_headers(), ttl=0, as_json=False)
        except http.DataUnavailable:
            failed += 1
            continue
        buys.extend(buys_from_submission(text, f["accession"], day.isoformat()))
    log(f"{day}: {len(filings)} Form 4 filings, {len(buys)} open-market buys >= ${MIN_BUY_VALUE:,}"
        + (f", {failed} fetch failures" if failed else ""))
    return buys


# ------------------------------------------------------------------ storage

def load_buys(path: str) -> list[Buy]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        out = []
        for r in csv.DictReader(fh):
            for k in ("shares", "price", "value"):
                r[k] = float(r[k] or 0)
            out.append(Buy(**{k: r[k] for k in BUY_FIELDS}))
        return out


def save_buys(buys: list[Buy], path: str, today: date) -> None:
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    unique = {(b.accession, b.insider, b.trade_date, b.shares, b.price): b for b in buys if b.filed >= cutoff}
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=BUY_FIELDS)
        w.writeheader()
        for b in sorted(unique.values(), key=lambda b: (b.filed, b.issuer_cik, b.insider)):
            w.writerow(asdict(b))


def load_alerted(path: str) -> dict[str, str]:
    """issuer CIK -> date of its last alert."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["issuer_cik"]: r["alerted"] for r in csv.DictReader(fh)}


def save_alerted(alerted: dict[str, str], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["issuer_cik", "alerted"])
        for cik, d in sorted(alerted.items()):
            w.writerow([cik, d])


# ------------------------------------------------------------------ clusters

@dataclass
class Cluster:
    issuer_cik: str
    issuer_name: str
    symbol: str
    insiders: list[str]
    total_value: float
    first_trade: str
    last_trade: str
    last_filed: str
    buys: list[Buy]


def find_clusters(buys: list[Buy], as_of: date, window_days: int = CLUSTER_WINDOW_DAYS,
                  min_insiders: int = CLUSTER_MIN_INSIDERS, min_total: float = CLUSTER_MIN_TOTAL) -> list[Cluster]:
    """Companies where >= min_insiders different insiders bought in the last window_days
    (by trade date, counting only filings public by as_of)."""
    start = (as_of - timedelta(days=window_days)).isoformat()
    end = as_of.isoformat()
    by_issuer: dict[str, list[Buy]] = {}
    for b in buys:
        if start <= b.trade_date <= end and b.filed <= end:
            by_issuer.setdefault(b.issuer_cik, []).append(b)
    clusters = []
    for cik, group in by_issuer.items():
        insiders = sorted({b.insider for b in group})
        total = sum(b.value for b in group)
        if len(insiders) < min_insiders or total < min_total:
            continue
        latest = max(group, key=lambda b: b.filed)
        clusters.append(Cluster(issuer_cik=cik, issuer_name=latest.issuer_name, symbol=latest.symbol,
                                insiders=insiders, total_value=total,
                                first_trade=min(b.trade_date for b in group),
                                last_trade=max(b.trade_date for b in group),
                                last_filed=latest.filed, buys=sorted(group, key=lambda b: b.trade_date)))
    clusters.sort(key=lambda c: -c.total_value)
    return clusters


def new_clusters(clusters: list[Cluster], alerted: dict[str, str], as_of: date) -> list[Cluster]:
    """Clusters not alerted within the last REALERT_AFTER_DAYS (one alert per episode)."""
    cutoff = (as_of - timedelta(days=REALERT_AFTER_DAYS)).isoformat()
    return [c for c in clusters if alerted.get(c.issuer_cik, "") < cutoff]


# ------------------------------------------------------------------ issue text

def _money(x: float) -> str:
    return f"${x / 1e6:,.1f}M" if x >= 1e6 else f"${x:,.0f}"


def issue_title(c: Cluster) -> str:
    ticker = c.symbol or "no ticker"
    return f"Insider cluster buy: {ticker} ({c.issuer_name}), {len(c.insiders)} insiders, {_money(c.total_value)}"


def issue_body(c: Cluster) -> str:
    rows = "\n".join(
        f"| {b.trade_date} | {b.insider} | {b.role} | {b.shares:,.0f} | ${b.price:,.2f} | {_money(b.value)} | "
        f"[filing]({ARCHIVES}edgar/data/{int(b.issuer_cik)}/{b.accession.replace('-', '')}/) |"
        for b in c.buys)
    return f"""**{len(c.insiders)} different insiders at {c.issuer_name}{f' ({c.symbol})' if c.symbol else ''} bought shares on the open market between {c.first_trade} and {c.last_trade}, {_money(c.total_value)} in total.**

| Trade date | Insider | Role | Shares | Price | Value | Source |
|---|---|---|---:|---:|---:|---|
{rows}

**How to read this**
- Open-market purchases (Form 4, code P) of at least {_money(MIN_BUY_VALUE)} each; awards, option exercises and sales are excluded.
- Insiders buying together is the insider signal with the most research behind it, but it is not a guarantee, and small companies can be illiquid and volatile. Size any position for the volatility.
- Check the filings for context (a post-selloff show of confidence, a planned program, a director joining) before acting.
- Next step: `mt analyze {c.symbol or '<ticker>'}` or a deep dive in the dashboard.

_Opened automatically by the insider-alerts workflow. Close the issue once reviewed; the same company won't alert again for {REALERT_AFTER_DAYS} days._
"""
