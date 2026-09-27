"""Temporary probe (removed before merge): how Microsoft's 10-K marks up its Item 1A heading."""
import re
import sys

sys.path.insert(0, ".")
from market_tracker import filings  # noqa: E402
from market_tracker.providers import sec  # noqa: E402

docs = filings.filings_for("MSFT", {"10-K"}, 2)
for d in docs[:1]:
    raw = sec._sec_get(d["url"], ttl=0, as_json=False)
    print("URL", d["url"], "len", len(raw))
    for m in list(re.finditer(r"1A", raw))[:8]:
        print("\nRAW:", repr(raw[max(0, m.start() - 250): m.end() + 350]))
    text = filings.html_to_text(raw)
    for m in list(re.finditer(r"(?i)item\s*1a", text))[:6]:
        print("\nTEXT:", repr(text[max(0, m.start() - 40): m.end() + 120]))
    for m in list(re.finditer(r"(?i)risk\s*factors", text))[:8]:
        print("\nRF:", repr(text[max(0, m.start() - 80): m.end() + 60]))
