"""People worth following, and what copying them would have earned from the day each move
became public (the day you could actually have acted).

- ARK Invest: the ETFs publish every holding every day; the difference between two days is
  what Cathie Wood's team bought and sold.
- Congress: House members' periodic transaction reports (PTRs), from the House Clerk's filing
  index and the PDFs themselves. Senators' reports come from the Senate's eFD search.
  Members report trades up to 45 days late, which the copy simulation accounts for.
- Company insiders: the filing watcher's open-market purchases (Form 4).
- Activists: 13D stakes by tracked investors (the filing watcher's data).
- Superinvestors: 13F quarterly holdings (see the Smart money tab).

The copy simulation puts the same dollar amount into each disclosed purchase at the close on
the disclosure date, sells on disclosed sales, and compares with putting the same dollars into
SPY on the same days. It ignores the gap between a close and when you would really have
traded, taxes and fees.
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from . import http
from .providers import market

ARK_URL = "https://assets.ark-funds.com/fund-documents/funds-etf-csv/{file}"
ARK_FUNDS = {
    "ARKK": "ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv",
    "ARKW": "ARK_NEXT_GENERATION_INTERNET_ETF_ARKW_HOLDINGS.csv",
    "ARKG": "ARK_GENOMIC_REVOLUTION_ETF_ARKG_HOLDINGS.csv",
    "ARKF": "ARK_FINTECH_INNOVATION_ETF_ARKF_HOLDINGS.csv",
    "ARKQ": "ARK_AUTONOMOUS_TECH._&_ROBOTICS_ETF_ARKQ_HOLDINGS.csv",
    "ARKX": "ARK_SPACE_&_DEFENSE_INNOVATION_ETF_ARKX_HOLDINGS.csv",
}
HOUSE_INDEX = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_PTR = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc}.pdf"
SENATE_HOME = "https://efdsearch.senate.gov/search/home/"
SENATE_DATA = "https://efdsearch.senate.gov/search/report/data/"
SENATE_BASE = "https://efdsearch.senate.gov"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126 Safari/537.36")


@dataclass
class Move:
    who: str                 # person or fund
    group: str               # congress / ark / insider / activist
    symbol: str
    action: str              # Buy / Sell / New / Exit / Stake
    traded: str              # trade date (YYYY-MM-DD) when known
    disclosed: str           # when it became public
    detail: str = ""
    amount: str = ""
    url: str = ""


# ------------------------------------------------------------------ ARK

def parse_ark_csv(text: str) -> tuple[str, dict[str, dict]]:
    """(as-of date, {ticker: {shares, weight, company}}) from an ARK holdings CSV."""
    rows = {}
    day = ""
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        t = r.get("ticker", "").upper().split(" ")[0]
        if not t or not r.get("shares"):
            continue
        try:
            shares = float(r["shares"].replace(",", ""))
            weight = float(r.get("weight (%)", "0").replace("%", "") or 0)
        except ValueError:
            continue
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", r.get("date", ""))
        if m:
            day = f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        rows[t] = {"shares": shares, "weight": weight, "company": r.get("company", "")}
    return day, rows


def ark_trades(fund: str, prev_day: str, prev: dict, day: str, cur: dict, min_change: float = 0.01) -> list[Move]:
    """Share-count changes between two daily snapshots (under 1% is noise from creations)."""
    out = []
    for t in sorted(set(prev) | set(cur)):
        a, b = prev.get(t, {}).get("shares", 0.0), cur.get(t, {}).get("shares", 0.0)
        if a == b:
            continue
        company = (cur.get(t) or prev.get(t) or {}).get("company", "")
        if a == 0:
            action, change = "New", "new position"
        elif b == 0:
            action, change = "Exit", "sold the whole position"
        else:
            pct = (b - a) / a
            if abs(pct) < min_change:
                continue
            action, change = ("Buy" if pct > 0 else "Sell"), f"{pct:+.1%} shares"
        out.append(Move(f"ARK {fund}", "ark", t, action, prev_day, day,
                        f"{company}: {change} ({abs(b - a):,.0f} shares; weight now {cur.get(t, {}).get('weight', 0):.2f}%)",
                        url=f"https://www.ark-funds.com/funds/{fund.lower()}"))
    return out


def fetch_ark(get=http.get) -> dict[str, tuple[str, dict]]:
    out = {}
    for fund, file in ARK_FUNDS.items():
        try:
            out[fund] = parse_ark_csv(get(ARK_URL.format(file=file), headers={"User-Agent": BROWSER_UA}, ttl=3600,
                                          as_json=False))
        except http.DataUnavailable:
            continue
    return out


# ------------------------------------------------------------------ Congress: House

def parse_house_index(zip_bytes: bytes, since: str) -> list[dict]:
    """Periodic transaction reports (FilingType P) filed on or after `since`, newest first."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
        root = ET.fromstring(z.read(name))
    out = []
    for m in root.iter("Member"):
        if (m.findtext("FilingType") or "").strip() != "P":
            continue
        mm, dd, yy = (m.findtext("FilingDate") or "0/0/0").strip().split("/")
        filed = f"{yy}-{int(mm):02d}-{int(dd):02d}"
        if filed < since:
            continue
        doc = (m.findtext("DocID") or "").strip()
        year = (m.findtext("Year") or yy).strip()
        who = " ".join(x for x in [m.findtext("First"), m.findtext("Last")] if x).strip()
        out.append({"who": f"Rep. {who}", "state": (m.findtext("StateDst") or "").strip(), "filed": filed, "doc": doc,
                    "url": HOUSE_PTR.format(year=year, doc=doc)})
    return sorted(out, key=lambda r: r["filed"], reverse=True)


