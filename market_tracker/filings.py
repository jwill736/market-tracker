"""Companies' own annual and quarterly reports: fetched from SEC EDGAR, cut into their sections,
compared year over year, and handed to "Ask a filing".

Year over year ("Lazy Prices"): Cohen, Malloy & Nguyen (Journal of Finance, 2020) found that
when a company's 10-K changes a lot from the year before (Risk Factors and Legal Proceedings
especially), its stock tends to do worse over the following year, and the market is slow to
notice, because almost nobody rereads a 100-page document that is mostly the same as last year's.
This reads both for you: how similar the sections are (cosine similarity of their words), how
much of this year's text is new, and the new sentences themselves.

The thresholds below are rules of thumb, not the paper's: the paper ranks companies against each
other, and one person's handful of holdings is too few to rank.
"""

from __future__ import annotations

import gzip
import html as htmllib
import math
import os
import re
from collections import Counter

SECTIONS = {
    "risk": (r"item\s*1a\.?\s*[-–—:.]?\s*risk\s+factors", r"item\s*1b\.?|item\s*1c\.?|item\s*2\.?\s*[-–—:.]?\s*properties"),
    "legal": (r"item\s*3\.?\s*[-–—:.]?\s*legal\s+proceedings", r"item\s*4\.?\s*[-–—:.]?\s*(mine|submission|\(?removed|reserved)"),
    "mdna": (r"item\s*7\.?\s*[-–—:.]?\s*management['’]?s\s+discussion", r"item\s*7a\.?|item\s*8\.?\s*[-–—:.]?\s*financial\s+statements"),
    "risk_q": (r"item\s*1a\.?\s*[-–—:.]?\s*risk\s+factors", r"item\s*2\.?\s*[-–—:.]?\s*unregistered|item\s*3\.?\s*[-–—:.]?\s*defaults|item\s*5\.?\s*[-–—:.]?\s*other"),
}
LABEL = {"risk": "Risk Factors", "legal": "Legal Proceedings", "mdna": "Management's Discussion", "risk_q": "Risk Factors"}
BIG_NEW = 0.20            # 20%+ of this year's section is new text: worth reading
WATCH_WORDS = re.compile(r"investigat|subpoena|material weakness|going concern|restat|default|covenant|impair|litigation|lawsuit|"
                         r"tariff|sanction|cyber|breach|customer concentration|loss of|terminat|downgrade|recall|shortage", re.I)


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|ix:header)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table)>", "\n", html)
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", html))
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def section(text: str, key: str) -> str:
    """The longest span from a section heading to the next section's heading (skips the table
    of contents, where the same headings sit a line apart)."""
    start_re, end_re = SECTIONS[key]
    best = ""
    for m in re.finditer(start_re, text, re.I):
        end = re.compile(end_re, re.I).search(text, m.end())
        chunk = text[m.end(): end.start() if end else min(len(text), m.end() + 400_000)]
        if len(chunk) > len(best):
            best = chunk
    return best.strip()


def _words(text: str) -> Counter:
    return Counter(w for w in re.findall(r"[a-z]{3,}", text.lower()))


def cosine(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    dot = sum(wa[w] * wb[w] for w in wa.keys() & wb.keys())
    na, nb = math.sqrt(sum(v * v for v in wa.values())), math.sqrt(sum(v * v for v in wb.values()))
    return dot / (na * nb) if na and nb else 0.0


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;])\s+(?=[A-Z(])", re.sub(r"\s+", " ", text))
    return [p.strip() for p in parts if len(p.strip()) >= 40]


def _norm(s: str) -> str:
    s = re.sub(r"\d[\d,.]*", "#", s.lower())            # new year, new numbers: not new text
    return re.sub(r"[^a-z#]+", " ", s).strip()


def compare(prev: str, cur: str) -> dict:
    if not prev or not cur:
        return {"similarity": None, "new_share": None, "new": [], "removed": [], "words": len(cur.split()), "words_before": len(prev.split())}
    old = {_norm(s) for s in sentences(prev)}
    now_s = sentences(cur)
    new = [s for s in now_s if _norm(s) not in old]
    cur_n = {_norm(s) for s in now_s}
    removed = [s for s in sentences(prev) if _norm(s) not in cur_n]
    new_chars = sum(len(s) for s in new)
    total = sum(len(s) for s in now_s) or 1
    flagged = sorted(new, key=lambda s: (not WATCH_WORDS.search(s), -len(s)))
    return {"similarity": round(cosine(prev, cur), 3), "new_share": round(new_chars / total, 3),
            "new": [s[:400] for s in flagged[:6]], "new_count": len(new), "removed": [s[:300] for s in removed[:3]],
            "removed_count": len(removed), "words": len(cur.split()), "words_before": len(prev.split())}


# ------------------------------------------------------------------ EDGAR

def _cache_dir() -> str:
    from . import config
    d = os.path.join(os.path.dirname(os.path.abspath(config.settings.db_path)) or ".", "logo_cache", "filings")
    os.makedirs(d, exist_ok=True)
    return d


def filings_for(symbol: str, forms: set[str], n: int = 2, get=None) -> list[dict]:
    """The latest n filings of these forms: {form, filed, period, url, accession}."""
    from .providers import sec
    cik = sec.ticker_map().cik_for(symbol.replace("-", "."))
    if not cik:
        return []
    data = (get or sec._sec_get)(sec.SUBMISSIONS.format(cik=cik))
    r = data["filings"]["recent"]
    out = []
    for i, form in enumerate(r["form"]):
        if form in forms and r["primaryDocument"][i]:
            acc = r["accessionNumber"][i]
            out.append({"form": form, "filed": r["filingDate"][i], "period": r["reportDate"][i], "accession": acc,
                        "url": sec.ARCHIVE.format(cik=int(cik), acc=acc.replace("-", "")) + "/" + r["primaryDocument"][i],
                        "company": data.get("name", "")})
            if len(out) >= n:
                break
    return out


def document_text(url: str, get=None) -> str:
    """A filing as plain text, kept on disk (filings never change once filed)."""
    from .providers import sec
    path = os.path.join(_cache_dir(), re.sub(r"[^A-Za-z0-9]+", "_", url.split("/data/")[-1])[:180] + ".txt.gz")
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        pass
    raw = (get or sec._sec_get)(url, ttl=86400, as_json=False)
    text = html_to_text(raw)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text)
    return text


def tenk_changes(symbol: str, get=None, text_fn=None) -> dict | None:
    """This year's 10-K against last year's, section by section. None for funds and coins."""
    docs = filings_for(symbol, {"10-K", "10-K405", "20-F"}, 2, get)
    if len(docs) < 2:
        return {"symbol": symbol, "error": "Fewer than two annual reports on file"} if docs else None
    cur_doc, prev_doc = docs
    text_fn = text_fn or (lambda u: document_text(u, get))
    cur, prev = text_fn(cur_doc["url"]), text_fn(prev_doc["url"])
    parts = {}
    for key in ("risk", "legal"):
        parts[key] = dict(compare(section(prev, key), section(cur, key)), label=LABEL[key])
    risk = parts["risk"]
    level = ("big" if (risk["new_share"] or 0) >= BIG_NEW or (risk["similarity"] is not None and risk["similarity"] < 0.9)
             else "some" if (risk["new_share"] or 0) >= 0.08 else "little")
    return {"symbol": symbol, "company": cur_doc["company"], "current": {k: cur_doc[k] for k in ("filed", "period", "url", "form")},
            "previous": {k: prev_doc[k] for k in ("filed", "period", "url", "form")}, "sections": parts, "level": level}
