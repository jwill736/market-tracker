import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from market_tracker import api, auth, importers, livefeed
from market_tracker.providers import market

# ------------------------------------------------------------------ login


@pytest.fixture
def locked(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "correct horse")
    monkeypatch.delenv("APP_SECRET", raising=False)
    monkeypatch.setattr(api, "throttle", auth.Throttle(limit=3))
    return TestClient(api.app)


def test_tokens_expire_and_resist_tampering(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "pw")
    tok = auth.make_token(now=1_000_000)
    assert auth.valid_token(tok, now=1_000_001)
    assert not auth.valid_token(tok, now=1_000_000 + 31 * 86400)          # expired
    exp, sig = tok.split(".")
    assert not auth.valid_token(f"{int(exp) + 999}.{sig}", now=1_000_001)  # extended by hand
    assert not auth.valid_token("garbage", now=1) and not auth.valid_token(None)
    monkeypatch.setenv("APP_PASSWORD", "changed")
    assert not auth.valid_token(tok, now=1_000_001)                        # new password signs everyone out


def test_pages_and_api_need_login(locked):
    r = locked.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert locked.get("/api/watchlist").status_code == 401
    assert locked.get("/healthz").json() == {"ok": True}
    assert "Sign in" in locked.get("/login").text


def test_phone_app_files_are_open_but_data_is_not(locked):
    sw = locked.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["content-type"].startswith("application/javascript")
    assert "/api/" in sw.text and "never stored" in sw.text
    man = locked.get("/static/manifest.webmanifest")
    assert man.status_code == 200 and man.json()["display"] == "standalone"
    for icon in man.json()["icons"]:
        r = locked.get(icon["src"])
        assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert locked.get("/static/app.js", follow_redirects=False).status_code == 303      # the app itself still needs login