_PTR_ROW = re.compile(
    r"\(([A-Z][A-Z.\-]{0,6})\)\s*(?:\[[A-Z]{2}\])?\s*(P|S\s*\(partial\)|S|E)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+"
    r"(\$[\d,]+\s*-\s*\$[\d,]+|Over \$[\d,]+|\$[\d,]+ and over)")
_HEADER_END = re.compile(r"\$200\?|Cap\.\s*Gains\s*>\s*\$200\??", re.I)


def parse_ptr_text(text: str) -> list[dict]:
    """Transactions from a House PTR's text: owner, asset, ticker, type, dates, amount range.
    Rows wrap across lines in the PDF, so the text is flattened and each row is found by its
    '(TICKER) [ST] P mm/dd/yyyy mm/dd/yyyy $amount' tail; the asset is the text before it."""
    flat = re.sub(r"\s+", " ", text.replace("\x00", " "))
    out = []
    prev_end = 0
    for m in _PTR_ROW.finditer(flat):
        before = flat[prev_end:m.start()]
        cut = list(_HEADER_END.finditer(before))
        if cut:
            before = before[cut[-1].end():]
        before = before.strip()[-140:]
        owner = re.match(r"^(SP|JT|DC)\s+", before)
        asset = before[owner.end():] if owner else before
        ticker, kind, traded, _notified, amount = m.groups()
        kind = kind.replace(" ", "")
        action = {"P": "Buy", "S": "Sell", "S(partial)": "Sell (partial)", "E": "Exchange"}.get(kind, kind)
        mm, dd, yy = traded.split("/")
        out.append({"owner": {"SP": "spouse", "JT": "joint", "DC": "child"}.get(owner.group(1) if owner else "", "self"),
                    "asset": asset.strip(" -"), "symbol": ticker, "action": action,
                    "traded": f"{yy}-{mm}-{dd}", "amount": re.sub(r"\s+", " ", amount)})
        prev_end = m.end()
    return out


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader     # imported lazily: only the House reports need it
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def house_moves(today: date, days: int = 30, limit: int = 40, get_bytes=None,
                text_fn: Callable[[bytes], str] | None = None) -> tuple[list[Move], list[str]]:
    get_bytes = get_bytes or http.get_bytes
    text_fn = text_fn or pdf_text
    since = (today - timedelta(days=days)).isoformat()
    errors: list[str] = []
    filings: list[dict] = []
    for year in sorted({today.year, (today - timedelta(days=days)).year}, reverse=True):
        try:
            filings += parse_house_index(get_bytes(HOUSE_INDEX.format(year=year)), since)
        except (http.DataUnavailable, zipfile.BadZipFile, ET.ParseError, StopIteration) as exc:
            errors.append(f"House index {year}: {exc}")
    moves: list[Move] = []
    for f in filings[:limit]:
        try:
            rows = parse_ptr_text(text_fn(get_bytes(f["url"])))
        except Exception as exc:          # scanned paper filings and odd PDFs: keep the link
            errors.append(f"{f['who']} {f['doc']}: {str(exc)[:80]}")
            rows = []
        if not rows:
            moves.append(Move(f["who"], "congress", "", "Report", "", f["filed"], "Open the filing to read it", url=f["url"]))
        for r in rows:
            moves.append(Move(f["who"], "congress", r["symbol"], r["action"], r["traded"], f["filed"],
                              f"{r['asset']} ({r['owner']})", r["amount"], f["url"]))
    return moves, errors


