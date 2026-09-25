"""Import trades from a broker's export: Robinhood and Coinbase CSVs, and a quick holdings list
for accounts with no export (Stash).

Robinhood: Account → Reports and statements → Reports → generate an "account activity" CSV.
Its columns are Activity Date, Process Date, Settle Date, Instrument, Description, Trans Code,
Quantity, Price, Amount. Buys and sells become ledger transactions; stock splits become a
zero-cost buy of the extra shares (the cost basis is unchanged, the average cost falls).
Dividends (CDIV, MDIV), foreign tax withheld on them (DTAX) and interest (INT, SLIP) become
income rows for the Income tab; a reinvested dividend is also its own Buy row in the file.
Everything else (deposits, options, transfers) is counted and skipped: this ledger tracks share
positions, and a transferred-in position has no cost basis in the file.

Each imported row carries a key derived from its contents, so importing the same file twice,
or overlapping date ranges, adds nothing twice.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from .providers import market

ROBINHOOD_COLUMNS = {"Activity Date", "Instrument", "Trans Code", "Quantity", "Price", "Amount"}
ROBINHOOD_INCOME = {"CDIV": "dividend", "MDIV": "dividend", "QDIV": "dividend", "DTAX": "tax_withheld",
                    "INT": "interest", "SLIP": "interest"}


@dataclass
class ImportResult:
    transactions: list[dict] = field(default_factory=list)
    income: list[dict] = field(default_factory=list)
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
                                     "note": "Robinhood import", "import_key": key, "account": "Robinhood"})
        elif code == "SPL" and instrument and day and qty and qty > 0:
            res.transactions.append({"symbol": market.normalize_symbol(instrument), "side": "buy", "quantity": qty,
                                     "price": 0.0, "fees": 0.0, "date": day,
                                     "note": "Robinhood import: stock split shares", "import_key": key,
                                     "account": "Robinhood"})
        elif code in ROBINHOOD_INCOME:
            amount = _money(row.get("Amount", ""))
            if day is None or amount is None:
                res.errors.append(f"Line {n}: couldn't read the {code} amount.")
                continue
            res.income.append({"symbol": market.normalize_symbol(instrument) if instrument else "", "day": day,
                               "amount": amount, "kind": ROBINHOOD_INCOME[code], "account": "Robinhood",
                               "import_key": key, "note": row.get("Description", "").strip()[:120]})
        else:
            res.skipped[code] += 1
    return res


# ------------------------------------------------------------------ Coinbase

COINBASE_BUYS = {"buy", "advanced trade buy", "recurring buy"}
COINBASE_SELLS = {"sell", "advanced trade sell"}
# Crypto received as income: its value on the day is the cost basis (and taxable income).
COINBASE_INCOME = {"rewards income", "staking income", "learning reward", "coinbase earn", "inflation reward",
                   "incentives rewards payout", "reward income"}
_CONVERT = re.compile(r"Converted\s+([\d.,]+)\s+(\S+)\s+to\s+([\d.,]+)\s+(\S+)", re.I)


def _find_header(lines: list[str], required: set[str]) -> int | None:
    for i, line in enumerate(lines[:30]):
        cols = {c.strip().strip('"') for c in next(csv.reader([line]))} if line.strip() else set()
        if required <= cols:
            return i
    return None


def parse_coinbase(text: str) -> ImportResult:
    """Coinbase: Profile → Statements (or Taxes → Documents) → generate a "Transaction history" CSV.
    Older and newer layouts both work; the rows above the header are skipped."""
    res = ImportResult()
    lines = text.lstrip("\ufeff").splitlines()
    head = _find_header(lines, {"Timestamp", "Transaction Type", "Asset", "Quantity Transacted"})
    if head is None:
        res.errors.append("This doesn't look like a Coinbase transaction history CSV (expected columns: Timestamp, "
                          "Transaction Type, Asset, Quantity Transacted, Price at Transaction).")
        return res
    reader = csv.DictReader(io.StringIO("\n".join(lines[head:])))
    seen: Counter = Counter()
    for n, row in enumerate(reader, start=head + 2):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        kind = row.get("Transaction Type", "").lower()
        if not kind:
            continue
        asset = row.get("Asset", "").upper()
        day = (row.get("Timestamp", "")[:10])
        qty = _money(row.get("Quantity Transacted", ""))
        qty = abs(qty) if qty is not None else None
        price = _money(row.get("Price at Transaction") or row.get("Spot Price at Transaction") or "")
        currency = (row.get("Price Currency") or row.get("Spot Price Currency") or "USD").upper()
        fees = abs(_money(row.get("Fees and/or Spread", "")) or 0.0)
        raw = row.get("ID") or "|".join(f"{k}={v}" for k, v in sorted(row.items()))
        key = "cb:" + hashlib.sha1(raw.encode()).hexdigest()[:20]
        seen[key] += 1
        if seen[key] > 1:
            key = f"{key}.{seen[key]}"
        if currency != "USD":
            res.errors.append(f"Line {n}: prices in {currency} aren't supported (only USD).")
            continue
        if not (asset and _date(day) and qty):
            if kind in COINBASE_BUYS | COINBASE_SELLS | COINBASE_INCOME or kind == "convert":
                res.errors.append(f"Line {n}: couldn't read the {kind} ({asset or 'no asset'}).")
            else:
                res.skipped[row.get("Transaction Type", kind)] += 1
            continue
        sym = market.normalize_symbol(asset + "-USD")
        base = {"date": _date(day), "note": "Coinbase import", "account": "Coinbase"}
        if kind in COINBASE_BUYS or kind in COINBASE_INCOME:
            if price is None:
                res.errors.append(f"Line {n}: no price for the {kind} of {asset}.")
                continue
            res.transactions.append(dict(base, symbol=sym, side="buy", quantity=qty, price=price,
                                         fees=fees if kind in COINBASE_BUYS else 0.0, import_key=key,
                                         note="Coinbase import" + ("" if kind in COINBASE_BUYS else f": {kind}")))
        elif kind in COINBASE_SELLS:
            res.transactions.append(dict(base, symbol=sym, side="sell", quantity=qty, price=price or 0.0, fees=fees,
                                         import_key=key))
        elif kind == "convert":
            m = _CONVERT.search(row.get("Notes", ""))
            if not m or price is None:
                res.errors.append(f"Line {n}: couldn't read the conversion ({row.get('Notes', '')[:60]}).")
                continue
            to_qty, to_asset = float(m.group(3).replace(",", "")), m.group(4).upper()
            subtotal = _money(row.get("Subtotal", "")) or qty * price
            res.transactions.append(dict(base, symbol=sym, side="sell", quantity=qty, price=price, fees=fees,
                                         import_key=key + ".out"))
            res.transactions.append(dict(base, symbol=market.normalize_symbol(to_asset + "-USD"), side="buy",
                                         quantity=to_qty, price=abs(subtotal) / to_qty if to_qty else 0.0, fees=0.0,
                                         import_key=key + ".in", note=f"Coinbase import: converted from {asset}"))
        else:
            res.skipped[row.get("Transaction Type", kind)] += 1
    return res


# ------------------------------------------------------------------ quick holdings list (Stash, anything else)

def parse_holdings_list(text: str, account: str, today: str) -> ImportResult:
    """One holding per line: SYMBOL SHARES TOTAL_COST [YYYY-MM-DD], separated by spaces, commas or tabs.
    For accounts with no trade export (Stash offers only PDF statements): the shares and the total
    cost basis are on the statement or in the app under each investment. Without a date the
    purchase is recorded today, which makes every gain look short-term until you correct it."""
    res = ImportResult()
    for n, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in re.split(r"[\s,;]+", line.replace("$", "")) if p]
        if len(parts) < 3 or parts[0].lower() == "symbol":
            if parts and parts[0].lower() != "symbol":
                res.errors.append(f"Line {n}: expected SYMBOL SHARES TOTAL_COST, got '{line[:40]}'.")
            continue
        try:
            qty, cost = float(parts[1]), float(parts[2])
        except ValueError:
            res.errors.append(f"Line {n}: shares and cost must be numbers ('{line[:40]}').")
            continue
        day = _date(parts[3]) if len(parts) > 3 else None
        if qty <= 0 or cost < 0:
            res.errors.append(f"Line {n}: shares must be above zero.")
            continue
        sym = market.normalize_symbol(parts[0])
        key = f"hl:{account.lower()}:" + hashlib.sha1(f"{sym}|{qty}|{cost}|{day}".encode()).hexdigest()[:16]
        res.transactions.append({"symbol": sym, "side": "buy", "quantity": qty, "price": cost / qty, "fees": 0.0,
                                 "date": day or today, "note": f"{account} holdings" + ("" if day else " (date unknown)"),
                                 "import_key": key, "account": account})
    return res
