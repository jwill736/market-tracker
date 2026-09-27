from datetime import date, datetime, timezone

from market_tracker import db, sentinel, weekly


def test_week_change_on_shares_held_now():
    closes = {"AAA": [("2026-09-18", 100.0), ("2026-09-19", 100.0), ("2026-09-25", 110.0)],
              "BBB": [("2026-09-19", 50.0), ("2026-09-25", 45.0)]}
    positions = [{"symbol": "AAA", "quantity": 10, "price": 110.0}, {"symbol": "BBB", "quantity": 20, "price": 45.0},
                 {"symbol": "NEW", "quantity": 1, "price": 5.0}]
    w = weekly.week_change(positions, closes, date(2026, 9, 27))
    assert [r["symbol"] for r in w["rows"]] == ["AAA", "BBB"] and w["total"] == 0.0
    assert w["rows"][0]["change"] == 100.0 and w["rows"][1]["change"] == -100.0


def test_compose_collects_the_week():
    now = datetime(2026, 9, 27, 21, 0, tzinfo=timezone.utc)
    week = {"total": 250.0, "pct": 1.2, "rows": [{"symbol": "AAA", "change": 300.0, "change_pct": 3.0, "value": 10300.0},
                                                  {"symbol": "BBB", "change": -50.0, "change_pct": -1.0, "value": 4950.0}]}
    plan = {"holdings": [{"symbol": "BBB", "verdict": "Review", "triggers": [{"level": "warn", "text": "revenue fell 2 quarters"}]}],
            "events": {"earnings": [{"symbol": "AAA", "date": "2026-09-30", "move_pct": 6.1}],
                       "macro": [{"kind": "Jobs report", "date": "2026-10-02"}, {"kind": "Jobs report", "date": "2026-10-02"}]}}
    desk = {"AAA": {"stories": [{"tier": "A", "first": "2026-09-24T10:00:00+00:00", "title": "AAA wins contract", "sources": 3},
                                {"tier": "C", "first": "2026-09-25T10:00:00+00:00", "title": "Rumor", "sources": 1},
                                {"tier": "B", "first": "2026-09-01T10:00:00+00:00", "title": "Old news", "sources": 2}]}}
    heads = [{"key": "bigmove:AAA:2026-09-24:up", "title": "AAA up 7.0%", "at": "2026-09-24T20:00:00+00:00", "symbol": "AAA"}]
    conf = {"score": 62.0, "total_value": 15000.0, "fixes": [{"level": 2, "text": "Stash VOO: 1.2 shares differ"}]}
    r = weekly.compose(today=date(2026, 9, 27), week=week, plan=plan, desk=desk, headsups=heads, confidence=conf, now=now)
    texts = [ln["text"] for ln in r["lines"]]
    assert r["title"] == "Your week: +$250 · 1 holding needs a decision"
    assert texts[0] == "Your holdings +$250 (+1.2%) this week"
    assert "BBB: Review - revenue fell 2 quarters" in texts and "AAA: AAA wins contract (3 outlets)" in texts
    assert not any("Rumor" in t or "Old news" in t for t in texts) and "AAA up 7.0%" in texts
    assert "AAA reports Wed Sep 30, options price about ±6.1%" in texts and texts.count("Jobs report Fri Oct 2") == 1
    assert "62% of your money was checked against a broker or statement lately" in texts and "Stash VOO: 1.2 shares differ" in texts
    assert "+$300" in weekly.push_text(r)


def test_due_sunday_evening_once():
    sun_459 = datetime(2026, 9, 27, 20, 59, tzinfo=timezone.utc)      # 4:59pm EDT
    sun_501 = datetime(2026, 9, 27, 21, 1, tzinfo=timezone.utc)
    assert not weekly.due(sun_459, "") and weekly.due(sun_501, "") and not weekly.due(sun_501, "2026-09-27")
    assert not weekly.due(datetime(2026, 9, 28, 21, 1, tzinfo=timezone.utc), "")


def test_sentinel_weekly_push_respects_cadence(monkeypatch):
    sent = []
    monkeypatch.setattr(sentinel.notify, "send", lambda m: sent.append(m) or True)
    fake = {"title": "Your week: +$1 · nothing needs you", "lines": [{"section": "The week", "text": "Your holdings +$1 this week", "level": 0, "symbol": ""}]}
    now = datetime(2026, 9, 27, 21, 1, tzinfo=timezone.utc)
    with db.connect() as conn:
        db.set_meta(conn, "weekly_sent", "")
        db.set_meta(conn, "push_cadence", "daily")
    assert sentinel.send_weekly_if_due(now, gather_fn=lambda now: fake) and not sent        # saved, not pushed
    with db.connect() as conn:
        db.set_meta(conn, "weekly_sent", "")
        db.set_meta(conn, "push_cadence", "weekly")
    assert sentinel.send_weekly_if_due(now, gather_fn=lambda now: fake)
    assert not sentinel.send_weekly_if_due(now, gather_fn=lambda now: fake)
    assert len(sent) == 1 and sent[0].title == fake["title"]
