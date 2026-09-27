from market_tracker import macro

CSV = """observation_date,DGS10,BAMLH0A0HYM2,SAHMREALTIME,T10Y3M
2026-01-02,4.50,3.00,0.10,0.5
2026-04-01,4.80,3.20,0.20,0.2
2026-09-24,5.10,.,0.55,-0.3
2026-09-25,5.18,4.90,.,-0.4
"""


def test_parse_skips_missing_values():
    d = macro.parse(CSV)
    assert d["DGS10"][-1] == ("2026-09-25", 5.18) and len(d["BAMLH0A0HYM2"]) == 3 and d["SAHMREALTIME"][-1] == ("2026-09-24", 0.55)


def test_gauges_and_pace():
    g = {x["id"]: x for x in macro.gauges(macro.parse(CSV))}
    assert g["SAHMREALTIME"]["state"] == "stress" and g["T10Y3M"]["state"] == "watch"
    assert g["BAMLH0A0HYM2"]["state"] == "stress"           # up 1.7 points in three months
    p = macro.pace(list(g.values()))
    assert p["level"] == "stressed" and p["tranches"] == 6 and "Don't sell" in p["text"]
    calm = [dict(x, state="normal") for x in g.values()]
    assert macro.pace(calm)["level"] == "normal"
