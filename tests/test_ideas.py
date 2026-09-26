from datetime import date

from fastapi.testclient import TestClient

from market_tracker import (accounts, api, benchmark, db, fees, goals, holdplan, lookthrough, price_alerts, schedules,
                            statement)


# ------------------------------------------------------------------ auto-invest schedules

def test_due_dates_weekly_biweekly_monthly():
    s = schedules.Schedule(1, "Stash", "VOO", 20, "week", 0, "2026-09-01")
    assert [d.isoformat() for d in schedules.due_dates(s, date(2026, 9, 22))] == ["2026-09-07", "2026-09-14", "2026-09-21"]
    s2 = schedules.Schedule(2, "Stash", "VOO", 20, "2weeks", 4, "2026-09-01")
    assert [d.isoformat() for d in schedules.due_dates(s2, date(2026, 9, 30))] == ["2026-09-04", "2026-09-18"]
    s3 = schedules.Schedule(3, "Stash", "VOO", 50, "month", 31 if False else 28, "2026-07-15")
    assert [d.isoformat() for d in schedules.due_dates(s3, date(2026, 9, 30))] == ["2026-07-28", "2026-08-28", "2026-09-28"]
    sat = schedules.Schedule(4, "Stash", "VOO", 50, "month", 5, "2026-09-01")          # Sep 5 2026 is a Saturday
    assert [d.isoformat() for d in schedules.due_dates(sat, date(2026, 9, 30))] == ["2026-09-07"]


def test_apply_records_once_and_skips_dates_already_covered():
    with db.connect() as conn:
        sid = schedules.add(conn, "Stash", "VOO", 20.0, "week", 0, "2026-09-01")
        db.add_transaction(conn, "VOO", "buy", 0.04, 500, "2026-09-15", 0, "email", import_key="em:x", account="Stash")
        added = schedules.apply(conn, date(2026, 9, 22), lambda s, d: 500.0)
        assert [a["date"] for a in added] == ["2026-09-07", "2026-09-21"]       # the 14th was covered by the email buy
        assert schedules.apply(conn, date(2026, 9, 22), lambda s, d: 500.0) == []
        keys = [t["import_key"] for t in db.list_transactions(conn)]
        assert f"sched:{sid}:2026-09-07" in keys and added[0]["quantity"] == 0.04


# ------------------------------------------------------------------ price lines

def test_price_alert_lines_fire_once_a_day():
    with db.connect() as conn:
        db.save_thesis(conn, "NKE", {"price_below": 60, "price_above": 90})
        price_alerts.set_target(conn, "AMD", 120, "on a dip")
    got = []
    fired = price_alerts.check(lambda s: {"NKE": 59.5, "AMD": 118.0}[s], date(2026, 9, 26), lambda *a: got.append(a))
    assert {(f["symbol"], f["kind"]) for f in fired} == {("NKE", "below"), ("AMD", "buy")}
    assert "NKE fell to $59.50: your sell-below line was $60.00" in got[0][3] or any("NKE fell to" in g[3] for g in got)
    assert price_alerts.check(lambda s: {"NKE": 59.0, "AMD": 110.0}[s], date(2026, 9, 26)) == []
    assert len(price_alerts.check(lambda s: {"NKE": 59.0, "AMD": 130.0}[s], date(2026, 9, 27))) == 1


def test_new_money_leftover_goes_to_a_dip_hit():
    plan = {"holdings": [], "base": 0, "cap": 0.2, "tax": {"blackout": []}, "dip_hits": ["AMD"]}
    out = holdplan.new_money(plan, 100)
    assert out["buys"][0]["symbol"] == "AMD" and "buy-the-dip" in out["buys"][0]["why"]


# ------------------------------------------------------------------ stale data

def test_stale_notes():
    rows = [{"name": "Robinhood", "auto": None, "last_trade": "2026-07-01", "positions": [1]},
            {"name": "Stash", "auto": None, "last_trade": "2026-09-20", "positions": [1]},
            {"name": "Coinbase", "auto": {"how": "x", "last": {"ok": False, "error": "401"}}, "last_trade": "2026-09-20", "positions": [1]}]
    notes = accounts.stale_notes(rows, date(2026, 9, 26))
    assert [n["account"] for n in notes] == ["Robinhood", "Coinbase"]
    assert "87 days" in notes[0]["text"] and "401" in notes[1]["text"]


