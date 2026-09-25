"""Dilution check: is this company set up to sell new shares?

Small companies are where insider buying carries the most information, and also where a
share sale can erase it. Three kinds of SEC filing show that a sale is possible or under way:

- a shelf registration (S-3, S-3ASR, or F-3 for foreign issuers): the company can sell
  shares at any time for three years;
- a prospectus supplement (424B5, sometimes 424B3/424B4/424B7): shares are being sold off
  a shelf, often through an at-the-market program that sells into the market day by day;
- an S-1 (or F-1) registration after the company is already public, for small companies
  without shelf eligibility.

This reads the company's recent filing list from EDGAR (one request, cached) and reports what
it finds. It says "possible", never "will": plenty of companies keep a shelf they never use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from . import http
from .providers import sec

SHELF_FORMS = {"S-3", "S-3/A", "S-3ASR", "F-3", "F-3/A", "F-3ASR"}
SALE_FORMS = {"424B5", "424B3", "424B4", "424B7"}
REGISTRATION_FORMS = {"S-1", "S-1/A", "F-1", "F-1/A"}
WATCH_FORMS = SHELF_FORMS | SALE_FORMS | REGISTRATION_FORMS
SHELF_DAYS = 3 * 365     # a shelf stays usable for three years
SALE_DAYS = 90
REGISTRATION_DAYS = 365


@dataclass
class DilutionCheck:
    cik: str
    level: str = "none"            # none / shelf / active
    notes: list[str] = field(default_factory=list)
    latest: list[dict] = field(default_factory=list)   # the filings behind the notes, newest first
    error: str = ""

    @property
    def flagged(self) -> bool:
        return self.level != "none"

    def to_dict(self) -> dict:
        return {"level": self.level, "notes": self.notes, "latest": self.latest[:3]}


def _submissions(cik: str) -> dict:
    return sec._sec_get(sec.SUBMISSIONS.format(cik=cik.zfill(10)), ttl=86400)


def check(cik: str, today: date, submissions_fn=_submissions) -> DilutionCheck:
    """Shelf, sale and registration filings in the company's recent filing list."""
    out = DilutionCheck(cik=cik.zfill(10))
    try:
        data = submissions_fn(cik)
    except http.DataUnavailable as exc:
        out.error = str(exc)
        return out
    recent = data.get("filings", {}).get("recent", {})
    forms, dates, accs = recent.get("form", []), recent.get("filingDate", []), recent.get("accessionNumber", [])
    shelves, sales, regs = [], [], []
    for form, filed, acc in zip(forms, dates, accs):
        age = (today - date.fromisoformat(filed)).days if filed else 10_000
        row = {"form": form, "filed": filed,
               "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"}
        if form in SHELF_FORMS and age <= SHELF_DAYS:
            shelves.append(row)
        elif form in SALE_FORMS and age <= SALE_DAYS:
            sales.append(row)
        elif form in REGISTRATION_FORMS and age <= REGISTRATION_DAYS:
            regs.append(row)
    if sales:
        out.level = "active"
        out.notes.append(f"{len(sales)} prospectus supplement{'s' if len(sales) > 1 else ''} in the last "
                         f"{SALE_DAYS} days (latest {sales[0]['filed']}): shares are being sold by the "
                         "company (possibly an at-the-market program) or by existing holders")
    if regs:
        out.level = "active"
        out.notes.append(f"{regs[0]['form']} registration filed {regs[0]['filed']}: a share sale may be coming")
    if shelves:
        if out.level == "none":
            out.level = "shelf"
        out.notes.append(f"shelf registration ({shelves[0]['form']}) filed {shelves[0]['filed']}: "
                         "the company can sell shares or other securities at any time")
    out.latest = sorted(sales + regs + shelves, key=lambda r: r["filed"], reverse=True)
    return out


def issue_section(d: DilutionCheck) -> str:
    """Markdown for an alert issue."""
    if d.error:
        return "**Dilution check:** couldn't read the company's filing list this time.\n"
    if not d.flagged:
        return "**Dilution check:** no shelf registration, S-1 or prospectus supplement on file recently.\n"
    head = ("**Dilution check: shares are being sold or registered.** Insider buying next to an active "
            "offering can mean insiders are supporting a raise, not signalling value."
            if d.level == "active" else
            "**Dilution check: shelf on file.** The company can issue shares at any time; many never do.")
    items = "\n".join(f"- {n}" for n in d.notes)
    links = ", ".join(f"[{r['form']} {r['filed']}]({r['url']})" for r in d.latest[:3])
    return f"{head}\n{items}\n- Filings: {links}\n"


def short_label(d: DilutionCheck) -> str:
    return {"active": "Dilution: active", "shelf": "Dilution: shelf"}.get(d.level, "")
