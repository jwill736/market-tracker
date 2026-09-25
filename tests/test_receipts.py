from datetime import date, datetime, timezone

import pytest

from market_tracker import receipts, scorecard
from market_tracker.providers import market

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)


def sig(sym, strength, early=True, kinds=("wire",), headline="Deal: X to acquire Y"):
    return {"symbol": sym, "strength": strength, "early": early, "kinds": list(kinds),
            "signals": [{"headline": headline}]}


def quote(sym):
    return market.Quote(sym, "stock", {"AAA": 10.0, "BBB": 20.0, "CCC": 5.0}[sym], 9.0, 1.0, "USD", "t", "t")


def test_snapshot_records_first_sighting_once_per_day():
    calls = []
    new = receipts.record_sightings(calls, [sig("AAA", 60), sig("BBB", 40, early=False), sig("CCC", 10),
                                            sig("USDT-USD", 90, kinds=("depeg",))], NOW, quote_fn=quote)
    assert [c.symbol for c in new] == ["AAA", "BBB"] and new[0].price == 10.0 and new[0].time == "15:00"
    assert receipts.record_sightings(calls, [sig("AAA", 80)], NOW, quote_fn=quote) == []


def alerts_log():
    return [scorecard.AlertRecord("2026-09-23", "cluster", "DY", "1", "Dycom", "3 insiders", "u"),
            scorecard.AlertRecord("2026-09-24", "big", "GME", "2", "GameStop", "$20M", "u")]


def calls():
    return [receipts.EarlyCall("2026-09-23", "14:05", "AAA", "wire", 60, 1, 10.0, "h"),
            receipts.EarlyCall("2026-09-24", "15:10", "BBB", "social", 40, 0, 20.0, "h"),
            receipts.EarlyCall("2026-09-25", "13:00", "CCC", "filing", 50, 1, 5.0, "h")]


def test_seal_chains_finished_days_and_verify_catches_edits(tmp_path):
    chain = []
    new = receipts.seal(chain, calls(), alerts_log(), date(2026, 9, 25), NOW)
    assert [s["day"] for s in new] == ["2026-09-23", "2026-09-24"]           # today stays open
    assert new[0]["calls"] == 2 and new[1]["prev"] == new[0]["hash"] and new[0]["prev"] == receipts.GENESIS
    assert receipts.seal(chain, calls(), alerts_log(), date(2026, 9, 25), NOW) == []
    path = tmp_path / "receipts.jsonl"
    receipts.save_chain(chain, str(path))
    chain = receipts.load_chain(str(path))
    assert receipts.verify(chain, calls(), alerts_log()) == []
    edited = calls()
    edited[0].price = 9.0                                                    # prettier entry price
    assert "2026-09-23" in receipts.verify(chain, edited, alerts_log())[0]
    dropped = [c for c in calls() if c.symbol != "BBB"]                       # hide a miss
    assert any("2026-09-24" in p for p in receipts.verify(chain, dropped, alerts_log()))
    forged = [dict(s) for s in chain]
    forged[0]["digest"] = "f" * 64
    assert len(receipts.verify(forged, calls(), alerts_log())) >= 2          # its hash and the next link break


def test_early_calls_round_trip(tmp_path):
    path = str(tmp_path / "early.csv")
    receipts.save_early(calls(), path)
    assert receipts.load_early(path) == calls()


def test_public_view_waits_24_hours_and_scores_misses_too():
    days = ["2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"]

    def hist(sym, n):
        closes = {"AAA": [10, 10, 9, 9, 8, 8, 8], "BBB": [20, 21, 22, 23, 24, 25, 26], "SPY": [100] * 7}[sym]
        return [market.PriceBar(d, float(c)) for d, c in zip(days, closes)]
    chain = []
    receipts.seal(chain, calls(), alerts_log(), date(2026, 10, 2), NOW)
    view = receipts.public_calls(calls(), chain, datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc), history_fn=hist)
    assert [c["symbol"] for c in view["calls"]] == ["BBB", "AAA"]              # CCC is under 24 h old
    aaa = next(c for c in view["calls"] if c["symbol"] == "AAA")
    assert aaa["excess_pct"] == pytest.approx(-20.0) and aaa["seal"] == chain[0]["hash"][:16]
