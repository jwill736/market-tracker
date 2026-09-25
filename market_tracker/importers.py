"""Import trades from a broker's export.

Robinhood: Account → Reports and statements → Reports → generate an "account activity" CSV.
Its columns are Activity Date, Process Date, Settle Date, Instrument, Description, Trans Code,
Quantity, Price, Amount. Buys and sells become ledger transactions; stock splits become a
zero-cost buy of the extra shares (the cost basis is unchanged, the average cost falls).
Everything else (dividends, deposits, interest, options, transfers) is counted and skipped:
this ledger tracks share positions, and a transferred-in position has no cost basis in the file.

Each imported row carries a key derived from its contents, so importing the same file twice,
or overlapping date ranges, adds nothing twice.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from .providers import market

ROBINHOOD_COLUMNS = {"Activity Date", "Instrument", "Trans Code", "Quantity", "Price", "Amount"}


@dataclass
class ImportResult:
    transactions: list[dict] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)


def _money(text: str) -> float | None:
    t = (text or "").strip().replace("$", "").replace(",", "")
    if not t:
        return None
    negative = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if negative else v


def _quantity(text: str) -> float | None:
    t = (text or "").strip().replace(",", "").rstrip("S")   # split rows can read "5S"
    try:
        return float(t) if t else None
    except ValueError:
        return None


def _date(text: str) -> str | None:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def row_key(row: dict) -> str:
    raw = "|".join(f"{k}={(row.get(k) or '').strip()}" for k in sorted(ROBINHOOD_COLUMNS | {"Description"}))
    return "rh:" + hashlib.sha1(raw.encode()).hexdigest()[:20]


def parse_robinhood(text: str) -> ImportResult:
    res = ImportResult()
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or not ROBINHOOD_COLUMNS <= {f.strip() for f in reader.fieldnames}:
        res.errors.append("This doesn't look like a Robinhood account activity CSV (expected columns: "
                          + ", ".join(sorted(ROBINHOOD_COLUMNS)) + ").")
        return res
    seen: Counter = Counter()
    for n, row in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "") for k, v in row.items()}
        code = row.get("Trans Code", "").strip()
        if not code:
            continue                      # blank lines and the disclaimer at the end of the file
        instrument = row.get("Instrument", "").strip()
        day = _date(row.get("Activity Date", ""))
        qty = _quantity(row.get("Quantity", ""))
        price = _money(row.get("Price", ""))
        key = row_key(row)
        seen[key] += 1
        if seen[key] > 1:                 # two identical fills on one day: keep both, distinct keys
            key = f"{key}.{seen[key]}"
        if code in ("Buy", "Sell"):
            if not (instrument and day and qty and price is not None):
                res.errors.append(f"Line {n}: couldn't read the {code.lower()} ({instrument or 'no symbol'}).")
                continue
            amount = _money(row.get("Amount", ""))
            gross = qty * price
            # Robinhood charges no commission; a small gap between amount and quantity x price
            # on a sell is regulatory fees.
            fees = round(gross - amount, 2) if (code == "Sell" and amount is not None and 0 < gross - amount < 0.01 * gross) else 0.0
            res.transactions.append({"symbol": market.normalize_symbol(instrument), "side": code.lower(),
                                     "quantity": qty, "price": price, "fees": fees, "date": day,
                                     "note": "Robinhood import", "import_key": key})
        elif code == "SPL" and instrument and day and qty and qty > 0:
            res.transactions.append({"symbol": market.normalize_symbol(instrument), "side": "buy", "quantity": qty,
                                     "price": 0.0, "fees": 0.0, "date": day,
                                     "note": "Robinhood import: stock split shares", "import_key": key})
        else:
            res.skipped[code] += 1
    return res
