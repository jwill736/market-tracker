"""Ask a filing: questions about a company answered from its own 10-K or 10-Q, with the
sentences quoted.

The filing goes to Claude as a document with citations turned on, so every claim in the answer
points at the exact passage it came from (shown under the answer; nothing is paraphrased
without a source). The filing is cached for a few minutes, so follow-up questions about the same
report cost about a tenth as much as the first. Uses your Anthropic API credits: a 10-K is
roughly 100,000-150,000 tokens, so a first question costs on the order of $0.50-0.75 at Claude
Opus 5's price, follow-ups a few cents.
"""

from __future__ import annotations

import anthropic

from . import filings
from .config import settings

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_CHARS = 2_500_000          # beyond this a filing won't fit in one request: say so rather than cut it
FORMS = {"10-K": {"10-K", "10-K405", "20-F"}, "10-Q": {"10-Q"}}

SYSTEM = """You answer an individual investor's questions about a company using only the SEC filings \
provided as documents. Rules:
- Every factual statement must be supported by a citation to the filings. If the filings don't \
answer the question, say that plainly and say what they do cover instead.
- Prefer the company's own numbers and wording. Give figures with their period (e.g. "fiscal 2026").
- Point out anything in the filings that cuts against a rosy reading: risks, one-time items, \
changed definitions, going-concern or material-weakness language.
- Be short: a direct answer first, then the supporting detail. No investment advice."""


def pick(symbol: str, which: list[str], get=None) -> list[dict]:
    """The latest filing of each requested kind (10-K, 10-Q)."""
    out = []
    for kind in which:
        docs = filings.filings_for(symbol, FORMS[kind], 1, get)
        if docs:
            out.append(docs[0])
    return out


def document_block(doc: dict, text: str) -> dict:
    return {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": text},
            "title": f"{doc['company']} {doc['form']} filed {doc['filed']} (period {doc['period']})",
            "citations": {"enabled": True}}


def build_request(docs: list[tuple[dict, str]], question: str) -> dict:
    blocks = [document_block(d, t) for d, t in docs]
    blocks[-1]["cache_control"] = {"type": "ephemeral"}      # the filings are the stable prefix; the question varies
    return {"model": settings.research_model, "max_tokens": 16000, "system": SYSTEM,
            "thinking": {"type": "adaptive"},
            "messages": [{"role": "user", "content": blocks + [{"type": "text", "text": question}]}],
            "betas": [FALLBACK_BETA], "fallbacks": "default"}


def parse(message) -> dict:
    """The answer as segments of text, each with the passages it cites."""
    if message.stop_reason == "refusal":
        return {"refused": True, "segments": [], "sources": []}
    segments, sources, seen = [], [], {}
    for block in message.content:
        if block.type != "text":
            continue
        cites = []
        for c in getattr(block, "citations", None) or []:
            key = (c.document_index, getattr(c, "start_char_index", None))
            if key not in seen:
                seen[key] = len(sources) + 1
                sources.append({"n": seen[key], "document": c.document_title, "quote": c.cited_text.strip()[:600]})
            cites.append(seen[key])
        segments.append({"text": block.text, "cites": cites})
    u = message.usage
    return {"refused": False, "segments": segments, "sources": sources, "model": message.model,
            "usage": {"input": u.input_tokens, "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                      "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0, "output": u.output_tokens}}


def ask(symbol: str, question: str, which: list[str], client: anthropic.Anthropic | None = None, get=None, text_fn=None) -> dict:
    docs = pick(symbol, which, get)
    if not docs:
        return {"error": f"No {' or '.join(which)} on file for {symbol} (funds and coins don't file these)."}
    text_fn = text_fn or (lambda u: filings.document_text(u, get))
    loaded = [(d, text_fn(d["url"])) for d in docs]
    too_big = [(d, len(t)) for d, t in loaded if len(t) > MAX_CHARS]
    if too_big:
        d, n = too_big[0]
        return {"error": f"The {d['form']} is {n:,} characters, too long to read in one request. Ask about the other filing instead."}
    req = build_request(loaded, question)
    client = client or anthropic.Anthropic()
    with client.beta.messages.stream(**req) as stream:      # long input: stream so the request can't time out
        final = stream.get_final_message()
    out = parse(final)
    out["filings"] = [{k: d[k] for k in ("form", "filed", "period", "url")} for d, _ in loaded]
    return out
