from datetime import date, datetime, timedelta, timezone

from market_tracker import assistant, auth, db, decisions, notify, sentinel


def _bars(start: str, prices: list[float]) -> list[tuple[str, float]]:
    d0 = date.fromisoformat(start)
    return [((d0 + timedelta(days=i)).isoformat(), p) for i, p in enumerate(prices)]


def test_scorecard_scores_followed_and_skipped_against_doing_nothing():
    with db.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS decisions")
        conn.executescript(decisions.SCHEMA)
        rows = [("sell:LOSR", "sell", "LOSR", "Decide on LOSR: sell $1,000?", 1000.0, "approved"),
                ("invest_cash:VOO", "invest_cash", "VOO", "Put $2,000 of idle cash into VOO", 2000.0, "skipped"),
                ("harvest:DOWN:x", "harvest", "DOWN", "Harvest a $900 loss in DOWN (saves ~$216)", 1000.0, "approved"),
                ("trim:OPEN", "trim", "OPEN", "Trim OPEN", 500.0, "open")]
        for key, kind, sym, title, amt, status in rows:
            conn.execute("INSERT INTO decisions (key, first_seen, kind, symbol, title, amount, status, decided) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (key, "2026-01-01", kind, sym, title, amt, status, "2026-01-01" if status != "open" else ""))
        n = 120
        hist = {"LOSR": _bars("2026-01-01", [100 - i * 0.2 for i in range(n)]),       # fell 18% over 3 months
                "VOO": _bars("2026-01-01", [100 + i * 0.05 for i in range(n)])}       # rose 4.5%
        card = decisions.scorecard(conn, lambda s: hist[s], date(2026, 4, 30), cash_yield=0.04)
    f, s = card["summary"]["followed"][91], card["summary"]["skipped"][91]
    assert card["followed"] == 2 and card["skipped"] == 1 and card["harvest_saved"] == 216.0
    assert f["n"] == 1 and f["avg_edge"] > 20 and f["dollars"] > 200            # selling LOSR into VOO helped
    assert s["n"] == 1 and 3 < s["avg_edge"] < 4.5                              # VOO against cash (minus the T-bill yield)
    assert card["summary"]["followed"][365]["n"] == 0                            # not a year yet
    assert "skipping them cost you" in card["text"] and "Too early" in card["text"]


def test_signed_action_links_do_one_thing_until_they_expire():
    tok = auth.sign_action("decide|skipped|trim:BIG", now=1_000_000)
    assert auth.read_action(tok, now=1_000_100) == "decide|skipped|trim:BIG"
    assert auth.read_action(tok, now=1_000_000 + 8 * 86400) is None               # a week later: expired
    body, _, sig = tok.partition(".")
    other = auth.sign_action("decide|approved|trim:BIG", now=1_000_000).partition(".")[0]
    assert auth.read_action(f"{other}.{sig}", now=1_000_100) is None              # can't swap in another action
    assert auth.read_action("garbage", now=1) is None and auth.is_open("/act/" + tok)


def test_push_buttons_and_the_act_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from market_tracker.api import app
    items = decisions.build({"holdings": [{"symbol": "BIG", "verdict": "Trim", "value": 30000.0, "trim_value": 6000.0, "triggers": []}],
                             "reinvest": [], "tax": {"harvest": []}}, today=date(2026, 9, 28))
    with db.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS decisions")
        visible = decisions.sync(conn, items, date(2026, 9, 28))
    assert decisions.push_actions(visible, "", auth.sign_action) == ()
    acts = decisions.push_actions(visible, "https://me.example/", auth.sign_action)
    assert acts[0] == "view, Do it, https://me.example/#decisions?do=trim%3ABIG, clear=true"
    assert acts[2].startswith("http, Skip, https://me.example/act/") and "method=POST" in acts[2]
    skip_url = acts[2].split(", ")[2]
    monkeypatch.setenv("APP_PASSWORD", "pw")                                    # the link works without signing in
    c = TestClient(app, base_url="https://me.example")
    token = auth.sign_action("decide|skipped|trim:BIG")
    r = c.post("/act/" + token)
    assert r.status_code == 200 and r.json()["status"] == "skipped", skip_url
    assert c.post("/act/" + token[:-1] + ("1" if token[-1] != "1" else "2")).status_code == 403   # always a different signature
    assert c.get("/api/decisions/scorecard").status_code == 401                 # everything else still needs a sign-in


def test_letter_on_sunday_within_budget(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda m: sent.append(m))
    with db.connect() as conn:
        db.set_meta(conn, "letter_week", "")
        db.set_meta(conn, "claude_spend", "{}")
        db.set_meta(conn, "claude_auto_budget", "2")
    letter = {"answer": "**Hold.** Nothing needs you this week.", "usage": {"input": 1, "output": 1}, "tools": []}
    saturday = datetime(2026, 10, 3, 23, tzinfo=timezone.utc)
    sunday_noon = datetime(2026, 10, 4, 16, tzinfo=timezone.utc)                  # noon ET: before the recap
    sunday = datetime(2026, 10, 4, 22, tzinfo=timezone.utc)                       # 6pm ET
    assert sentinel.letter_weekly(saturday, write_fn=lambda: letter) == 0
    assert sentinel.letter_weekly(sunday_noon, write_fn=lambda: letter) == 0
    assert sentinel.letter_weekly(sunday, write_fn=lambda: letter) == 1
    assert sentinel.letter_weekly(sunday, write_fn=lambda: letter) == 0          # once a week
    assert sent[-1].title == "Your letter from Plumbline" and sent[-1].body.startswith("Hold.")
    with db.connect() as conn:
        db.set_meta(conn, "letter_week", "")
        db.set_meta(conn, "claude_spend", '{"%s": 2.5}' % datetime.now().strftime("%Y-%m"))
    assert sentinel.letter_weekly(sunday, write_fn=lambda: 1 / 0) == 0           # over budget: never calls Claude
    assert sent[-1].title == "No letter this week"


def test_spend_is_estimated_and_tallied():
    u = {"input": 1_000_000, "output": 100_000, "cache_read": 2_000_000, "cache_write": 0}
    assert abs(assistant.cost(u, "claude-opus-5") - (5 + 2.5 + 1.0)) < 1e-9
    with db.connect() as conn:
        db.set_meta(conn, "claude_spend", "{}")
    out = assistant._spent({"usage": {"input": 200_000, "output": 0, "cache_read": 0, "cache_write": 0}})
    with db.connect() as conn:
        assert out["cost"] == 1.0 and assistant.month_spend(conn) == 1.0


def test_monthly_scorecard_push(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda m: sent.append(m))
    with db.connect() as conn:
        db.set_meta(conn, "scorecard_month", "")
    card = {"followed": 3, "skipped": 1, "text": "You followed 3 calls and skipped 1."}
    assert sentinel.scorecard_monthly(datetime(2026, 10, 2, 15, tzinfo=timezone.utc), card_fn=lambda c: card) == 0   # not the 1st
    first = datetime(2026, 11, 1, 15, tzinfo=timezone.utc)
    assert sentinel.scorecard_monthly(first, card_fn=lambda c: card) == 1
    assert sentinel.scorecard_monthly(first, card_fn=lambda c: card) == 0
    assert sent[0].body == card["text"]


def test_a_push_with_buttons_is_a_valid_request(monkeypatch):
    import httpx
    seen = {}

    def handler(request):
        seen["actions"] = request.headers.get("Actions")
        return httpx.Response(200, json={})
    monkeypatch.setenv("NTFY_TOPIC", "t")
    tok = "A" * 90 + ".b3c1"
    acts = ("view, Do it, https://plumbline-abc123.fly.dev/#decisions?do=fix_data%3Atrust%3A2026-39, clear=true",
            f"http, Later, https://plumbline-abc123.fly.dev/act/{tok}, method=POST, clear=true",
            f"http, Skip, https://plumbline-abc123.fly.dev/act/{tok}, method=POST, clear=true")
    ok = notify.send(notify.Message(title="Check your numbers", body="• x", actions=acts), client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert ok and seen["actions"].count("clear=true") == 3 and seen["actions"].endswith("clear=true")
