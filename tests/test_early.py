from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from market_tracker import api, early, radar, sentinel
from market_tracker.providers import market

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

# Shapes copied from the live APIs (2026-09-25), trimmed.
STOCKTWITS = {"symbols": [
    {"symbol": "AMD", "title": "Advanced Micro Devices Inc", "instrument_class": "Stock", "trending_score": 8.41,
     "trends": {"summary": "Meta's Muse AI-agent launch has fueled expectations that agentic workloads could lift demand for server CPUs."}},
    {"symbol": "ZEC.X", "title": "Zcash", "instrument_class": "Crypto", "trending_score": 3.0, "trends": {}}]}
APE = {"results": [
    {"rank": 4, "ticker": "NBIS", "name": "Nebius Group", "mentions": 87, "rank_24h_ago": 18, "mentions_24h_ago": 27},
    {"rank": 2, "ticker": "SPY", "name": "SPDR S&amp;P 500 ETF Trust", "mentions": 256, "rank_24h_ago": 1, "mentions_24h_ago": 225},
    {"rank": 90, "ticker": "TINY", "name": "Tiny", "mentions": 3, "rank_24h_ago": 400, "mentions_24h_ago": 0}]}
GECKO = {"coins": [{"item": {"id": "ondo-finance", "name": "Ondo", "symbol": "ONDO", "score": 0,
                             "data": {"price_change_percentage_24h": {"usd": 30.2}}}}]}
BINANCE = {"data": {"catalogs": [{"articles": [
    {"code": "abc", "title": "Binance Will List Hyperliquid (HYPE) with Seed Tag Applied", "releaseDate": NOW.timestamp() * 1000 - 3600e3},
    {"code": "old", "title": "Binance Will List Oldcoin (OLD)", "releaseDate": (NOW - timedelta(days=9)).timestamp() * 1000},
    {"code": "x", "title": "Binance Futures Will Launch USDⓈ-M HYPE Perpetual", "releaseDate": NOW.timestamp() * 1000}]}]}}


def test_social_sources():
    st = early.stocktwits_signals(STOCKTWITS)
    assert [s.symbol for s in st] == ["AMD", "ZEC-USD"] and "server CPUs" in st[0].headline
    ape = early.apewisdom_signals(APE)
    assert [s.symbol for s in ape] == ["NBIS"]                          # SPY busy but flat; TINY too few mentions
    assert "up from 27" in ape[0].headline and "rank 18 → 4" in ape[0].headline


def test_press_release_classification_and_tickers():
    items = [
        {"title": "Acme Robotics Enters Into Definitive Agreement to Be Acquired by Big Co for $2.1 Billion",
         "summary": "SAN JOSE -- Acme Robotics, Inc. (NASDAQ: ACME) today announced...", "url": "https://gnw/1", "published": "t"},
        {"title": "BioCo Announces Pricing of $50 Million Public Offering", "summary": "BioCo (NYSE American: BIOC)",
         "url": "https://gnw/2", "published": "t"},
        {"title": "Company hosts webinar", "summary": "(NASDAQ: WEB)", "url": "u", "published": "t"},
        {"title": "FDA Approves Drug X", "summary": "no ticker here", "url": "u", "published": "t"}]
    sigs = early.wire_signals(items, "GlobeNewswire")
    assert [(s.symbol, s.tone, s.headline.split(":")[0]) for s in sigs] == [("ACME", 1, "Deal"), ("BIOC", -1, "Offering")]
    named = early.wire_signals(items[3:], "Wire", name_to_ticker=lambda title: "DRUG")
    assert named[0].symbol == "DRUG" and named[0].headline.startswith("FDA / trial")


