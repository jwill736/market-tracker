"""Near-real-time filing watcher.

The daily insider scan reads the SEC's end-of-day index, so a purchase filed at 10am shows
up the next morning. This module reads EDGAR's "latest filings" feed instead, which lists
filings minutes after the SEC accepts them, and handles three kinds:

- Form 4 open-market purchases: stored with the daily scan's buys, so an insider cluster
  alerts as soon as the filing that completes it appears, under the same rules.
- Large single purchases: an officer or director buying $1M or more in one filing.
- Schedule 13D/13G: a filer crossing 5% of a company. 13D means the filer may try to
  influence the company (the activist form); it is due within 5 business days, far fresher
  than a 13F. Filings by the tracked investors alert; every initial 13D is kept for the site.

Run it every few minutes (GitHub Actions, or `mt watch --loop` on any machine). It keeps a
cursor of accessions already seen, so each filing is handled once.
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta, timezone

from . import alerts, dilution, http
from .investors import INVESTORS

FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&company=&dateb="
        "&owner=include&start={start}&count=100&output=atom")
# The feed's type filter matches by prefix ("4" also returns 424B2 prospectuses), so every
# entry is checked against these exact form types.
INSIDER_FORMS = {"4"}
STAKE_FORMS = {"SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
               "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}  # SC ... = pre-2025 form names
# Offering-related forms, watched only for companies that alerted recently ("424B" also
# matches 424B2 structured notes, which the exact-form check drops).
FEED_QUERIES = ["4", "SCHEDULE 13D", "SCHEDULE 13G", "SC 13D", "SC 13G", "S-3", "424B", "S-1", "F-3", "F-1"]

BIG_BUY_VALUE = 1_000_000
FIRST_RUN_LOOKBACK = timedelta(minutes=30)
OVERLAP = timedelta(minutes=15)      # re-read a little of the last window; seen-set dedupes
MAX_PAGES = 5                        # 500 entries per form per poll
KEEP_SEEN = 5000
KEEP_STAKE_DAYS = 120
_NS = "{http://www.w3.org/2005/Atom}"


# ------------------------------------------------------------------ feed

@dataclass
class FeedEntry:
    accession: str
    form: str
    cik: str
    name: str
    role: str          # Issuer / Reporting / Subject / Filed by
    updated: str       # ISO timestamp with offset, Eastern time
    link: str


def parse_feed(xml_text: str) -> list[FeedEntry]:
    """Entries of an EDGAR getcurrent Atom feed. Each filing appears once per party
    (issuer and reporting owner, or subject company and filer)."""
    root = ET.fromstring(xml_text)
    out = []
    for e in root.iter(_NS + "entry"):
        title = (e.findtext(_NS + "title") or "").strip()
        m_acc = re.search(r"accession-number=(\d{10}-\d{2}-\d{6})", e.findtext(_NS + "id") or "")
        m_title = re.match(r"(.+?) - (.*) \((\d{4,10})\) \(([^)]+)\)\s*$", title)
        if not m_acc or not m_title:
            continue
        cat = e.find(_NS + "category")
        form = (cat.get("term") if cat is not None else None) or m_title.group(1)
        link = e.find(_NS + "link")
        out.append(FeedEntry(accession=m_acc.group(1), form=form.strip().upper(),
                             cik=m_title.group(3).zfill(10), name=m_title.group(2).strip(),
                             role=m_title.group(4).strip(), updated=(e.findtext(_NS + "updated") or "").strip(),
                             link=link.get("href", "") if link is not None else ""))
    return out


def _when(ts: str) -> datetime:
    try:
        d = datetime.fromisoformat(ts)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _sec_headers() -> dict:
    return alerts._headers()


def fetch_feed(form: str, start: int) -> str:
    return http.get(FEED.format(form=form.replace(" ", "+"), start=start), headers=_sec_headers(),
                    ttl=0, as_json=False)


def new_entries(form: str, seen: set[str], since: datetime,
                feed_fn: Callable[[str, int], str] = fetch_feed, max_pages: int = MAX_PAGES) -> list[FeedEntry]:
    """Entries updated at or after `since` whose accession hasn't been handled, newest first.
    Pages until an entry older than `since` shows up (the feed is newest-first)."""
    out = []
    for page in range(max_pages):
        entries = parse_feed(feed_fn(form, page * 100))
        if not entries:
            break
        older = False
        for e in entries:
            if _when(e.updated) < since:
                older = True
                continue
            if e.accession not in seen:
                out.append(e)
        if older or len(entries) < 100:
            break
    return out


def by_accession(entries: list[FeedEntry]) -> dict[str, list[FeedEntry]]:
    groups: dict[str, list[FeedEntry]] = {}
    for e in entries:
        groups.setdefault(e.accession, []).append(e)
    return groups


# ------------------------------------------------------------------ stakes (13D / 13G)

@dataclass
class Stake:
    filed: str
    accession: str
    form: str
    subject_cik: str
    subject_name: str
    filer_cik: str
    filer_name: str
    tracked: str       # the tracked investor's name, or "" for anyone else
    url: str


STAKE_FIELDS = [f.name for f in fields(Stake)]


def tracked_investor(cik: str, name: str) -> str:
    """The tracked investor behind a filer, matched on CIK, fund name, or first plus last name
    (13Ds are often filed by the person: 'ICAHN CARL C')."""
    upper = name.upper()
    for inv in INVESTORS:
        if cik.zfill(10) == inv.cik.zfill(10) or inv.expected_name in upper:
            return inv.person
        person = re.sub(r"\(.*?\)", "", inv.person).split()
        if len(person) >= 2 and all(re.search(rf"\b{re.escape(p.upper())}\b", upper) for p in (person[0], person[-1])):
            return inv.person
    return ""


def stake_from_group(group: list[FeedEntry]) -> Stake | None:
    subject = next((e for e in group if e.role.lower().startswith("subject")), None)
    filer = next((e for e in group if e.role.lower().startswith("filed")), None)
    if subject is None or filer is None:
        return None
    return Stake(filed=subject.updated[:10], accession=subject.accession, form=subject.form,
                 subject_cik=subject.cik, subject_name=subject.name, filer_cik=filer.cik,
                 filer_name=filer.name, tracked=tracked_investor(filer.cik, filer.name),
                 url=subject.link or filer.link)


def is_initial_13d(form: str) -> bool:
    return form in ("SCHEDULE 13D", "SC 13D")


def load_stakes(path: str) -> list[Stake]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [Stake(**{k: r.get(k, "") for k in STAKE_FIELDS}) for r in csv.DictReader(fh)]


def save_stakes(stakes: list[Stake], path: str, today: date) -> None:
    cutoff = (today - timedelta(days=KEEP_STAKE_DAYS)).isoformat()
    unique = {s.accession: s for s in stakes if s.filed >= cutoff}
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=STAKE_FIELDS)
        w.writeheader()
        for s in sorted(unique.values(), key=lambda s: (s.filed, s.accession)):
            w.writerow(asdict(s))


# ------------------------------------------------------------------ large single buys

@dataclass
class BigBuy:
    issuer_cik: str
    issuer_name: str
    symbol: str
    insider: str
    role: str
    value: float
    trade_dates: list[str]
    accession: str


def big_buys(new: list[alerts.Buy], threshold: float = BIG_BUY_VALUE) -> list[BigBuy]:
    """One insider's open-market purchases in one filing, summed, when they reach threshold."""
    groups: dict[tuple, list[alerts.Buy]] = {}
    seen: set[tuple] = set()
    for b in new:
        if alerts._same_trade(b) in seen:   # the same purchase reported by a related filer
            continue
        seen.add(alerts._same_trade(b))
        groups.setdefault((b.issuer_cik, b.insider, b.accession), []).append(b)
    out = []
    for (cik, insider, acc), g in groups.items():
        total = sum(b.value for b in g)
        if total >= threshold:
            out.append(BigBuy(issuer_cik=cik, issuer_name=g[0].issuer_name, symbol=g[0].symbol, insider=insider,
                              role=g[0].role, value=total, trade_dates=sorted({b.trade_date for b in g}),
                              accession=acc))
    return sorted(out, key=lambda x: -x.value)


