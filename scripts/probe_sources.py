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
from market_tracker import idealab, screen as scr  # noqa: E402
scr.load = lambda get=None: d
c = idealab.chatter()
print("chatter:", [(r["symbol"], r["reddit"], r["rising"], r["stocktwits"], r["caution"], r["warnings"][:2]) for r in c["rows"][:12]])
t = time.time()
sl = idealab.sleepers()
print(f"sleepers ({time.time() - t:.0f}s):", sl["note"])
for x in sl["sleepers"]:
    print("  ", x["symbol"], x["level"], x["evidence"], round((x["cap"] or 0) / 1e9, 1), x["why"])
items = idealab.daily_items()
print("daily idea items:", len(items), [(i["symbol"], i["source"], i.get("price")) for i in items][:25])
