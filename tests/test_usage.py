from datetime import date, timedelta

from market_tracker import db, usage


def test_record_view_suggest_and_hide():
    with db.connect() as conn:
        db.set_meta(conn, "usage", "{}")
        db.set_meta(conn, "hidden_pages", "[]")
        d0 = date(2026, 8, 1)
        for i in range(35):
            usage.record(conn, "home", d0 + timedelta(days=i))
            if i % 3 == 0:
                usage.record(conn, "ideas", d0 + timedelta(days=i))
        usage.record(conn, "chatter", d0)
        v = usage.view(conn, ["home", "ideas", "chatter", "people", "portfolio"], d0 + timedelta(days=35))
        rows = {r["page"]: r for r in v["pages"]}
        assert rows["home"]["days_last_30"] == 30 and not rows["home"]["suggest_hide"]
        assert set(v["suggest"]) == {"chatter", "people"} and not rows["portfolio"]["suggest_hide"]     # core pages never suggested
        assert usage.set_hidden(conn, "chatter", True) == ["chatter"] and usage.set_hidden(conn, "home", True) == ["chatter"]
        v = usage.view(conn, ["chatter", "people"], d0 + timedelta(days=35))
        assert v["suggest"] == ["people"] and v["hidden"] == ["chatter"]
        assert usage.view(conn, ["people"], d0 + timedelta(days=5))["text"].startswith("Suggestions start")
