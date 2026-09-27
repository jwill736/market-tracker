from datetime import date

from market_tracker import moneyflow


def _facts(vals):
    rows = [{"start": s, "end": e, "val": v, "form": "10-Q", "filed": "2026-08-01"} for (s, e), v in vals]
    return {"facts": {"us-gaap": {"PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": rows}}}}}


QTRS = [("2024-07-01", "2024-09-30"), ("2024-10-01", "2024-12-31"), ("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
        ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31"), ("2026-01-01", "2026-03-31"), ("2026-04-01", "2026-06-30")]


def test_capex_ttm_and_waves():
    f = _facts([(q, 10.0 if i < 4 else 15.0) for i, q in enumerate(QTRS)])
    assert moneyflow.capex_ttm(f) == (60.0, 40.0, "2026-06-30")
    rows = moneyflow.waves(lambda s: f, lambda s: {"score": 70, "return_12m_pct": 95, "above_200d": 0.1} if s == "NVDA" else None)
    ai = next(w for w in rows if w["wave"].startswith("Cloud"))
    assert ai["growth"] == 0.5 and ai["total"] == 300.0
    chips = next(c for c in ai["suppliers"] if c["category"] == "AI chips")
    nvda = next(x for x in chips["companies"] if x["symbol"] == "NVDA")
    assert nvda["priced_in"] == "Already ran hard" and chips["companies"][1]["priced_in"] is None


def test_contracts_matches_the_company_and_compares_to_revenue():
    def post(url, json):
        assert json["filters"]["recipient_search_text"] == ["LOCKHEED MARTIN"]
        return {"results": [{"name": "LOCKHEED MARTIN CORPORATION", "amount": 25e9}, {"name": "LOCKHEED MARTIN CORP", "amount": 5e9},
                            {"name": "SIKORSKY AIRCRAFT", "amount": 1e9}]}
    r = moneyflow.contracts("Lockheed Martin Corp", 75e9, date(2026, 9, 27), post)
    assert r["amount"] == 30e9 and r["share"] == 0.4 and r["material"] and "40% of revenue" in r["text"]


def test_suppliers_of_and_lagging():
    hits = {"hits": {"hits": [{"_source": {"display_names": ["CIRRUS LOGIC INC  (CRUS)  (CIK 0000772406)"]}},
                              {"_source": {"display_names": ["Apple Inc.  (AAPL)  (CIK 0000320193)"]}}]}}
    assert moneyflow.suppliers_of("Apple", date(2026, 9, 27), get=lambda url, params: hits) == ["CRUS", "AAPL"]
    closes = {"AAPL": [100.0] * 21 + [115.0], "CRUS": [50.0] * 21 + [50.5], "BIGCO": [10.0] * 21 + [10.0]}
    lag = moneyflow.lagging({"AAPL": "Apple"}, lambda n: ["CRUS", "AAPL", "BIGCO"], lambda s: closes[s],
                            lambda s: {"CRUS": {"cap": 5e9, "score": 60}, "BIGCO": {"cap": 5e10}}.get(s))
    assert [x["symbol"] for x in lag] == ["CRUS"] and "Apple +15% in a month" in lag[0]["why"]
