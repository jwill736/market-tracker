"""Temporary probe for the next idea-engine round (removed before merge)."""
import json
import subprocess
import time
from datetime import date

from market_tracker import fundamentals, screen
from market_tracker.providers import sec


def step(name, fn):
    t = time.monotonic()
    try:
        out = fn()
        print(f"== {name} ({time.monotonic() - t:.1f}s)\n{out}\n", flush=True)
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"== {name} FAILED: {type(exc).__name__}: {str(exc)[:400]}\n{traceback.format_exc()[-800:]}\n", flush=True)


def screen_run():
    subprocess.run(["mt", "screen", "--out", "/tmp/screen.json"], check=True)
    d = json.load(open("/tmp/screen.json"))
    return "\n".join(f"{s}: {screen.lookup(d, s)}" for s in ("XOM", "BRK-B", "GOOG", "NEM", "JPM"))


def shorts_run():
    from market_tracker import shorts
    d = json.load(open("/tmp/screen.json"))
    return json.dumps(shorts.for_symbols(["GME", "AAPL", "VAL", "AROC", "CVNA", "BRK-B"], lambda s: screen.lookup(d, s)), indent=0)[:1500]


def pead_run():
    from market_tracker import pead
    d = json.load(open("/tmp/screen.json"))
    c = pead.candidates(date.today())
    rows = pead.build(date.today(), lambda s: screen.lookup(d, s))
    return f"{len(c)} candidates: {c[:30]}\nqualified {len(rows)}:\n" + "\n".join(f"{r['symbol']} {r['filed']} {r['move_pct']} {r['times_usual']} | {r['outlook'][:120]}" for r in rows)


def spin_run():
    from market_tracker import spinoffs
    d = json.load(open("/tmp/screen.json"))
    rows = spinoffs.build(date.today(), lookup_fn=lambda s: screen.lookup(d, s))
    return f"{len(rows)} registrants\n" + "\n".join(f"{r['stage']:10} {r['ticker']} {r['name'][:50]} filed {r['first_filed']}..{r['last_filed']} since {r['trading_since']} days {r['days_trading']} ret {r['return_pct']} spy {r['spy_pct']} log {r['loggable']}" for r in rows[:40])


def ideas_job():
    import os
    os.makedirs("/tmp/ideas", exist_ok=True)
    p = subprocess.run(["mt", "ideas-log", "--dir", "/tmp/ideas"], capture_output=True, text=True)
    return p.stdout[-3000:] + p.stderr[-1500:]


def nee():
    out = []
    for s in ("NEE", "AEP"):
        cik = sec.ticker_map().cik_for(s)
        f = sec._sec_get(fundamentals.COMPANYFACTS.format(cik=str(cik).zfill(10)), ttl=1)
        for ns, tags in f["facts"].items():
            hits = [(t, len(v.get("units", {}).get("USD", []))) for t, v in tags.items()
                    if any(w in t for w in ("CapitalExpend", "Construction", "PaymentsToAcquire", "Additions", "Capex"))]
            out.append(f"{s} {ns}: {hits[:14]}")
        from market_tracker import moneyflow
        out.append(f"{s} capex_ttm {moneyflow.capex_ttm(f)}")
    return "\n".join(out)


step("screen with the fixes", screen_run)
step("short interest", shorts_run)
step("raised guidance", pead_run)
step("spin-offs", spin_run)
step("NEE / AEP capex", nee)
step("daily ideas job", ideas_job)
t = time.monotonic()
p = subprocess.run(["mt", "screen-backtest", "--out", "/tmp/bt.json"], capture_output=True, text=True)
print(p.stdout[-2500:], p.stderr[-1500:])
d = json.load(open("/tmp/bt.json"))
print("companies per period:", [pr["companies"] for pr in d["periods"]])
print(f"backtest took {time.monotonic() - t:.0f}s")
