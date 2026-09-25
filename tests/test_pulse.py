from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from market_tracker import alerts, api, dilution, http, pulse
from market_tracker.providers import market

NOW = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def screen(*rows):
    return {"finance": {"result": [{"quotes": [
        {"symbol": s, "shortName": s + " Inc", "regularMarketPrice": p, "regularMarketChangePercent": c,
         "regularMarketVolume": v, "averageDailyVolume3Month": a} for s, p, c, v, a in rows]}]}}


def article(title, source, hours_ago):
    ts = NOW.timestamp() - hours_ago * 3600
    return {"title": title, "source": source, "url": "https://example.com/" + title.replace(" ", "-"),
            "published": datetime.fromtimestamp(ts, timezone.utc).isoformat(), "sentiment": 0.0}


def news_data(arts, sentiment=0.1):
    return {"count": len(arts), "avg_sentiment": sentiment, "articles": arts}


def quote(sym, price=100.0, chg=1.0):
    return market.Quote(sym, market.asset_class(sym), price, price / (1 + chg / 100), chg, "USD", "test", "t")


# ------------------------------------------------------------------ movers and attention

def test_parse_screen_reads_relative_volume():
    ms = pulse.parse_screen(screen(("ABC", 10.0, 12.5, 3_000_000, 1_000_000), ("XYZ", 5.0, 8.0, None, 1)), "gainers")
    assert [m.symbol for m in ms] == ["ABC", "XYZ"] and ms[0].rel_volume == 3.0 and ms[1].rel_volume is None
    assert ms[0].name == "ABC Inc" and ms[0].lists == ["gainers"]
    assert pulse.parse_screen({"finance": {"result": []}}, "gainers") == []


def test_attention_and_tags():
    arts = [article(f"story {i}", "Yahoo", i) for i in range(6)] + [article("the long read", "Reuters", 100)]
    a = pulse.attention(news_data(arts), NOW)
    assert a["count_7d"] == 7 and a["count_48h"] == 6 and a["sources"] == 2
    assert [d["title"] for d in a["deep"]] == ["the long read"] and a["headline"]["title"] == "story 0"
    m = pulse.Mover("ABC", "ABC", 10.0, 6.0, rel_volume=2.5, attention=a)
    pulse.tag(m)
    assert m.tags == ["In the news", "Deep coverage", "Unusual volume"]
    quiet = pulse.Mover("QQ", "QQ", 10.0, -7.0, attention=pulse.attention(news_data([]), NOW))
    pulse.tag(quiet)
    assert quiet.tags == ["Moving without news"]


def test_deep_source_matching_is_by_outlet_name():
    assert pulse.is_deep_source("Reuters") and pulse.is_deep_source("The Wall Street Journal")
    assert pulse.is_deep_source("Barron's") and not pulse.is_deep_source("Motley Fool")


def test_fallback_movers_ranks_large_caps():
    changes = {"AAA": 3.0, "BBB": -4.0, "CCC": 1.0, "DDD": None}

    def q(sym):
        if changes[sym] is None:
            raise http.DataUnavailable("down")
        return quote(sym, chg=changes[sym])

    lists = pulse.fallback_movers(q, universe=list(changes))
    assert [m.symbol for m in lists["gainers"]] == ["AAA", "CCC"] and [m.symbol for m in lists["losers"]] == ["BBB"]


# ------------------------------------------------------------------ sleepers

def buy(sym, cik, insider, value, trade="2026-09-15", filed="2026-09-16", acc=None, role="Director"):
    return alerts.Buy(filed=filed, accession=acc or f"{cik}-{insider}", issuer_cik=cik, issuer_name=sym + " Corp",
                      symbol=sym, insider=insider, role=role, trade_date=trade, shares=value / 10, price=10.0,
                      value=value)


BUYS = [
    # a cluster: three insiders at QUIET
    buy("QUIET", "1", "A", 60_000), buy("QUIET", "1", "B", 80_000), buy("QUIET", "1", "C", 1_200_000),
    # one $2M buy at LOUD, which is all over the news
    buy("LOUD", "2", "D", 2_000_000),
    # $1.5M across two filings by one director at RAN, which already ran 40%
    buy("RAN", "3", "E", 800_000, acc="x1"), buy("RAN", "3", "E", 800_000, acc="x2"),
    buy("FUND", "4", "F", 5_000_000),
    buy("OLD", "5", "G", 9_000_000, trade="2026-06-01", filed="2026-06-02"),
    buy("SMALL", "6", "H", 50_000),
]


def test_insider_candidates_one_line_per_idea():
    c = pulse.insider_candidates(BUYS, TODAY)
    assert set(c) == {"QUIET", "LOUD", "FUND"}          # RAN: two filings of $800k each, below $1M per filing
    assert c["QUIET"]["reasons"] == ["3 insiders bought $1.3M (2026-09-15 to 2026-09-15)",
                                     "largest: C (Director) bought $1.2M"]
    assert c["LOUD"]["reasons"] == ["D (Director) bought $2.0M"]


