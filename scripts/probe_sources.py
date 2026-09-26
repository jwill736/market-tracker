"""Temporary probe (removed before merge): what the 10-K comparison counts as new in Apple's Risk Factors."""
import random
import sys

sys.path.insert(0, ".")
from market_tracker import filings  # noqa: E402

docs = filings.filings_for("AAPL", {"10-K"}, 2)
cur, prev = (filings.section(filings.document_text(d["url"]), "risk") for d in docs)
old_s, now_s = filings.sentences(prev), filings.sentences(cur)
print("sentences", len(old_s), len(now_s))
new = filings._unmatched(now_s, old_s)
print("new", len(new), "share", round(sum(map(len, new)) / sum(map(len, now_s)), 3))
pool = [filings._tokens(s) for s in old_s]
index = {}
for i, t in enumerate(pool):
    for w in t:
        index.setdefault(w, []).append(i)
random.seed(1)
for s in random.sample(new, min(8, len(new))):
    tok = filings._tokens(s)
    best, bi = 0, None
    for i, p in enumerate(pool):
        j = len(tok & p) / len(tok | p) if tok | p else 0
        if j > best:
            best, bi = j, i
    print("\nNEW:", s[:300])
    print(f"BEST OLD ({best:.2f}):", old_s[bi][:300] if bi is not None else None)
lens = sorted(len(s) for s in now_s)
print("\nlength percentiles", lens[len(lens) // 10], lens[len(lens) // 2], lens[-len(lens) // 10], lens[-1])
print("\nLONGEST NEW:", max(new, key=len)[:600])
# rerun: containment matching, joined sentences and word forms
