"""SEC EDGAR: 13F-HR institutional holdings and Form 4 insider transactions.

EDGAR requires a descriptive User-Agent with a contact email (SEC_USER_AGENT) and caps
clients at 10 requests/second; `http` throttles SEC hosts accordingly.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .. import http
from ..config import settings
from ..investors import HIGH_CONVICTION, INVESTORS, Investor

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
TICKERS = "https://www.sec.gov/files/company_tickers.json"


def _sec_get(url: str, ttl: float = 3600, as_json: bool = True):
    return http.get(url, headers={"User-Agent": settings.sec_user_agent,
                                  "Accept-Encoding": "gzip, deflate"}, ttl=ttl, as_json=as_json)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el: ET.Element, name: str) -> ET.Element | None:
    for child in el.iter():
        if _local(child.tag) == name:
            return child
    return None


def _text(el: ET.Element | None, name: str, default: str = "") -> str:
    if el is None:
        return default
    found = _find(el, name)
    if found is None:
        return default
    # Form 4 wraps most scalars in <value>.
    value = _find(found, "value")
    txt = (value.text if value is not None else found.text) or ""
    return txt.strip() or default


def _num(txt: str) -> float:
    try:
        return float(txt.replace(",", ""))
    except ValueError:
        return 0.0


# ------------------------------------------------------------------ ticker map

# Words that carry no identity. "DE"/"DEL"/"MO"/"N" are state-of-incorporation tags and
# 13F truncations ("... INC N" is "INC NEW" cut at the column limit).
_NOISE = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC",
          "HLDG", "HLDGS", "HOLDING", "HOLDINGS", "GROUP", "GRP", "CLASS", "CL", "COM", "NEW", "DEL",
          "DE", "MD", "MO", "NY", "NJ", "PA", "THE", "OF", "AND", "SA", "NV", "AG", "LP", "LLC",
          "TRUST", "ADR", "ADS", "SPONSORED", "SPON", "ORD", "SHS", "N", "A", "B", "C"}
# SEC 13F issuer names are heavily abbreviated; company_tickers.json titles mostly aren't.
_ABBREV = {"AMER": "AMERICA", "AMERN": "AMERICAN", "FINL": "FINANCIAL", "PETE": "PETROLEUM",
           "INTL": "INTERNATIONAL", "NATL": "NATIONAL", "MGMT": "MANAGEMENT", "SVCS": "SERVICES",
           "SVC": "SERVICE", "SYS": "SYSTEMS", "TECHNOLOGIES": "TECHNOLOGY", "TECH": "TECHNOLOGY",
           "COMMUNICATION": "COMMUNICATIONS", "COMM": "COMMUNICATIONS", "PPTYS": "PROPERTIES",
           "RES": "RESOURCES", "ENTMT": "ENTERTAINMENT", "LABS": "LABORATORIES", "MFG": "MANUFACTURING",
           "INDS": "INDUSTRIES", "GEN": "GENERAL", "ELEC": "ELECTRIC", "PHARMACEUTICALS": "PHARMACEUTICAL",
           "BANCORPORATION": "BANCORP", "HEALTHCARE": "HEALTH CARE", "INVT": "INVESTMENT",
           "INVTS": "INVESTMENTS", "DEV": "DEVELOPMENT", "MTRS": "MOTORS",
           "PAC": "PACIFIC", "SOLUTIONS": "SOLUTION", "UTD": "UNITED"}


def company_tokens(name: str) -> tuple[str, ...]:
    name = re.sub(r"['’]", "", name.upper())  # DOMINO'S -> DOMINOS
    name = re.sub(r"/\s*[A-Z]{2,4}\s*/?", " ", name)  # state tags: "VERISIGN INC/CA", "/DE/"
    words = re.sub(r"[^A-Z0-9 ]", " ", name.replace("&", " AND ")).split()
    # Re-join spelled-out initials ("P L C" -> "PLC"); "&" was turned into AND above, so
    # "S&P" stays two letters on both sides and still matches.
    merged: list[str] = []
    run = ""
    for w in words:
        if len(w) == 1 and w.isalpha():
            run += w
            continue
        if run:
            merged.append(run)
            run = ""
        merged.append(w)
    if run:
        merged.append(run)
    out: list[str] = []
    for w in merged:
        w = _ABBREV.get(w, w)
        out.extend(x for x in w.split() if x not in _NOISE)
    return tuple(out)


def normalize_company(name: str) -> str:
    return " ".join(company_tokens(name))


@dataclass
class TickerMap:
    by_ticker: dict[str, dict]
    by_name: dict[str, str]
    # (first two tokens) -> [(tokens, ticker)] in company_tickers order (largest first)
    by_prefix: dict[tuple[str, ...], list[tuple[tuple[str, ...], str]]] = field(default_factory=dict)
    by_compact: dict[str, str] = field(default_factory=dict)
    by_sorted: dict[str, str] = field(default_factory=dict)

    def cik_for(self, ticker: str) -> str | None:
        row = self.by_ticker.get(ticker.upper())
        return str(row["cik_str"]).zfill(10) if row else None

    def ticker_for_issuer(self, issuer: str) -> str | None:
        tokens = company_tokens(issuer)
        # Same words, different spacing ("SIRIUSXM" vs "SIRIUS XM") or order ("HORTON D R").
        exact = (self.by_name.get(" ".join(tokens)) or self.by_compact.get("".join(tokens))
                 or (self.by_sorted.get(" ".join(sorted(tokens))) if len(tokens) >= 2 else None))
        if exact or len(tokens) < 2:
            # Single-word names only match exactly: "APPLE" must not become "APPLE HOSPITALITY".
            return exact
        # 13F names are truncated at a column limit, and titles carry extra suffixes, so fall
        # back to a prefix match in either direction, requiring two identifying words.
        for cand_tokens, ticker in self.by_prefix.get(tokens[:2], []):
            n = min(len(tokens), len(cand_tokens))
            if cand_tokens[:n] == tokens[:n]:
                return ticker
        return None


def build_ticker_map(raw: dict) -> TickerMap:
    by_ticker: dict[str, dict] = {}
    by_name: dict[str, str] = {}
    by_prefix: dict[tuple[str, ...], list[tuple[tuple[str, ...], str]]] = {}
    by_compact: dict[str, str] = {}
    by_sorted: dict[str, str] = {}
    for row in raw.values():
        ticker = row["ticker"].upper()
        by_ticker[ticker] = row
        tokens = company_tokens(row["title"])
        # First (lowest index = largest company) ticker wins for a name, which prefers
        # the primary share class.
        by_name.setdefault(" ".join(tokens), ticker)
        by_compact.setdefault("".join(tokens), ticker)
        if len(tokens) >= 2:
            by_sorted.setdefault(" ".join(sorted(tokens)), ticker)
            by_prefix.setdefault(tokens[:2], []).append((tokens, ticker))
    return TickerMap(by_ticker, by_name, by_prefix, by_compact, by_sorted)


_ticker_map: TickerMap | None = None


def ticker_map() -> TickerMap:
    global _ticker_map
    if _ticker_map is None:
        _ticker_map = build_ticker_map(_sec_get(TICKERS, ttl=86400))
    return _ticker_map


# ------------------------------------------------------------------ 13F

@dataclass
class Holding:
    issuer: str
    cusip: str
    title: str
    value_usd: float
    shares: float
    put_call: str | None = None
    ticker: str | None = None


@dataclass
class Filing13F:
    investor: str
    filer_name: str
    period: str
    filed: str
    accession: str
    holdings: list[Holding] = field(default_factory=list)

    @property
    def total_value(self) -> float:
        return sum(h.value_usd for h in self.holdings)


def parse_13f_infotable(xml_text: str, report_date: str = "") -> list[Holding]:
    """Parse a 13F information table, aggregating rows per CUSIP + put/call.

    Since 2023-01-03 values are reported in whole dollars; earlier filings used $1000s.
    """
    root = ET.fromstring(xml_text)
    multiplier = 1000.0 if report_date and report_date < "2022-12-31" else 1.0
    agg: dict[tuple[str, str | None], Holding] = {}
    for row in root.iter():
        if _local(row.tag) != "infoTable":
            continue
        put_call = _text(row, "putCall") or None
        cusip = _text(row, "cusip")
        key = (cusip, put_call)
        value = _num(_text(row, "value")) * multiplier
        shares = _num(_text(row, "sshPrnamt"))
        if key in agg:
            agg[key].value_usd += value
            agg[key].shares += shares
        else:
            agg[key] = Holding(issuer=_text(row, "nameOfIssuer"), cusip=cusip,
                               title=_text(row, "titleOfClass"), value_usd=value,
                               shares=shares, put_call=put_call)
    return sorted(agg.values(), key=lambda h: h.value_usd, reverse=True)


def _recent_filings(cik: str, forms: set[str]) -> tuple[str, list[dict]]:
    data = _sec_get(SUBMISSIONS.format(cik=cik), ttl=3600)
    recent = data["filings"]["recent"]
    rows = []
    for i, form in enumerate(recent["form"]):
        if form in forms:
            rows.append({k: recent[k][i] for k in
                         ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")})
    return data.get("name", ""), rows


def all_filings(cik: str, forms: set[str]) -> tuple[str, list[dict]]:
    """Every filing of the given forms, following EDGAR's older-history pages beyond the
    ~1,000 most recent filings that the main submissions file holds."""
    data = _sec_get(SUBMISSIONS.format(cik=cik), ttl=3600)
    pages = [data["filings"]["recent"]]
    for extra in data["filings"].get("files", []):
        page = _sec_get("https://data.sec.gov/submissions/" + extra["name"], ttl=86400)
        # Older-history pages hold the column arrays at the top level; accept either shape.
        pages.append(page if "form" in page else page.get("filings", {}).get("recent", {"form": []}))
    rows = []
    for page in pages:
        for i, form in enumerate(page["form"]):
            if form in forms:
                rows.append({k: page[k][i] for k in
                             ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")})
    rows.sort(key=lambda r: r["filingDate"], reverse=True)
    return data.get("name", ""), rows


def _infotable_url(cik: str, accession: str) -> str:
    acc = accession.replace("-", "")
    index = _sec_get(ARCHIVE.format(cik=int(cik), acc=acc) + "/index.json", ttl=86400)
    items = index["directory"]["item"]
    xmls = [it["name"] for it in items if it["name"].lower().endswith(".xml")
            and "primary_doc" not in it["name"].lower()]
    if not xmls:
        raise http.DataUnavailable(f"No information table in 13F {accession}")
    # The info table is the largest non-primary XML.
    sizes = {it["name"]: int(it.get("size") or 0) for it in items if str(it.get("size") or "0").isdigit()}
    xmls.sort(key=lambda n: -sizes.get(n, 0))
    return ARCHIVE.format(cik=int(cik), acc=acc) + "/" + xmls[0]


def verify_investor(inv: Investor) -> tuple[str, bool]:
    """Return EDGAR's filer name for the investor's CIK and whether it matches expectations."""
    name = _sec_get(SUBMISSIONS.format(cik=inv.cik), ttl=86400).get("name", "")
    return name, inv.expected_name in name.upper()


def get_13f_filings(inv: Investor, count: int = 2) -> list[Filing13F]:
    """Most recent `count` 13F-HR filings (amendments excluded), newest first."""
    filer_name, rows = _recent_filings(inv.cik, {"13F-HR"})
    tmap = None
    try:
        tmap = ticker_map()
    except http.DataUnavailable:
        pass
    seen_periods: set[str] = set()
    filings = []
    for row in rows:
        if row["reportDate"] in seen_periods:
            continue
        seen_periods.add(row["reportDate"])
        xml_text = _sec_get(_infotable_url(inv.cik, row["accessionNumber"]), ttl=86400, as_json=False)
        holdings = parse_13f_infotable(xml_text, row["reportDate"])
        if tmap:
            for h in holdings:
                h.ticker = tmap.ticker_for_issuer(h.issuer)
        filings.append(Filing13F(investor=inv.key, filer_name=filer_name, period=row["reportDate"],
                                 filed=row["filingDate"], accession=row["accessionNumber"], holdings=holdings))
        if len(filings) >= count:
            break
    return filings


@dataclass
class PositionChange:
    issuer: str
    ticker: str | None
    cusip: str
    action: str  # new | added | reduced | exited | unchanged
    shares_now: float
    shares_before: float
    value_now: float
    weight_now: float  # % of reported portfolio
    share_change_pct: float | None


def diff_filings(current: Filing13F, previous: Filing13F | None) -> list[PositionChange]:
    """Quarter-over-quarter changes in common-stock positions (options rows excluded)."""
    now = {h.cusip: h for h in current.holdings if not h.put_call}
    before = {h.cusip: h for h in previous.holdings if not h.put_call} if previous else {}
    total = sum(h.value_usd for h in now.values()) or 1.0
    changes = []
    for cusip in set(now) | set(before):
        h_now, h_before = now.get(cusip), before.get(cusip)
        s_now = h_now.shares if h_now else 0.0
        s_before = h_before.shares if h_before else 0.0
        if previous is None:
            action = "unchanged"
        elif not h_before:
            action = "new"
        elif not h_now:
            action = "exited"
        elif s_now > s_before * 1.02:
            action = "added"
        elif s_now < s_before * 0.98:
            action = "reduced"
        else:
            action = "unchanged"
        ref = h_now or h_before
        changes.append(PositionChange(
            issuer=ref.issuer, ticker=ref.ticker, cusip=cusip, action=action,
            shares_now=s_now, shares_before=s_before,
            value_now=h_now.value_usd if h_now else 0.0,
            weight_now=(h_now.value_usd / total * 100) if h_now else 0.0,
            share_change_pct=((s_now / s_before - 1) * 100) if s_before else None,
        ))
    changes.sort(key=lambda c: (-c.value_now, c.issuer))
    return changes


def investor_report(inv: Investor) -> dict:
    filings = get_13f_filings(inv, count=2)
    if not filings:
        raise http.DataUnavailable(f"No 13F-HR filings found for {inv.fund}")
    return build_report(inv, filings[0], filings[1] if len(filings) > 1 else None)


def build_report(inv: Investor, current: Filing13F, previous: Filing13F | None,
                 as_of: date | None = None) -> dict:
    """Report for one investor from two consecutive 13F filings. `as_of` sets the date the
    staleness is measured from (today for live use; the rebalance date in backtests)."""
    changes = diff_filings(current, previous)
    return {
        "investor": asdict(inv),
        "filer_name": current.filer_name,
        "cik_verified": inv.expected_name in current.filer_name.upper(),
        "period": current.period,
        "filed": current.filed,
        "previous_period": previous.period if previous else None,
        "total_value_usd": current.total_value,
        "positions": len(held := [asdict(c) for c in changes if c.shares_now]),
        "top_holdings": held[:25],
        "holdings": held,
        "moves": {
            action: [asdict(c) for c in changes if c.action == action]
            for action in ("new", "added", "reduced", "exited")
        },
        "staleness_days": ((as_of or date.today()) - date.fromisoformat(current.period)).days,
    }


def smart_money_for_ticker(ticker: str, reports: list[dict]) -> dict:
    """Aggregate tracked investors' latest 13F stance on one ticker."""
    ticker = ticker.upper()
    holders, buyers, sellers = [], [], []
    for rep in reports:
        key = rep["investor"]["key"]
        weight = 1.0 if key in HIGH_CONVICTION else 0.35
        pos = next((p for p in rep.get("holdings", rep["top_holdings"]) if p["ticker"] == ticker), None)
        for action in ("new", "added", "reduced", "exited"):
            for move in rep["moves"][action]:
                if move["ticker"] != ticker:
                    continue
                entry = {"investor": rep["investor"]["person"], "fund": rep["investor"]["fund"],
                         "action": action, "weight_pct": move["weight_now"], "period": rep["period"],
                         "conviction_weight": weight}
                (buyers if action in ("new", "added") else sellers).append(entry)
        if pos:
            holders.append({"investor": rep["investor"]["person"], "weight_pct": pos["weight_now"],
                            "value_usd": pos["value_now"], "period": rep["period"]})
    buy_w = sum(b["conviction_weight"] for b in buyers)
    sell_w = sum(s["conviction_weight"] for s in sellers)
    net = (buy_w - sell_w) / (buy_w + sell_w) if (buy_w + sell_w) else 0.0
    return {"ticker": ticker, "holders": holders, "buyers": buyers, "sellers": sellers,
            "net_flow": net, "investors_scanned": len(reports)}