def test_crypto_sources_and_depegs():
    assert early.coingecko_signals(GECKO)[0].symbol == "ONDO-USD"
    b = early.binance_signals(BINANCE, NOW)
    assert [s.symbol for s in b] == ["HYPE-USD"] and b[0].kind == "listing"
    products = [{"id": "BTC-USD", "quote_currency": "USD", "status": "online"},
                {"id": "NEW-USD", "quote_currency": "USD", "status": "online"},
                {"id": "OLD-USD", "quote_currency": "USD", "status": "delisted"}]
    first, known = early.coinbase_new_pairs(products, set())
    assert first == [] and known == {"BTC-USD", "NEW-USD"}             # first check only remembers
    new, _ = early.coinbase_new_pairs(products + [{"id": "HOT-USD", "quote_currency": "USD", "status": "online"}], known)
    assert [s.symbol for s in new] == ["HOT-USD"]
    quotes = {"USDT-USD": 0.9931, "DAI-USD": 1.0002, "PYUSD-USD": 1.0}
    dep = early.depeg_signals(lambda s: market.Quote(s, "crypto", quotes[s], 1.0, 0.0, "USD", "t", "t"))
    assert [s.symbol for s in dep] == ["USDT-USD"] and dep[0].strength == 70


def test_filing_catalysts_use_8k_items():
    entries = [radar.FeedEntry("a-1", "8-K", "0000000001", "Acme", "Filer", "2026-09-24T16:00:00-04:00", "u", ["1.01", "9.01"]),
               radar.FeedEntry("a-2", "8-K", "0000000002", "NoTicker", "Filer", "t", "u", ["2.01"]),
               radar.FeedEntry("a-3", "8-K", "0000000001", "Acme", "Filer", "t", "u", ["2.02"])]
    got = early.filing_signals(entries, {"0000000001": "ACME"})
    assert [(s.symbol, s.headline.split(" (")[0]) for s in got] == [("ACME", "Material agreement signed")]


def test_merge_rewards_independent_evidence_and_mainstream_check():
    sigs = [early.Signal("NBIS", "social", "reddit", 30), early.Signal("NBIS", "wire", "deal", 40, tone=1),
            early.Signal("AMD", "social", "stocktwits", 35), early.Signal("AMD", "social", "reddit", 30)]
    merged = early.merge(sigs, mine={"AMD"})
    by = {m.symbol: m for m in merged}
    assert by["NBIS"].strength == 40 + 15 + 10 and by["NBIS"].kinds == ["social", "wire"] and by["NBIS"].tone == 1
    assert by["AMD"].strength == 35 + 15 and by["AMD"].yours
    arts = {"NBIS": [{"source": "Seeking Alpha", "published": (NOW - timedelta(hours=2)).isoformat()}],
            "AMD": [{"source": "Reuters", "published": (NOW - timedelta(hours=h)).isoformat()} for h in (1, 2, 3)]}
    early.check_mainstream(merged, NOW, news_fn=lambda s, c, d: {"articles": arts[s]})
    assert by["NBIS"].early and by["NBIS"].mainstream_24h == 0
    assert not by["AMD"].early and by["AMD"].mainstream_24h == 3


def test_gather_survives_dead_sources():
    from market_tracker import http

    def get(url, **kw):
        if "stocktwits" in url:
            return STOCKTWITS
        if "coingecko" in url:
            return GECKO
        raise http.DataUnavailable("blocked")

    sigs, errors, pairs = early.gather(get=get, now=NOW, quote_fn=lambda s: (_ for _ in ()).throw(http.DataUnavailable("x")),
                                       tickers_fn=lambda: {})
    assert {s.symbol for s in sigs} == {"AMD", "ZEC-USD", "ONDO-USD"}
    assert any(e.startswith("Reddit stocks") for e in errors) and pairs == set()


