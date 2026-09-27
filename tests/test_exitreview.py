from datetime import date

from market_tracker import exitreview, taxes


def test_review_gain_needs_an_edge_and_waiting_saves():
    lots = [taxes.Lot("LOSR", "2024-01-10", 100, 50.0), taxes.Lot("LOSR", "2026-08-01", 50, 90.0)]
    r = exitreview.review("LOSR", lots, 100.0, date(2026, 9, 28), st_rate=0.24, lt_rate=0.15, reasons=["bottom 50"])
    # 100 shares +$5,000 long-term (15%) = $750; 50 shares +$500 short-term (24%) = $120
    assert r["value"] == 15000 and r["tax_now"] == 870
    assert 1.8 < r["need_edge_pct_per_year"]["3y"] < 2.1 and r["need_edge_pct_per_year"]["5y"] < r["need_edge_pct_per_year"]["3y"]
    assert "costs about $870 in tax now" in r["text"] and r["reasons"] == ["bottom 50"]


def test_review_loss_is_a_free_switch_and_sorting():
    lots = [taxes.Lot("DOWN", "2025-01-10", 10, 200.0)]
    r = exitreview.review("DOWN", lots, 100.0, date(2026, 9, 28), 0.24, 0.15)
    assert r["tax_now"] < 0 and "costs nothing in tax" in r["text"] and r["need_edge_pct_per_year"] == {}
    txs = [{"id": 1, "date": "2025-01-10", "symbol": "DOWN", "side": "buy", "quantity": 10, "price": 200.0, "fee": 0, "account": "a"},
           {"id": 2, "date": "2024-01-10", "symbol": "UP", "side": "buy", "quantity": 10, "price": 50.0, "fee": 0, "account": "a"}]
    out = exitreview.for_holdings({"DOWN": ["x"], "UP": ["y"], "NONE": ["z"]}, txs, {"DOWN": 100.0, "UP": 100.0}, date(2026, 9, 28))
    assert [o["symbol"] for o in out] == ["DOWN", "UP"]                   # tax-free switches first
