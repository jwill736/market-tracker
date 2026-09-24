import math

import pytest

from market_tracker.analytics import indicators as ind
from tests.conftest import synthetic_closes


def test_sma_and_series_agree():
    vals = [1, 2, 3, 4, 5, 6]
    assert ind.sma(vals, 3) == 5
    assert ind.sma(vals, 10) is None
    assert ind.sma_series(vals, 3) == [None, None, 2, 3, 4, 5]


def test_rsi_bounds_and_extremes():
    assert ind.rsi(list(range(1, 40))) == 100.0  # only gains
    assert ind.rsi(list(range(40, 1, -1))) < 1  # only losses
    r = ind.rsi(synthetic_closes(200))
    assert 0 <= r <= 100
    assert ind.rsi([1, 2, 3]) is None


def test_max_drawdown():
    assert ind.max_drawdown([100, 120, 60, 90, 130]) == pytest.approx(-0.5)
    assert ind.max_drawdown([1, 2, 3]) == 0


def test_period_return_with_skip():
    closes = list(range(1, 300))
    assert ind.period_return(closes, 252, skip=21) == pytest.approx(closes[-22] / closes[-253] - 1)


def test_vol_estimates_recover_true_vol():
    closes = synthetic_closes(2000, drift=0, vol=0.02, seed=3)
    true_ann = 0.02 * math.sqrt(252)
    assert ind.annualized_vol(closes) == pytest.approx(true_ann, rel=0.08)
    assert ind.ewma_vol(closes) == pytest.approx(true_ann, rel=0.35)


def test_correlation():
    a = [0.01, -0.02, 0.03, 0.0, 0.01]
    assert ind.correlation(a, a) == pytest.approx(1.0)
    assert ind.correlation(a, [-x for x in a]) == pytest.approx(-1.0)


def test_snapshot_keys_on_short_history():
    snap = ind.snapshot(synthetic_closes(60))
    assert snap["sma200"] is None and snap["above_sma200"] is None
    assert snap["sma50"] is not None
