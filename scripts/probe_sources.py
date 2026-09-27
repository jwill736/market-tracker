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
from datetime import date as _d  # noqa: E402
t = time.time()
try:
    ic = idealab.insider_candidates(_d.today())
    print(f"insider candidates ({time.time() - t:.0f}s): {len(ic)}", [(k, v.get('company'), v.get('value'), v.get('insider'), v.get('trade_date'), (scr.lookup(d, k) or {}).get('cap')) for k, v in list(ic.items())[:12]])
    for k, v in list(ic.items())[:3]:
        t2 = time.time()
        print("  classify", k, idealab.classify_insider(v), f"{time.time() - t2:.0f}s")
except Exception as exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
t = time.time()
sl = idealab.sleepers()
print(f"sleepers ({time.time() - t:.0f}s):", sl["note"])
for x in sl["sleepers"]:
    print("  ", x["symbol"], x["level"], x["evidence"], round((x["cap"] or 0) / 1e9, 1), x["why"])
items = idealab.daily_items()
print("daily idea items:", len(items), [(i["symbol"], i["source"], i.get("price")) for i in items][:25])