# ------------------------------------------------------------------ vs the market

def test_benchmark_counts_when_money_went_in():
    bars = [("2026-01-02", 100.0), ("2026-06-01", 120.0), ("2026-09-25", 150.0)]
    txs = [{"symbol": "AAA", "side": "buy", "quantity": 10, "price": 100, "date": "2026-01-02", "account": "Robinhood"},
           {"symbol": "AAA", "side": "buy", "quantity": 10, "price": 120, "date": "2026-06-01", "account": "Stash"}]
    r = benchmark.compare(txs, bars, {"AAA": 130.0})
    # You: 2,600 now on 2,200 in (+400). VOO: 10 + 10 shares at 150 = 3,000 (+800).
    assert (r["value"], r["bench_value"], r["gain"], r["bench_gain"], r["ahead"]) == (2600.0, 3000.0, 400.0, 800.0, -400.0)
    sold = txs + [{"symbol": "AAA", "side": "sell", "quantity": 5, "price": 150, "date": "2026-09-25", "account": "Robinhood"}]
    r2 = benchmark.compare(sold, bars, {"AAA": 150.0})
    assert r2["taken_out"] == 750.0 and r2["ahead"] == 0.0
    by = benchmark.compare(txs, bars, {"AAA": 130.0}, account="Stash")
    assert by["invested"] == 1200.0 and by["bench_value"] == 1500.0


# ------------------------------------------------------------------ look-through

NPORT = """<?xml version="1.0"?><edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><formData><genInfo><repPdDate>2026-06-30</repPdDate></genInfo>
<invstOrSecs><invstOrSec><name>Apple Inc</name><title>APPLE</title><cusip>037833100</cusip><pctVal>7.0</pctVal><assetCat>EC</assetCat></invstOrSec>
<invstOrSec><name>Microsoft Corp</name><title>MSFT</title><cusip>594918104</cusip><pctVal>6.0</pctVal><assetCat>EC</assetCat></invstOrSec>
<invstOrSec><name>Cash Mgmt Fund</name><title>x</title><cusip>000</cusip><pctVal>0.1</pctVal><assetCat>STIV</assetCat></invstOrSec>
</invstOrSecs></formData></edgarSubmission>"""


def test_parse_nport_and_exposure():
    period, rows = lookthrough.parse_nport(NPORT)
    assert period == "2026-06-30" and [r["pct"] for r in rows] == [0.07, 0.06, 0.001]
    fund = {"period": period, "source": "VOO", "holdings": rows}
    res = lookthrough.exposure([{"symbol": "VOO", "market_value": 10000}, {"symbol": "AAPL", "market_value": 1000}],
                               {"VOO": fund}, resolve=lambda n: {"Apple Inc": "AAPL", "Microsoft Corp": "MSFT"}.get(n))
    aapl = res["top"][0]
    assert aapl["symbol"] == "AAPL" and aapl["value"] == 1700.0 and aapl["via"] == {"direct": 1000.0, "VOO": 700.0}
    assert round(aapl["weight"], 4) == round(1700 / 11000, 4)


def test_fund_lookup_uses_proxy_and_cache():
    calls = []

    def get(url, params=None, headers=None, ttl=None, as_json=True):
        calls.append(url)
        if "company_tickers_mf" in url:
            return {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[1100663, "S000004310", "C1", "IVV"]]}
        if "efts" in url:
            assert params["q"] == '"S000004310"'
            return {"hits": {"hits": [{"_id": "0001752724-26-000001:primary_doc.xml", "_source": {"ciks": ["0001100663"], "period_ending": "2026-06-30", "file_date": "2026-08-28"}}]}}
        assert url.endswith("/1100663/000175272426000001/primary_doc.xml")
        return NPORT
    got = lookthrough.holdings("SPY", get, now=1000)
    assert got["source"] == "IVV" and got["holdings"][0]["name"] == "Apple Inc"
    n = len(calls)
    assert lookthrough.holdings("SPY", get, now=2000)["period"] == "2026-06-30" and len(calls) == n


