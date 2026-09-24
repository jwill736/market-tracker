import pytest

from market_tracker.analytics import portfolio as pf
from market_tracker.analytics import signals
from market_tracker.analytics.indicators import snapshot
from tests.conftest import synthetic_closes


def test_composite_renormalizes_missing_components():
    ind = snapshot([100 * 1.002 ** i for i in range(300)])  # steady uptrend
    out = signals.composite(ind)
    assert out["coverage"] == 0.5
    assert out["components"]["smart_money"] is None
    assert out["score"] > 40 and out["label"] == "Strong bullish"


def test_insider_and_smart_money_components():
    ins = {"cluster_buy": True, "distinct_buyers": 4, "window_days": 90, "open_market_buys": 5,
           "buy_value_usd": 1e6, "discretionary_sell_value_usd": 0, "planned_10b5_1_sells": 0}
    assert signals.insider_component(ins)[0] == 1.0
    sm = {"investors_scanned": 10, "buyers": [{}] * 3, "sellers": [], "holders": [{"weight_pct": 8}],
          "net_flow": 1.0}
    assert signals.smart_money_component(sm)[0] == 1.0
    assert signals.smart_money_component({"investors_scanned": 0}) is None


def test_downtrend_is_bearish_and_flags_drawdown():
    closes = [100 * 0.997 ** i for i in range(300)]
    out = signals.composite(snapshot(closes), sm_staleness_days=80)
    assert out["score"] < -40
    assert any("drawdown" in f for f in out["risk_flags"])
    assert any("13F data" in f for f in out["risk_flags"])


def test_suggested_max_weight_scales_with_vol():
    assert signals.suggested_max_weight(0.2) == 0.20  # capped
    assert signals.suggested_max_weight(1.0) == pytest.approx(0.02 / (1.0 * (21 / 252) ** 0.5))
    assert signals.suggested_max_weight(None) is None


TXS = [
    {"id": 1, "symbol": "AAPL", "side": "buy", "quantity": 10, "price": 100, "fees": 0, "date": "2026-01-02"},
    {"id": 2, "symbol": "AAPL", "side": "buy", "quantity": 10, "price": 120, "fees": 2, "date": "2026-02-02"},
    {"id": 3, "symbol": "AAPL", "side": "sell", "quantity": 5, "price": 150, "fees": 1, "date": "2026-03-02"},
    {"id": 4, "symbol": "BTC-USD", "side": "buy", "quantity": 0.5, "price": 60000, "fees": 0, "date": "2026-03-05"},
]


def test_average_cost_and_realized_pnl():
    pos = pf.build_positions(TXS)
    aapl = pos["AAPL"]
    assert aapl.quantity == 15
    assert aapl.avg_cost == pytest.approx(2202 / 20)
    assert aapl.realized_pnl == pytest.approx(5 * (150 - 110.1) - 1)
    assert aapl.cost_basis == pytest.approx(110.1 * 15)


def test_oversell_rejected():
    with pytest.raises(ValueError):
        pf.build_positions(TXS + [{"symbol": "AAPL", "side": "sell", "quantity": 100, "price": 1, "date": "2026-04-01"}])


def test_value_positions_weights_sum_to_100():
    pos = pf.build_positions(TXS)
    out = pf.value_positions(pos, {"AAPL": {"price": 200, "change_pct": 1.0},
                                   "BTC-USD": {"price": 70000, "change_pct": -2.0}})
    assert sum(p["weight"] for p in out["positions"]) == pytest.approx(100)
    assert out["total_value"] == pytest.approx(15 * 200 + 0.5 * 70000)
    assert out["day_change_value"] == pytest.approx(3000 * 1 / 101 - 35000 * 2 / 98)


def test_risk_report_warns_on_concentration_and_correlation():
    a = synthetic_closes(300, seed=1)
    dates = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(300)]
    hist = {"A": list(zip(dates, a)), "B": list(zip(dates, [x * 2 for x in a]))}
    rep = pf.risk_report({"A": 0.6, "B": 0.4}, hist, {"A": "stock", "B": "stock"})
    assert rep["annual_vol"] > 0
    assert any("A is 60%" in w for w in rep["warnings"])
    assert any("correlated" in w for w in rep["warnings"])


def test_inverse_vol_weights():
    w = pf.inverse_vol_weights({"A": 0.2, "B": 0.4})
    assert w["A"] == pytest.approx(2 / 3)
