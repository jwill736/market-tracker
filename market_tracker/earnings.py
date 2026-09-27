"""Earnings recap: what the company itself said on results day, and how the stock took it.

On results day a US company files an 8-K (item 2.02, "Results of Operations") with its press
release attached as exhibit 99.1. That release is free on EDGAR the moment it's filed, before
any article. From it:
- the headline sentences: revenue, earnings per share, margins, cash flow, backlog, quoted
  exactly (not paraphrased), in the order the company put them;
- the outlook: sentences about guidance, with whether it was raised, lowered or kept;
- the stock's move on the first trading day after the release, against its usual daily move;
- once the quarterly report (10-Q) is out, the audited numbers from its XBRL: revenue and
  earnings per share against a year earlier.

Press releases are the company's own framing (adjusted, "non-GAAP" figures lead). The 10-Q
numbers are the check. Ask a filing can answer questions about the release itself.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import filings, http

ET = ZoneInfo("America/New_York")
EXHIBIT = re.compile(r"(?:ex|exhibit)[-_ ]?99|(?<!\d)99[-_.]?0?1(?!\d)", re.I)
MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:billion|million|thousand|B|M)?|\d+(?:\.\d+)?\s?%|\d+(?:\.\d+)?\s*percent", re.I)
HEADLINE = re.compile(r"revenue|net sales|total sales|earnings per share|per diluted share|diluted eps|\beps\b|net income|operating income|"
                      r"operating margin|gross margin|free cash flow|operating cash flow|bookings|backlog|remaining performance obligation|"
                      r"subscribers|comparable sales|same-store", re.I)
OUTLOOK = re.compile(r"outlook|guidance|expects?\b|expected to|anticipates?|forecast|full[- ]year|fiscal (?:year )?20\d\d|next quarter|"
                     r"(?:first|second|third|fourth) quarter of (?:fiscal )?20\d\d", re.I)
RAISED = re.compile(r"\b(rais(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|lift(?:s|ed)?|boost(?:s|ed)?)\b[^.]{0,60}\b(outlook|guidance|forecast|expectations)", re.I)
LOWERED = re.compile(r"\b(lower(?:s|ed|ing)?|reduc(?:e|es|ed|ing)|cut(?:s|ting)?|withdr[ae]w(?:s|n)?)\b[^.]{0,60}\b(outlook|guidance|forecast|expectations)", re.I)
KEPT = re.compile(r"\b(reaffirm(?:s|ed|ing)?|maintain(?:s|ed|ing)?|reiterat(?:e|es|ed|ing)|confirm(?:s|ed|ing)?)\b[^.]{0,60}\b(outlook|guidance|forecast|expectations)", re.I)
BOILERPLATE = re.compile(r"forward-looking statements?|safe harbor|private securities litigation|risks and uncertainties|non-gaap financial measures? (?:is|are) "
                         r"(?:included|provided|presented)|reconciliation|conference call|webcast|about [A-Z][\w.&,' ]+\b(?:inc|corp|company)\b", re.I)


def latest_release(symbol: str, get=None, before: str | None = None) -> dict | None:
    """The latest results 8-K (item 2.02) and its press-release exhibit: {filed, accepted, url, company}."""
    from .providers import sec
    cik = filings.cik_of(symbol)
    if not cik:
        return None
    get = get or sec._sec_get
    data = get(sec.SUBMISSIONS.format(cik=str(cik).zfill(10)))
    r = data["filings"]["recent"]
    for i, form in enumerate(r["form"]):
        items = (r.get("items") or [""] * len(r["form"]))[i] or ""
        if form not in ("8-K", "8-K/A") or "2.02" not in items:
            continue
        if before and r["filingDate"][i] >= before:
            continue
        acc = r["accessionNumber"][i]
        base = sec.ARCHIVE.format(cik=int(cik), acc=acc.replace("-", ""))
        index = get(base + "/index.json")
        names = [it["name"] for it in index["directory"]["item"] if it["name"].lower().endswith((".htm", ".html", ".txt"))
                 and not it["name"].lower().endswith("-index.htm") and it["name"] != r["primaryDocument"][i]]
        ex = [n for n in names if EXHIBIT.search(n)]
        if not ex:
            continue
        ex = [n for n in ex if re.search(r"99[-_.]?0?1(?!\d)", n)] or ex      # 99.1 is the release; 99.2 is usually slides or a CFO letter
        return {"form": "8-K", "filed": r["filingDate"][i], "accepted": (r.get("acceptanceDateTime") or [""] * len(r["form"]))[i],
                "period": r["reportDate"][i], "accession": acc, "url": base + "/" + sorted(ex, key=len)[0],
                "company": data.get("name", ""), "cik": str(cik)}
    return None


def _sentences(text: str) -> list[str]:
    """Sentences within each paragraph (a release's headline has no full stop to split on)."""
    return [s for para in text.split("\n") for s in filings.sentences(para) if len(s) <= 700 and not BOILERPLATE.search(s)]


def highlights(text: str, n: int = 6) -> list[str]:
    """The first sentences that state a result with a number, in the release's own order."""
    out = []
    for s in _sentences(text):
        if HEADLINE.search(s) and MONEY.search(s):
            out.append(s)
        if len(out) >= n:
            break
    return out


def outlook(text: str, n: int = 5) -> dict:
    lines = [s for s in _sentences(text) if OUTLOOK.search(s) and (MONEY.search(s) or RAISED.search(s) or LOWERED.search(s) or KEPT.search(s))][:n]
    blob = " ".join(lines)
    direction = ("raised" if RAISED.search(blob) else "lowered" if LOWERED.search(blob) else "kept" if KEPT.search(blob)
                 else "given" if lines else "none")
    return {"direction": direction, "lines": lines}


def reaction(bars: list[tuple[str, float]], filed: str, accepted: str = "") -> dict | None:
    """The first trading day's move after the release. Filed after the 4pm close (most are):
    the next day's close against the filing day's; before the open: that day's against the day before."""
    after_close = True
    if accepted:
        try:
            t = datetime.fromisoformat(accepted.replace("Z", "+00:00")).astimezone(ET)
            after_close = t.hour >= 16 or t.date().isoformat() != filed
        except ValueError:
            pass
    days = [d for d, _ in bars]
    closes = dict(bars)
    try:
        i = next(k for k, d in enumerate(days) if (d > filed if after_close else d >= filed))
    except StopIteration:
        return None
    base_i = i - 1
    if base_i < 0:
        return None
    move = closes[days[i]] / closes[days[base_i]] - 1
    from .moves import typical_move
    sd = typical_move([c for _, c in bars[max(0, base_i - 61): base_i + 1]])
    return {"day": days[i], "move_pct": round(move * 100, 2), "typical": round(sd, 2) if sd else None,
            "times_usual": round(abs(move * 100) / sd, 1) if sd else None}


def audited(symbol: str, release_filed: str, get=None) -> dict | None:
    """The quarter's numbers from the 10-Q/10-K XBRL, once it covers the released quarter."""
    from . import fundamentals
    qs = fundamentals.for_symbol(symbol, get)
    if not qs:
        return None
    q = qs[0]
    lag = (date.fromisoformat(release_filed) - date.fromisoformat(q.end)).days
    if not 0 <= lag <= 100:
        return None
    return {"quarter_end": q.end, "revenue": q.revenue, "revenue_yoy": q.revenue_yoy, "eps": q.eps, "eps_yoy": q.eps_yoy,
            "operating_margin": q.operating_margin, "operating_margin_yoy": q.operating_margin_yoy}


def recap(symbol: str, get=None, text_fn=None, history_fn=None) -> dict | None:
    rel = latest_release(symbol, get)
    if not rel:
        return None
    text_fn = text_fn or (lambda u: filings.document_text(u, get))
    text = text_fn(rel["url"])
    out = {"symbol": symbol, "release": {k: rel[k] for k in ("filed", "accepted", "url", "company", "period")},
           "highlights": [s[:500] for s in highlights(text)], "outlook": outlook(text)}
    out["outlook"]["lines"] = [s[:500] for s in out["outlook"]["lines"]]
    try:
        if history_fn:
            bars = history_fn(symbol)
        else:
            from .providers import market
            bars = [(b.date, b.close) for b in market.get_history(symbol, 200)]
        out["reaction"] = reaction(bars, rel["filed"], rel.get("accepted") or "")
    except (http.DataUnavailable, KeyError, ValueError):
        out["reaction"] = None
    try:
        out["audited"] = audited(symbol, rel["filed"], get)
    except (http.DataUnavailable, KeyError, TypeError, ValueError):
        out["audited"] = None
    out["age_days"] = (date.today() - date.fromisoformat(rel["filed"])).days
    return out
