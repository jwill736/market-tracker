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
    # Inline tags join what they wrap: small-caps headings are often "R<span>ISK</span> F<span>ACTORS</span>".
    html = re.sub(r"(?i)</?(span|font|a|b|i|u|em|strong|small|sup|sub|ix:[a-z]+)\b[^>]*>", "", html)
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


STOP = frozenset("""the and for that with are this from not our may could its which have has any other such can been
will would these their they them than more also into including were was all but has had there those under each only
some such within about over upon both through between while where when what who whom whose""".split())


def _words(text: str) -> Counter:
    """Content words only: with "the" and "and" in, every pair of long reports scores about 1.0."""
    return Counter(w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOP)


def cosine(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    dot = sum(wa[w] * wb[w] for w in wa.keys() & wb.keys())
    na, nb = math.sqrt(sum(v * v for v in wa.values())), math.sqrt(sum(v * v for v in wb.values()))
    return dot / (na * nb) if na and nb else 0.0


_SPLIT = re.compile(r"(?<=[.;])(?<![A-Z]\.[A-Z]\.)(?<!\b[A-Z]\.)(?<!\bInc\.)(?<!\bCo\.)(?<!\bNo\.)(?<!\bvs\.)(?<!\bSt\.)\s+(?=[A-Z(“\"])")


def sentences(text: str) -> list[str]:
    """Sentences, not split after abbreviations like U.S., D.C., Inc. or No."""
    parts = _SPLIT.split(re.sub(r"\s+", " ", text))
    return [p.strip() for p in parts if len(p.strip()) >= 40]


def _norm(s: str) -> str:
    s = re.sub(r"\d[\d,.]*", "#", s.lower())            # new year, new numbers: not new text
    return re.sub(r"[^a-z#]+", " ", s).strip()


SAME = 0.75     # a sentence whose words are 75%+ inside one of last year's is an edit or a split, not new text
MERGED = 0.85   # ...or 85%+ inside two of last year's sentences together: two old sentences joined into one


def _stem(w: str) -> str:
    """Crude: "broader" and "broad", "bases" and "base" count as the same word."""
    for suf in ("ing", "ed", "er", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def _tokens(s: str) -> frozenset:
    return frozenset(_stem(w) for w in _norm(s).split() if len(w) > 2 and w not in STOP)


def _closest(tok: frozenset, pool: list[frozenset], index: dict[str, list[int]]) -> float:
    """How much of this sentence's words are found in last year's text, as one sentence or two.
    Containment rather than overlap: companies often split one long sentence into several, and
    each piece is then almost entirely inside the old sentence. Two sentences because they also
    join old ones together. The two-sentence figure is scaled so it has to clear MERGED, not SAME."""
    if not tok:
        return 1.0
    seen: dict[int, int] = {}
    for w in tok:
        for i in index.get(w, ()):
            seen[i] = seen.get(i, 0) + 1
    if not seen:
        return 0.0
    first = max(seen, key=seen.get)
    one = seen[first] / len(tok)
    rest = tok - pool[first]
    more: dict[int, int] = {}
    for w in rest:
        for i in index.get(w, ()):
            if i != first:
                more[i] = more.get(i, 0) + 1
    two = (seen[first] + max(more.values(), default=0)) / len(tok)
    return max(one, two * SAME / MERGED if one >= 0.4 else 0.0)


def _unmatched(a: list[str], b: list[str]) -> list[str]:
    """Sentences of a with no exact or close counterpart in b."""
    exact = {_norm(s) for s in b}
    pool = [_tokens(s) for s in b]
    index: dict[str, list[int]] = {}
    for i, t in enumerate(pool):
        for w in t:
            index.setdefault(w, []).append(i)
    return [s for s in a if _norm(s) not in exact and _closest(_tokens(s), pool, index) < SAME]


def compare(prev: str, cur: str) -> dict:
    if not prev or not cur:
        return {"similarity": None, "new_share": None, "new": [], "removed": [], "words": len(cur.split()), "words_before": len(prev.split())}
    old_s, now_s = sentences(prev), sentences(cur)
    new = _unmatched(now_s, old_s)
    removed = _unmatched(old_s, now_s)
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


def cik_of(symbol: str) -> str | None:
    """SEC's ticker list writes share classes with a dash (BRK-B); other sources use a dot."""
    from .providers import sec
    m = sec.ticker_map()
    for t in dict.fromkeys((symbol, symbol.replace(".", "-"), symbol.replace("-", "."))):
        cik = m.cik_for(t)
        if cik:
            return cik
    return None


def filings_for(symbol: str, forms: set[str], n: int = 2, get=None, cik: str | None = None) -> list[dict]:
    """The latest n filings of these forms: {form, filed, period, url, accession}."""
    from .providers import sec
    cik = cik or cik_of(symbol)
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
    raw = (get or sec._sec_get)(url, ttl=0, as_json=False)      # the text is kept on disk; don't hold the HTML in memory
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
