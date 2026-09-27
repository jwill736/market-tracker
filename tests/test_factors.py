import io
import random
import zipfile

from market_tracker import factors

CSV = """This file was created by CMPT_ME_BEME_OP_INV_RETS_DAILY using the 202607 CRSP database.
The 1-month TBill return is from Ibbotson and Associates Inc.

,Mkt-RF,SMB,HML,RMW,CMA,RF
20260701,   0.50,  -0.10,   0.20,   0.05,  -0.03,   0.017
20260702,  -1.20,   0.30,  -0.40,   0.10,   0.02,   0.017

 Copyright 2026 Eugene F. Fama and Kenneth R. French
"""
MOM = """Momentum factor daily

,Mom
20260701,   0.40
20260702,  -0.25
"""


def _zip(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("F-F.csv", text)
    return b.getvalue()


def test_parse_french_reads_daily_rows_in_decimals():
    rows = factors.parse_french(CSV)
    assert list(rows) == ["2026-07-01", "2026-07-02"]
    assert abs(rows["2026-07-02"]["Mkt-RF"] + 0.012) < 1e-12 and abs(rows["2026-07-01"]["RF"] - 0.00017) < 1e-12


def test_load_joins_momentum():
    factors._cache.clear()
    data = factors.load(lambda url: _zip(MOM if "Momentum" in url else CSV))
    assert data["2026-07-01"]["Mom"] == 0.004 and set(data["2026-07-01"]) >= set(factors.FACTORS) | {"RF"}
    factors._cache.clear()


def _synthetic(n=400, seed=7):
    rnd = random.Random(seed)
    ff, days = {}, []
    for i in range(n):
        d = f"2025-{1 + i // 28 % 12:02d}-{1 + i % 28:02d}" if i < 336 else f"2026-{1 + (i - 336) // 28:02d}-{1 + (i - 336) % 28:02d}"
        days.append(d)
        ff[d] = {f: rnd.gauss(0, 0.01) for f in factors.FACTORS} | {"RF": 0.0001}
    return ff, days


def test_ols_recovers_known_loadings():
    ff, days = _synthetic()
    rnd = random.Random(1)
    true = {"Mkt-RF": 1.2, "SMB": -0.3, "HML": -0.5, "RMW": 0.0, "CMA": 0.0, "Mom": 0.25}
    daily = {d: ff[d]["RF"] + sum(true[f] * ff[d][f] for f in factors.FACTORS) + rnd.gauss(0, 0.002) for d in days}
    r = factors.exposure(daily, ff)
    got = {ld["factor"]: ld["beta"] for ld in r["loadings"]}
    for f, b in true.items():
        assert abs(got[f] - b) < 0.05, (f, got[f])
    assert r["r2"] > 0.9
    texts = {ld["factor"]: ld["text"] for ld in r["loadings"]}
    assert "more than the market" in texts["Mkt-RF"] and "growth" in texts["HML"] and "large companies" in texts["SMB"]
    assert texts["RMW"] == "No clear tilt."


def test_build_weights_holdings_and_leaves_out_coins():
    ff, days = _synthetic()
    closes = {"AAA": [], "BBB": []}
    pa = pb = 100.0
    for d in days:
        pa *= 1 + ff[d]["Mkt-RF"] * 1.1 + ff[d]["RF"]
        pb *= 1 + ff[d]["Mkt-RF"] * 0.9 + ff[d]["HML"] * 0.6 + ff[d]["RF"]
        closes["AAA"].append((d, pa))
        closes["BBB"].append((d, pb))
    closes["VOO"] = closes["AAA"]
    positions = [{"symbol": "AAA", "market_value": 500.0}, {"symbol": "BBB", "market_value": 500.0}, {"symbol": "BTC-USD", "market_value": 250.0}]
    r = factors.build(positions, lambda s: closes[s], ff)
    assert r["crypto_share"] == 20.0 and r["holdings"] == 2
    got = {ld["factor"]: ld["beta"] for ld in r["loadings"]}
    assert abs(got["Mkt-RF"] - 1.0) < 0.05 and abs(got["HML"] - 0.3) < 0.05
    assert r["reference"]["loadings"][0]["beta"] > 1.05
    assert "value" in " ".join(r["tilts"]) and r["summary"].startswith("Market sensitivity 1.0")
