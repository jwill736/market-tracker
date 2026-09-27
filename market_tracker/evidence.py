"""Which idea lists have earned a dollar amount, and which are research only.

Every backtested list in this app (the screen, opportunistic insider buying, spin-offs, the
screen's bottom 50) failed to beat SPY by more than luck, or, for spin-offs, only did so if the
half of the sample with no prices hadn't done badly. So a list starts as research: its ideas are
shown, logged and scored, but the app doesn't size them and won't put them in Decisions.

A list earns sizing when its own forward record beats VOO:
- its paper portfolio (paper.py) has 12+ finished months and is ahead of VOO, or
- its logged ideas (ideas.py) have 20+ results at 6 months, beat VOO on average and more than
  55% of the time.
Your own ideas are yours to size; the limits in sizing.py still apply.
"""

from __future__ import annotations

PAPER_OF = {"qvm": "screen_2b", "sleeper": "sleepers", "spinoff": "spinoffs"}
PAPER_MONTHS = 12
BOARD_BEAT = 55
ALWAYS = {"manual"}


def status(paper_lists: list[dict] | None, board: list[dict] | None, sources: dict[str, str]) -> dict[str, dict]:
    books = {p["list"]: p for p in paper_lists or []}
    rows = {b["source"]: b for b in board or []}
    out = {}
    for src, label in sources.items():
        if src in ALWAYS:
            out[src] = {"label": label, "earned": True, "why": "Your own idea: sized inside your limits."}
            continue
        book = books.get(PAPER_OF.get(src, ""))
        months = len(book["months"]) if book else 0
        if book and months >= PAPER_MONTHS and book["ahead"] > 0:
            out[src] = {"label": label, "earned": True,
                        "why": f"Its paper portfolio is ${book['ahead']:,.0f} ahead of VOO on $10,000 after {months} months."}
            continue
        six = (rows.get(src) or {}).get("6m") or {}
        if six.get("enough") and (six.get("avg_edge") or 0) > 0 and (six.get("beat_voo") or 0) > BOARD_BEAT:
            out[src] = {"label": label, "earned": True,
                        "why": f"Its logged ideas beat VOO by {six['avg_edge']:+.1f} points at 6 months ({six['beat_voo']}% of {six['resolved']})."}
            continue
        have = []
        if src in PAPER_OF:
            have.append(f"paper portfolio {months} of {PAPER_MONTHS} months" + (f", {'ahead of' if book['ahead'] > 0 else 'behind'} VOO" if months else ""))
        have.append(f"{six.get('resolved') or 0} of 20 idea results at 6 months")
        out[src] = {"label": label, "earned": False, "why": "Research, not advice: " + "; ".join(have) + "."}
    return out


def earned(st: dict[str, dict]) -> set[str]:
    return {k for k, v in st.items() if v["earned"]}
