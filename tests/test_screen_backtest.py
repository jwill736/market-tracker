import math
from datetime import date, timedelta

from market_tracker import screen, screen_backtest as sb


def _calendar(start="2011-01-03", n=4200):
    d0 = date.fromisoformat(start)
    out, d = [], d0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_rebalance_days_wait_for_filings():
    cal = _calendar(n=800)
    days = sb.rebalance_days(cal, (2011, 4))
    y, q, i = days[0]
    assert (y, q) == (2011, 4) and cal[i] >= "2012-05-14" and cal[i - 1] < "2012-05-14"     # Dec 31 + 135 days
    assert all(cal[i] <= cal[-1] for _, _, i in days)


def test_align_price_at_and_forward():
    cal = ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-06"]
    p = sb.Prices(cal, {"A": sb.align(cal, [("2020-01-01", 10.0), ("2020-01-06", 12.0)]), "SPY": sb.align(cal, [(d, 100.0) for d in cal])})
    assert math.isnan(p.series["A"][1]) and sb.price_at(p, "A", 2) == 10.0 and sb.price_at(p, "ZZZ", 2) is None
    r, n = sb.forward(p, ["A", "ZZZ"], 0, 3)
    assert round(r, 3) == 0.2 and n == 1
    assert sb.forward(p, ["A"], 2, 5) == (None, 0)          # past the end of the calendar


def test_run_end_to_end_on_synthetic_history():
    cal = _calendar(n=3900)
    universe_n = 240
    syms = [f"S{k:03d}" for k in range(universe_n)]
    # Quality (operating profit on assets) drives returns: good companies compound faster.
    drift = {s: 0.0002 + 0.0008 * (k / universe_n) for k, s in enumerate(syms)}
    series = {"SPY": sb.align(cal, [(d, 100 * 1.0004 ** i) for i, d in enumerate(cal)])}
    for s in syms:
        series[s] = sb.align(cal, [(d, 20 * (1 + drift[s]) ** i) for i, d in enumerate(cal)])
    p = sb.Prices(cal, series)
    ciks = {s: 1000 + k for k, s in enumerate(syms)}

    def gather(y, q):
        F = {k: {} for k in ("assets", "assets_ya", "equity", "shares", "shares_ya", "shares_dei", "shares_dei_ya", "operating_income",
                             "net_income", "revenue", "rev_q", "rev_q_ya", "rpo", "rpo_ya")}
        for k, s in enumerate(syms):
            c = ciks[s]
            F["assets"][c], F["assets_ya"][c], F["equity"][c] = 1e10, 1e10, 5e9
            F["shares"][c], F["shares_ya"][c] = 5e8, 5e8
            F["operating_income"][c], F["net_income"][c] = 1e8 + 1e7 * k, 5e7 + 5e6 * k
        F["assets"][99999] = 5e9                            # a big filer with no ticker today
        return F

    listed = {s: {"name": s, "price": 1.0, "cap": 1e9 + k, "sector": "Tech" if k % 2 else "Energy", "industry": ""} for k, s in enumerate(syms)}

    class TM:
        by_ticker = {s: {"cik_str": ciks[s]} for s in syms}

        def cik_for(self, t):
            return str(ciks[t]).zfill(10) if t in ciks else None
    import market_tracker.providers.sec as sec
    orig = sec.ticker_map
    sec.ticker_map = lambda: TM()
    try:
        out = sb.run(listed_fn=lambda: listed, prices=p, gather_fn=gather, log=lambda m: None)
    finally:
        sec.ticker_map = orig
    s = out["summary"]
    assert out["periods"] and s["large"]["3m"]["periods"] >= 40
    assert s["large"]["3m"]["avg_edge"] > s["bottom"]["3m"]["avg_edge"]       # quality compounding shows up in the ranking
    assert s["large"]["growth"]["screen"] > s["large"]["growth"]["spy"]
    assert 0 < out["survivorship_pct"] < 1 and "Survivorship" in out["verdict"]
    assert set(out["periods"][0]["groups"]["large"]["top"][:2]) == {"S238", "S239"}     # the best of each sector
    assert s["bottom_quality"]["check"] and s["bottom_quality"]["3m"]["avg_edge"] < 0      # quality drives returns here
    assert s["large"]["12m"]["t"] is not None and s["large"]["3m"]["breakeven_missing"] is not None
    assert "Of the four grades alone" in out["verdict"]


def test_newey_west_widens_overlapping_errors():
    import random
    rng = random.Random(3)
    base = [rng.gauss(0.01, 0.05) for _ in range(60)]
    overlapping = [sum(base[i:i + 4]) for i in range(57)]       # 12-month holds started each quarter
    plain = sb.newey_west_t(overlapping, 0)
    nw = sb.newey_west_t(overlapping, 3)
    assert plain is not None and nw is not None and abs(nw) < abs(plain)
    assert sb.newey_west_t([0.1] * 5, 1) is None
    assert sb.breakeven_missing(0.3, 0.3) == 1.0 and sb.breakeven_missing(None, 0.3) is None


def test_parse_history_undoes_splits_for_market_values():
    day = 86400
    res = {"timestamp": [1_600_000_000, 1_600_000_000 + day, 1_600_000_000 + 2 * day],
           "indicators": {"quote": [{"close": [100.0, 101.0, 102.0]}], "adjclose": [{"adjclose": [98.0, 99.0, 102.0]}]},
           "events": {"splits": {"x": {"date": 1_600_000_000 + day, "numerator": 4, "denominator": 1}}}}
    adj, raw = sb.parse_history(res)
    assert [c for _, c in adj] == [98.0, 99.0, 102.0]
    assert [c for _, c in raw] == [400.0, 101.0, 102.0]           # traded at $400 before the 4-for-1 split
    p = sb.Prices(["a", "b"], {"X": sb.align(["a", "b"], [("a", 10.0), ("b", 11.0)])}, {"X": sb.align(["a", "b"], [("a", 40.0), ("b", 11.0)])})
    assert sb.price_traded(p, "X", 0) == 40.0 and sb.price_at(p, "X", 0) == 10.0 and sb.price_traded(p, "Y", 0) is None
