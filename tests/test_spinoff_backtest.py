from market_tracker import spinoff_backtest as sb


def _days(n, start=2010):
    from datetime import date, timedelta
    d, out = date(start, 1, 4), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_run_prices_spinoffs_and_drops_exchange_moves():
    days = _days(1500)
    spy = [(d, 100.0 * 1.0003 ** i) for i, d in enumerate(days)]
    regs = {y: [] for y in range(2010, 2027)}
    names = {}
    for k in range(14):
        filed = days[10 * k]
        regs[2010 + (k % 3)].append({"cik": f"{k:010d}", "name": f"Spin {k}", "ticker": None, "first_filed": filed, "last_filed": filed})
        names[f"{k:010d}"] = f"S{k}"
    regs[2011].append({"cik": "0000000099", "name": "Old Co", "ticker": "OLD", "first_filed": days[400], "last_filed": days[400]})
    regs[2012].append({"cik": "0000000098", "name": "Gone Co", "ticker": None, "first_filed": days[30], "last_filed": days[30]})

    def hist(sym):
        if sym == "SPY":
            return spy
        if sym == "OLD":
            return spy[:900]                               # traded long before registering
        k = int(sym[1:])
        start = 10 * k + 5
        return [(d, 20.0 * 1.0006 ** i) for i, d in enumerate(days[start:])]
    import market_tracker.spinoff_backtest as mod
    orig = mod.registrations_by_year
    mod.registrations_by_year = lambda y, get=None: regs.get(y, [])
    try:
        out = sb.run(history_fn=hist, ticker_fn=lambda c: names.get(c), first_year=2010, log=lambda m: None)
    finally:
        mod.registrations_by_year = orig
    assert out["priced"] == 14 and out["missing"] == 1 and out["exchange_moves"] == 1
    s = out["summary"]["from_day20"]["12m"]
    assert s["spinoffs"] == 14 and s["avg_edge"] > 0 and s["beat_pct"] == 100
    assert "1 of 16 registrants have no ticker" in out["verdict"]


def test_stress_test_on_the_missing_half():
    from market_tracker import spinoff_backtest as sb
    bt = {"priced": 171, "missing": 164, "summary": {"from_day20": {"12m": {"spinoffs": 160, "avg_edge": 23.1, "t": 2.46}}}}
    st = sb.stress(bt)
    assert -26 < st["breakeven"] < -22                    # the missing ones trailing SPY by ~24 points erases it
    assert st["scenarios"][0]["avg_edge"] > 10 and st["scenarios"][1]["avg_edge"] < 0
    assert not st["robust"] and "unproven" in st["text"]
    few = dict(bt, missing=10)
    assert sb.stress(few)["robust"]                       # with few missing, a -30 on them still leaves an edge
    assert sb.stress({"priced": 0}) is None
