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
from market_tracker import idealab, screen as scr  # noqa: E402
scr.load = lambda get=None: d
t = time.time()
c = idealab.chatter()
print(f"chatter ({time.time() - t:.0f}s):", c["errors"], [(r["symbol"], r["reddit"], r["rising"], r["stocktwits"], r["caution"], r["warnings"][:2]) for r in c["rows"][:10]])
t = time.time()
mf = idealab.money_flow()
for w in mf["waves"]:
    print("wave", w["wave"], "total", w["total"], "growth", w["growth"], [(s["symbol"], s["capex"], s["growth"]) for s in w["spenders"]])
    print("   ", [(c2["category"], [(x["symbol"], x["score"], x["priced_in"]) for x in c2["companies"]]) for c2 in w["suppliers"][:3]])
print(f"lagging ({time.time() - t:.0f}s):", [(x["symbol"], x["why"]) for x in mf["lagging"][:8]])
print("contracts LMT:", idealab.contracts_for("LMT"))
print("contracts PLTR:", idealab.contracts_for("PLTR"))
t = time.time()
sl = idealab.sleepers()
print(f"sleepers ({time.time() - t:.0f}s):", sl["note"], [(x["symbol"], x["level"], x["why"]) for x in sl["sleepers"][:10]])
from market_tracker import hype as hp  # noqa: E402
print("priced-in NVDA:", hp.for_symbol("NVDA", d))
print("priced-in PLTR:", hp.for_symbol("PLTR", d))
