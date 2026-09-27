from datetime import date, timedelta

from market_tracker import stress


def daily(start, end, f):
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    return [((d0 + timedelta(days=i)).isoformat(), f(i)) for i in range((d1 - d0).days + 1)]


SPY = daily("2007-01-01", "2026-09-26", lambda i: 100 * (1.0003 ** i) * (0.5 if 300 <= i <= 800 else 1.0))


def test_replay_uses_own_history_or_a_beta_stand_in():
    old = [(d, c * 2) for d, c in SPY]                                         # same moves as the market
    young = [(d, c) for d, c in SPY if d >= "2024-01-01"]
    young = [(d, c ** 1.0) for d, c in young]
    hist = {"SPY": SPY, "OLD": old, "YOUNG": young, "BTC-USD": daily("2015-01-01", "2026-09-26", lambda i: 300 * 1.001 ** i)}
    pos = [{"symbol": "OLD", "market_value": 10_000}, {"symbol": "YOUNG", "market_value": 5_000}]
    out = stress.replay(pos, lambda s: hist.get(s, []), [dict(stress.CRISES[0], start="2007-10-09", end="2009-03-09")])
    c = out[0]
    old_r = next(h for h in c["holdings"] if h["symbol"] == "OLD")
    young_r = next(h for h in c["holdings"] if h["symbol"] == "YOUNG")
    assert old_r["how"] == "its own prices" and old_r["change_pct"] < -40
    assert young_r["how"].startswith("stand-in: 1.0× the S&P 500")
    assert abs(young_r["change_pct"] - c["market_pct"]) < 0.5
    assert c["stand_ins"] == 1 and c["change"] < 0 and c["change_pct"] < -40


def test_crypto_before_bitcoin_existed_uses_bitcoins_2022_fall():
    btc = daily("2015-01-01", "2026-09-26", lambda i: 300 * 1.0001 ** i * (0.35 if "2021-11-08" < (date(2015, 1, 1) + timedelta(days=i)).isoformat() <= "2022-11-21" else 1))
    out = stress.replay([{"symbol": "BTC-USD", "market_value": 1000}], lambda s: {"SPY": SPY, "BTC-USD": btc}.get(s, []),
                        [stress.CRISES[0]])
    h = out[0]["holdings"][0]
    assert "Bitcoin's 2022 fall" in h["how"] and h["change_pct"] < -50


def test_correlations_flag_twins():
    a = daily("2025-01-01", "2026-09-26", lambda i: 100 + (i % 7) * 3 + i * 0.1)
    b = [(d, c * 1.5) for d, c in a]
    c = daily("2025-01-01", "2026-09-26", lambda i: 100 + ((i * 5) % 11) - (i % 3) * 4)
    r = stress.correlations(["A", "B", "C"], lambda s: {"A": a, "B": b, "C": c}[s])
    assert r["pairs"][0]["a"] == "A" and r["pairs"][0]["b"] == "B" and r["pairs"][0]["rho"] > 0.99
    assert all(p["b"] != "C" for p in r["pairs"])
