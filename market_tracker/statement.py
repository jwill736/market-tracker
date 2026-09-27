"""Statement check: upload a monthly statement PDF (Stash, Robinhood, any broker) and the app
compares the share counts in it with what the ledger says for that account.

Statements differ in layout, so this looks for each ticker the account holds (and any other
ticker-looking word) followed by a share quantity on the same line, and reports three lists:
matches, differences, and holdings the statement shows that the ledger doesn't have. Anything
it can't read is left out rather than guessed; the differences are what to fix (Stash / other
lets you set the right numbers).
"""

from __future__ import annotations

import io
import re

TICKER = re.compile(r"\b([A-Z]{1,5}(?:\.[A-Z])?)\b")
QTY = re.compile(r"(?<![\d$.,])(\d{1,7}(?:,\d{3})*\.\d{1,6}|\d{1,7}\.\d{1,6}|\d{1,7})(?![\d%.])")
NOT_TICKERS = {"USD", "ETF", "INC", "LLC", "CORP", "CO", "THE", "AND", "FOR", "OF", "TOTAL", "CASH", "NA", "N", "A", "I",
               "PAGE", "YTD", "APR", "APY", "FDIC", "SIPC", "ID", "PO", "ACH", "DRIP", "LTD", "PLC", "TR", "FD", "US"}


def text_of_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)


def quantities(text: str, known: set[str]) -> dict[str, float]:
    """{ticker: shares} read from statement text."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        for m in TICKER.finditer(line):
            sym = m.group(1)
            if sym in NOT_TICKERS or (len(sym) == 1 and sym not in known):
                continue
            rest = line[m.end():]
            q = QTY.search(rest)
            if not q:
                continue
            val = float(q.group(1).replace(",", ""))
            # Share counts on statements come before prices and values; take the first number.
            if sym in known or (0 < val < 1e7 and "." in q.group(1)):
                out.setdefault(sym, val)
            break
    return out


def compare(statement: dict[str, float], ledger: dict[str, float], tolerance: float = 0.001) -> dict:
    match, diff, extra, missing = [], [], [], []
    for sym, q in sorted(statement.items()):
        if sym in ledger:
            (match if abs(q - ledger[sym]) <= max(tolerance, tolerance * q) else diff).append(
                {"symbol": sym, "statement": q, "ledger": round(ledger[sym], 6), "difference": round(q - ledger[sym], 6)})
        else:
            extra.append({"symbol": sym, "statement": q})
    for sym, q in sorted(ledger.items()):
        if sym not in statement and q > 1e-9:
            missing.append({"symbol": sym, "ledger": round(q, 6)})
    return {"match": match, "differences": diff, "not_in_ledger": extra, "not_on_statement": missing}
