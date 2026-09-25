from datetime import date, datetime, timezone

from market_tracker import brief, db, sentinel
from market_tracker.providers import market

TODAY = date(2026, 9, 28)      # a Monday


def q(sym, chg, session="pre"):
    return market.Quote(sym, "stock", 100.0, 100.0 / (1 + chg / 100), chg, "USD", "t", "t", session=session)


PLAN = {
    "holdings": [
        {"symbol": "GPUS", "verdict": "Sell?", "value": 180.0, "triggers": [
            {"kind": "thesis", "level": "info", "text": "No reason written down yet"},
            {"kind": "tripwire", "level": "sell", "text": "Your line: sell below $0.20. It's $0.18"}]},
        {"symbol": "NVDA", "verdict": "Hold", "value": 9000.0, "triggers": []},
        {"symbol": "KO", "verdict": "Hold", "value": 1000.0, "triggers": []}],
    "radar": {"KO": [{"level": 2, "headline": "Auditor change", "filed": "2026-09-27", "level_name": "Serious"},
                     {"level": 2, "headline": "Old news", "filed": "2026-08-01"}]},
    "events": {"earnings": [{"symbol": "NVDA", "date": "2026-09-29", "estimated": False, "move_pct": 8.0, "move_dollars": 720.0}],
               "macro": [{"date": "2026-09-28", "kind": "Inflation (CPI)", "consensus": "2.9%"},
                         {"date": "2026-10-09", "kind": "Jobs report", "consensus": None}]},
    "tax": {"clock": [{"symbol": "NVDA", "bought": "2025-09-30", "long_term_on": "2026-10-01", "days": 3, "saving": 108.0}],
            "blackout": [{"symbol": "AAPL", "until": "2026-09-30", "avoid": ["AAPL"], "sold": "2026-08-30", "loss": 50}]},
}


def test_compose_orders_what_needs_you_first():
    b = brief.compose(today=TODAY, plan=PLAN, quotes={"NVDA": q("NVDA", 3.0), "KO": q("KO", -0.5)},
                      early_data={"signals": [{"symbol": "KO", "strength": 40, "early": True, "signals": [{"headline": "KO deal"}]},
                                              {"symbol": "ZZZ", "strength": 90, "signals": [{"headline": "not yours"}]}]},
                      crypto={"alerts": [{"level": 2, "text": "Bitget lost $387M", "date": "2026-09-28", "coins": []}]})
    texts = [ln["text"] for ln in b["lines"]]
    assert b["title"] == "Morning brief: 1 holding needs a decision"
    assert texts[0].startswith("Portfolio +") and "GPUS: Sell? - Your line: sell below $0.20" in texts[1]
    assert any(t == "KO: Auditor change (Serious)" for t in texts) and not any("Old news" in t for t in texts)
    assert any("NVDA reports tomorrow: options price about ±8.0%, ±$720 on yours" == t for t in texts)
    assert any(t.startswith("Inflation (CPI) today, forecast 2.9%") for t in texts) and not any("Jobs" in t for t in texts)
    assert any(t.startswith("NVDA +3.0% pre-market (+$262 on yours)") for t in texts)
    assert any("KO deal (not in the mainstream yet)" in t for t in texts) and not any("not yours" in t for t in texts)
    assert any("Bitget" in t for t in texts)
    assert any("NVDA shares bought 2025-09-30 turn long-term Thu Oct 1" in t for t in texts)
    assert any("From Wed Sep 30 you can buy AAPL again" in t for t in texts)


def test_quiet_day_and_schedule():
    b = brief.compose(today=TODAY, plan={"holdings": [{"symbol": "VTI", "verdict": "Hold", "value": 1000.0, "triggers": []}]},
                      quotes={})
    assert b["title"] == "Morning brief: nothing needs you today" and "Holding is the plan" in b["lines"][-1]["text"]
    monday_827 = datetime(2026, 9, 28, 12, 27, tzinfo=timezone.utc)       # 8:27 ET (EDT)
    monday_831 = datetime(2026, 9, 28, 12, 31, tzinfo=timezone.utc)
    saturday = datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc)
    assert not brief.due(monday_827, "") and brief.due(monday_831, "") and not brief.due(monday_831, "2026-09-28")
    assert not brief.due(saturday, "")


def test_sentinel_sends_once(monkeypatch):
    sent = []
    monkeypatch.setattr(sentinel.notify, "send", lambda m: sent.append(m) or True)
    fake = {"title": "Morning brief: nothing needs you today", "date": "2026-09-28", "generated_at": "x",
            "lines": [{"section": "All quiet", "text": "Holding is the plan.", "level": 0, "symbol": ""}]}
    now = datetime(2026, 9, 28, 12, 31, tzinfo=timezone.utc)
    with db.connect() as conn:
        db.set_meta(conn, "brief_sent", "")
    assert sentinel.send_brief_if_due(now, gather_fn=lambda now: fake)
    assert not sentinel.send_brief_if_due(now, gather_fn=lambda now: fake)
    assert len(sent) == 1 and sent[0].title == fake["title"] and "Holding is the plan." in sent[0].body


def test_gather_uses_the_last_early_scan_instead_of_running_one(monkeypatch):
    from market_tracker import holdplan
    monkeypatch.setattr(holdplan, "gather", lambda today=None: {"holdings": [{"symbol": "KO", "verdict": "Hold", "value": 100.0,
                                                                          "triggers": []}], "radar": {}, "events": {}, "tax": {}})
    monkeypatch.setattr(market, "get_live_quote", lambda s: q(s, 0.1, "regular"))
    monkeypatch.setattr(sentinel, "build_early", lambda mine: (_ for _ in ()).throw(AssertionError("scanned inline")))
    sentinel.early_cache.clear()
    holdplan.clear_cache()
    b = brief.gather(now=datetime(2026, 9, 28, 12, 31, tzinfo=timezone.utc))
    assert not any(ln["section"] == "Early wire" for ln in b["lines"])
    sentinel.early_cache.get("early", lambda: {"signals": [{"symbol": "KO", "strength": 50, "early": True,
                                                           "signals": [{"headline": "KO deal"}]}]})
    b = brief.gather(now=datetime(2026, 9, 28, 12, 31, tzinfo=timezone.utc))
    assert any("KO deal" in ln["text"] for ln in b["lines"])
