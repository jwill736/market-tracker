"""Scary-filing radar: the SEC filings that usually mean trouble, within minutes of filing.

Sources, all public and free:
- EDGAR's latest-filings feed. Each 8-K entry lists its item numbers ("Item 4.02: Non-Reliance on
  Previously Issued Financial Statements"), so a filing can be classified without opening it.
  Also polled: late-filing notices (NT 10-K / NT 10-Q), exchange delistings (Form 25-NSE),
  voluntary delistings (25) and deregistrations (15), which end public reporting.
- Each company's filing list (data.sec.gov submissions), which carries the same 8-K item numbers,
  for the history of what you own.
- EDGAR full-text search for "substantial doubt ... going concern" in recent 10-K and 10-Q filings.

Levels: 3 = act today (bankruptcy, restatement, delisting, default), 2 = serious (auditor change,
late report, cyber incident, write-down, layoffs, going-concern doubt), 1 = read it (officer
departure, reverse split, cancelled contract). An 8-K item 5.02 covers appointments as well as
departures, so it is only level 1 until you read it.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from html import unescape

from . import http
from .providers import sec

FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&company=&dateb="
        "&owner=include&start=0&count={count}&output=atom")
EFTS = "https://efts.sec.gov/LATEST/search-index"
_NS = "{http://www.w3.org/2005/Atom}"

ITEMS: dict[str, tuple[int, str, str]] = {
    "1.03": (3, "Bankruptcy or receivership", "Shareholders are usually last in line in a bankruptcy."),
    "2.04": (3, "Debt default or acceleration", "Lenders can demand repayment now."),
    "3.01": (3, "Delisting notice or listing-rule failure", "The exchange may remove the stock."),
    "4.02": (3, "Past financial statements can't be relied on", "A restatement is coming; the reported numbers were wrong."),
    "4.01": (2, "Auditor changed", "Auditors rarely leave healthy clients mid-stream; read why."),
    "1.05": (2, "Material cybersecurity incident", "Costs, lawsuits and lost customers can follow."),
    "2.06": (2, "Material impairment (write-down)", "Something the company paid for is worth much less."),
    "2.05": (2, "Restructuring or layoffs", "Costs to shrink the business."),
    "5.02": (1, "Director or officer change", "Check whether the CEO or CFO left, and whether it was sudden."),
    "3.03": (1, "Change to shareholders' rights", "Often a reverse split, a warning sign for a low-priced stock."),
    "1.02": (1, "Material agreement terminated", "A major contract, customer or loan ended."),
}
FORMS: dict[str, tuple[int, str, str]] = {
    "NT 10-K": (2, "Annual report will be late", "Late reports often come before bad news or accounting problems."),
    "NT 10-Q": (2, "Quarterly report will be late", "Late reports often come before bad news or accounting problems."),
    "NT 20-F": (2, "Annual report will be late", "Late reports often come before bad news or accounting problems."),
    "25-NSE": (3, "Exchange is delisting the stock", "Trading on the exchange ends in about 10 days."),
    "25": (3, "Company is delisting its stock", "The company is taking the stock off its exchange."),
    # (Form 25s for bonds are dropped and for warrants/preferred softened: see refine_delisting.)
    "15-12G": (2, "Deregistering: public reports will stop", "You would lose audited financial reports."),
    "15-12B": (2, "Deregistering: public reports will stop", "You would lose audited financial reports."),
    "15-15D": (2, "Deregistering: public reports will stop", "You would lose audited financial reports."),
}
GOING_CONCERN = (2, "Going-concern doubt in its latest report",
                 "The auditor or management doubts the company can last another year. Read the context: "
                 "some reports say the doubt was resolved.")
FEED_FORMS = ["8-K", "NT 10-K", "NT 10-Q", "25-NSE", "25", "15-12G", "15-12B", "15-15D"]
DELISTING_FORMS = {"25", "25-NSE"}
# A Form 25 removes one class of securities. Large companies file them all the time for bonds
# that matured or were redeemed; only a delisting of the shares is an emergency.
_EQUITY = re.compile(r"common\s+stock|ordinary\s+shares|common\s+shares|american\s+depositary|"
                     r"class\s+[a-c]\s+(?:common|ordinary)", re.I)
_DEBT = re.compile(r"\bnotes?\b|\bdebentures?\b|\bbonds?\b|\bdue\s+(?:19|20)\d\d\b", re.I)
_OTHER = re.compile(r"\bwarrants?\b|\bunits?\b|\bpreferred\b|\brights\b", re.I)
LEVEL_NAMES = {3: "Act today", 2: "Serious", 1: "Read it"}


@dataclass
class Alert:
    accession: str
    cik: str
    company: str
    form: str
    filed: str                  # YYYY-MM-DD
    level: int
    headline: str
    why: str
    items: list[dict] = field(default_factory=list)   # [{code, label, level}]
    url: str = ""
    symbol: str = ""
    when: str = ""              # acceptance time when known (ISO)

    def to_dict(self) -> dict:
        return asdict(self) | {"level_name": LEVEL_NAMES.get(self.level, "")}


def classify(form: str, item_codes: list[str]) -> tuple[int, str, str, list[dict]] | None:
    """(level, headline, why, matched items) for a filing, or None if it isn't on the radar."""
    form = form.strip().upper()
    if form in FORMS:
        level, head, why = FORMS[form]
        return level, head, why, []
    if not form.startswith("8-K"):
        return None
    hits = [{"code": c, "label": ITEMS[c][1], "level": ITEMS[c][0]} for c in item_codes if c in ITEMS]
    if not hits:
        return None
    top = max(hits, key=lambda h: h["level"])
    level, head, why = ITEMS[top["code"]]
    if len(hits) > 1:
        head += " + " + ", ".join(h["label"].lower() for h in hits if h is not top)
    return level, head, why, sorted(hits, key=lambda h: -h["level"])


