import json
from datetime import date, timedelta

from market_tracker import paper


def _bars(n, p0, step, start="2026-01-01"):
    d0 = date.fromisoformat(start)
    return [((d0 + timedelta(days=i)).isoformat(), p0 * (1 + step) ** i) for i in range(n)]


def test_members_books_and_due():
    data = {"top_all": [{"symbol": "A"}, {"symbol": "B"}], "small_mid": [{"symbol": "C"}], "bottom": [{"symbol": "Z"}]}
    m = paper.members(data, [{"symbol": "S", "evidence": 2}, {"symbol": "T", "evidence": 1}],
                      [{"ticker": "NEW", "stage": "trading", "days_trading": 40}, {"ticker": "OLD", "stage": "trading", "days_trading": 400}])
    assert m == {"screen_2b": ["A", "B"], "screen_small_mid": ["C"], "sleepers": ["S"], "spinoffs": ["NEW"], "bottom": ["Z"]}
    books = paper.make_books(date(2026, 2, 2), m, lambda s: None if s == "B" else 10.0)
    b2b = next(b for b in books if b["list"] == "screen_2b")
    assert b2b["prices"] == {"A": 10.0} and b2b["skipped"] == ["B"] and b2b["month"] == "2026-02"
    assert not paper.due(books, date(2026, 2, 20)) and paper.due(books, date(2026, 3, 2))


def test_performance_chains_months_against_voo(tmp_path):
    hist = {"VOO": _bars(120, 100, 0.001), "UP": _bars(120, 10, 0.003), "DN": _bars(120, 10, -0.002)}
    books = [{"month": "2026-01", "day": "2026-01-02", "list": "screen_2b", "prices": {"UP": hist["UP"][1][1]}, "skipped": []},
             {"month": "2026-02", "day": "2026-02-02", "list": "screen_2b", "prices": {"UP": hist["UP"][32][1], "DN": hist["DN"][32][1]}, "skipped": []},
             {"month": "2026-01", "day": "2026-01-02", "list": "bottom", "prices": {"DN": hist["DN"][1][1]}, "skipped": []}]
    out = {p["list"]: p for p in paper.performance(books, lambda s: hist[s], date(2026, 4, 30))}
    s2b = out["screen_2b"]
    assert len(s2b["months"]) == 2 and s2b["months"][0]["to"] == "2026-02-02" and s2b["months"][1]["names"] == 2
    assert s2b["value"] > s2b["voo_value"] and out["bottom"]["ahead"] < 0
    n = paper.run_job(str(tmp_path), date(2026, 3, 2), lambda: {"screen_2b": ["UP"]}, lambda s: 12.0, log=lambda m: None)
    assert n == 1 and paper.run_job(str(tmp_path), date(2026, 3, 9), lambda: {"screen_2b": ["UP"]}, lambda s: 1, log=lambda m: None) == 0
    rows = [json.loads(x) for x in (tmp_path / paper.FILE).read_text().splitlines()]
    assert rows[0]["prices"] == {"UP": 12.0}
