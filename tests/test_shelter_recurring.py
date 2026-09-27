from datetime import date

from market_tracker import db, decisions, recurring, shelter
from market_tracker.schedules import Schedule

TODAY = date(2026, 9, 28)


def test_untagged_then_no_shelter_then_room_left():
    names = ["Coinbase", "Robinhood", "Stash"]
    v = shelter.view({}, names, [], {}, TODAY)
    assert v["untagged"] == names and shelter.decision(v)["kind"] == "tax_setup"
    with db.connect() as conn:
        db.set_meta(conn, "account_tax", "{}")
        for a in names:
            shelter.save(conn, a, "taxable", None, 2026)
        v = shelter.view(shelter.settings(conn), names, [], {}, TODAY)
        d = shelter.decision(v)
        assert d["kind"] == "shelter" and d["title"] == "Open a Roth IRA: up to $7,500 a year grows tax-free"
        shelter.save(conn, "Robinhood IRA", "roth_ira", 2000, 2026)
        shelter.save_person(conn, age50=True, hsa_family=False)
        v = shelter.view(shelter.settings(conn), names + ["Robinhood IRA"], [], {}, TODAY)
    ira = v["room"][0]
    assert ira["limit"] == 8600 and ira["left"] == 6600 and ira["deadline"] == "April 15, 2027"
    assert shelter.decision(v)["title"] == "Fund your IRA: $6,600 of 2026 room left"


def test_high_yield_in_taxable_is_flagged_and_old_years_reset():
    st = {"accounts": {"Robinhood": {"type": "taxable", "contributed": 0, "year": 2026},
                       "Roth": {"type": "roth_ira", "contributed": 7500, "year": 2025}}}          # last year's number doesn't count
    pos = [{"account": "Robinhood", "symbol": "O", "value": 10000.0}, {"account": "Robinhood", "symbol": "VOO", "value": 10000.0}]
    v = shelter.view(st, ["Robinhood", "Roth"], pos, {("Robinhood", "O"): 550.0, ("Robinhood", "VOO"): 120.0}, TODAY)
    assert [m["symbol"] for m in v["location"]] == ["O"] and "don't sell" in v["location"][0]["text"]
    assert v["room"][0]["left"] == 7500
    assert shelter.view(st, ["Robinhood"], [], {}, date(2031, 1, 5))["limits_year"] == 2026    # falls back, says so


def _tx(day, side, qty, price, key=""):
    return {"date": day, "side": side, "quantity": qty, "price": price, "symbol": "VOO", "import_key": key}


def test_recurring_plan_from_what_you_invest_by_hand():
    txs = [_tx("2026-04-03", "buy", 1, 400), _tx("2026-05-02", "buy", 1, 450), _tx("2026-06-10", "buy", 1, 500),
           _tx("2026-07-09", "buy", 1, 300), _tx("2026-07-20", "sell", 1, 100), _tx("2026-08-01", "buy", 5, 20, "sched:1:2026-08-01")]
    p = recurring.plan(txs, [], TODAY)
    assert p["active_months"] == 4 and p["by_hand_monthly"] == 300.0         # the median month: one lump can't set it
    assert p["suggest"]["weekly"] == 70 and "$70 a week into VOO" in p["suggest"]["text"]
    sched = [Schedule(1, "Stash", "VOO", 20.0, "week", 0, "2026-01-05")]
    assert "already on autopilot" in recurring.plan(txs, sched, TODAY)["suggest"]["text"]
    quiet = recurring.plan([], [], TODAY, idle_cash=900.0)
    assert quiet["suggest"]["weekly"] is None and "$900 is sitting in cash" in quiet["suggest"]["text"]


def test_decisions_carry_the_shelter_and_automation_calls():
    plan = {"holdings": [], "reinvest": [], "tax": {"harvest": []}}
    out = decisions.build(plan, today=TODAY, shelter={"kind": "shelter", "title": "Fund your IRA: $6,600 of 2026 room left", "why": ["x"], "amount": 6600.0},
                          automate={"symbol": "VOO", "weekly": 60, "text": "y"})
    assert [d["kind"] for d in out] == ["shelter", "automate"]
    assert out[0]["action"] == {"type": "open", "page": "accounts"} and out[1]["title"] == "Automate it: $60 a week into VOO"


def test_one_big_transfer_doesnt_set_the_recurring_size():
    txs = [_tx("2026-04-03", "buy", 100, 400), _tx("2026-05-02", "buy", 1, 200), _tx("2026-06-10", "buy", 1, 200), _tx("2026-07-09", "buy", 1, 200)]
    assert recurring.plan(txs, [], TODAY)["by_hand_monthly"] == 200.0


def test_partly_tagged_still_asks_for_the_rest():
    st = {"accounts": {"Robinhood": {"type": "taxable", "contributed": 0, "year": 2026}}}
    v = shelter.view(st, ["Coinbase", "Robinhood", "Stash"], [], {}, TODAY)
    assert "Still to say what Coinbase, Stash are" in v["text"] and shelter.decision(v)["kind"] == "tax_setup"
