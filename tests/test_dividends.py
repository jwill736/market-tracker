from datetime import date

from market_tracker import dividends as dv

TODAY = date(2026, 9, 25)


def _yahoo(divs):
    from datetime import datetime, timezone
    return {"chart": {"result": [{"events": {"dividends": {
        str(i): {"date": int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()), "amount": a}
        for i, (d, a) in enumerate(divs)}}}]}}


KO = [("2025-09-15", 0.51), ("2025-11-28", 0.51), ("2026-03-13", 0.53), ("2026-06-12", 0.53), ("2026-09-12", 0.53)]
MONTHLY = [(f"2026-{m:02d}-01", 0.1) for m in range(1, 10)]


def test_parse_yahoo_dividends_sorted():
    got = dv.parse_yahoo_dividends(_yahoo(list(reversed(KO)))["chart"]["result"][0])
    assert got == KO


def test_parse_nasdaq_declared():
    data = {"data": {"dividends": {"rows": [{"exOrEffDate": "11/10/2026", "type": "Cash", "amount": "$0.27",
                                             "paymentDate": "11/13/2026"}]}}}
    assert dv.parse_nasdaq(data) == {"ex_date": "2026-11-10", "pay_date": "2026-11-13", "amount": 0.27}
    # Nasdaq's answer for NYSE names: no rows
    assert dv.parse_nasdaq({"data": {"dividends": {"rows": None}}, "message": "Non-Nasdaq symbols"}) is None


def test_cadence_quarterly_monthly_and_special():
    q = dv.cadence(KO, TODAY)
    assert q["per_year"] == 4 and q["amount"] == 0.53 and q["last_ex"] == "2026-09-12"
    assert dv.cadence(MONTHLY, TODAY)["per_year"] == 12
    special = KO + [("2026-09-20", 3.00)]
    c = dv.cadence(special, TODAY)
    assert c["amount"] == 0.53 and c["per_year"] == 4          # the one-off doesn't set the rate
    assert dv.cadence([("2020-01-01", 1.0)], TODAY) is None     # stopped paying


def test_shares_on_counts_the_day_before():
    txs = [{"symbol": "KO", "side": "buy", "quantity": 10, "date": "2026-06-11", "account": "Stash"},
           {"symbol": "KO", "side": "buy", "quantity": 5, "date": "2026-06-12", "account": "Stash"},
           {"symbol": "KO", "side": "buy", "quantity": 7, "date": "2026-01-01", "account": "Robinhood"}]
    assert dv.shares_on(txs, "KO", "2026-06-12", "Stash") == 10       # bought on the ex-date: not paid
    assert dv.shares_on(txs, "KO", "2026-06-12") == 17


def test_build_recorded_estimated_forward_and_upcoming():
    txs = [{"id": 1, "symbol": "KO", "side": "buy", "quantity": 100, "price": 50.0, "date": "2025-01-02", "account": "Robinhood"},
           {"id": 2, "symbol": "KO", "side": "buy", "quantity": 10, "price": 60.0, "date": "2026-01-02", "account": "Stash"},
           {"id": 3, "symbol": "NVDA", "side": "buy", "quantity": 1, "price": 100.0, "date": "2026-01-02", "account": "Robinhood"}]
    income = [{"symbol": "KO", "day": "2026-10-01", "amount": 53.0, "kind": "dividend", "account": "Robinhood"},
              {"symbol": "KO", "day": "2026-07-01", "amount": 53.0, "kind": "dividend", "account": "Robinhood"}]
    positions = [{"symbol": "KO", "quantity": 110, "market_value": 7700.0, "cost_basis": 5600.0},
                 {"symbol": "NVDA", "quantity": 1, "market_value": 180.0, "cost_basis": 100.0}]

    def get(url, params=None, headers=None, ttl=None):
        if "yahoo" in url:
            return _yahoo(KO if "/KO" in url else [])
        return {"data": {"dividends": {"rows": None}}}
    d = dv.build(positions, txs, income, TODAY, get=get)
    ko = d["holdings"][0]
    assert ko["symbol"] == "KO" and ko["annual_income"] == round(0.53 * 4 * 110, 2)
    assert ko["yield_on_cost"] == round(0.53 * 4 * 110 / 5600 * 100, 2)
    # Robinhood's rows are used as recorded; Stash (no rows) is estimated from ex-dates x shares.
    stash = [r for r in d["received"] if r["account"] == "Stash"]
    assert stash and all(r["estimated"] for r in stash)
    assert {r["day"] for r in stash} == {"2026-03-13", "2026-06-12", "2026-09-12"}
    assert stash[0]["amount"] == 5.3
    assert not any(r["account"] == "Robinhood" and r["estimated"] for r in d["received"])
    # Next ex-date projected from the ~quarterly spacing, marked estimated
    assert d["upcoming"][0]["symbol"] == "KO" and d["upcoming"][0]["estimated"]
    assert "2026-12" in d["upcoming"][0]["ex_date"] or "2026-11" in d["upcoming"][0]["ex_date"]
    assert len(d["months"]) == 13 and round(sum(m["amount"] for m in d["months"]), 2) == round(0.53 * 110 * 4, 2)
    assert [h["symbol"] for h in d["holdings"]] == ["KO"]      # NVDA pays nothing in this fixture


def test_declared_date_replaces_projection():
    positions = [{"symbol": "KO", "quantity": 10, "market_value": 700.0, "cost_basis": 500.0}]
    txs = [{"id": 1, "symbol": "KO", "side": "buy", "quantity": 10, "price": 50.0, "date": "2025-01-02", "account": ""}]

    def get(url, params=None, headers=None, ttl=None):
        if "yahoo" in url:
            return _yahoo(KO)
        return {"data": {"dividends": {"rows": [{"exOrEffDate": "11/27/2026", "type": "Cash", "amount": "$0.55",
                                                 "paymentDate": "12/15/2026"}]}}}
    d = dv.build(positions, txs, [], TODAY, get=get)
    u = d["upcoming"][0]
    assert u == {"symbol": "KO", "ex_date": "2026-11-27", "pay_date": "2026-12-15", "per_share": 0.55, "estimated": False, "amount": 5.5}
    assert sum(1 for m in d["months"] if m["amount"]) == 4


def test_nasdaq_history_is_the_fallback_when_yahoo_fails():
    from market_tracker import http

    def get(url, params=None, headers=None, ttl=None):
        if "yahoo" in url:
            raise http.DataUnavailable("429")
        return {"data": {"dividends": {"rows": [{"exOrEffDate": "08/10/2026", "type": "Cash", "amount": "$0.27"},
                                                {"exOrEffDate": "05/11/2026", "type": "Cash", "amount": "$0.26"}]}}}
    assert dv.history_any("AAPL", get) == [("2026-05-11", 0.26), ("2026-08-10", 0.27)]
