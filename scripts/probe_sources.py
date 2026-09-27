"""Temporary probe (removed before merge): run the weekly screen and the idea pages on real data."""
import json
import subprocess
import sys
import time

sys.path.insert(0, ".")
t = time.time()
subprocess.run([sys.executable, "-m", "market_tracker.cli", "screen", "--out", "/tmp/screen.json"], check=False)
print(f"screen took {time.time() - t:.0f}s")
d = json.load(open("/tmp/screen.json"))
print("top_all:", [(r["symbol"], r["score"], r["grades"]) for r in d["top_all"][:12]])
print("backlog:", [(b["symbol"], b["why"]) for b in d["backlog"][:8]])
print("small_mid:", len(d["small_mid"]), [(r["symbol"], r["score"], round(r["cap"] / 1e9, 1)) for r in d["small_mid"][:15]])
from collections import Counter  # noqa: E402
print("sectors:", Counter(v[9] for v in d["lookup"].values()).most_common(14))
print("score None:", sum(1 for v in d["lookup"].values() if v[0] is None), "of", len(d["lookup"]))
for s in ["AAPL", "MSFT", "NVDA", "KO", "JPM", "GEV", "PLTR", "XOM"]:
    print(s, dict(zip(["score", "q", "v", "m", "iss", "ag", "r12pct", "above200", "cap", "sector"], d["lookup"].get(s) or [])))
from market_tracker import macro, hype  # noqa: E402
print("macro pace:", macro.build()["pace"])
th = hype.themes()
print("themes:", [(r["theme"], r["last_6m"], r["prior_6m"], r["level"]) for r in th])