def test_find_sleepers_filters_news_runups_and_funds():
    buys = BUYS + [buy("RAN", "3", "E", 1_500_000, acc="x3")]
    news_counts = {"QUIET": 1, "LOUD": 25, "RAN": 0}
    runs = {"QUIET": 1.05, "LOUD": 1.0, "RAN": 1.40}

    def history(sym, days):
        return [market.PriceBar(f"2026-08-{i:02d}", 100.0) for i in range(1, 23)] + \
               [market.PriceBar("2026-09-24", 100.0 * runs[sym])]

    found = pulse.find_sleepers(
        buys, TODAY, quote_fn=lambda s: quote(s, 12.0, 0.5), history_fn=history,
        news_fn=lambda s, c, d: news_data([{}] * news_counts[s]), is_fund=lambda cik: cik == "4",
        dilution_fn=lambda cik: dilution.DilutionCheck(cik="0", level="none"))
    assert [s.symbol for s in found] == ["QUIET"]
    s = found[0]
    assert "only 1 headline this week" in s.reasons and "price +5% over the past month" in s.reasons
    assert s.price == 12.0 and s.dilution == ""


def test_sleepers_need_a_quote_and_are_capped():
    buys = [buy(f"S{i}", str(i), "A", 2_000_000 + i) for i in range(15)] + [buy("AXIA3", "99", "B", 9_000_000)]

    def q(sym):
        if sym == "AXIA3":
            raise http.DataUnavailable("no US quote")
        return quote(sym)

    found = pulse.find_sleepers(buys, TODAY, quote_fn=q, history_fn=lambda s, d: [],
                                news_fn=lambda s, c, d: news_data([]), is_fund=lambda cik: False,
                                dilution_fn=lambda cik: dilution.DilutionCheck(cik="0", level="none"), show=12)
    assert len(found) == 12 and "AXIA3" not in {s.symbol for s in found} and found[0].symbol == "S14"


def test_active_dilution_sorts_last():
    buys = [buy("AAA", "1", "A", 3_000_000), buy("BBB", "2", "B", 1_500_000)]
    found = pulse.find_sleepers(
        buys, TODAY, quote_fn=lambda s: quote(s), history_fn=lambda s, d: [],
        news_fn=lambda s, c, d: news_data([]), is_fund=lambda cik: False,
        dilution_fn=lambda cik: dilution.DilutionCheck(cik="0", level="active" if cik == "1" else "none"))
    assert [s.symbol for s in found] == ["BBB", "AAA"] and found[1].dilution == "Dilution: active"


# ------------------------------------------------------------------ sell watch

def analysis(**ind):
    sig = ind.pop("sig", {"score": 5, "label": "Neutral", "suggested_max_weight": 0.10})
    ins = ind.pop("ins", None)
    return {"indicators": ind, "signal": sig, "insiders": ins}


def test_sell_flags_cover_each_rule():
    a = analysis(above_sma200=False, momentum_12_1=-0.2, sig={"score": -30, "label": "Bearish", "suggested_max_weight": 0.05},
                 ins={"open_market_buys": 0, "discretionary_sell_value_usd": 3_000_000, "window_days": 90})
    flags = pulse.sell_flags(a, weight=12.0, dil=dilution.DilutionCheck(cik="0", level="active"), unrealized_pct=-30)
    texts = " | ".join(f.text for f in flags)
    for bit in ("200-day", "momentum is negative", "bearish (-30)", "Insiders sold $3.0M", "12% of your portfolio",
                "selling shares", "tax-loss"):
        assert bit in texts, bit
    assert pulse.verdict(flags) == "Review"


def test_healthy_holding_has_no_flags_and_extended_one_is_watch():
    ok = analysis(above_sma200=True, momentum_12_1=0.3, return_3m=0.1, rsi14=55)
    assert pulse.sell_flags(ok, 5.0, None, 12.0) == [] and pulse.verdict([]) == "No red flags"
    hot = analysis(above_sma200=True, momentum_12_1=0.9, return_3m=0.6, rsi14=82)
    flags = pulse.sell_flags(hot, 5.0, None, 80.0)
    assert len(flags) == 1 and "extended" in flags[0].text and pulse.verdict(flags) == "Watch"
    # A big position with no volatility estimate still gets flagged past 25%.
    big = analysis(above_sma200=True, sig={"score": 20, "label": "Bullish", "suggested_max_weight": None})
    assert "40% of your portfolio" in pulse.sell_flags(big, 40.0, None, None)[0].text


