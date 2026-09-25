from datetime import date

import pytest

from market_tracker import taxes


def tx(i, sym, side, qty, price, day, account="Robinhood"):
    return {"id": i, "symbol": sym, "side": side, "quantity": qty, "price": price, "fees": 0, "date": day, "account": account}


def test_identical_groups_and_replacements():
    assert taxes.identical("VOO", "SPY") and taxes.identical("goog", "GOOGL") and not taxes.identical("VOO", "VTI")
    assert taxes.replacement("VOO")[0] == "VTI" and taxes.replacement("NVDA")[0] == "XLK"
    assert taxes.replacement("ZZZZ")[0] == "VTI"


def test_cross_account_wash_sale_is_caught():
    txs = [tx(1, "VOO", "buy", 10, 500, "2026-01-05"),
           tx(2, "VOO", "sell", 10, 450, "2026-09-01"),                       # $500 loss
           tx(3, "SPY", "buy", 5, 600, "2026-09-10", account="Stash")]         # same index, other broker, 9 days later
    w = taxes.wash_sales(txs)
    assert len(w) == 1 and w[0].replacement_symbol == "SPY" and w[0].replacement_account == "Stash"
    assert w[0].loss == 500 and w[0].disallowed == pytest.approx(250)          # 5 of 10 shares replaced
    assert "Stash" in w[0].note


def test_selling_the_shares_you_just_bought_is_not_a_wash():
    txs = [tx(1, "AAPL", "buy", 10, 200, "2026-09-01"), tx(2, "AAPL", "sell", 10, 180, "2026-09-20")]
    assert taxes.wash_sales(txs) == []
    b = taxes.blackout(txs, date(2026, 9, 25))
    assert b[0]["symbol"] == "AAPL" and b[0]["until"] == "2026-10-21" and b[0]["loss"] == 200


def test_gains_and_crypto_are_not_washes():
    txs = [tx(1, "MSFT", "buy", 1, 100, "2026-01-01"), tx(2, "MSFT", "sell", 1, 150, "2026-06-01"),
           tx(3, "MSFT", "buy", 1, 140, "2026-06-05"),
           tx(4, "BTC-USD", "buy", 1, 90000, "2026-01-01"), tx(5, "BTC-USD", "sell", 1, 80000, "2026-06-01"),
           tx(6, "BTC-USD", "buy", 1, 79000, "2026-06-02")]
    assert taxes.wash_sales(txs) == []


def test_clock_and_harvest():
    today = date(2026, 9, 25)
    txs = [tx(1, "NVDA", "buy", 10, 100, "2025-10-20"),                      # short-term, +$1,200, long-term on 10-21
           tx(2, "NKE", "buy", 20, 80, "2026-02-01"),                        # down 25%
           tx(3, "NKE", "buy", 5, 60, "2026-09-15", account="Stash"),         # recent buy elsewhere: a blocker
           tx(4, "TSLA", "buy", 1, 300, "2026-03-01")]                       # small loss: not worth it
    prices = {"NVDA": 220.0, "NKE": 60.0, "TSLA": 290.0}
    s = taxes.summary(txs, prices, today)
    clock = s["clock"][0]
    assert clock["symbol"] == "NVDA" and clock["days"] == 26 and clock["long_term_on"] == "2026-10-21"
    assert clock["saving"] == pytest.approx(1200 * (0.24 - 0.15))
    h = s["harvest"]
    assert [x["symbol"] for x in h] == ["NKE"] and h[0]["loss"] == 400 and h[0]["replacement"] == "XLY"
    assert h[0]["blocked_by"][0]["account"] == "Stash" and h[0]["tax_saved"] == pytest.approx(96)


def test_realized_this_year_counts_disallowed_losses_back():
    txs = [tx(1, "VOO", "buy", 10, 500, "2026-01-05"), tx(2, "VOO", "sell", 10, 450, "2026-09-01"),
           tx(3, "VOO", "buy", 10, 440, "2026-09-05")]
    r = taxes.summary(txs, {"VOO": 450.0}, date(2026, 9, 25))["realized"]
    assert r["short_term"] == -500 and r["wash_disallowed"] == 500 and r["net"] == 0
