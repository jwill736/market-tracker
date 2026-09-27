from datetime import date

from market_tracker import hype


def test_valuation_against_its_own_history():
    ends = [f"20{y}-{m}-30" for y in range(21, 27) for m in ("03", "06", "09", "12") if f"20{y}-{m}" <= "2026-06"]
    eps = {e: 1.0 for e in ends}
    bars = [(e, 20.0 + (int(e[2:4]) - 21) * 2) for e in ends]
    bars.append(("2026-09-25", 22.0))
    v = hype.valuation(eps, bars)
    assert v["pe"] == 5.5 and v["pct"] <= 10 and v["high"] == 7.5      # four quarters of $1: P/E 22/4, the cheapest of its last 20
    rich = hype.valuation(eps, bars[:-1] + [("2026-09-25", 200.0)])
    assert rich["pct"] == 100
    assert hype.valuation({k: -1.0 for k in eps}, bars)["pe"] is None


def test_flags_and_crowded_themes():
    row = {"return_12m_pct": 97, "above_200d": 0.45, "asset_growth": 0.4, "issuance": 0.08}
    f = hype.flags("OKLO", row, {"pe": 80.0, "pct": 95, "low": 20.0, "high": 90.0, "quarters": 20}, {"Nuclear power": "crowded"})
    texts = " ".join(x["text"] for x in f)
    assert len(f) == 6 and "top 5%" in texts and "45% above" in texts and "diluted" in texts and "crowded theme (Nuclear power)" in texts
    assert hype.flags("KO", {"return_12m_pct": 40, "above_200d": 0.02}, None) == []


def test_theme_counts():
    counts = {'"nuclear"': (60, 30), '"uranium"': (10, 12)}

    def count(q, a, b):
        n = counts.get(q, (5, 5))
        return n[0] if (date(2026, 9, 27) - b).days < 30 else n[1]
    rows = {r["theme"]: r for r in hype.themes(date(2026, 9, 27), count)}
    assert rows["Nuclear power"]["level"] == "crowded" and rows["Nuclear power"]["ratio"] == 2.0
    assert rows["Uranium"]["level"] == "quiet"
    assert hype.theme_of("CCJ") == ["Uranium"]