def all_investor_reports(keys: list[str] | None = None) -> tuple[list[dict], list[str]]:
    reports, errors = [], []
    for inv in INVESTORS:
        if keys and inv.key not in keys:
            continue
        try:
            reports.append(investor_report(inv))
        except (http.DataUnavailable, KeyError, ET.ParseError) as exc:
            errors.append(f"{inv.fund}: {exc}")
    return reports, errors


# ------------------------------------------------------------------ Form 4

@dataclass
class InsiderTrade:
    insider: str
    role: str
    date: str
    code: str  # P = open-market buy, S = open-market sale, A = award, M = option exercise, ...
    shares: float
    price: float
    acquired: bool
    shares_after: float
    ten_b5_1: bool = False
    filed: str = ""  # date the Form 4 became public; backtests must not use it before then

    @property
    def value(self) -> float:
        return self.shares * self.price


CODE_LABELS = {"P": "Open-market buy", "S": "Open-market sale", "A": "Grant/award", "M": "Option exercise",
               "F": "Tax withholding", "G": "Gift", "D": "Disposition to issuer", "C": "Conversion",
               "X": "Option exercise", "J": "Other"}


def parse_form4(xml_text: str) -> list[InsiderTrade]:
    root = ET.fromstring(xml_text)
    owner = _find(root, "reportingOwner")
    name = _text(owner, "rptOwnerName")
    rel = _find(root, "reportingOwnerRelationship")
    roles = []
    if rel is not None:
        if _text(rel, "isDirector") in ("1", "true"):
            roles.append("Director")
        if _text(rel, "isOfficer") in ("1", "true"):
            roles.append(_text(rel, "officerTitle") or "Officer")
        if _text(rel, "isTenPercentOwner") in ("1", "true"):
            roles.append("10% owner")
    ten_b5_1 = _text(root, "aff10b5One") in ("1", "true")
    trades = []
    for tx in root.iter():
        if _local(tx.tag) != "nonDerivativeTransaction":
            continue
        coding = _find(tx, "transactionCoding")
        amounts = _find(tx, "transactionAmounts")
        post = _find(tx, "postTransactionAmounts")
        trades.append(InsiderTrade(
            insider=name,
            role=", ".join(roles) or "Insider",
            date=_text(tx, "transactionDate"),
            code=_text(coding, "transactionCode"),
            shares=_num(_text(amounts, "transactionShares", "0")),
            price=_num(_text(amounts, "transactionPricePerShare", "0")),
            acquired=_text(amounts, "transactionAcquiredDisposedCode") == "A",
            shares_after=_num(_text(post, "sharesOwnedFollowingTransaction", "0")),
            ten_b5_1=ten_b5_1,
        ))
    return trades