# ------------------------------------------------------------------ fees

def test_fees_with_cheaper_twin():
    pages = {"spy": "<td>Expense Ratio</td><td>0.0945%</td>", "voo": "Expense Ratio 0.03%", "ivv": "Expense Ratio 0.03%",
             "splg": "Expense Ratio 0.02%", "aapl": "no fee here"}

    def get(url, headers=None, ttl=None, as_json=True):
        return pages[url.rstrip("/").rsplit("/", 1)[-1]]
    out = fees.build([{"symbol": "SPY", "market_value": 10000}, {"symbol": "AAPL", "market_value": 5000}], get)
    f = out["funds"][0]
    assert f["symbol"] == "SPY" and f["expense_ratio"] == 0.000945 and f["per_year"] == 9.45
    assert [t["symbol"] for t in f["cheaper"]] == ["SPLG", "VOO", "IVV"] and f["cheaper"][0]["saves_per_year"] == 7.45
    assert len(out["funds"]) == 1                                       # AAPL has no expense ratio
    assert round(fees.drag(10000, 0.006) - fees.drag(10000, 0.0003)) == 11179          # the number in fees.py's docstring


# ------------------------------------------------------------------ goals

def test_goal_projection():
    g = goals.project(10000, 500, 20, 300000)
    assert g["bad"] < g["typical"] < g["good"] and 0 < g["chance"] < 1
    assert g["contributed"] == 130000 and g["monthly_for_even_odds"] > 0
    assert goals.project(10000, 500, 20, 300000) == g                  # same seed, same answer
    assert goals.project(1_000_000, 0, 10, 100)["chance"] == 1.0


# ------------------------------------------------------------------ statement check

def test_statement_quantities_and_compare():
    text = ("Stash Invest Statement September 2026\nHOLDINGS\nVanguard S&P 500 ETF VOO 3.214567 $500.00 $1,607.28\n"
            "Apple Inc. AAPL 1.50 $250.00 $375.00\nSCHD 10.000000 $27.50 $275.00\nTOTAL 2,257.28\n")
    q = statement.quantities(text, {"VOO", "AAPL"})
    assert q == {"VOO": 3.214567, "AAPL": 1.5, "SCHD": 10.0}
    r = statement.compare(q, {"VOO": 3.214567, "AAPL": 1.0, "QQQ": 2.0})
    assert [m["symbol"] for m in r["match"]] == ["VOO"] and r["differences"][0]["difference"] == 0.5
    assert r["not_in_ledger"] == [{"symbol": "SCHD", "statement": 10.0}] and r["not_on_statement"] == [{"symbol": "QQQ", "ledger": 2.0}]


# ------------------------------------------------------------------ endpoints

def test_endpoints(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_LOGIN", raising=False)
    from market_tracker import schedules as sch
    monkeypatch.setattr(sch, "close_on", lambda s, d: 500.0)
    monkeypatch.setattr(api, "livefeed_price", lambda s: 110.0)
    c = TestClient(api.app)
    r = c.post("/api/schedules", json={"account": "Stash", "symbol": "voo", "amount": 25, "every": "week", "day": 0, "start": "2026-09-14"})
    assert r.status_code == 200 and r.json()["schedules"][0]["symbol"] == "VOO"
    assert c.post("/api/schedules", json={"symbol": "VOO", "amount": 25, "every": "month", "day": 0, "start": "2026-09-01"}).status_code == 422
    t = c.post("/api/buy-targets", json={"symbol": "amd", "price": 120}).json()
    assert t[0]["symbol"] == "AMD" and t[0]["gap_pct"] == -8.3
    assert c.delete("/api/buy-targets/AMD").json() == []
    assert c.get("/api/goal").json() == {"goal": None}
    import base64
    assert c.post("/api/statement-check", json={"account": "Stash", "pdf_base64": base64.b64encode(b"not a pdf at all").decode()}).status_code == 400
