"""Temporary probe (removed before merge): run the backtests on real data; diagnose ApeWisdom."""
import json
import subprocess
import sys
import time

what = sys.argv[1]
t = time.monotonic()
if what == "apewisdom":
    from market_tracker import http
    from market_tracker.reading import BROWSER_UA
    for i in range(3):
        for hdr in ({"User-Agent": BROWSER_UA, "Accept": "application/json"}, {}):
            try:
                d = http.get("https://apewisdom.io/api/v1.0/filter/all-stocks/page/1", headers=hdr, ttl=0)
                rows = d.get("results", [])
                print(f"try {i} hdr={bool(hdr)}: {len(rows)} rows; first {rows[:2]}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"try {i} hdr={bool(hdr)}: {type(exc).__name__} {exc}", flush=True)
            time.sleep(2)
    from market_tracker import idealab
    r = idealab.chatter()
    print("errors:", r["errors"], "rows:", [(x["symbol"], x["reddit"], x["stocktwits"]) for x in r["rows"][:10]])
else:
    p = subprocess.run(["mt", what, "--out", f"/tmp/{what}.json"], capture_output=True, text=True)
    print(p.stdout[-6000:])
    print(p.stderr[-3000:])
    try:
        d = json.load(open(f"/tmp/{what}.json"))
        print(json.dumps(d.get("summary"), indent=0)[:9000])
    except Exception as exc:  # noqa: BLE001
        print("no json:", exc)
print(f"took {time.monotonic() - t:.0f}s")
