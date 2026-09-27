import sqlite3
from datetime import date, timedelta

import pytest

from market_tracker import db, ideas


def _bars(start, n, p0, step):
    d0 = date.fromisoformat(start)
    return [((d0 + timedelta(days=i)).isoformat(), p0 * (1 + step) ** i) for i in range(n)]


def test_log_dedupes_and_is_write_once():
    with db.connect() as conn:
        n = ideas.log(conn, "2026-01-05", [{"symbol": "AAA", "source": "qvm", "reason": "top decile", "price": 10.0},
                                          {"symbol": "BBB", "source": "qvm", "reason": "x"}], price_fn=lambda s: 20.0)
        assert n == 2
        assert ideas.log(conn, "2026-01-20", [{"symbol": "AAA", "source": "qvm", "price": 11.0}]) == 0      # within 30 days
        assert ideas.log(conn, "2026-01-20", [{"symbol": "AAA", "source": "insider", "price": 11.0}]) == 1   # another screen
        rid = conn.execute("SELECT id FROM ideas WHERE symbol='AAA' AND source='qvm'").fetchone()["id"]
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE ideas SET price = 1 WHERE id = ?", (rid,))
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM ideas WHERE id = ?", (rid,))
        assert ideas.decide(conn, rid, "bought", "2026-01-06") and not ideas.decide(conn, rid, "passed", "2026-01-07")
        with pytest.raises(ValueError):
            ideas.log(conn, "2026-01-05", [{"symbol": "CCC", "source": "tips-from-a-friend", "price": 1.0}])


def test_score_against_voo_and_leaderboard():
    rows = [{"id": 1, "day": "2025-01-02", "symbol": "WIN", "source": "qvm", "price": 100.0, "decision": "bought"},
            {"id": 2, "day": "2025-01-02", "symbol": "LOSE", "source": "chatter", "price": 100.0, "decision": ""}]
    hist = {"VOO": _bars("2025-01-01", 500, 100.0, 0.0003), "WIN": _bars("2025-01-01", 500, 100.0, 0.001),
            "LOSE": _bars("2025-01-01", 500, 100.0, -0.001)}
    r = ideas.score(rows, lambda s: hist[s], date(2026, 5, 1))
    win = next(i for i in r["items"] if i["symbol"] == "WIN")
    assert set(win["results"]) == {"3m", "6m", "12m"} and win["results"]["12m"]["edge"] > 20
    board = {b["source"]: b for b in r["leaderboard"]}
    assert board["qvm"]["6m"]["beat_voo"] == 100 and board["chatter"]["6m"]["beat_voo"] == 0
    assert board["chatter"]["12m"]["worst"] < -20 and not board["qvm"]["6m"]["enough"]
    assert r["decisions"]["bought"]["n"] == 1 and r["verdict"].startswith("Too early to tell")


def test_sentinel_logs_ideas_once_a_day():
    from market_tracker import sentinel
    items = [{"symbol": "ZZZ", "source": "qvm", "price": 5.0, "reason": "grade 90"}]
    with db.connect() as conn:
        db.set_meta(conn, "ideas_logged", "")
    assert sentinel.log_ideas_daily(date(2026, 3, 2), items_fn=lambda d: items) == 1
    assert sentinel.log_ideas_daily(date(2026, 3, 2), items_fn=lambda d: 1 / 0) == 0          # already ran today: not even computed
