"""TEMPORARY: probe historical SEC/Yahoo sources the score backtest will depend on.
Removed before this PR is marked ready."""

import io
import re
import time
import zipfile

import httpx

from market_tracker.config import settings

UA = {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}
c = httpx.Client(timeout=120, follow_redirects=True)


def get(url, **kw):
    t = time.monotonic()
    r = c.get(url, headers=UA, **kw)
    print(f"  {r.status_code} {len(r.content):>10,}B {time.monotonic() - t:5.1f}s  {url}")
    time.sleep(0.15)
    return r


print("== Data set index pages")
for page in ["https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets",
             "https://www.sec.gov/dera/data/form-345",
             "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"]:
    r = get(page)
    if r.status_code == 200:
        links = sorted(set(re.findall(r'href="([^"]+\.zip)"', r.text)))
        print(f"    {len(links)} zip links; first: {links[:3]}; last: {links[-3:]}")

print("== Guessed insider zip names")
base = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/"
for name in ["2015q1_form345.zip", "2024q1_form345.zip", "2026q2_form345.zip"]:
    r = get(base + name)
    if r.status_code == 200 and name == "2024q1_form345.zip":
        z = zipfile.ZipFile(io.BytesIO(r.content))
        for info in z.infolist():
            print(f"    {info.filename} {info.file_size:,}B")
            if info.filename.lower().endswith(".tsv"):
                with z.open(info) as fh:
                    head = [fh.readline().decode("utf-8", "replace").rstrip("\n") for _ in range(2)]
                print("      cols:", head[0][:600])
                print("      row1:", head[1][:400])

print("== Submissions history depth")
for cik, who in [("0001067983", "Berkshire"), ("0000921669", "Icahn"), ("0001423053", "Citadel")]:
    d = get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()
    rec = d["filings"]["recent"]
    f13 = [dt for f, dt in zip(rec["form"], rec["filingDate"]) if f == "13F-HR"]
    print(f"    {who}: recent={len(rec['form'])} filings, 13F-HR in recent={len(f13)}, "
          f"oldest 13F in recent={min(f13) if f13 else None}; older pages={[x['name'] for x in d['filings']['files']]}")

print("== Yahoo long history")
r = c.get("https://query1.finance.yahoo.com/v8/finance/chart/AAPL", params={"range": "10y", "interval": "1d"},
          headers={"User-Agent": "Mozilla/5.0"})
res = r.json()["chart"]["result"][0]
print(f"    AAPL 10y: {len(res['timestamp'])} bars, first ts {res['timestamp'][0]}, adjclose present: {'adjclose' in res['indicators']}")