# ------------------------------------------------------------------ live feed

@dataclass
class FeedEntry:
    accession: str
    form: str
    cik: str
    name: str
    role: str
    updated: str
    link: str
    items: list[str]


def parse_feed(xml_text: str) -> list[FeedEntry]:
    root = ET.fromstring(xml_text)
    out = []
    for e in root.iter(_NS + "entry"):
        title = (e.findtext(_NS + "title") or "").strip()
        m_acc = re.search(r"accession-number=(\d{10}-\d{2}-\d{6})", e.findtext(_NS + "id") or "")
        m_title = re.match(r"(.+?) - (.*) \((\d{4,10})\) \(([^)]+)\)\s*$", title)
        if not m_acc or not m_title:
            continue
        cat = e.find(_NS + "category")
        form = ((cat.get("term") if cat is not None else None) or m_title.group(1)).strip().upper()
        link = e.find(_NS + "link")
        summary = unescape(e.findtext(_NS + "summary") or "")
        items = re.findall(r"Item\s+(\d+\.\d{2})", summary)
        out.append(FeedEntry(m_acc.group(1), form, m_title.group(3).zfill(10), m_title.group(2).strip(),
                             m_title.group(4).strip(), (e.findtext(_NS + "updated") or "").strip(),
                             link.get("href", "") if link is not None else "", items))
    return out


def alerts_from_feed(entries: list[FeedEntry]) -> list[Alert]:
    out: dict[str, Alert] = {}
    for e in entries:
        # Delisting forms list the company as "Subject" and the exchange as "Filed by".
        if e.role.lower().startswith("filed by") or e.accession in out:
            continue
        got = classify(e.form, e.items)
        if not got:
            continue
        level, head, why, hits = got
        out[e.accession] = Alert(e.accession, e.cik, e.name, e.form, e.updated[:10], level, head, why, hits,
                                 e.link, when=e.updated)
    return list(out.values())


def delisting_scope(text: str) -> str:
    """equity / debt / other / unknown: which security a Form 25 removes."""
    body = re.sub(r"<SEC-HEADER>.*?</SEC-HEADER>", " ", text or "", flags=re.S | re.I)
    body = _strip_tags(body)
    if _EQUITY.search(body):
        return "equity"
    if _DEBT.search(body):
        return "debt"
    if _OTHER.search(body):
        return "other"
    return "unknown"


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s)))


def refine_delisting(a: Alert, get=http.get) -> Alert | None:
    """Read the Form 25 itself: drop bond delistings, soften warrants and preferred."""
    if a.form not in DELISTING_FORMS or not a.url.endswith("-index.htm"):
        return a
    try:
        scope = delisting_scope(get(a.url.replace("-index.htm", ".txt"), headers=sec_headers(), ttl=7 * 86400,
                                    as_json=False))
    except http.DataUnavailable:
        scope = "unknown"
    if scope == "debt":
        return None
    if scope == "other":
        a.level, a.headline = 1, "Delisting of warrants, units or preferred shares"
        a.why = "Not the common stock, but check what it means for the company."
    elif scope == "unknown":
        a.level, a.headline = 2, "Delisting filing (couldn't tell which security)"
        a.why = "Open the filing: a delisting of the shares is serious; of a bond, routine."
    return a


