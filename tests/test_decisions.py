from datetime import date, datetime, timezone

from market_tracker import db, decisions, notify, sentinel


def _plan(**kw):
    base = {"holdings": [], "reinvest": [], "tax": {"harvest": []}}
    base.update(kw)
    return base


def test_build_ranks_real_problems_first_and_holds_otherwise():
    plan = _plan(holdings=[
        {"symbol": "BAD", "verdict": "Sell?", "value": 5000.0, "triggers": [{"level": "sell", "text": "Your tripwire: below $40"}],
         "wait_until": "2026-11-02", "wait_saves": 120.0},
        {"symbol": "BIG", "verdict": "Trim", "value": 30000.0, "trim_value": 6000.0, "triggers": [{"level": "trim", "text": "32% of your portfolio"}]},
        {"symbol": "OK", "verdict": "Hold", "value": 9000.0, "wait_until": "2026-10-20", "wait_saves": 300.0}],
        reinvest=[{"symbol": "PLTR", "why": "At your buy price", "source": "dip"}, {"symbol": "VTI", "why": "under your target", "source": "target"}],
        tax={"harvest": [{"symbol": "DOWN", "account": "Robinhood", "quantity": 10, "loss": -900.0, "tax_saved": 216.0,
                          "replacement": "VOO", "replacement_why": "similar exposure, not identical", "blocked_by": [], "note": ""}]})
    plan["holdings"].append({"symbol": "DOWN", "verdict": "Hold", "value": 1000.0, "price": 100.0})
    reviews = [{"symbol": "LOSR", "value": 4000.0, "tax_now": 10.0, "reasons": ["screen grade fell to 30/100"], "text": "Selling costs ~$10.",
                "thesis": True},
               {"symbol": "LOW", "value": 3000.0, "tax_now": 900.0, "reasons": ["in the screen's bottom 50"], "text": "Selling costs ~$900."}]
    health = [{"key": "health:coinbase:fail:x", "name": "Coinbase", "text": "Coinbase: failing for 5 hours"}]
    fresh = [{"file": "screen.json", "name": "Stock screen", "stale": True, "age_days": 12, "text": "old"}]
    opt = [{"symbol": "SPIN", "amount": 800.0, "why": ["Spin-off trading since ..."]}]
    out = decisions.build(plan, reviews=reviews, idle_cash=2500.0, health=health, freshness=fresh, optional=opt,
                          bottom_note="within luck", today=date(2026, 9, 28))
    kinds = [d["kind"] for d in out]
    assert kinds == ["fix_sync", "sell", "fix_data", "harvest", "switch", "trim", "invest_cash", "wait_long_term", "switch", "optional_idea"]
    sell = out[1]
    assert sell["action"] == {"type": "trade", "symbol": "BAD", "side": "sell", "dollars": 5000.0}
    assert any("saves about $120" in w for w in sell["why"]) and sell["evidence"]["level"] == "rule"
    assert out[4]["title"] == "Swap LOSR ($4,000) for VOO" and out[4]["evidence"]["level"] == "mixed"
    weak = out[8]                                   # a low screen grade alone: unproven, ranked below idle cash, not pushed
    assert weak["title"] == "Review LOW: $3,000, $900 tax to switch" and weak["evidence"]["level"] == "unproven"
    assert weak["priority"] < 45 and "within luck" in weak["why"] and "within luck" not in out[4]["why"]
    assert out[6]["title"] == "Put $2,500 of idle cash into VTI" and out[-1]["evidence"]["level"] == "unproven"
    assert any("PLTR" in w and "Size it" in w for w in out[6]["why"])                 # a single stock is the alternative, not the default
    assert out[3]["title"] == "Harvest a $900 loss in DOWN (saves ~$216)"
    calm = decisions.build(_plan(holdings=[{"symbol": "OK", "verdict": "Hold", "value": 1.0}]), today=date(2026, 9, 28))
    assert [d["kind"] for d in calm] == ["hold"]


def test_sync_decide_hide_and_reappear():
    items = decisions.build(_plan(holdings=[{"symbol": "BIG", "verdict": "Trim", "value": 30000.0, "trim_value": 6000.0, "triggers": []}]),
                            today=date(2026, 9, 28))
    with db.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS decisions")
        v = decisions.sync(conn, items, date(2026, 9, 28))
        assert v[0]["new"] and v[0]["status"] == "open"
        assert not decisions.sync(conn, items, date(2026, 9, 29))[0]["new"]
        assert decisions.decide(conn, "trim:BIG", "skipped", date(2026, 9, 29))["until"] == "2026-10-29"
        assert decisions.sync(conn, items, date(2026, 10, 1)) == []                       # hidden while skipped
        assert decisions.sync(conn, items, date(2026, 10, 30))[0]["status"] == "open"   # back after 30 days
        assert decisions.history(conn) == []
        decisions.decide(conn, "trim:BIG", "approved", date(2026, 10, 30))
        assert decisions.history(conn)[0]["status"] == "approved"
    adv = decisions.advice_items(v)
    assert adv == [{"symbol": "BIG", "action": "Trim", "source": "decision", "reason": "Trim BIG by $6,000"}]
    title, body = decisions.push_text(v)
    assert title == "Trim BIG by $6,000" and "Decisions" in body


def test_sentinel_decides_once_a_day_and_pushes_new(monkeypatch):
    from market_tracker.providers import market
    sent = []
    monkeypatch.setattr(notify, "send", lambda m: sent.append(m))
    monkeypatch.setattr(market, "get_quote", lambda s: market.Quote(s, "stock", 50.0, 50.0, 0.0, "USD", "t", "2026-01-01"))
    items = decisions.build(_plan(holdings=[{"symbol": "BAD", "verdict": "Sell?", "value": 5000.0, "triggers": []}]), today=date(2026, 9, 28))
    with db.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS decisions")
        db.set_meta(conn, "decisions_day", "")
    early = datetime(2026, 9, 28, 10, tzinfo=timezone.utc)      # 6am ET: too early
    assert sentinel.decisions_daily(early, gather_fn=lambda d: items) == 0
    t = datetime(2026, 9, 28, 14, tzinfo=timezone.utc)
    assert sentinel.decisions_daily(t, gather_fn=lambda d: items) == 1
    assert sentinel.decisions_daily(t, gather_fn=lambda d: 1 / 0) == 0
    assert sent and sent[0].title.startswith("Decide on BAD")
    from market_tracker import advice
    with db.connect() as conn:
        assert any(r["source"] == "decision" and r["symbol"] == "BAD" for r in advice.logged(conn))
