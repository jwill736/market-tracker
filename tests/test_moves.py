from datetime import datetime, timezone

from market_tracker import moves

NOW = datetime(2026, 9, 26, 18, tzinfo=timezone.utc)
DESK = {"NKE": {"stories": [{"title": "Nike cuts outlook", "event": "guidance_cut", "tier": "A", "confidence": 0.96, "sources": 2,
                             "action": "review", "first": "2026-09-26T08:00:00+00:00", "items": [{"url": "https://x"}]},
                            {"title": "Old story", "event": "other", "tier": "C", "confidence": 0.8, "sources": 1, "action": "ignore",
                             "first": "2026-09-20T08:00:00+00:00", "items": []}]}}


def test_typical_move():
    closes = [100 * (1.01 if i % 2 else 0.99) ** 1 for i in range(80)]
    assert moves.typical_move(closes) > 1.5
    assert moves.typical_move([100, 101]) is None


def test_today_attributes_dollars_and_flags_big_moves_with_reasons():
    pos = [{"symbol": "NKE", "market_value": 9_000, "day_change_pct": -10.0},
           {"symbol": "AAPL", "market_value": 20_000, "day_change_pct": 1.0},
           {"symbol": "TSLA", "market_value": 5_000, "day_change_pct": 8.0}]
    out = moves.today(pos, {"NKE": 1.8, "AAPL": 1.2, "TSLA": 3.5}, DESK, NOW)
    nke = out["rows"][0]
    assert nke["symbol"] == "NKE" and nke["change"] == -1000.0 and nke["big"]
    assert nke["why"]["event"] == "guidance_cut" and "continue" in nke["note"]
    tsla = next(r for r in out["rows"] if r["symbol"] == "TSLA")
    assert tsla["big"] and tsla["why"] is None and "reverse" in tsla["note"]
    assert not next(r for r in out["rows"] if r["symbol"] == "AAPL")["big"]
    assert out["headline"].startswith("Down $") and "NKE took off $1,000" in out["headline"]
    title, body = moves.push_text(nke)
    assert title.startswith("NKE down 10.0% (-$1,000), 5.6× its usual move") and "Nike cuts outlook" in body
