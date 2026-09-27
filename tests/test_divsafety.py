from datetime import date

from market_tracker import divsafety

QUARTERS = [("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31"), ("2026-01-01", "2026-03-31"), ("2026-04-01", "2026-06-30")]


def facts(div, ocf, capex, net, op, da, debt, cash):
    def flow(v):
        return {"units": {"USD": [{"start": s, "end": e, "val": v, "form": "10-Q", "filed": "2026-08-01"} for s, e in QUARTERS]}}

    def inst(v):
        return {"units": {"USD": [{"end": "2026-06-30", "val": v, "form": "10-Q", "filed": "2026-08-01"}]}}
    return {"facts": {"us-gaap": {"PaymentsOfDividendsCommonStock": flow(div), "NetCashProvidedByUsedInOperatingActivities": flow(ocf),
                                  "PaymentsToAcquirePropertyPlantAndEquipment": flow(capex), "NetIncomeLoss": flow(net),
                                  "OperatingIncomeLoss": flow(op), "DepreciationDepletionAndAmortization": flow(da),
                                  "LongTermDebt": inst(debt), "CashAndCashEquivalentsAtCarryingValue": inst(cash)}}}


def growing(years=12, start=2014):
    hist, amt = [], 0.40
    for y in range(start, start + years + 1):
        for m in ("03", "06", "09", "12"):
            hist.append((f"{y}-{m}-15", round(amt, 4)))
        amt *= 1.05
    return hist


def test_well_covered_grower_gets_an_a():
    f = facts(div=100, ocf=400, capex=100, net=250, op=300, da=50, debt=500, cash=200)
    g = divsafety.assess(f, growing(), date(2026, 9, 27))
    assert g["fcf_payout"] == 33.3 and g["net_debt_ebitda"] == 0.21 and g["history"]["years_growing"] >= 10
    assert g["grade"] == "A" and not g["history"]["cut"]


def test_paying_more_than_it_earns_with_heavy_debt_and_a_cut_is_an_f():
    hist = [("2024-03-15", 0.5), ("2024-06-15", 0.5), ("2024-09-15", 0.5), ("2024-12-15", 0.25), ("2025-03-15", 0.25),
            ("2025-06-15", 0.25), ("2025-09-15", 0.25), ("2025-12-15", 0.25), ("2026-03-15", 0.25), ("2026-06-15", 0.25)]
    f = facts(div=300, ocf=300, capex=100, net=150, op=100, da=20, debt=2000, cash=100)
    g = divsafety.assess(f, hist, date(2026, 9, 27))
    assert g["fcf_payout"] == 150.0 and g["net_debt_ebitda"] > 3.5
    assert g["history"]["cut"]["date"] == "2024-12-15"
    assert g["grade"] == "F" and any("more than it brings in" in r for r in g["reasons"]) and any("Cut the dividend" in r for r in g["reasons"])


def test_special_dividend_is_not_a_cut_and_reits_get_a_caveat():
    hist = [("2025-03-15", 0.5), ("2025-06-15", 0.5), ("2025-07-01", 3.0), ("2025-09-15", 0.5), ("2025-12-15", 0.52), ("2026-03-15", 0.52)]
    assert divsafety.streak(hist, date(2026, 9, 27))["cut"] is None
    f = facts(div=380, ocf=400, capex=0, net=200, op=300, da=100, debt=1500, cash=100)
    g = divsafety.assess(f, hist, date(2026, 9, 27), sic="6798")
    assert g["reit"] and any("real estate trust" in r for r in g["reasons"])


def test_negative_free_cash_flow_is_flagged():
    g = divsafety.grade({"fcf": -10.0, "fcf_payout": None, "history": {}})
    assert g["score"] == 50 and "negative" in g["reasons"][0]
