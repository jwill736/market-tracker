from datetime import datetime, timedelta, timezone

from market_tracker import cooloff, db

NOW = datetime(2026, 9, 28, 15, tzinfo=timezone.utc)


def test_rule_backed_sells_pass_and_others_wait_48_hours():
    plan = {"holdings": [{"symbol": "BAD", "verdict": "Sell?"}, {"symbol": "OK", "verdict": "Hold"}], "tax": {"harvest": [{"symbol": "DOWN"}]}}
    opened = [{"kind": "trim", "symbol": "BIG", "title": "Trim BIG by $6,000"}]
    for sym in ("BAD", "DOWN", "BIG"):
        warn, block, cooling = cooloff.gate(sym, plan, opened, None, NOW)
        assert not block and cooling is None and warn[0].startswith("Backed by a rule")
    warn, block, cooling = cooloff.gate("OK", plan, opened, None, NOW)
    assert cooling["state"] == "ask" and "write down why" in block[0]
    with db.connect() as conn:
        db.set_meta(conn, "cooloff", "{}")
        cooloff.start(conn, "OK", "Down 20% after earnings, I'm nervous", NOW)
        entry = cooloff.load(conn)["OK"]
    _, block, cooling = cooloff.gate("OK", plan, opened, entry, NOW + timedelta(hours=10))
    assert cooling["state"] == "waiting" and cooling["left_hours"] == 38.0 and "nervous" in block[0]
    warn, block, cooling = cooloff.gate("OK", plan, opened, entry, NOW + timedelta(hours=49))
    assert not block and "Still true?" in warn[0]
    assert cooloff.check(entry, NOW + timedelta(days=20))["state"] == "ask"          # an old reason doesn't cover a new sell


def test_override_skips_the_wait_and_is_logged():
    with db.connect() as conn:
        db.set_meta(conn, "cooloff", "{}")
        db.set_meta(conn, "cooloff_log", "[]")
        cooloff.start(conn, "OK", "Need the cash for a car this week", NOW, override=True)
        entry = cooloff.load(conn)["OK"]
        log = __import__("json").loads(db.get_meta(conn, "cooloff_log", "[]"))
    assert cooloff.check(entry, NOW)["state"] == "done" and log[-1]["override"] is True


def test_the_wait_is_shown_in_eastern_time():
    c = cooloff.check({"reason": "x" * 12, "started": NOW.isoformat()}, NOW)
    assert c["until_text"] == "Wed Sep 30, 11:00am ET"