def test_login_flow_and_throttle(locked):
    assert locked.post("/login", content="password=wrong",
                       headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 401
    r = locked.post("/login", content="password=correct+horse", follow_redirects=False,
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 303 and auth.COOKIE in r.cookies
    assert locked.get("/api/watchlist").status_code == 200
    assert locked.get("/api/session").json() == {"auth": True}
    locked.get("/logout")
    locked.cookies.clear()
    for _ in range(3):
        locked.post("/login", content="password=nope", headers={"content-type": "application/x-www-form-urlencoded"})
    blocked = locked.post("/login", content="password=correct+horse",
                          headers={"content-type": "application/x-www-form-urlencoded"})
    assert blocked.status_code == 429     # even the right password waits once the limit is hit


def test_hosted_without_password_refuses_to_serve(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("REQUIRE_LOGIN", "1")
    c = TestClient(api.app)
    assert c.get("/api/watchlist").status_code == 503 and c.get("/").status_code == 503
    assert c.get("/healthz").status_code == 200


def test_local_use_needs_no_login(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    c = TestClient(api.app)
    assert c.get("/api/watchlist").status_code == 200 and c.get("/api/session").json() == {"auth": False}


# ------------------------------------------------------------------ live price hub

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_hub_subscriptions_and_fanout():
    clock = Clock()
    hub = livefeed.PriceHub(min_gap=0.25, clock=clock)
    a = hub.subscribe(["btc", "AAPL"])
    b = hub.subscribe(["AAPL"])
    assert a.symbols == {"BTC-USD", "AAPL"} and hub.crypto() == {"BTC-USD"} and hub.stocks() == {"AAPL"}
    v = hub.version
    assert hub.publish(livefeed.Tick("AAPL", 200.0, 1.0, "t", "test"))
    assert a.queue.qsize() == 1 and b.queue.qsize() == 1
    assert not hub.publish(livefeed.Tick("AAPL", 201.0, 1.5, "t", "test"))   # within MIN_GAP: stored, not pushed
    assert hub.latest["AAPL"].price == 201.0 and a.queue.qsize() == 1
    clock.t = 1.0
    hub.publish(livefeed.Tick("BTC-USD", 90_000.0, 0.5, "t", "test"))
    assert a.queue.qsize() == 2 and b.queue.qsize() == 1                    # b doesn't watch BTC
    hub.unsubscribe(b)
    assert hub.version == v and "AAPL" in hub.wanted                        # a still wants AAPL
    hub.unsubscribe(a)
    assert hub.version > v and not hub.wanted


def test_parse_coinbase_and_finnhub():
    t = livefeed.parse_coinbase({"type": "ticker", "product_id": "BTC-USD", "price": "101000.5", "open_24h": "100000",
                                 "time": "2026-09-25T12:00:00Z"})
    assert t.symbol == "BTC-USD" and t.price == 101000.5 and abs(t.change_pct - 1.0005) < 1e-9
    assert livefeed.parse_coinbase({"type": "subscriptions"}) is None
    hub = livefeed.PriceHub()
    hub.prev_close["AAPL"] = 200.0
    ticks = livefeed.parse_finnhub({"type": "trade", "data": [
        {"s": "AAPL", "p": 201.0, "t": 1_790_000_000_000, "v": 10},
        {"s": "AAPL", "p": 202.0, "t": 1_790_000_000_500, "v": 5},
        {"s": "MSFT", "p": 500.0, "t": 1_790_000_000_600, "v": 1}]}, hub)
    by = {t.symbol: t for t in ticks}
    assert by["AAPL"].price == 202.0 and abs(by["AAPL"].change_pct - 1.0) < 1e-9   # last trade in the batch
    assert by["MSFT"].change_pct is None                                          # no previous close yet
    assert livefeed.parse_finnhub({"type": "ping"}, hub) == []


class FakeWS:
    def __init__(self, messages, hub, then_add=None):
        self.messages, self.sent, self.hub, self.then_add = list(messages), [], hub, then_add

    async def send(self, m):
        self.sent.append(json.loads(m))

    async def recv(self):
        if self.then_add and not self.messages:
            self.hub.subscribe(self.then_add)
            self.then_add = None
            raise asyncio.TimeoutError
        if not self.messages:
            raise ConnectionError("closed")
        return json.dumps(self.messages.pop(0))

    async def close(self):
        pass


def test_coinbase_session_subscribes_and_publishes():
    hub = livefeed.PriceHub(min_gap=0)
    client = hub.subscribe(["BTC-USD", "AAPL"])
    ws = FakeWS([{"type": "ticker", "product_id": "BTC-USD", "price": "100", "open_24h": "99"}], hub, then_add=["ETH"])

    async def connect(url):
        return ws

    with pytest.raises(ConnectionError):
        asyncio.run(livefeed.coinbase_session(hub, connect=connect))
    assert ws.sent[0] == {"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]}  # crypto only
    assert ws.sent[1] == {"type": "subscribe", "product_ids": ["ETH-USD"], "channels": ["ticker"]}  # added later
    assert client.queue.get_nowait().price == 100.0


def test_poll_stocks_publishes_quotes():
    hub = livefeed.PriceHub(min_gap=0)
    client = hub.subscribe(["AAPL", "BTC-USD"])
    calls = []

    def quote(sym):
        calls.append(sym)
        return market.Quote(sym, "stock", 210.0, 200.0, 5.0, "USD", "test", "t")

    async def stop(_):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(livefeed.poll_stocks(hub, quote_fn=quote, sleep=stop))
    assert calls == ["AAPL"] and hub.prev_close["AAPL"] == 200.0 and client.queue.get_nowait().price == 210.0


def test_live_stream_sends_a_snapshot(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    monkeypatch.setattr(api, "_safe_quote", lambda s: {"symbol": s, "price": 123.0, "change_pct": 1.0,
                                                        "previous_close": 121.8, "as_of": "t", "source": "test"})
    monkeypatch.setattr(livefeed.hub, "start", lambda: None)
    with TestClient(api.app) as c, c.stream("GET", "/api/stream/live?symbols=AAPL,btc&snapshot_only=true") as r:
        lines = []
        for line in r.iter_lines():
            lines.append(line)
            if line.startswith("data:"):
                break
    assert lines[0] == "event: snapshot"
    snap = json.loads(lines[1][5:])
    assert {x["symbol"] for x in snap} == {"AAPL", "BTC-USD"} and snap[0]["price"] == 123.0


# ------------------------------------------------------------------ Robinhood import

RH = '''"Activity Date","Process Date","Settle Date","Instrument","Description","Trans Code","Quantity","Price","Amount"
"9/2/2026","9/2/2026","9/3/2026","AAPL","Apple Common Stock","Buy","10","$200.00","($2,000.00)"
"9/5/2026","9/5/2026","9/8/2026","AAPL","Apple Common Stock","Sell","4","$210.00","$839.98"
"9/10/2026","9/10/2026","9/10/2026","AAPL","Cash Div: R/D 2026-09-05 P/D 2026-09-10 - 6 shares at 0.26","CDIV","","","$1.56"
"9/12/2026","9/12/2026","9/12/2026","NVDA","NVIDIA Corp","Buy","2","$150.00","($300.00)"
"9/15/2026","9/15/2026","9/15/2026","NVDA","Forward Split","SPL","18S","",""
"9/16/2026","9/16/2026","9/16/2026","","ACH Deposit","ACH","","","$1,000.00"
"","","","","","","","",""
"The data provided is for informational purposes only."
'''


def test_parse_robinhood():
    res = importers.parse_robinhood(RH)
    txs = res.transactions
    assert [(t["symbol"], t["side"], t["quantity"], t["price"]) for t in txs] == [
        ("AAPL", "buy", 10, 200.0), ("AAPL", "sell", 4, 210.0), ("NVDA", "buy", 2, 150.0), ("NVDA", "buy", 18, 0.0)]
    assert txs[0]["date"] == "2026-09-02" and txs[1]["fees"] == 0.02     # regulatory fee on the sale
    assert dict(res.skipped) == {"ACH": 1} and not res.errors
    assert [(r["symbol"], r["day"], r["amount"], r["kind"]) for r in res.income] == [("AAPL", "2026-09-10", 1.56, "dividend")]
    assert len({t["import_key"] for t in txs}) == 4
    assert importers.parse_robinhood(RH).transactions[0]["import_key"] == txs[0]["import_key"]   # stable
    assert importers.parse_robinhood("a,b\n1,2").errors


def test_import_endpoint_previews_commits_and_dedupes(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    c = TestClient(api.app)
    preview = c.post("/api/import/robinhood", json={"csv": RH}).json()
    assert preview["new"] == 4 and not preview["committed"] and c.get("/api/transactions").json() == []
    assert {p["symbol"]: p["quantity"] for p in preview["positions"]} == {"AAPL": 6, "NVDA": 20}
    assert c.post("/api/import/robinhood", json={"csv": RH, "commit": True}).json()["new"] == 4
    again = c.post("/api/import/robinhood", json={"csv": RH, "commit": True}).json()
    assert again["new"] == 0 and again["duplicates"] == 4 and len(c.get("/api/transactions").json()) == 4
    assert preview["income_new"] == 1 and again["income_new"] == 0      # the dividend is kept once
    nvda = next(h for h in c.get("/api/holdings").json() if h["symbol"] == "NVDA")
    assert nvda["quantity"] == 20 and nvda["avg_cost"] == 15.0      # split: same cost, ten times the shares
    partial = RH.splitlines()[0] + '\n"9/20/2026","","","TSLA","Tesla","Sell","3","$300.00","$900.00"\n'
    r = c.post("/api/import/robinhood", json={"csv": partial})
    assert r.status_code == 400 and "full history" in r.json()["detail"]