# ------------------------------------------------------------------ state

@dataclass
class WatchState:
    seen: list[str] = field(default_factory=list)
    last_poll: str = ""

    @classmethod
    def load(cls, path: str) -> WatchState:
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return cls(seen=d.get("seen", []), last_poll=d.get("last_poll", ""))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"seen": self.seen[-KEEP_SEEN:], "last_poll": self.last_poll}, fh)


# ------------------------------------------------------------------ one poll

@dataclass
class PollResult:
    filings: int = 0
    new_buys: list[alerts.Buy] = field(default_factory=list)
    clusters: list[alerts.Cluster] = field(default_factory=list)
    big: list[BigBuy] = field(default_factory=list)
    stakes: list[Stake] = field(default_factory=list)          # every new 13D/13G seen
    tracked_stakes: list[Stake] = field(default_factory=list)  # the ones that alert
    dilution: list[FeedEntry] = field(default_factory=list)     # offering filings by recently alerted companies
    failures: int = 0


def submission_text(entry: FeedEntry) -> str:
    return http.get(f"{alerts.ARCHIVES}edgar/data/{int(entry.cik)}/{entry.accession}.txt",
                    headers=_sec_headers(), ttl=0, as_json=False)


def poll(state: WatchState, buys: list[alerts.Buy], alerted: dict[str, str], *, now: datetime,
         feed_fn: Callable[[str, int], str] = fetch_feed,
         submission_fn: Callable[[FeedEntry], str] = submission_text,
         is_fund: Callable[[str], bool] = alerts.issuer_is_fund,
         watch_ciks: set[str] | frozenset[str] = frozenset(),
         log: Callable[[str], None] = lambda m: None) -> PollResult:
    """Read the feeds once and work out what's new. Mutates `buys`, `alerted` and `state`
    (the caller saves them) and returns what should be announced."""
    since = (_when(state.last_poll) - OVERLAP) if state.last_poll else now - FIRST_RUN_LOOKBACK
    seen = set(state.seen)
    entries: list[FeedEntry] = []
    for form in FEED_QUERIES:
        try:
            entries += new_entries(form, seen, since, feed_fn)
        except (http.DataUnavailable, ET.ParseError) as exc:
            log(f"feed {form!r}: {exc}")
    res = PollResult()
    today = now.date()
    for acc, group in by_accession(entries).items():
        form = group[0].form
        if form in INSIDER_FORMS:
            res.filings += 1
            try:
                text = submission_fn(group[0])
            except http.DataUnavailable:
                res.failures += 1
                continue   # not marked seen, so the next poll retries it
            res.new_buys += alerts.buys_from_submission(text, acc, group[0].updated[:10])
        elif form in STAKE_FORMS:
            res.filings += 1
            stake = stake_from_group(group)
            if stake is None:
                continue   # the other party's entry may arrive on the next poll
            res.stakes.append(stake)
            key = "13d:" + stake.accession
            if stake.tracked and key not in alerted:
                alerted[key] = today.isoformat()
                res.tracked_stakes.append(stake)
        elif form in dilution.WATCH_FORMS:
            hit = next((e for e in group if e.cik in watch_ciks), None)
            if hit is None:
                continue   # offering filings matter only for companies that alerted recently
            res.dilution.append(hit)
        else:
            continue   # a prefix match such as 424B2: filtered out every time, so not worth recording
        state.seen.append(acc)

    if res.new_buys:
        buys.extend(res.new_buys)
        touched = {b.issuer_cik for b in res.new_buys}
        clusters = [c for c in alerts.find_clusters(buys, today) if c.issuer_cik in touched]
        res.clusters = alerts.new_clusters(clusters, alerted, today, is_fund)
        for c in res.clusters:
            alerted[c.issuer_cik] = today.isoformat()
        cutoff = (today - timedelta(days=alerts.REALERT_AFTER_DAYS)).isoformat()
        for big in big_buys(res.new_buys):
            key = "big:" + big.issuer_cik
            if (big.symbol.strip().upper() in alerts.NO_TICKER or alerted.get(key, "") >= cutoff
                    or is_fund(big.issuer_cik)):
                continue
            alerted[key] = today.isoformat()
            res.big.append(big)
    state.last_poll = now.isoformat(timespec="seconds")
    return res