def test_early_endpoint_logs_first_sighting_once(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    sentinel.early_cache.clear()
    fake = [early.Signal("NBIS", "wire", "Deal: Nebius to acquire X", 60, "u", source="GlobeNewswire", tone=1)]
    monkeypatch.setattr(early, "gather", lambda **kw: (fake, [], {"BTC-USD"}))
    monkeypatch.setattr(early.news, "get_news", lambda s, c, d: {"articles": []})
    monkeypatch.setattr(market, "get_quote", lambda s: market.Quote(s, "stock", 101.0, 100.0, 1.0, "USD", "t", "t"))
    sent = []
    monkeypatch.setattr(sentinel.notify, "send", lambda m: sent.append(m) or True)
    c = TestClient(api.app)
    c.post("/api/watchlist/NBIS")
    d = c.get("/api/early").json()
    s = d["signals"][0]
    assert s["symbol"] == "NBIS" and s["early"] and s["yours"] and d["history"][0]["price"] == 101.0
    sentinel.early_cache.clear()
    assert len(c.get("/api/early").json()["history"]) == 1               # logged once a day
    assert sentinel.early_headsups(d) == 1 and sentinel.early_headsups(d) == 0
    assert "not in the mainstream yet" in sent[0].title


def test_gather_skips_a_source_that_hangs():
    import threading
    import time

    from market_tracker import http
    release = threading.Event()

    def get(url, **kw):
        if "globenewswire" in url:
            release.wait(5)                     # a wire that never answers in time
            raise http.DataUnavailable("late")
        if "stocktwits" in url:
            return STOCKTWITS
        raise http.DataUnavailable("blocked")

    t0 = time.monotonic()
    sigs, errors, _ = early.gather(get=get, now=NOW, quote_fn=lambda s: (_ for _ in ()).throw(http.DataUnavailable("x")),
                                   tickers_fn=lambda: {}, deadline=0.5)
    release.set()
    assert time.monotonic() - t0 < 3 and "AMD" in {s.symbol for s in sigs}
    assert "GlobeNewswire: slow to answer, skipped this round" in errors


def test_scorecard_scores_against_spy_over_the_same_days():
    from market_tracker.providers import market as mk
    days = [f"2026-09-{d:02d}" for d in (1, 2, 3, 4, 7, 8, 9, 10)]          # trading days
    closes = {"AAA": [10, 10, 11, 11, 12, 12, 12, 13], "BBB": [20, 20, 19, 18, 18, 17, 17, 17],
              "SPY": [100, 100, 101, 101, 102, 102, 103, 103]}

    def hist(sym, n):
        return [mk.PriceBar(d, float(c)) for d, c in zip(days, closes[sym])]
    logged = [{"day": "2026-09-01", "symbol": "AAA", "kinds": "wire,social", "early": 1, "price": 10.0},
              {"day": "2026-09-02", "symbol": "BBB", "kinds": "social", "early": 0, "price": 20.0},
              {"day": "2026-09-09", "symbol": "AAA", "kinds": "wire", "early": 1, "price": 12.0}]
    s = early.scorecard(logged, horizon=5, history_fn=hist)
    # AAA 1 Sep -> 5 closes later (8 Sep, 12): +20%; SPY 100 -> 102: +2%; excess +18
    # BBB 2 Sep -> 9 Sep (17): -15%; SPY 100 -> 103: +3%; excess -18
    by = {(r["symbol"], r["day"]): r for r in s["rows"]}
    assert by[("AAA", "2026-09-01")]["excess_pct"] == pytest.approx(18.0)
    assert by[("BBB", "2026-09-02")]["excess_pct"] == pytest.approx(-18.0)
    assert s["pending"] == 1 and s["all"]["count"] == 2 and s["all"]["hit_rate"] == 0.5
    assert s["early"]["count"] == 1 and s["early"]["median_excess_pct"] == pytest.approx(18.0)
    assert s["by_kind"]["social"]["count"] == 2 and s["by_kind"]["wire"]["count"] == 1


def test_scorecard_endpoint(monkeypatch):
    from market_tracker import db
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    monkeypatch.setattr(early, "scorecard", lambda logged, horizon: {"horizon": horizon, "n": len(logged)})
    with db.connect() as conn:
        db.log_early(conn, "2026-09-01", "ZZZ", "wire", 50, True, 5.0, "x")
    r = TestClient(api.app).get("/api/early/scorecard", params={"horizon": 20}).json()
    assert r["horizon"] == 20 and r["n"] >= 1
