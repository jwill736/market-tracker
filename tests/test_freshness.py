import json
from datetime import date, datetime, timezone

from market_tracker import db, freshness, http, notify, sentinel


def _get(files):
    def get(url, as_json=True):
        name = url.rsplit("/", 1)[-1]
        if name not in files:
            raise http.DataUnavailable("404")
        return files[name]
    return get


def test_check_reads_each_kind_and_flags_stale():
    chain = "\n".join(json.dumps({"day": d, "sealed_at": d + "T21:30:00+00:00"}) for d in ("2026-09-20", "2026-09-25"))
    csv = "symbol,company,insider,role,trade_date,filed,shares,price,value,url\nAAA,A,B,Director,2026-09-10,2026-09-11,1,1,1,x\n"
    files = {"screen.json": {"as_of": "2026-09-27T06:50:00+00:00"}, "ideas_chain.jsonl": chain, "insider_buys.csv": csv,
             "tenk_rank.json": {"as_of": "2026-09-01T00:00:00+00:00"}}
    r = {x["file"]: x for x in freshness.check(date(2026, 9, 28), _get(files))}
    assert not r["screen.json"]["stale"] and r["screen.json"]["age_days"] == 1
    assert r["ideas_chain.jsonl"]["as_of"] == "2026-09-25" and not r["ideas_chain.jsonl"]["stale"]
    assert r["tenk_rank.json"]["stale"] and "Check the weekly '10-K ranking' job" in r["tenk_rank.json"]["text"]
    assert r["screen_backtest.json"]["missing"] and not r["screen_backtest.json"]["stale"]
    s = freshness.summary(list(r.values()))
    assert s["stale"] >= 1 and not s["ok"]


def test_alerts_due_once_until_recovered():
    stale = [{"file": "a", "stale": True}, {"file": "b", "stale": False}]
    due, keep = freshness.alerts_due(stale, set())
    assert [d["file"] for d in due] == ["a"] and keep == {"a"}
    due, keep = freshness.alerts_due(stale, keep)
    assert due == [] and keep == {"a"}
    due, keep = freshness.alerts_due([{"file": "a", "stale": False}], keep)
    assert keep == set()                                     # recovered: the next episode pushes again


def test_sentinel_pushes_stale_files_once(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda m: sent.append(m))
    with db.connect() as conn:
        db.set_meta(conn, "freshness_hour", "")
        db.set_meta(conn, "freshness_alerted", "[]")
    res = [{"file": "screen.json", "name": "Stock screen", "stale": True, "text": "Stock screen: last built ..."}]
    t = datetime(2026, 9, 28, 10, tzinfo=timezone.utc)
    assert sentinel.freshness_check(t, check_fn=lambda d: res) == 1
    assert sentinel.freshness_check(t, check_fn=lambda d: 1 / 0) == 0                     # same hour: not rechecked
    assert sentinel.freshness_check(datetime(2026, 9, 28, 11, tzinfo=timezone.utc), check_fn=lambda d: res) == 0
    assert sent[0].title == "Stale data: Stock screen"
