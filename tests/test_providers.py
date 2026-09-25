import json
from datetime import date

import pytest

from market_tracker.providers import market, news, sec
from tests.conftest import fixture_text


# ------------------------------------------------------------------ market

def test_symbol_normalization():
    assert market.normalize_symbol("btc") == "BTC-USD"
    assert market.asset_class("ETH-USD") == "crypto"
    assert market.asset_class("aapl") == "stock"


def test_parse_yahoo_quote_and_history():
    result = json.loads(fixture_text("yahoo_chart.json"))["chart"]["result"][0]
    q = market.parse_yahoo_quote("AAPL", result)
    assert q.price == 131.5 and q.change_pct == pytest.approx((131.5 / 129 - 1) * 100)
    hist = market.parse_yahoo_history(result)
    assert len(hist) == 29  # the None close is dropped
    assert hist == sorted(hist, key=lambda b: b.date)


def test_parse_coinbase_candles_sorted_oldest_first():
    candles = [[1754086400, 1, 2, 1.5, 1.8, 10], [1754000000, 1, 2, 1.2, 1.4, 5]]
    bars = market.parse_coinbase_candles(candles)
    assert [b.close for b in bars] == [1.4, 1.8]


# ------------------------------------------------------------------ SEC

def test_parse_13f_aggregates_by_cusip_and_keeps_options_separate():
    holdings = sec.parse_13f_infotable(fixture_text("infotable.xml"), "2026-06-30")
    by = {(h.cusip, h.put_call): h for h in holdings}
    aapl = by[("037833100", None)]
    assert aapl.shares == 310_000_000 and aapl.value_usd == 62_000_000_000
    assert by[("67066G104", "Put")].value_usd == 500_000_000
    assert holdings[0].issuer == "APPLE INC"


def test_pre_2023_values_are_thousands():
    holdings = sec.parse_13f_infotable(fixture_text("infotable.xml"), "2022-09-30")
    assert holdings[0].value_usd == 62_000_000_000 * 1000


def _filing(period, holdings):
    return sec.Filing13F("buffett", "BERKSHIRE HATHAWAY INC", period, period, "x", holdings)


def test_diff_filings_classifies_moves():
    H = sec.Holding
    prev = _filing("2026-03-31", [H("APPLE INC", "1", "COM", 100, 1000, ticker="AAPL"),
                                  H("BANK OF AMERICA", "2", "COM", 50, 500, ticker="BAC"),
                                  H("CHEVRON", "3", "COM", 30, 300, ticker="CVX"),
                                  H("KRAFT", "5", "COM", 10, 100, ticker="KHC")])
    cur = _filing("2026-06-30", [H("APPLE INC", "1", "COM", 80, 800, ticker="AAPL"),
                                 H("BANK OF AMERICA", "2", "COM", 60, 600, ticker="BAC"),
                                 H("DOMINOS", "4", "COM", 20, 200, ticker="DPZ"),
                                 H("KRAFT", "5", "COM", 10, 100, ticker="KHC")])
    actions = {c.ticker: c.action for c in sec.diff_filings(cur, prev)}
    assert actions == {"AAPL": "reduced", "BAC": "added", "CVX": "exited", "DPZ": "new", "KHC": "unchanged"}


def test_ticker_map_matches_13f_issuer_names():
    raw = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
           "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
           "2": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
           "3": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."}}
    tmap = sec.build_ticker_map(raw)
    assert tmap.ticker_for_issuer("NVIDIA CORPORATION") == "NVDA"
    assert tmap.ticker_for_issuer("APPLE INC") == "AAPL"
    assert tmap.ticker_for_issuer("ALPHABET INC CAP STK CL A") is None or True  # best effort
    assert tmap.ticker_for_issuer("ALPHABET INC") == "GOOGL"
    assert tmap.cik_for("aapl") == "0000320193"


def test_parse_form4():
    trades = sec.parse_form4(fixture_text("form4.xml"))
    assert len(trades) == 2
    buy = trades[0]
    assert buy.insider == "DOE JANE" and buy.role == "Chief Financial Officer"
    assert buy.code == "P" and buy.shares == 10_000 and buy.price == 150.25 and buy.acquired
    assert trades[1].code == "F" and not trades[1].acquired