# ------------------------------------------------------------------ Congress: Senate

def senate_moves(today: date, days: int = 30, session=None) -> tuple[list[Move], list[str]]:
    """Senators' PTRs from efdsearch.senate.gov (accepts the site's terms, then searches)."""
    import httpx
    errors: list[str] = []
    moves: list[Move] = []
    own = session is None
    s = session or httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": BROWSER_UA})
    try:
        home = s.get(SENATE_HOME)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', home.text)
        if not token:
            return [], ["Senate eFD: no form token (the site may be blocking servers)"]
        s.post(SENATE_HOME, data={"prohibition_agreement": "1", "csrfmiddlewaretoken": token.group(1)},
               headers={"Referer": SENATE_HOME})
        csrf = s.cookies.get("csrftoken") or token.group(1)
        start = (today - timedelta(days=days)).strftime("%m/%d/%Y 00:00:00")
        r = s.post(SENATE_DATA, data={"start": "0", "length": "60", "report_types": "[11]", "filer_types": "[]",
                                      "submitted_start_date": start, "submitted_end_date": "", "candidate_state": "",
                                      "senator_state": "", "office_id": "", "first_name": "", "last_name": "",
                                      "csrfmiddlewaretoken": csrf},
                   headers={"Referer": "https://efdsearch.senate.gov/search/", "X-CSRFToken": csrf})
        rows = r.json().get("data") or []
        for first, last, _office, link_html, filed in rows[:40]:
            m = re.search(r'href="([^"]+)"', link_html)
            if not m or "/ptr/" not in m.group(1):
                continue
            url = SENATE_BASE + m.group(1)
            who = f"Sen. {first} {last}".strip()
            mm, dd, yy = filed.split("/")
            disclosed = f"{yy}-{mm}-{dd}"
            page = s.get(url, headers={"Referer": "https://efdsearch.senate.gov/search/"}).text
            got = parse_senate_ptr(page)
            if not got:
                moves.append(Move(who, "congress", "", "Report", "", disclosed, "Open the filing to read it", url=url))
            for t in got:
                moves.append(Move(who, "congress", t["symbol"], t["action"], t["traded"], disclosed,
                                  f"{t['asset']} ({t['owner']})", t["amount"], url))
    except Exception as exc:
        errors.append(f"Senate eFD: {str(exc)[:120]}")
    finally:
        if own:
            s.close()
    return moves, errors


def parse_senate_ptr(html: str) -> list[dict]:
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 8 or not re.match(r"\d{2}/\d{2}/\d{4}", cells[1]):
            continue
        ticker = cells[3].split()[0] if cells[3] and cells[3] != "--" else ""
        if not ticker:
            continue
        mm, dd, yy = cells[1].split("/")
        out.append({"traded": f"{yy}-{mm}-{dd}", "owner": cells[2].lower(), "symbol": ticker.upper(), "asset": cells[4],
                    "action": {"Purchase": "Buy", "Sale (Full)": "Sell", "Sale (Partial)": "Sell (partial)"}.get(cells[6], cells[6]),
                    "amount": cells[7]})
    return out


