import pytest

from market_tracker.analytics import backtest, forecast
from tests.conftest import synthetic_closes


def test_lognormal_cone_is_ordered_and_widens():
    closes = synthetic_closes(400)
    cone = forecast.lognormal_cone(closes)
    for row in cone:
        assert row["p5"] < row["p25"] < row["p50"] < row["p75"] < row["p95"]
        assert 0 < row["prob_up"] < 1
    widths = [r["p95"] - r["p5"] for r in cone]
    assert widths == sorted(widths)


def test_bootstrap_is_deterministic_with_seed():
    closes = synthetic_closes(400)
    assert forecast.bootstrap(closes, seed=11) == forecast.bootstrap(closes, seed=11)
    row = forecast.bootstrap(closes)[0]
    assert row["p5"] < row["p50"] < row["p95"]


def test_forecast_requires_history():
    assert forecast.lognormal_cone([100, 101]) == []
    assert forecast.bootstrap(synthetic_closes(30)) == []


def test_buy_hold_matches_asset_return_minus_one_entry_cost():
    closes = synthetic_closes(600)
    res = backtest.run(closes, backtest.rule_buy_hold, cost_bps=10, warmup=252)
    expected = closes[-1] / closes[252] * (1 - 0.001) - 1
    assert res["total_return"] == pytest.approx(expected, rel=1e-9)
    assert res["trades"] == 1 and res["exposure"] == 1.0


def test_trend_rule_sits_out_a_crash():
    up = [100 * 1.001 ** i for i in range(400)]
    crash = [up[-1] * 0.99 ** i for i in range(1, 200)]
    closes = up + crash
    trend = backtest.run(closes, backtest.rule_trend_sma200)
    hold = backtest.run(closes, backtest.rule_buy_hold)
    assert trend["max_drawdown"] > hold["max_drawdown"]  # less negative


def test_rules_have_no_lookahead():
    closes = synthetic_closes(500)
    for rule in backtest.RULES.values():
        full = rule(closes)
        for cut in (260, 300, 499):
            assert rule(closes[:cut + 1])[cut] == full[cut]


def test_compare_short_history_returns_none_results():
    out = backtest.compare(synthetic_closes(100))
    assert all(v is None for v in out["results"].values())
