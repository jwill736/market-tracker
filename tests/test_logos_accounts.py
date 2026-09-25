from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from market_tracker import accounts, api, coinbase_sync, db, logos, snaptrade

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 50


class Resp:
    def __init__(self, status, body=b"", ctype="image/png", js=None):
        self.status_code, self.content, self.headers, self._js = status, body, {"content-type": ctype}, js

    def json(self):
        return self._js


def test_logo_first_source_then_disk_cache():
    calls = []

    def get(url, params=None):
        calls.append(url)
        return Resp(404, b"nope", "text/html") if "financialmodelingprep" in url else Resp(200, PNG)
    data, ctype = logos.logo("aapl", get)
    assert data == PNG and ctype == "image/png"
    assert "financialmodelingprep.com/image-stock/AAPL.png" in calls[0] and "parqet" in calls[1]
    calls.clear()
    assert logos.logo("AAPL", get)[0] == PNG and calls == []          # served from disk


def test_missing_logo_is_a_monogram_and_not_refetched_for_a_week():
    calls = []

    def get(url, params=None):
        calls.append(url)
        return Resp(404, b"", "text/html")
    data, ctype = logos.logo("ZZZQ", get, now=1_000_000)
    assert ctype == "image/svg+xml" and b">Z</text>" in data
    n = len(calls)
    logos.logo("ZZZQ", get, now=1_000_000 + 3600)
    assert len(calls) == n


def test_crypto_logo_falls_back_to_coingecko():
    def get(url, params=None):
        if "spothq" in url:
            return Resp(404, b"", "text/plain")
        if "coingecko.com/api" in url:
            return Resp(200, js={"coins": [{"symbol": "SUIX", "large": "https://x/wrong.png"},
                                           {"symbol": "SUI", "large": "https://coin-images.coingecko.com/sui.png"}]})
        assert url == "https://coin-images.coingecko.com/sui.png"
        return Resp(200, PNG)
    assert logos.logo("SUI-USD", get)[0] == PNG


def test_bad_symbols_never_reach_the_network():
    assert logos.safe("../etc/passwd") is None
    assert logos.logo("../x", lambda *a, **k: 1 / 0)[1] == "image/svg+xml"


def test_names(monkeypatch):
    from market_tracker.providers import sec

    class TM:
        by_ticker = {"AAPL": {"title": "Apple Inc."}, "BRK.B": {"title": "BERKSHIRE HATHAWAY INC"}}
    monkeypatch.setattr(sec, "ticker_map", lambda: TM())
    monkeypatch.setattr(logos.http, "get", lambda url, **k: {"name": "Sui"} if "coinbase" in url else
                        {"chart": {"result": [{"meta": {"longName": "Vanguard S&P 500 ETF"}}]}})
    logos._names.clear()
    got = logos.names(["AAPL", "BRK-B", "SUI", "VOO", "../bad"])
    assert got == {"AAPL": "Apple Inc.", "BRK-B": "Berkshire Hathaway Inc", "SUI-USD": "Sui", "VOO": "Vanguard S&P 500 ETF"}


def _tx(sym, side, q, p, d, acct, key=None):
    return {"symbol": sym, "side": side, "quantity": q, "price": p, "date": d, "account": acct, "import_key": key, "fees": 0}


def test_overview_per_account(monkeypatch):
    monkeypatch.setattr(coinbase_sync, "configured", lambda: False)
    monkeypatch.setattr(snaptrade, "configured", lambda: False)
    txs = [_tx("AAPL", "buy", 10, 100, "2026-01-02", "Robinhood", "rh:1"), _tx("AAPL", "sell", 5, 120, "2026-03-02", "Robinhood", "rh:2"),
           _tx("VOO", "buy", 2, 500, "2026-02-01", "Stash", "hl:stash:1"), _tx("BTC-USD", "buy", 0.1, 60000, "2026-04-01", "Coinbase", "cb:1"),
           _tx("AAPL", "buy", 1, 150, "2026-05-01", "Stash", None)]
    inc = [{"symbol": "AAPL", "day": "2026-08-13", "amount": 1.3, "kind": "dividend", "account": "Robinhood"}]
    got = {a["name"]: a for a in accounts.overview(txs, inc, date(2026, 9, 25))}
    rh = got["Robinhood"]
    assert rh["positions"] == [{"symbol": "AAPL", "quantity": 5, "cost": 500.0}] and rh["income_12m"] == 1.3
    assert rh["sources"] == {"Robinhood CSV": 2} and "import again" in rh["advice"]
    st = got["Stash"]
    assert {p["symbol"] for p in st["positions"]} == {"VOO", "AAPL"} and st["sources"] == {"Typed in": 1, "Added by hand": 1}
    assert "Stash has no export" in st["advice"]
    assert "View-only API key" in got["Coinbase"]["advice"]


def test_auto_sync_runs_when_due_and_records(monkeypatch):
    monkeypatch.setattr(coinbase_sync, "configured", lambda: True)
    monkeypatch.setattr(snaptrade, "configured", lambda: False)
    runs = []
    monkeypatch.setitem(accounts.SYNCS, "coinbase", (lambda: True, lambda: runs.append(1) or {"new": 2, "differences": []}))
    raised = []
    now = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
    assert accounts.auto_sync(now, lambda *a: raised.append(a)) == ["coinbase"]
    assert runs == [1] and "2 new trades" in raised[0][3]
    assert accounts.auto_sync(now + timedelta(hours=1)) == []               # not due yet
    assert accounts.auto_sync(now + timedelta(hours=7)) == ["coinbase"]
    last = accounts.last_sync("coinbase")
    assert last["ok"] and last["new"] == 2

    def boom():
        raise accounts.SyncError(502, "Coinbase: 401")
    monkeypatch.setitem(accounts.SYNCS, "coinbase", (lambda: True, boom))
    accounts.auto_sync(now + timedelta(hours=20))
    assert accounts.last_sync("coinbase") == {"at": (now + timedelta(hours=20)).isoformat(), "ok": False, "new": 0,
                                              "income_new": 0, "differences": 0, "error": "Coinbase: 401"}


def test_endpoints(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    monkeypatch.setattr(logos, "fetch_logo", lambda sym, get=None: (PNG, "image/png") if sym == "NKE" else None)
    c = TestClient(api.app)
    r = c.get("/api/logo/nke")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG
    assert c.get("/api/logo/NOPEX").headers["content-type"].startswith("image/svg+xml")
    c.post("/api/transactions", json={"symbol": "NKE", "side": "buy", "quantity": 3, "price": 80, "date": "2026-02-01", "account": "Robinhood"})
    c.post("/api/transactions", json={"symbol": "NKE", "side": "buy", "quantity": 1, "price": 70, "date": "2026-03-01", "account": "Stash"})
    h = c.get("/api/holdings").json()[0]
    assert h["by_account"] == {"Robinhood": 3, "Stash": 1} and h["accounts"] == ["Robinhood", "Stash"]
    a = c.get("/api/accounts").json()
    assert {x["name"] for x in a["accounts"]} == {"Robinhood", "Stash"} and "coinbase_api" in a["connections"]
    with db.connect() as conn:
        assert db.get_meta(conn, "sync:coinbase", "") == ""
