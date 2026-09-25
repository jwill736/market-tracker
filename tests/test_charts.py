from datetime import date

import pytest
from fastapi.testclient import TestClient

from market_tracker import api, charts
from market_tracker.providers import market

TXS = [
    {"id": 1, "symbol": "AAA", "side": "buy", "quantity": 10, "price": 10.0, "fees": 0, "date": "2026-09-01"},
    {"id": 2, "symbol": "AAA", "side": "buy", "quantity": 10, "price": 12.0, "fees": 0, "date": "2026-09-10"},
    {"id": 3, "symbol": "BTC-USD", "side": "buy", "quantity": 0.1, "price": 50000.0, "fees": 0, "date": "2026-09-05"},
]


def hist(sym, days):
    if sym == "AAA":
        return [market.PriceBar(f"2026-09-{d:02d}", 10.0 + d * 0.2) for d in range(1, 25)]
    return [market.PriceBar(f"2026-09-{d:02d}", 50000.0 + d * 100) for d in range(1, 25)]


def test_daily_history_counts_purchases_as_deposits_not_gains():
    d = charts.portfolio_history(TXS, "1m", today=date(2026, 9, 24), history_fn=hist)
    by_day = {p["t"]: p["v"] for p in d["points"]}
    assert len(by_day) == 24
    first, last = d["points"][0]["v"], d["points"][-1]["v"]
    assert first == pytest.approx(10 * 10.2)                          # 1 Sep: 10 shares only
    assert last == pytest.approx(20 * 14.8 + 0.1 * 52400)
    deposits = 10 * 12.0 + 0.1 * 50000                                # bought after the first day
    assert d["net_deposits"] == pytest.approx(deposits)
    assert d["gain"] == pytest.approx(last - first - deposits, abs=0.01)


def test_all_time_gain_is_value_minus_money_put_in():
    d = charts.portfolio_history(TXS, "all", today=date(2026, 9, 24), history_fn=hist)
    put_in = 10 * 10 + 10 * 12 + 0.1 * 50000
    assert d["gain"] == pytest.approx(d["end"] - put_in, abs=0.01)


def test_intraday_history_sums_holdings_on_a_grid():
    series = {"AAA": {"points": [{"t": 1000, "p": 15.0}, {"t": 1300, "p": 16.0}], "reference": 14.0},
              "BTC-USD": {"points": [{"t": 1000, "p": 50000.0}, {"t": 1600, "p": 51000.0}], "reference": 49000.0}}
    d = charts.portfolio_history(TXS, "1d", today=date(2026, 9, 24), intraday_fn=lambda s, r: series[s])
    vals = [p["v"] for p in d["points"]]
    assert vals[0] == pytest.approx(20 * 15 + 0.1 * 50000)
    assert vals[-1] == pytest.approx(20 * 16 + 0.1 * 51000)          # BTC carried forward, then updated
    assert d["start"] == pytest.approx(20 * 14 + 0.1 * 49000)          # previous close
    assert d["gain"] == pytest.approx(vals[-1] - d["start"])


MONDAY = int(__import__('datetime').datetime(2026, 9, 21, tzinfo=__import__('datetime').timezone.utc).timestamp())


def test_candles_parsing_and_weekly():
    result = {"timestamp": [1, 2, 3], "meta": {"chartPreviousClose": 9.5},
              "indicators": {"quote": [{"open": [10, 11, None], "high": [12, 13, 1], "low": [9, 10, 1],
                                        "close": [11, 12, 1], "volume": [100, None, 5]}]}}
    cs = charts.parse_yahoo_candles(result)
    assert [(c.o, c.c, c.v) for c in cs] == [(10.0, 11.0, 100.0), (11.0, 12.0, 0.0)]
    cb = charts.parse_coinbase_ohlc([[200, 1, 5, 2, 4, 10], [100, 0.5, 3, 1, 2, 7]])
    assert [(c.t, c.o, c.h, c.l, c.c) for c in cb] == [(100, 1, 3, 0.5, 2), (200, 2, 5, 1, 4)]
    days = [charts.Candle(MONDAY + i * 86400, 10 + i, 12 + i, 9 + i, 11 + i, 1) for i in range(8)]   # Mon..Mon
    wk = charts.weekly(days)
    assert len(wk) == 2 and wk[0].o == 10 and wk[0].c == 17 and wk[0].h == 18 and wk[0].v == 7


def test_candles_for_a_stock_uses_previous_close_on_1d():
    def get(url, params=None, **kw):
        assert params["interval"] == "5m" and params["includePrePost"] == "true"
        return {"chart": {"result": [{"timestamp": [1, 2], "meta": {"chartPreviousClose": 99.0},
                                      "indicators": {"quote": [{"open": [100, 101], "high": [102, 103], "low": [99, 100],
                                                                "close": [101, 102], "volume": [5, 6]}]}}]}}
    d = charts.candles("AAPL", "1d", get=get)
    assert d["reference"] == 99.0 and len(d["candles"]) == 2 and d["candles"][1]["c"] == 102


def test_sparklines_downsample():
    pts = [{"t": i, "p": float(i)} for i in range(500)]
    out = charts.sparklines(["AAA", "BAD"], intraday_fn=lambda s, r: {"points": pts, "reference": 1.0} if s == "AAA"
                            else (_ for _ in ()).throw(charts.http.DataUnavailable("x")), n=48)
    assert list(out) == ["AAA"] and len(out["AAA"]["p"]) == 49 and out["AAA"]["p"][-1] == 499.0


def test_history_and_cash_endpoints(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    api.history_cache.store.clear()
    c = TestClient(api.app)
    assert c.get("/api/cash").json() == {"cash": 0.0}
    assert c.post("/api/cash", json={"cash": 1234.5}).json() == {"cash": 1234.5}
    assert c.post("/api/cash", json={"cash": -1}).status_code == 422
    empty = c.get("/api/portfolio/history?range=1m").json()
    assert empty["points"] == [] and empty["cash"] == 1234.5
    monkeypatch.setattr(charts.market, "get_history", hist)
    real = charts.portfolio_history
    monkeypatch.setattr(api.charts, "portfolio_history",
                        lambda txs, r: real(txs, r, today=date(2026, 9, 24), history_fn=hist))
    for t in TXS:
        c.post("/api/transactions", json={k: t[k] for k in ("symbol", "side", "quantity", "price", "date")})
    d = c.get("/api/portfolio/history?range=1m").json()
    assert len(d["points"]) == 24 and d["gain"] is not None
