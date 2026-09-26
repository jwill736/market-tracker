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


def test_offsite_roundtrip_folder_and_restore(tmp_path, monkeypatch):
    import base64
    from market_tracker import offsite
    from datetime import datetime, timezone
    for k in ("OFFSITE_DIR", "OFFSITE_REPO", "OFFSITE_GITHUB_TOKEN", "OFFSITE_KEY", "OFFSITE_SALT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(api, "_env_path", lambda: str(tmp_path / ".env"))
    c = TestClient(api.app)
    c.post("/api/transactions", json={"symbol": "VTI", "side": "buy", "quantity": 3, "price": 250, "date": "2025-01-02", "account": "Stash"})
    folder = tmp_path / "drive"
    folder.mkdir()
    assert c.post("/api/offsite", json={"passphrase": "short"}).status_code == 400
    r = c.post("/api/offsite", json={"passphrase": "correct horse battery staple", "dir": str(folder)}).json()
    assert r["backup"]["ok"] and len(list(folder.glob("plumbline-*.plb"))) == 1
    env = (tmp_path / ".env").read_text()
    assert "OFFSITE_KEY=" in env and "correct horse" not in env          # the passphrase itself isn't stored
    blob = next(folder.glob("*.plb")).read_bytes()
    assert blob[:4] == b"PLB1" and b"VTI" not in blob
    import pytest
    with pytest.raises(offsite.BackupError):
        offsite.decrypt(blob, "wrong passphrase!!")
    # Change the data, then restore the backup: the trade is back, the old file kept.
    tx = c.get("/api/transactions").json()[0]["id"]
    c.delete(f"/api/transactions/{tx}")
    assert c.get("/api/transactions").json() == []
    bad = c.post("/api/offsite/restore", json={"file_base64": base64.b64encode(blob).decode(), "passphrase": "nope nope nope"})
    assert bad.status_code == 400
    ok = c.post("/api/offsite/restore", json={"file_base64": base64.b64encode(blob).decode(), "passphrase": "correct horse battery staple"})
    assert ok.status_code == 200 and "before-restore" in ok.json()["previous_kept_as"]
    assert [t["symbol"] for t in c.get("/api/transactions").json()] == ["VTI"]
    # Keeps the last 14 in the folder.
    for d in range(20):
        offsite.to_folder(b"x", str(folder), datetime(2026, 1, d + 1, tzinfo=timezone.utc))
    assert len(list(folder.glob("plumbline-*.plb"))) == 14


def test_offsite_github_upload_updates_in_place():
    import base64
    from datetime import datetime, timezone
    from market_tracker import offsite
    calls = []

    class R:
        def __init__(self, code, js=None):
            self.status_code, self._js = code, js or {}

        def json(self):
            return self._js

    def send(method, url, **kw):
        calls.append((method, url, kw.get("json")))
        return R(200, {"sha": "abc"}) if method == "GET" else R(200)
    where = offsite.to_github(b"blob", "me/backup", "tok", datetime(2026, 9, 26, tzinfo=timezone.utc), send)
    assert where == "github.com/me/backup/plumbline.plb"
    put = calls[1]
    assert put[0] == "PUT" and put[2]["sha"] == "abc" and base64.b64decode(put[2]["content"]) == b"blob"
    with __import__("pytest").raises(offsite.BackupError):
        offsite.check_private("me/backup", "t", lambda m, u, **k: R(200, {"private": False}))


def test_health_pushes_long_failures_and_unconfirmed_schedule_buys(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from market_tracker import accounts, health, schedules
    monkeypatch.setenv("COINBASE_API_KEY_NAME", "k")
    monkeypatch.setenv("COINBASE_API_PRIVATE_KEY", "p")
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc)
    accounts.record("coinbase", None, "Coinbase: 401 Unauthorized", t0)
    accounts.record("coinbase", None, "Coinbase: 401 Unauthorized", t0 + timedelta(hours=1))
    st = {s["name"]: s for s in health.status(t0 + timedelta(hours=2))}
    assert not st["Coinbase"]["ok"] and not st["Coinbase"]["push"]           # 2 hours: not yet
    later = health.problems(t0 + timedelta(hours=4))
    assert later[0]["push"] and "failing for 4 hours" in later[0]["title"]
    key = later[0]["key"]
    accounts.record("coinbase", {"new": 0}, None, t0 + timedelta(hours=5))
    assert health.problems(t0 + timedelta(hours=5)) == []
    accounts.record("coinbase", None, "again", t0 + timedelta(hours=6))
    assert health.status(t0 + timedelta(hours=10))[0]["key"] != key           # a new episode, a new push
    # A schedule's buy with no confirmation (email on).
    monkeypatch.setenv("MAIL_USER", "me@example.com")
    monkeypatch.setenv("MAIL_APP_PASSWORD", "x")
    with db.connect() as conn:
        sid = schedules.add(conn, "Stash", "VOO", 20, "week", 0, "2026-09-07")
        db.add_transaction(conn, "VOO", "buy", 0.04, 500, "2026-09-14", import_key=f"sched:{sid}:2026-09-14", account="Stash")
        db.add_transaction(conn, "VOO", "buy", 0.04, 500, "2026-09-21", import_key=f"sched:{sid}:2026-09-21", account="Stash")
        db.add_transaction(conn, "VOO", "buy", 0.04, 501, "2026-09-22", import_key="em:abc", account="Stash")   # confirms the 21st
    p = [x for x in health.problems(datetime(2026, 9, 26, 12, tzinfo=timezone.utc)) if x["name"] == "Auto-invest"]
    assert [x["key"] for x in p] == [f"health:sched:{sid}:2026-09-14"]
