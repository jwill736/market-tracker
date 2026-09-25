
import pytest
from fastapi.testclient import TestClient

from market_tracker import api, db, pickers
from market_tracker.providers import market


def msg(i, sym, side, price, created, user="ripster47"):
    return {"id": i, "body": f"${sym} {side}", "created_at": created, "user": {"username": user, "followers": 9589},
            "entities": {"sentiment": {"basic": side} if side else None}, "symbols": [{"symbol": sym}],
            "prices": [{"symbol": sym, "price": str(price)}]}


PAGE = {"user": {"username": "ripster47", "name": "Ripster", "followers": 9589}, "cursor": {"more": False},
        "messages": [msg(3, "AAA", "Bullish", 10, "2026-09-01T15:00:00Z"),
                     msg(2, "BBB", "Bearish", 50, "2026-09-01T16:00:00Z"),
                     msg(1, "CCC", None, 5, "2026-09-01T17:00:00Z"),                       # untagged: not a call
                     msg(4, "BTC.X", "Bullish", 90000, "2026-09-02T12:00:00Z")]}
DAYS = [f"2026-09-{d:02d}" for d in (1, 2, 3, 4, 8, 9, 10, 11, 14)]


def hist(sym, n):
    closes = {"AAA": [10, 10, 11, 11, 12, 12, 12, 12, 12], "BBB": [50, 49, 48, 46, 45, 44, 44, 44, 44],
              "SPY": [100, 100, 100, 101, 101, 101, 101, 101, 101],
              "BTC-USD": [90000, 90000, 89000, 88000, 87000, 86000, 85000, 85000, 85000]}[sym]
    return [market.PriceBar(d, float(c)) for d, c in zip(DAYS, closes)]


def test_calls_and_score():
    calls = pickers.calls_from_stream(PAGE)
    assert [(c["symbol"], c["side"], c["price"]) for c in calls] == [("AAA", "bullish", 10.0), ("BBB", "bearish", 50.0),
                                                                       ("BTC-USD", "bullish", 90000.0)]
    s = pickers.score(calls, history_fn=hist)
    by = {c["symbol"]: c for c in s["calls"]}
    # AAA +20% vs SPY +1% over 5 closes: right. BBB fell 12% vs SPY +1%: a bearish call, right. BTC fell: wrong.
    assert by["AAA"]["right"] and by["AAA"]["excess_pct"] == pytest.approx(19.0)
    assert by["BBB"]["right"] and by["BBB"]["excess_pct"] == pytest.approx(13.0)
    assert by["BTC-USD"]["right"] is False
    assert s["scored"] == 3 and s["hit_rate"] == pytest.approx(2 / 3) and s["median_excess_pct"] == pytest.approx(13.0)


def test_follow_and_deleted_posts_still_count(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    api.pickers_cache.store.clear()
    pages = {"now": PAGE}

    def get(url, params=None, **kw):
        if "suggested" in url:
            return {"messages": [msg(9, "AAA", "Bullish", 1, "2026-09-01T00:00:00Z", user="alphatrends")]}
        return pages["now"]
    monkeypatch.setattr(pickers.http, "get", get)
    monkeypatch.setattr(pickers.early.market, "get_history", hist)
    monkeypatch.setattr(market, "get_quote", lambda s: market.Quote(s, "stock", 11.0, 10.0, 1.0, "USD", "t", "t"))
    c = TestClient(api.app)
    assert c.post("/api/pickers/follow", json={"username": "Ripster47"}).json() == {"following": ["ripster47"]}
    assert c.post("/api/pickers/follow", json={"username": "bad name!"}).status_code == 422
    v = c.get("/api/pickers").json()
    assert v["following"][0]["username"] == "ripster47" and v["following"][0]["scored"] == 3
    assert v["suggested"][0]["username"] == "alphatrends"
    pages["now"] = {"user": PAGE["user"], "cursor": {"more": False}, "messages": []}      # the author deletes everything
    api.pickers_cache.store.clear()
    assert c.get("/api/pickers").json()["following"][0]["scored"] == 3
    with db.connect() as conn:
        assert "st:ripster47" not in db.follows(conn) and "st:ripster47" in db.follows(conn, include_pickers=True)
    assert c.delete("/api/pickers/follow", params={"username": "ripster47"}).json() == {"following": []}
