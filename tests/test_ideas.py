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


def test_sentinel_logs_ideas_once_a_day_without_a_github_log():
    from datetime import datetime, timezone

    from market_tracker import sentinel
    items = [{"symbol": "ZZZ", "source": "qvm", "price": 5.0, "reason": "grade 90"}]
    with db.connect() as conn:
        db.set_meta(conn, "ideas_logged", "")
        db.set_meta(conn, "ideas_synced", "")
    at = lambda h: datetime(2026, 3, 2, h, tzinfo=timezone.utc)  # noqa: E731
    assert sentinel.log_ideas_daily(date(2026, 3, 2), items_fn=lambda d: items, remote_fn=lambda: None, now=at(10)) == 1
    assert sentinel.log_ideas_daily(date(2026, 3, 2), items_fn=lambda d: 1 / 0, remote_fn=lambda: 1 / 0, now=at(10)) == 0   # same hour
    assert sentinel.log_ideas_daily(date(2026, 3, 2), items_fn=lambda d: 1 / 0, remote_fn=lambda: None, now=at(11)) == 0   # already today


def test_github_log_append_seal_verify_and_tamper():
    from datetime import datetime, timezone
    rows, chain = [], []
    now = datetime(2026, 3, 2, 21, 30, tzinfo=timezone.utc)
    new = ideas.append_day(rows, chain, "2026-03-02", [{"symbol": "AAA", "source": "qvm", "price": 10, "reason": "grade 91"},
                                                       {"symbol": "BBB", "source": "backlog", "reason": "backlog +40%"},
                                                       {"symbol": "NOP", "source": "qvm"}],
                           price_fn=lambda s: {"BBB": 20.0}.get(s), now=now)
    assert [r["symbol"] for r in new] == ["AAA", "BBB"] and chain[0]["ideas"] == 2          # no price, no record
    assert ideas.append_day(rows, chain, "2026-03-02", [{"symbol": "CCC", "source": "qvm", "price": 1}]) is None   # sealed
    assert ideas.append_day(rows, chain, "2026-03-03", [{"symbol": "AAA", "source": "qvm", "price": 11}], now=now) == []
    assert len(chain) == 2 and chain[1]["ideas"] == 0 and chain[1]["prev"] == chain[0]["hash"]    # empty days are sealed too
    assert ideas.verify_log(rows, chain) == []
    tampered = [dict(r, price=9.0) if r["symbol"] == "AAA" else r for r in rows]
    assert any("don't match the seal" in p for p in ideas.verify_log(tampered, chain))
    assert any("unsealed" in p for p in ideas.verify_log(rows + [dict(rows[0], day="2026-03-09")], chain))
    with pytest.raises(ValueError):
        ideas.append_day(rows, chain, "2026-03-04", [{"symbol": "X", "source": "hunch", "price": 1}])


def test_run_job_writes_files_and_refuses_a_broken_log(tmp_path):
    items = lambda d: [{"symbol": "AAA", "source": "qvm", "price": 10.0, "reason": "grade 91"}]  # noqa: E731
    out = ideas.run_job(str(tmp_path), date(2026, 3, 2), items, log=lambda m: None)
    assert out == {"new": 1, "sealed": True, "total": 1}
    assert ideas.run_job(str(tmp_path), date(2026, 3, 2), items, log=lambda m: None) == {"new": 0, "sealed": False}
    lp = tmp_path / ideas.LOG_FILE
    lp.write_text(lp.read_text().replace("grade 91", "grade 99"))
    with pytest.raises(RuntimeError):
        ideas.run_job(str(tmp_path), date(2026, 3, 3), items, log=lambda m: None)


def test_sync_imports_the_github_log_once():
    rows = [ideas.record("2026-04-01", {"symbol": "SYN", "source": "qvm", "reason": "grade 88"}, 12.5),
            ideas.record("2026-04-01", {"symbol": "SYN2", "source": "hunch"}, 1.0),
            ideas.record("2026-04-01", {"symbol": "LOC", "source": "backlog"}, 3.0)]
    with db.connect() as conn:
        ideas.log(conn, "2026-03-25", [{"symbol": "LOC", "source": "backlog", "price": 2.9}])   # the app logged it first
        assert ideas.sync(conn, rows) == 1 and ideas.sync(conn, rows) == 0
        assert ideas.has_github_rows(conn)
        got = [r for r in ideas.logged(conn) if r["symbol"] == "SYN"][0]
        assert got["data"]["logged_by"] == "github" and got["price"] == 12.5
    assert ideas.load_remote(get=lambda u: "\n".join(__import__("json").dumps(r) for r in rows) + "\n")[0]["symbol"] == "SYN"
    from market_tracker import http

    def down(u):
        raise http.DataUnavailable("404")
    assert ideas.load_remote(get=down) is None


def test_daily_items_survive_a_broken_source(monkeypatch):
    from market_tracker import idealab, screen
    from market_tracker.providers import market
    monkeypatch.setattr(screen, "load", lambda get=None: {"top_large": [{"symbol": "BIG", "score": 90.0, "price": 1.0, "grades": {}}],
                                                         "top_all": [{"symbol": "AAA", "score": 81.0, "price": 10.0, "grades": {}}],
                                                         "small_mid": [{"symbol": "AAA", "score": 81.0, "price": 10.0, "grades": {}}],
                                                         "backlog": [{"symbol": "BBB", "why": "Backlog +40%", "price": 5.0}]})
    monkeypatch.setattr(idealab, "sleepers", lambda today=None: 1 / 0)
    monkeypatch.setattr(idealab, "chatter", lambda: {"rows": [{"symbol": "CCC", "reddit": 99, "stocktwits": True}]})

    def boom(today=None):
        raise RuntimeError("site changed")
    monkeypatch.setattr(idealab, "events", boom)
    monkeypatch.setattr(market, "get_quote", lambda s: market.Quote(s, "stock", 7.0, 7.0, 0.0, "USD", "t", "2026-01-01"))
    got = {(i["symbol"], i["source"], i["price"]) for i in idealab.daily_items(date(2026, 3, 2))}
    assert got == {("AAA", "qvm", 10.0), ("BBB", "backlog", 5.0), ("CCC", "chatter", 7.0)}