def fetch_feed(form: str, count: int = 100, get=http.get) -> list[FeedEntry]:
    text = get(FEED.format(form=form.replace(" ", "+"), count=count), headers=sec_headers(), ttl=60, as_json=False)
    # The type filter matches by prefix ("25" also returns 25-NSE); keep exact forms only.
    return [e for e in parse_feed(text) if e.form == form or (form == "8-K" and e.form in ("8-K", "8-K/A"))]


def sec_headers() -> dict:
    from .config import settings
    return {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def scan_market(get=http.get, forms: list[str] = FEED_FORMS) -> tuple[list[Alert], list[str]]:
    """Every radar filing in the latest feed pages (the last few hours of 8-Ks; days of the
    rarer forms). Returns (alerts, errors)."""
    alerts: list[Alert] = []
    errors: list[str] = []
    for form in forms:
        try:
            found = alerts_from_feed(fetch_feed(form, 100 if form == "8-K" else 40, get))
        except (http.DataUnavailable, ET.ParseError) as exc:
            errors.append(f"{form}: {exc}")
            continue
        alerts += [r for r in (refine_delisting(a, get) for a in found) if r]
    return alerts, errors


# ------------------------------------------------------------------ one company's history

def company_alerts(cik: str, submissions: dict, since: date, symbol: str = "", get=http.get) -> list[Alert]:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    name = submissions.get("name", "")
    out = []
    rows = zip(recent.get("accessionNumber", []), recent.get("form", []), recent.get("filingDate", []),
               recent.get("items", []), recent.get("primaryDocument", []), recent.get("acceptanceDateTime", []))
    for acc, form, filed, items, doc, accepted in rows:
        if filed < since.isoformat():
            break                                    # newest first
        got = classify(form, [c.strip() for c in (items or "").split(",") if c.strip()])
        if not got:
            continue
        level, head, why, hits = got
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{acc}-index.htm"
        a = refine_delisting(Alert(acc, cik.zfill(10), name, form, filed, level, head, why, hits, url, symbol,
                                   accepted or ""), get)
        if a:
            out.append(a)
    return out


# ------------------------------------------------------------------ going concern (full-text search)

def going_concern_ciks(today: date, days: int = 120, get=http.get, pages: int = 5) -> dict[str, dict]:
    """CIK -> the latest 10-K/10-Q in the window whose text says 'substantial doubt' about
    continuing as a 'going concern'."""
    found: dict[str, dict] = {}
    start = (today - timedelta(days=days)).isoformat()
    for page in range(pages):
        data = get(EFTS, params={"q": '"substantial doubt" "going concern"', "forms": "10-K,10-Q",
                                 "dateRange": "custom", "startdt": start, "enddt": today.isoformat(),
                                 "from": page * 100}, headers=sec_headers(), ttl=43200)
        hits = ((data or {}).get("hits") or {}).get("hits") or []
        for h in hits:
            src = h.get("_source") or {}
            adsh = src.get("adsh") or h.get("_id", "").split(":")[0]
            for cik in src.get("ciks") or []:
                prev = found.get(cik.zfill(10))
                if not prev or src.get("file_date", "") > prev["filed"]:
                    found[cik.zfill(10)] = {"filed": src.get("file_date", ""), "form": src.get("form", ""),
                                            "accession": adsh, "name": (src.get("display_names") or [""])[0]}
        total = (((data or {}).get("hits") or {}).get("total") or {}).get("value", 0)
        if (page + 1) * 100 >= total:
            break
    return found


def going_concern_alert(cik: str, hit: dict, symbol: str = "") -> Alert:
    level, head, why = GOING_CONCERN
    acc = hit["accession"]
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{acc}-index.htm" if acc else
           f"https://efts.sec.gov/LATEST/search-index?q=%22going%20concern%22&ciks={cik}")
    name = re.sub(r"\s+\(.*$", "", hit.get("name", "")).strip()
    return Alert(acc or f"gc-{cik}", cik, name, hit.get("form", ""), hit.get("filed", ""), level, head, why, [], url,
                 symbol)


# ------------------------------------------------------------------ tickers

_cik_ticker: dict[str, str] | None = None


def cik_to_ticker() -> dict[str, str]:
    """CIK -> primary ticker (the SEC list is ordered largest company first)."""
    global _cik_ticker
    if _cik_ticker is None:
        m: dict[str, str] = {}
        for ticker, row in sec.ticker_map().by_ticker.items():
            m.setdefault(str(row["cik_str"]).zfill(10), ticker)
        _cik_ticker = m
    return _cik_ticker


def when_sort_key(a: Alert) -> str:
    return a.when or a.filed


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
