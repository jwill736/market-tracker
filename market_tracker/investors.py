"""Curated list of high-profile investors whose 13F-HR filings we track.

CIKs are SEC Central Index Keys. `expected_name` is compared against the filer name that
EDGAR returns so a wrong CIK is flagged instead of silently tracking the wrong fund.

Caveat baked into the product: 13F filings are due 45 days after quarter end, cover only
US-listed long equity/options positions, and omit shorts, cash, bonds, and non-US assets.
You are seeing where these investors *were*, not where they are.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Investor:
    key: str
    person: str
    fund: str
    cik: str
    expected_name: str
    style: str


INVESTORS: list[Investor] = [
    Investor("buffett", "Warren Buffett", "Berkshire Hathaway", "0001067983", "BERKSHIRE HATHAWAY",
             "Concentrated value; durable moats, long holding periods"),
    Investor("ackman", "Bill Ackman", "Pershing Square", "0001336528", "PERSHING SQUARE",
             "Concentrated activist; 8-12 large-cap positions"),
    Investor("burry", "Michael Burry", "Scion Asset Management", "0001649339", "SCION ASSET",
             "Contrarian deep value; heavy use of options, high turnover"),
    Investor("tepper", "David Tepper", "Appaloosa", "0001656456", "APPALOOSA",
             "Macro-aware opportunistic; distressed and cyclical bets"),
    Investor("druckenmiller", "Stanley Druckenmiller", "Duquesne Family Office", "0001536411", "DUQUESNE",
             "Top-down macro; fast rotation into secular winners"),
    Investor("dalio", "Ray Dalio", "Bridgewater Associates", "0001350694", "BRIDGEWATER",
             "Systematic macro / risk parity; very diversified book"),
    Investor("soros", "George Soros", "Soros Fund Management", "0001029160", "SOROS FUND",
             "Global macro; reflexivity-driven"),
    Investor("icahn", "Carl Icahn", "Icahn Enterprises / Carl C. Icahn", "0000921669", "ICAHN",
             "Activist; very concentrated control stakes"),
    Investor("loeb", "Dan Loeb", "Third Point", "0001040273", "THIRD POINT",
             "Event-driven activist"),
    Investor("klarman", "Seth Klarman", "Baupost Group", "0001061768", "BAUPOST",
             "Margin-of-safety value; patient, often holds cash"),
    Investor("li_lu", "Li Lu", "Himalaya Capital", "0001709323", "HIMALAYA",
             "Buffett/Munger-style concentrated value"),
    Investor("einhorn", "David Einhorn", "Greenlight Capital", "0001079114", "GREENLIGHT",
             "Long/short value"),
    Investor("coleman", "Chase Coleman", "Tiger Global Management", "0001167483", "TIGER GLOBAL",
             "Growth / tech-heavy"),
    Investor("laffont", "Philippe Laffont", "Coatue Management", "0001135730", "COATUE",
             "Tech growth"),
    Investor("griffin", "Ken Griffin", "Citadel Advisors", "0001423053", "CITADEL",
             "Multi-strategy; huge turnover (13F is mostly hedges/market-making noise)"),
    Investor("simons", "Jim Simons (founder)", "Renaissance Technologies", "0001037389", "RENAISSANCE",
             "Quant; thousands of short-horizon positions (13F signal is weak)"),
    Investor("wood", "Cathie Wood", "ARK Investment Management", "0001697748", "ARK INVEST",
             "Disruptive-innovation growth; ARK also publishes daily trades"),
]

# Investors whose 13F moves are most informative for copy-style signals: concentrated,
# low-turnover books. Quant / multi-strat books are tracked for reference but down-weighted.
HIGH_CONVICTION = {"buffett", "ackman", "tepper", "druckenmiller", "icahn", "loeb", "klarman",
                   "li_lu", "einhorn", "burry"}


def by_key(key: str) -> Investor | None:
    return next((i for i in INVESTORS if i.key == key), None)
