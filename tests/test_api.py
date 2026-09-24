import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from market_tracker import api, research, service
from market_tracker.providers import market
from tests.conftest import synthetic_closes


@pytest.fixture
def fake_market(monkeypatch):
    closes = synthetic_closes(900, seed=5)
    from datetime import date, timedelta
    start = date(2023, 1, 1)
    bars = [market.PriceBar((start + timedelta(days=i)).isoformat(), c) for i, c in enumerate(closes)]

    def quote(sym):
        sym = market.normalize_symbol(sym)
        return market.Quote(sym, market.asset_class(sym), closes[-1], closes[-2],
                            (closes[-1] / closes[-2] - 1) * 100, "USD", "test", "2025-06-19T00:00:00+00:00")

    monkeypatch.setattr(market, "get_quote", quote)
    monkeypatch.setattr(market, "get_history", lambda sym, days=400: bars[-days:])
    monkeypatch.setattr(service.news, "get_news", lambda sym, company=None: {
        "count": 0, "avg_sentiment": 0.0, "positive": 0, "negative": 0, "top_terms": [], "articles": [], "errors": []})
    return closes


@pytest.fixture
def client():
    return TestClient(api.app)


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Market Tracker" in r.text
    assert client.get("/static/app.js").status_code == 200


def test_analyze_crypto_without_sec(client, fake_market):
    r = client.get("/api/analyze/btc?smart_money=false&insiders=false")
    assert r.status_code == 200
    a = r.json()
    assert a["symbol"] == "BTC-USD" and a["asset_class"] == "crypto"
    assert a["signal"]["components"]["trend"] is not None
    assert a["forecast"]["lognormal"][0]["horizon_days"] == 7  # crypto uses calendar horizons
    assert a["backtest"]["results"]["buy_hold"] is not None


def test_transactions_and_portfolio(client, fake_market):
    assert client.post("/api/transactions", json={"symbol": "aapl", "side": "buy", "quantity": 10,
                                                  "price": 50, "date": "2026-01-02"}).status_code == 201
    bad = client.post("/api/transactions", json={"symbol": "AAPL", "side": "sell", "quantity": 99,
                                                 "price": 50, "date": "2026-02-02"})
    assert bad.status_code == 400 and "exceeds" in bad.json()["detail"]
    assert client.post("/api/transactions", json={"symbol": "AAPL", "side": "buy", "quantity": -1,
                                                  "price": 50}).status_code == 422
    p = client.get("/api/portfolio").json()
    assert p["positions"][0]["symbol"] == "AAPL"
    assert p["total_value"] == pytest.approx(10 * fake_market[-1])
    txs = client.get("/api/transactions").json()
    assert client.delete(f"/api/transactions/{txs[0]['id']}").status_code == 200
    assert client.delete("/api/transactions/999").status_code == 404


def test_watchlist(client):
    assert client.post("/api/watchlist/eth").json() == ["ETH-USD"]
    assert client.delete("/api/watchlist/ETH-USD").json() == []


def test_unknown_investor_404(client):
    assert client.get("/api/investors/nobody").status_code == 404
    assert len(client.get("/api/investors").json()) >= 10


# ------------------------------------------------------------------ research (Claude mocked)

class FakeStream:
    def __init__(self, events, final):
        self.events, self.final = events, final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self):
        return self.final


def _text_event(t):
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=t))


class FakeClient:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []
        outer = self

        class _Messages:
            def stream(self, **kw):
                outer.calls.append(kw)
                return outer.turns.pop(0)

            def parse(self, **kw):
                outer.calls.append(kw)
                return SimpleNamespace(stop_reason="end_turn", parsed_output=research.Verdict(
                    rating="Hold", conviction="Medium", horizon="6-12 months", thesis="t", catalysts=["c"],
                    risks=["r"], invalidation="i", max_position_pct=4.0))

        self.beta = SimpleNamespace(messages=_Messages())


def test_stream_memo_resumes_after_pause_turn_and_sends_fallbacks():
    search_start = SimpleNamespace(type="content_block_start",
                                   content_block=SimpleNamespace(type="server_tool_use", name="web_search"))
    paused = FakeStream([search_start, _text_event("Part 1. ")],
                        SimpleNamespace(stop_reason="pause_turn", content=["block"]))
    done = FakeStream([_text_event("Part 2.")], SimpleNamespace(stop_reason="end_turn", content=[]))
    fake = FakeClient([paused, done])
    events = list(research.stream_memo({"symbol": "AAPL", "asset_class": "stock"}, "Is it cheap?", client=fake))
    assert events[0] == {"type": "status", "text": "Using web_search…"}
    assert events[-1] == {"type": "done", "memo": "Part 1. Part 2."}
    first, second = fake.calls
    assert first["fallbacks"] == "default" and research.FALLBACK_BETA in first["betas"]
    assert "Is it cheap?" in first["messages"][0]["content"]
    assert second["messages"][-1] == {"role": "assistant", "content": ["block"]}
    v = research.extract_verdict("AAPL", "memo", client=fake)
    assert v.rating == "Hold"


def test_stream_memo_reports_refusal():
    fake = FakeClient([FakeStream([], SimpleNamespace(stop_reason="refusal", content=[]))])
    events = list(research.stream_memo({"symbol": "X", "asset_class": "stock"}, client=fake))
    assert events[-1]["type"] == "error"


def test_research_endpoint_streams_sse(client, fake_market, monkeypatch):
    fake = FakeClient([FakeStream([_text_event("Memo body")], SimpleNamespace(stop_reason="end_turn", content=[]))])
    monkeypatch.setattr(research, "_client", lambda: fake)
    monkeypatch.setattr(service, "smart_money_reports", lambda refresh=False: ([], []))
    monkeypatch.setattr(service.sec, "get_insider_trades", lambda sym, days=180: [])
    with client.stream("GET", "/api/research/AAPL") as r:
        body = "".join(r.iter_text())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert types[0] == "status" and "text" in types and types[-1] == "end"
    verdict = next(e for e in events if e["type"] == "verdict")
    assert verdict["verdict"]["rating"] == "Hold"


def test_journal_endpoints(client, fake_market, tmp_path, monkeypatch):
    monkeypatch.setenv("MT_JOURNAL_PATH", str(tmp_path / "j.csv"))
    assert client.get("/api/journal/report").json()["entries"] == 0
    client.post("/api/watchlist/AAPL")
    r = client.post("/api/journal/record").json()
    assert r["recorded"] == 1 and r["skipped"] == []
    rep = client.get("/api/journal/report").json()
    assert rep["entries"] == 1 and rep["verdict"].startswith("Too early")


def test_verify_investor(monkeypatch):
    from market_tracker.investors import by_key
    from market_tracker.providers import sec
    monkeypatch.setattr(sec, "_sec_get", lambda url, ttl=0, as_json=True: {"name": "BERKSHIRE HATHAWAY INC"})
    assert sec.verify_investor(by_key("buffett")) == ("BERKSHIRE HATHAWAY INC", True)
    assert sec.verify_investor(by_key("ackman"))[1] is False