# ------------------------------------------------------------------ announcements

def _money(x: float) -> str:
    return alerts._money(x)


def filing_url(cik: str, accession: str) -> str:
    return f"{alerts.ARCHIVES}edgar/data/{int(cik)}/{accession.replace('-', '')}/"


def big_buy_message(b: BigBuy) -> tuple[str, str, str]:
    title = f"Insider buy: {b.symbol} {_money(b.value)} by {b.insider}"
    body = (f"{b.insider} ({b.role}) bought {_money(b.value)} of {b.issuer_name} on the open market "
            f"({', '.join(b.trade_dates)}). One large buy is weaker evidence than a cluster; check the filing.")
    return title, body, filing_url(b.issuer_cik, b.accession)


def stake_title(s: Stake) -> str:
    kind = "Activist stake (13D)" if "13D" in s.form else "Passive stake (13G)"
    amended = " amended" if s.form.endswith("/A") else ""
    return f"{kind}{amended}: {s.tracked or s.filer_name} in {s.subject_name}"


def stake_body(s: Stake) -> str:
    what = ("Schedule 13D: the filer owns more than 5% and may try to influence the company "
            "(board seats, a sale, capital returns). It is due within 5 business days of crossing 5%."
            if "13D" in s.form else
            "Schedule 13G: the filer owns more than 5% as a passive investor.")
    return f"""**{s.filer_name}**{f' ({s.tracked}, a tracked investor)' if s.tracked else ''} filed a **{s.form}** on **{s.subject_name}** on {s.filed}.

{what}{' This is an amendment: the stake or the filer’s plans changed.' if s.form.endswith('/A') else ''}

- Filing: {s.url or filing_url(s.subject_cik, s.accession)}
- Next step: find the ticker and run `mt analyze <ticker>`, or open a deep dive in the dashboard. Read Item 4 (purpose of the transaction) in the filing first.

_Opened automatically by the filing watcher._
"""


def summary_lines(res: PollResult) -> list[str]:
    lines = [f"{res.filings} new filings, {len(res.new_buys)} qualifying buys, {len(res.stakes)} 13D/13G, "
             f"{len(res.clusters)} cluster alerts, {len(res.big)} large-buy alerts, "
             f"{len(res.tracked_stakes)} tracked-investor stakes, {len(res.dilution)} offering filings by alerted companies"
             + (f", {res.failures} fetch failures" if res.failures else "")]
    for c in res.clusters:
        lines.append(f"  CLUSTER {c.symbol or '-'} {c.issuer_name}: {len(c.insiders)} insiders, {_money(c.total_value)}")
    for b in res.big:
        lines.append(f"  BIG     {b.symbol or '-'} {b.issuer_name}: {b.insider} {_money(b.value)}")
    for e in res.dilution:
        lines.append(f"  DILUTE  {e.form:<8} {e.name[:50]}")
    for s in res.stakes:
        lines.append(f"  {'TRACKED' if s.tracked else 'STAKE  '} {s.form:<14} {s.filer_name[:40]} -> {s.subject_name[:40]}")
    return lines


def sleep_until_next(interval: float, started: float) -> None:
    time.sleep(max(0.0, interval - (time.monotonic() - started)))