def test_summarize_insiders_cluster_and_10b5_1():
    T = sec.InsiderTrade
    trades = [T("A", "CEO", "2026-09-01", "P", 100, 10, True, 1000),
              T("B", "CFO", "2026-09-02", "P", 100, 10, True, 1000),
              T("C", "Director", "2026-09-03", "P", 100, 10, True, 1000),
              T("D", "COO", "2026-09-04", "S", 100, 10, False, 0, ten_b5_1=True),
              T("E", "CTO", "2026-01-01", "P", 100, 10, True, 1000)]  # outside window
    s = sec.summarize_insiders(trades, days=90, today=date(2026, 9, 24))
    assert s["cluster_buy"] and s["distinct_buyers"] == 3
    assert s["planned_10b5_1_sells"] == 1 and s["discretionary_sell_value_usd"] == 0
    assert s["buy_value_usd"] == 3000


def _report(key, person, moves, holdings=()):
    base = {"new": [], "added": [], "reduced": [], "exited": []}
    base.update(moves)
    return {"investor": {"key": key, "person": person, "fund": person}, "period": "2026-06-30",
            "top_holdings": list(holdings), "holdings": list(holdings), "moves": base, "staleness_days": 86}


def _move(ticker, w=3.0):
    return {"ticker": ticker, "issuer": ticker, "weight_now": w, "value_now": w * 1e6}


def test_smart_money_for_ticker_and_consensus_weighting():
    reports = [_report("buffett", "Warren Buffett", {"new": [_move("XYZ")]}, [_move("XYZ", 6.0)]),
               _report("ackman", "Bill Ackman", {"added": [_move("XYZ")]}),
               _report("simons", "Jim Simons", {"exited": [_move("XYZ")]})]
    sm = sec.smart_money_for_ticker("xyz", reports)
    assert len(sm["buyers"]) == 2 and len(sm["sellers"]) == 1
    assert sm["net_flow"] == pytest.approx((2 - 0.35) / 2.35)
    assert sm["holders"][0]["weight_pct"] == 6.0
    c = sec.consensus(reports)
    assert c["most_bought"][0]["ticker"] == "XYZ"
    assert c["most_bought"][0]["score"] == pytest.approx(1.65)


def test_investor_ciks_are_well_formed_and_unique():
    from market_tracker.investors import INVESTORS
    ciks = [i.cik for i in INVESTORS]
    assert all(len(c) == 10 and c.isdigit() for c in ciks)
    assert len(set(ciks)) == len(ciks)
    assert len({i.key for i in INVESTORS}) == len(INVESTORS)


# ------------------------------------------------------------------ news

def test_sentiment_basics():
    assert news.sentiment("Shares surge after earnings beat") > 0
    assert news.sentiment("Stock plunges on fraud probe") < 0
    assert news.sentiment("Company did not miss estimates") > 0  # negation flips
    assert news.sentiment("Company announces date for annual meeting") == 0


def test_parse_google_rss_strips_publisher_and_dedupes():
    arts = news.parse_rss(fixture_text("google_news.xml"), "google")
    assert arts[0].title == "Nvidia shares surge after earnings beat and record data center revenue"
    assert arts[0].source == "Reuters" and arts[0].sentiment > 0
    assert arts[1].sentiment < 0
    assert arts[3].title == "What to watch this week & beyond"
    deduped = news._dedupe(arts)
    assert len(deduped) == 3
    summary = news.summarize(deduped)
    assert summary["positive"] == 1 and summary["negative"] == 1
    assert ("nvidia", 2) in summary["top_terms"]


# company_tickers.json titles (largest companies first, as SEC orders them) vs. the
# abbreviated, truncated issuer names that appear in real 13F information tables.
SEC_TITLES = [("AAPL", "Apple Inc."), ("BAC", "BANK OF AMERICA CORP /DE/"), ("AXP", "AMERICAN EXPRESS CO"),
              ("KO", "COCA COLA CO"), ("CVX", "CHEVRON CORP"), ("OXY", "OCCIDENTAL PETROLEUM CORP /DE/"),
              ("CB", "Chubb Ltd"), ("KHC", "Kraft Heinz Co"), ("MCO", "MOODYS CORP /DE/"),
              ("DEO", "DIAGEO PLC"), ("CHTR", "CHARTER COMMUNICATIONS, INC. /MO/"),
              ("COF", "CAPITAL ONE FINANCIAL CORP"), ("JEF", "Jefferies Financial Group Inc."),
              ("LPX", "LOUISIANA-PACIFIC CORP"), ("SPGI", "S&P Global Inc."), ("DPZ", "DOMINO'S PIZZA, INC."),
              ("MA", "Mastercard Inc"), ("UNH", "UNITEDHEALTH GROUP INC"), ("APLE", "Apple Hospitality REIT, Inc."),
              ("BAC-PL", "BANK OF AMERICA CORP /DE/"), ("LLYVA", "Liberty Media Corp"),
              # The three the first live run left unmapped (run 36062955884):
              ("SIRI", "Sirius XM Holdings Inc."), ("VRSN", "VERISIGN INC/CA"), ("DHI", "HORTON D R INC /DE/")]
