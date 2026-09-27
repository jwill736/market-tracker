from datetime import datetime, timedelta, timezone

from market_tracker import sizing

POS = [{"symbol": "VOO", "market_value": 60000.0}, {"symbol": "AAPL", "market_value": 12000.0}, {"symbol": "MSFT", "market_value": 8000.0},
       {"symbol": "SPEC1", "market_value": 7000.0}]
SECTOR = {"AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology", "CF": "Basic Materials", "SPEC1": "Energy", "TINY": "Energy"}.get


def test_core_idea_is_capped_at_five_percent_and_uses_cash_first():
    r = sizing.size("CF", "qvm", 50.0, POS, 13000.0, SECTOR, set())
    assert r["amount"] == 5000.0 and r["shares"] == 100 and "5.0% of your portfolio" in r["binding"]      # 5% of $100k
    assert any("$5,000 from your cash" in ln for ln in r["lines"])


def test_sector_limit_and_what_you_already_own():
    pos = POS + [{"symbol": "NVDA", "market_value": 4000.0}]
    r = sizing.size("NVDA", "qvm", 100.0, pos, 9000.0, SECTOR, set())
    assert r["amount"] == 1000.0 and "one core idea" in r["binding"]      # 5% of $100k minus the $4k held
    r = sizing.size("MSFT", "qvm", 100.0, pos, 9000.0, SECTOR, set())
    assert r["amount"] == 0 and "one core idea" in r["binding"]           # already $8k: over 5%
    full = [{"symbol": "AAPL", "market_value": 26000.0}, {"symbol": "VOO", "market_value": 74000.0}]
    r = sizing.size("MSFT", "qvm", 100.0, full, 0.0, SECTOR, set())
    assert r["amount"] == 0 and r["lines"][0].startswith("Nothing more") and "Technology" in r["binding"]


def test_speculative_bucket_volatility_and_chatter_wait():
    r = sizing.size("TINY", "sleeper", 10.0, POS, 13000.0, SECTOR, {"SPEC1"})
    assert r["amount"] == 2000.0                                           # 2% per speculative name
    r = sizing.size("TINY", "sleeper", 10.0, POS, 13000.0, SECTOR, {"SPEC1"}, vol=0.9)
    assert r["amount"] == 1000.0 and any("halved" in ln for ln in r["lines"])
    heavy = [dict(p) for p in POS] + [{"symbol": "SPEC2", "market_value": 9500.0}]
    r = sizing.size("TINY", "sleeper", 10.0, heavy, 3500.0, SECTOR, {"SPEC1", "SPEC2"})
    assert r["amount"] == 0 and "speculative limit" in r["binding"]
    now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    r = sizing.size("TINY", "chatter", 10.0, POS, 13000.0, SECTOR, set(), now=now)
    assert r["amount"] is None and r["locked_until"] and not any(ln.startswith("Up to") for ln in r["lines"])
    r = sizing.size("TINY", "chatter", 10.0, POS, 13000.0, SECTOR, set(), cooling_started=now - timedelta(hours=49), now=now)
    assert r["amount"] == 2000.0 and r["locked_until"] is None
    assert sizing.size("X", "qvm", 1.0, [], 0.0, SECTOR, set())["binding"] == "no portfolio"


def test_annual_vol():
    import math
    flat = [100.0 * (1.001 ** i) for i in range(300)]
    assert sizing.annual_vol(flat) < 0.01 and sizing.annual_vol(flat[:30]) is None
    zig = [100.0 * (1.03 if i % 2 else 0.97) for i in range(300)]
    assert sizing.annual_vol(zig) > 0.5 and not math.isnan(sizing.annual_vol(zig))