def test_sell_watch_orders_worst_first_and_skips_dilution_for_crypto():
    analyses = {"GOOD": analysis(above_sma200=True), "BAD": analysis(above_sma200=False, momentum_12_1=-0.1),
                "BTC-USD": analysis(above_sma200=True)}
    asked = []
    rows = pulse.sell_watch(
        [{"symbol": "GOOD", "weight": 10, "unrealized_pct": 5}, {"symbol": "BAD", "weight": 10, "unrealized_pct": -5},
         {"symbol": "BTC-USD", "weight": 5, "unrealized_pct": 1}, {"symbol": "GONE", "weight": 1, "unrealized_pct": 0}],
        analyze_fn=lambda s: analyses.get(s) or (_ for _ in ()).throw(http.DataUnavailable("x")),
        cik_fn=lambda s: asked.append(s) or "9", dilution_fn=lambda cik: dilution.DilutionCheck(cik="0", level="none"))
    assert [r["symbol"] for r in rows][0] == "BAD" and rows[0]["verdict"] == "Review"
    assert "BTC-USD" not in asked
    gone = next(r for r in rows if r["symbol"] == "GONE")
    assert gone["error"] == "analysis unavailable" and gone["verdict"] == "Couldn't check"
    assert [r["symbol"] for r in rows] == ["BAD", "GONE", "GOOD", "BTC-USD"]     # unchecked ranks above all-clear


# ------------------------------------------------------------------ assembled + endpoints

def fake_screen(name):
    rows = {"gainers": [("ABC", 10.0, 12.0, 3e6, 1e6), ("SHARED", 50.0, 6.0, 1e6, 1e6)],
            "losers": [("DEF", 20.0, -9.0, 1e6, 1e6)],
            "active": [("SHARED", 50.0, 6.0, 1e6, 1e6)]}[name]
    return pulse.parse_screen(screen(*rows), name)


def fake_news(sym, company=None, days=7):
    if sym == "ABC":
        return news_data([article(f"abc {i}", "Yahoo", i) for i in range(5)] + [article("abc deep", "Bloomberg", 30)])
    if sym == "BTC-USD":
        raise http.DataUnavailable("no news")
    return news_data([])


def build_stubbed(**kw):
    kw.setdefault("screen_fn", fake_screen)
    return pulse.build(quote_fn=lambda s: quote(s, chg=2.0 if s == "BTC-USD" else -1.0),
                       news_fn=fake_news, buys_fn=lambda: [], sleepers_fn=lambda buys, today: [], now=NOW, **kw)


def test_build_merges_lists_and_ranks_attention():
    d = build_stubbed()
    assert [m["symbol"] for m in d["movers"]["gainers"]] == ["ABC", "SHARED"]
    shared = d["movers"]["active"][0]
    assert shared["symbol"] == "SHARED" and shared["lists"] == ["gainers", "active"]
    assert d["in_the_news"][0]["symbol"] == "ABC" and d["in_the_news"][0]["tags"][:2] == ["In the news", "Deep coverage"]
    assert [m["symbol"] for m in d["deep_coverage"]] == ["ABC"]
    assert d["movers"]["losers"][0]["tags"] == ["Moving without news"]
    assert len(d["movers"]["crypto"]) == len(pulse.CRYPTO) and d["errors"] == []


def test_build_falls_back_when_screens_fail():
    def broken(name):
        raise http.DataUnavailable("403")

    d = build_stubbed(screen_fn=broken)
    assert any("large caps" in e for e in d["errors"]) and d["movers"]["losers"] and not d["movers"]["gainers"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    pulse.pulse_cache.store.clear()
    pulse.sellwatch_cache.store.clear()
    return TestClient(api.app)


def test_pulse_endpoint_caches(client, monkeypatch):
    calls = []
    monkeypatch.setattr(pulse, "build", lambda: calls.append(1) or {"generated_at": "t", "movers": {}})
    assert client.get("/api/pulse").json()["generated_at"] == "t"
    client.get("/api/pulse")
    assert len(calls) == 1
    client.get("/api/pulse?refresh=true")
    assert len(calls) == 2


def test_sellwatch_endpoint_uses_holdings(client, monkeypatch):
    monkeypatch.setattr(market, "get_quote", lambda s: quote(s, 120.0, 1.0))
    client.post("/api/transactions", json={"symbol": "AAPL", "side": "buy", "quantity": 10, "price": 100.0,
                                           "date": "2026-01-02"})
    seen = {}

    def fake_sell_watch(positions, **kw):
        seen["positions"] = positions
        return [{"symbol": "AAPL", "verdict": "Watch", "flags": []}]

    monkeypatch.setattr(pulse, "sell_watch", fake_sell_watch)
    out = client.get("/api/sellwatch").json()
    assert out["holdings"][0]["verdict"] == "Watch"
    p = seen["positions"][0]
    assert p["symbol"] == "AAPL" and round(p["weight"]) == 100 and round(p["unrealized_pct"]) == 20
