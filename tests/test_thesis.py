from datetime import date, datetime, timezone

from market_tracker import db, notify, sentinel, thesis
from market_tracker.providers.sec import InsiderTrade


def _idea(sym, source, day="2026-01-05", edge=0.0, decision="bought", id_=1):
    return {"id": id_, "symbol": sym, "source": source, "day": day, "decision": decision, "so_far": {"edge": edge}}


def test_reasons_per_kind_of_idea():
    today = date(2026, 9, 27)
    assert thesis.reasons(_idea("A", "qvm"), today, {"score": 44}) == ["screen grade fell to 44/100"]
    assert thesis.reasons(_idea("A", "qvm", day="2025-06-01", edge=-18), today, {"score": 60}) == ["trails VOO by 18 points after a year"]
    assert thesis.reasons(_idea("B", "backlog"), today, {"rpo_growth": 0.05, "revenue_growth": 0.12})[0].startswith("backlog now growing slower")
    trades = [InsiderTrade("CEO X", "CEO", "2026-03-01", "S", 10000, 50.0, False, 0),
              InsiderTrade("DIR Y", "Director", "2026-02-01", "P", 1000, 40.0, True, 0),
              InsiderTrade("CEO X", "CEO", "2025-12-01", "S", 99999, 50.0, False, 0)]          # before the idea: ignored
    r = thesis.reasons(_idea("C", "sleeper"), today, {"score": 72}, insider_trades=trades)
    assert r == ["insiders sold $500,000 since the idea (bought $40,000)"]
    recap = {"release": {"filed": "2026-08-01"}, "outlook": {"direction": "lowered"}}
    assert thesis.reasons(_idea("D", "pead", day="2026-05-01", edge=-12), today, None, recap=recap) == [
        "outlook lowered in the 2026-08-01 results release", "trails VOO by 12 points after 3 months"]
    assert thesis.reasons(_idea("E", "chatter", edge=-31), today, None) == ["trails VOO by 31 points since 2026-01-05"]
    assert thesis.reasons(_idea("F", "chatter", edge=-10), today, {"score": 20}) == []        # chatter isn't held to a grade


def test_watched_and_check():
    items = [_idea("HELD", "qvm", decision="", id_=1), _idea("GONE", "qvm", decision="", id_=2),
             _idea("NOPE", "qvm", decision="passed", id_=3), _idea("BUY", "qvm", id_=4),
             _idea("BUY", "qvm", day="2025-12-01", id_=5)]
    w = thesis.watched(items, {"HELD", "NOPE"})
    assert [(i["symbol"], i["id"]) for i in w] == [("HELD", 1), ("BUY", 4)]
    found = thesis.check(items, {"HELD"}, date(2026, 9, 27), lambda s: {"score": 30})
    assert [f["symbol"] for f in found] == ["HELD", "BUY"] and found[0]["reasons"] == ["screen grade fell to 30/100"]


def test_weekly_push_once_per_problem(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda m: sent.append(m))
    with db.connect() as conn:
        db.set_meta(conn, "thesis_week", "")
        db.set_meta(conn, "thesis_alerted", "[]")
    found = lambda today, items_fn: [{"symbol": "A", "source": "qvm", "day": "2026-01-05", "reasons": ["screen grade fell to 44/100"]}]  # noqa: E731
    sun = datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc)                    # Sunday 2pm ET
    assert sentinel.thesis_check_weekly(sun, check_fn=found) == 1
    assert sentinel.thesis_check_weekly(sun, check_fn=found) == 0              # same week
    with db.connect() as conn:
        db.set_meta(conn, "thesis_week", "")
    later = lambda today, items_fn: [{"symbol": "A", "source": "qvm", "day": "2026-01-05", "reasons": ["screen grade fell to 41/100"]}]  # noqa: E731
    assert sentinel.thesis_check_weekly(datetime(2026, 10, 4, 18, tzinfo=timezone.utc), check_fn=later) == 0   # same problem, new number
    assert "the reason you bought may be gone" in sent[0].title
    early_sunday = datetime(2026, 10, 11, 13, 0, tzinfo=timezone.utc)          # 9am ET Sunday: still last week's check
    with db.connect() as conn:
        assert db.get_meta(conn, "thesis_week", "") == "2026-10-04"
    assert sentinel.thesis_check_weekly(early_sunday, check_fn=lambda *a: 1 / 0) == 0