def get_insider_trades(ticker: str, days: int = 180, max_filings: int = 40) -> list[InsiderTrade]:
    cik = ticker_map().cik_for(ticker)
    if not cik:
        raise http.DataUnavailable(f"{ticker} not found in SEC ticker list")
    _, rows = _recent_filings(cik, {"4"})
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    trades: list[InsiderTrade] = []
    for row in rows[:max_filings]:
        if row["filingDate"] < cutoff:
            break
        # primaryDocument is the XSL-rendered path (xslF345X05/foo.xml); raw XML drops the prefix.
        doc = row["primaryDocument"].split("/")[-1]
        url = ARCHIVE.format(cik=int(cik), acc=row["accessionNumber"].replace("-", "")) + "/" + doc
        try:
            trades.extend(parse_form4(_sec_get(url, ttl=86400, as_json=False)))
        except (http.DataUnavailable, ET.ParseError):
            continue
    return [t for t in trades if t.date >= cutoff]


def summarize_insiders(trades: list[InsiderTrade], days: int = 90, today: date | None = None) -> dict:
    """Open-market activity summary. Buys are the informative signal: insiders sell for
    many reasons (taxes, diversification, 10b5-1 plans) but buy for one."""
    cutoff = ((today or date.today()) - timedelta(days=days)).isoformat()
    recent = [t for t in trades if t.date >= cutoff]
    buys = [t for t in recent if t.code == "P"]
    sells = [t for t in recent if t.code == "S"]
    discretionary_sells = [t for t in sells if not t.ten_b5_1]
    buy_value = sum(t.value for t in buys)
    sell_value = sum(t.value for t in discretionary_sells)
    distinct_buyers = len({t.insider for t in buys})
    return {
        "window_days": days,
        "open_market_buys": len(buys),
        "open_market_sells": len(sells),
        "planned_10b5_1_sells": len(sells) - len(discretionary_sells),
        "buy_value_usd": buy_value,
        "discretionary_sell_value_usd": sell_value,
        "distinct_buyers": distinct_buyers,
        "cluster_buy": distinct_buyers >= 3,
        "trades": [dict(asdict(t), value=t.value, label=CODE_LABELS.get(t.code, t.code))
                   for t in sorted(recent, key=lambda t: t.date, reverse=True)[:50]],
    }


def consensus(reports: list[dict], limit: int = 25) -> dict:
    """Where tracked investors are collectively buying and selling this quarter."""
    agg: dict[str, dict] = {}
    for rep in reports:
        key = rep["investor"]["key"]
        weight = 1.0 if key in HIGH_CONVICTION else 0.35
        for action in ("new", "added", "reduced", "exited"):
            for move in rep["moves"][action]:
                name = move["ticker"] or move["issuer"]
                row = agg.setdefault(name, {"ticker": move["ticker"], "issuer": move["issuer"],
                                            "buyers": [], "sellers": [], "score": 0.0})
                side = "buyers" if action in ("new", "added") else "sellers"
                row[side].append({"investor": rep["investor"]["person"], "action": action,
                                  "weight_pct": move["weight_now"]})
                row["score"] += weight if side == "buyers" else -weight
    rows = sorted(agg.values(), key=lambda r: r["score"], reverse=True)
    return {
        "most_bought": [r for r in rows if r["score"] > 0][:limit],
        "most_sold": [r for r in reversed(rows) if r["score"] < 0][:limit],
        "periods": sorted({r["period"] for r in reports}),
    }
