from datetime import date, timedelta

from market_tracker import performance


def bars(points):
    """points: [(day offset from 2025-01-01, close)] -> daily series with linear steps."""
    d0 = date(2025, 1, 1)
    return [((d0 + timedelta(days=o)).isoformat(), c) for o, c in points]


def test_xirr_simple():
    r = performance.xirr([("2025-01-01", -100.0), ("2026-01-01", 110.0)])
    assert abs(r - 0.0998) < 0.002          # 365 days is a hair under 365.25


def test_buying_the_top_costs_you_even_when_the_holding_ends_flat():
    # Price 100 → 200 (day 200) → 100 (day 500). You buy 10 at the start and 10 more at the top.
    series = bars([(o, 100 + 100 * o / 200) for o in range(0, 201)] + [(o, 200 - 100 * (o - 200) / 300) for o in range(201, 501)])
    txs = [{"symbol": "X", "side": "buy", "quantity": 10, "price": 100.0, "date": "2025-01-01", "id": 1},
           {"symbol": "X", "side": "buy", "quantity": 10, "price": 200.0, "date": series[200][0], "id": 2}]
    today = date.fromisoformat(series[500][0])
    r = performance.analyze(txs, lambda s, n: series, today)
    assert abs(r["time_weighted"]) < 0.5                     # the holding went nowhere
    assert r["money_weighted_annual"] < -15 and r["timing_gap"] < -15
    assert r["value"] == 2000.0 and r["gain"] == -1000.0
    assert "cost you" in performance.verdict(r)
    assert r["contribution"][0]["symbol"] == "X" and r["contribution"][0]["gain"] == -1000.0


def test_selling_early_versus_never_selling_and_ytd_contribution():
    up = bars([(o, 100 * (1 + o / 365)) for o in range(0, 700)])      # doubles in a year, keeps going
    txs = [{"symbol": "X", "side": "buy", "quantity": 10, "price": 100.0, "date": "2025-01-01", "id": 1},
           {"symbol": "X", "side": "sell", "quantity": 5, "price": 150.0, "date": up[182][0], "id": 2},
           {"symbol": "Y", "side": "buy", "quantity": 1, "price": 50.0, "date": up[10][0], "id": 3, "transfer": 9}]   # a move: ignored
    today = date.fromisoformat(up[600][0])
    r = performance.analyze(txs, lambda s, n: up if s == "X" else [], today)
    px = up[600][1]
    assert r["never_sold_value"] == round(10 * px, 2)
    assert r["sales_effect"] == round(5 * px + 750 - 10 * px, 2) and r["sales_effect"] < 0
    ytd = performance.analyze(txs, lambda s, n: up, today, start=date(2026, 1, 1))
    c = {x["symbol"]: x for x in ytd["contribution"]}
    jan1_px = dict(up)["2025-12-31"]
    assert c["X"]["gain"] == round(5 * (px - jan1_px), 2)
    assert "Y" not in c


def test_empty():
    assert performance.analyze([], lambda s, n: [], date(2026, 1, 1)) == {"empty": True}


def test_fill_price_vs_adjusted_close_isnt_counted_as_performance_and_income_counts():
    # A flat stock whose adjusted history sits 5% under the price actually paid (dividend adjustment),
    # paying $10 a quarter in cash dividends.
    flat = bars([(o, 95.0) for o in range(0, 800)])
    txs = [{"symbol": "KO", "side": "buy", "quantity": 10, "price": 100.0, "date": "2025-01-01", "id": 1},
           {"symbol": "SOL-USD", "side": "buy", "quantity": 1, "price": 95.0, "date": "2025-01-01", "id": 2,
            "note": "Coinbase import: staking income"}]
    income = [{"day": flat[i][0], "amount": 10.0, "kind": "dividend"} for i in (90, 180, 270, 360, 450, 540, 630, 720)]
    today = date.fromisoformat(flat[799][0])
    r = performance.analyze(txs, lambda s, n: flat, today, None, income)
    assert abs(r["time_weighted"]) < 0.01                      # flat holding: 0%, not -5%
    assert r["put_in"] == 1000.0                               # the staking reward isn't money put in
    assert r["income"] == 80.0 and r["taken_out"] == 0.0
    assert r["gain"] == round(10 * 95 + 95 + 80 - 1000, 2)
