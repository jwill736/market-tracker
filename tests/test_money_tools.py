from datetime import date

from fastapi.testclient import TestClient

from market_tracker import api, cash, db


class Q:
    def __init__(self, price):
        self.price = price


def test_cash_since_moves_only_when_amount_changes():
    with db.connect() as conn:
        cash.save(conn, "Stash", 800, 0.0, date(2026, 9, 1))
        cash.save(conn, "Stash", 800, None, date(2026, 9, 20))          # same amount: still since Sept 1
        cash.save(conn, "Robinhood", 50, None, date(2026, 9, 20))
        accts = cash.load(conn)
        assert accts["Stash"]["since"] == "2026-09-01" and db.get_meta(conn, "cash") == "850"
        cash.save(conn, "Robinhood", 0, None, date(2026, 9, 21))        # zero removes it
        assert "Robinhood" not in cash.load(conn)
    v = cash.view(accts, date(2026, 9, 26), 0.042, True)
    stash = v["accounts"][0]
    assert stash["idle"] and stash["days"] == 25 and stash["missed_per_year"] == 33.6
    assert "about $34 a year" in v["note"]
    assert cash.tbill_yield(lambda s: Q(4.3)) == (0.043, True)
    assert cash.tbill_yield(lambda s: Q(float("nan")))[1] is False


def test_cash_endpoints(monkeypatch):
    monkeypatch.setattr(cash, "tbill_yield", lambda quote_fn=None: (0.04, True))
    c = TestClient(api.app)
    assert c.post("/api/cash/accounts", json={"account": "Stash", "amount": 250, "apy": 1.5}).status_code == 200
    v = c.get("/api/cash/accounts").json()
    assert v["total"] == 250 and v["accounts"][0]["apy"] == 1.5 and not v["accounts"][0]["idle"]
    assert c.get("/api/cash").json()["cash"] == 250