# ------------------------------------------------------------------ insiders and activists

def insider_moves(buys, today: date, days: int = 14, min_value: float = 250_000) -> list[Move]:
    since = (today - timedelta(days=days)).isoformat()
    agg: dict[tuple, dict] = {}
    for b in buys:
        if b.filed < since or not b.symbol or b.symbol.upper() in ("NONE", "N/A"):
            continue
        k = (b.insider, b.symbol, b.accession)
        a = agg.setdefault(k, {"b": b, "value": 0.0})
        a["value"] += b.value
    out = []
    for a in sorted(agg.values(), key=lambda a: -a["value"]):
        b = a["b"]
        if a["value"] < min_value:
            continue
        out.append(Move(f"{b.insider} ({b.role.split(',')[0]})", "insider", b.symbol, "Buy", b.trade_date, b.filed,
                        b.issuer_name, f"${a['value']:,.0f}",
                        f"https://www.sec.gov/Archives/edgar/data/{int(b.issuer_cik)}/{b.accession.replace('-', '')}/"))
    return out[:40]


# ------------------------------------------------------------------ copy simulation

def copy_sim(moves: list[Move], history_fn: Callable | None = None, amount: float = 1000.0,
             benchmark: str = "SPY", today: date | None = None) -> dict:
    """Same dollars into each disclosed buy at the disclosure-day close; sales close the position."""
    today = today or date.today()
    history_fn = history_fn or market.get_history
    trades = sorted((m for m in moves if m.symbol and m.action in ("Buy", "New", "Sell", "Sell (partial)", "Exit")),
                    key=lambda m: m.disclosed)
    if not trades:
        return {"trades": 0}
    first = trades[0].disclosed
    span = (today - date.fromisoformat(first)).days + 10
    cache: dict[str, dict[str, float]] = {}

    def closes(sym):
        if sym not in cache:
            try:
                cache[sym] = {b.date: b.close for b in history_fn(sym, max(span, 30))}
            except http.DataUnavailable:
                cache[sym] = {}
        return cache[sym]

    def close_on(sym, day):
        c = closes(sym)
        days = [d for d in c if d >= day]
        return c[min(days)] if days else None

    def last(sym):
        c = closes(sym)
        return c[max(c)] if c else None

    held: dict[str, float] = {}
    bench_units = cash_in = cash_out = 0.0
    used = 0
    for m in trades:
        p = close_on(m.symbol, m.disclosed)
        if not p:
            continue
        if m.action in ("Buy", "New"):
            held[m.symbol] = held.get(m.symbol, 0.0) + amount / p
            cash_in += amount
            bp = close_on(benchmark, m.disclosed)
            bench_units += amount / bp if bp else 0
            used += 1
        elif held.get(m.symbol):
            frac = 0.5 if "partial" in m.action else 1.0
            units = held[m.symbol] * frac
            held[m.symbol] -= units
            cash_out += units * p
            used += 1
    value = sum(u * (last(s) or 0) for s, u in held.items()) + cash_out
    bench_value = bench_units * (last(benchmark) or 0)
    return {"trades": used, "invested": cash_in, "value": round(value, 2),
            "return_pct": round((value / cash_in - 1) * 100, 2) if cash_in else None,
            "benchmark": benchmark, "benchmark_value": round(bench_value, 2),
            "benchmark_return_pct": round((bench_value / cash_in - 1) * 100, 2) if cash_in else None,
            "since": first}


def moves_dicts(moves: list[Move]) -> list[dict]:
    return [asdict(m) | {"lag_days": _lag(m)} for m in moves]