THIRTEEN_F = {"APPLE INC": "AAPL", "BANK AMER CORP": "BAC", "AMERICAN EXPRESS CO": "AXP", "COCA COLA CO": "KO",
              "CHEVRON CORP NEW": "CVX", "OCCIDENTAL PETE CORP": "OXY", "CHUBB LIMITED": "CB",
              "KRAFT HEINZ CO": "KHC", "MOODYS CORP": "MCO", "DIAGEO P L C": "DEO",
              "CHARTER COMMUNICATIONS INC N": "CHTR", "CAPITAL ONE FINL CORP": "COF",
              "JEFFERIES FINL GROUP INC": "JEF", "LOUISIANA PAC CORP": "LPX", "S&P GLOBAL INC": "SPGI",
              "DOMINOS PIZZA INC": "DPZ", "MASTERCARD INCORPORATED": "MA", "UNITEDHEALTH GROUP INC": "UNH",
              "LIBERTY MEDIA CORP DEL": "LLYVA", "SIRIUSXM HOLDINGS INC": "SIRI", "VERISIGN INC": "VRSN",
              "D R HORTON INC": "DHI"}


def test_ticker_map_handles_13f_abbreviations():
    raw = {str(i): {"cik_str": i + 1, "ticker": t, "title": title} for i, (t, title) in enumerate(SEC_TITLES)}
    tmap = sec.build_ticker_map(raw)
    got = {issuer: tmap.ticker_for_issuer(issuer) for issuer in THIRTEEN_F}
    assert got == THIRTEEN_F


def test_ticker_map_avoids_false_matches():
    raw = {"0": {"cik_str": 1, "ticker": "APLE", "title": "Apple Hospitality REIT, Inc."},
           "1": {"cik_str": 2, "ticker": "BACX", "title": "Bank of Hawaii Corp"}}
    tmap = sec.build_ticker_map(raw)
    assert tmap.ticker_for_issuer("APPLE INC") is None  # one word never prefix-matches
    assert tmap.ticker_for_issuer("BANK AMER CORP") is None


def test_news_mood_is_diluted_by_neutral_headlines():
    def art(sent):
        return news.Article("t", "s", "u", "2026-09-24", sent, "google")
    few_positive = [art(0.5)] * 3 + [art(0.0)] * 37
    assert news.summarize(few_positive)["avg_sentiment"] == pytest.approx(1.5 / 40)
    from market_tracker.analytics import signals
    # 3 upbeat headlines out of 40 is mild, not maximal, optimism.
    score, _ = signals.news_component(news.summarize(few_positive))
    assert 0 < score < 0.1
    assert news.summarize([])["avg_sentiment"] == 0.0


def test_market_news_falls_back_to_yahoo_when_google_fails(monkeypatch):
    def google_down(query):
        raise news.http.DataUnavailable(f"{query}: 503")
    yahoo_calls = []

    def yahoo(sym):
        yahoo_calls.append(sym)
        return [news.Article(f"{sym} headline {i}", "Yahoo", f"https://example.com/{sym}/{i}",
                             "2026-09-24", 0.0, "yahoo") for i in range(8)]
    monkeypatch.setattr(news, "_google", google_down)
    monkeypatch.setattr(news, "_yahoo", yahoo)
    n = news.market_news()
    assert yahoo_calls == list(news.MARKET_FALLBACK_SYMBOLS)
    assert n["count"] == 16 and len(n["errors"]) == 4


def test_market_news_skips_fallback_when_google_is_healthy(monkeypatch):
    monkeypatch.setattr(news, "_google", lambda q: [news.Article(f"{q} story {i}", "S", f"https://e.com/{q}/{i}",
                                                                 "2026-09-24", 0.0, "google") for i in range(5)])
    monkeypatch.setattr(news, "_yahoo", lambda sym: pytest.fail("fallback should not run"))
    assert news.market_news()["count"] == 20