def _lag(m: Move) -> int | None:
    try:
        return (datetime.fromisoformat(m.disclosed) - datetime.fromisoformat(m.traded)).days
    except ValueError:
        return None


# ------------------------------------------------------------------ assembled (with the app's database)

def ark_moves_stored(get=http.get) -> tuple[list[Move], dict[str, str]]:
    """Fetch today's ARK holdings, store them, and diff the two latest stored days per fund."""
    import json
    from . import db
    moves: list[Move] = []
    status: dict[str, str] = {}
    for fund, (day, rows) in fetch_ark(get).items():
        if not day or not rows:
            continue
        with db.connect() as conn:
            db.save_snapshot(conn, "ark:" + fund, day, json.dumps(rows))
            snaps = db.snapshots(conn, "ark:" + fund, 2)
        if len(snaps) < 2:
            status[fund] = f"first snapshot {day}; trades show after the next trading day"
            continue
        (d1, cur), (d0, prev) = snaps[0], snaps[1]
        moves += ark_trades(fund, d0, json.loads(prev), d1, json.loads(cur))
        status[fund] = f"{d0} → {d1}"
    return moves, status


def activist_moves(today: date, get=http.get, tickers_fn=None) -> list[Move]:
    from .pulse import DATA_URL
    from .radar import cik_to_ticker
    tickers = (tickers_fn or cik_to_ticker)()
    text = get(f"{DATA_URL}/stakes.csv", ttl=600, as_json=False)
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("filed", "") < (today - timedelta(days=60)).isoformat():
            continue
        sym = tickers.get((r.get("subject_cik") or "").zfill(10), "")
        who = r.get("tracked") or r.get("filer_name") or ""
        out.append(Move(who, "activist", sym, "Stake", "", r.get("filed", ""),
                        f"{r.get('form')}: {r.get('subject_name')}", url=r.get("url", "")))
    return sorted(out, key=lambda m: m.disclosed, reverse=True)[:40]


def build(today: date | None = None, follows: dict[str, str] | None = None, *, ark_fn=None, house_fn=None,
          senate_fn=None, insiders_fn=None, activists_fn=None) -> dict:
    today = today or date.today()
    errors: list[str] = []
    sections: dict[str, list[Move]] = {}

    def attempt(name, fn):
        try:
            return fn()
        except Exception as exc:
            errors.append(f"{name}: {str(exc)[:120]}")
            return None

    got = attempt("ARK", ark_fn or ark_moves_stored)
    ark_status = {}
    if got:
        sections["ark"], ark_status = got
    got = attempt("House", house_fn or (lambda: house_moves(today)))
    if got:
        sections["house"], errs = got
        errors += errs[:5]
    got = attempt("Senate", senate_fn or (lambda: senate_moves(today)))
    if got:
        sections["senate"], errs = got
        errors += errs[:3]

    def default_insiders():
        from .pulse import load_watcher_buys
        return insider_moves(load_watcher_buys(), today)
    sections["insider"] = attempt("Insiders", insiders_fn or default_insiders) or []
    sections["activist"] = attempt("Activists", activists_fn or (lambda: activist_moves(today))) or []
    congress = sorted(sections.pop("house", []) + sections.pop("senate", []), key=lambda m: m.disclosed, reverse=True)
    sections["congress"] = congress
    follows = follows or {}
    followed = sorted((m for ms in sections.values() for m in ms if m.who in follows), key=lambda m: m.disclosed,
                      reverse=True)
    people: dict[str, dict] = {}
    for grp, ms in sections.items():
        for m in ms:
            p = people.setdefault(m.who, {"who": m.who, "group": grp, "moves": 0, "latest": m.disclosed})
            p["moves"] += 1
            p["latest"] = max(p["latest"], m.disclosed)
    return {"as_of": today.isoformat(), "sections": {k: moves_dicts(v) for k, v in sections.items()},
            "following": moves_dicts(followed), "people": sorted(people.values(), key=lambda p: p["latest"], reverse=True),
            "follows": follows, "ark_status": ark_status, "errors": errors}
