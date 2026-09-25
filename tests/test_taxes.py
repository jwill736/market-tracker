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


# ------------------------------------------------------------------ year-end planner

def test_tax_on_netting_rules():
    st_r, lt_r = 0.24, 0.15
    assert taxes.tax_on(1000, 2000, st_r, lt_r) == 1000 * 0.24 + 2000 * 0.15
    assert taxes.tax_on(-500, 2000, st_r, lt_r) == 1500 * 0.15          # short loss nets against long gain
    assert taxes.tax_on(3000, -1000, st_r, lt_r) == 2000 * 0.24
    assert taxes.tax_on(-10000, 2000, st_r, lt_r) == -3000 * 0.24       # $3,000 cap on other income
    assert taxes.tax_on(-1000, -500, st_r, lt_r, offset=1500) == -1500 * 0.24


def _ye_tax(st=0.0, lt=0.0, harvest=(), lots=()):
    return {"realized": {"short_term": st, "long_term": lt}, "harvest": list(harvest), "lots": list(lots)}


def _h(sym, loss, long_term=False, blocked=()):
    return {"symbol": sym, "account": "Robinhood", "quantity": 10, "loss": loss, "long_term": long_term,
            "replacement": "XLK", "blocked_by": list(blocked)}


def test_year_end_harvests_until_losses_stop_helping():
    today = date(2026, 11, 2)
    tax = _ye_tax(st=4000, harvest=[_h("AAA", 5000), _h("BBB", 3000), _h("CCC", 400)])
    y = taxes.year_end(tax, [], today)
    # AAA wipes the 4,000 gain and 1,000 of income; BBB adds 2,000 more of the 3,000 offset
    # and carries 1,000 forward; CCC then saves nothing this year.
    assert [p["symbol"] for p in y["harvest"]] == ["AAA", "BBB"]
    assert y["tax_before"] == 960.0 and y["tax_after"] == -720.0 and y["saves"] == 1680.0
    assert y["carry_forward"] == 1000.0
    assert y["last_trading_day"] == "2026-12-31" and y["days_left"] == 59


def test_year_end_blocked_losses_and_warnings():
    today = date(2026, 12, 1)
    txs = [{"symbol": "VOO", "side": "buy", "quantity": 1, "price": 500, "date": d} for d in ("2026-09-01", "2026-10-01", "2026-11-01")]
    tax = _ye_tax(st=2000, harvest=[_h("VOO", 1500), _h("KO", 800, blocked=[{"symbol": "KO", "date": "2026-11-20"}]), _h("T", 900)])
    y = taxes.year_end(tax, txs, today, upcoming_dividends=[{"symbol": "T", "ex_date": "2026-12-20"}], drip_symbols={"T"})
    assert y["blocked"][0]["symbol"] == "KO" and "2026-12-21" in y["blocked"][0]["why"]
    voo = next(p for p in y["harvest"] if p["symbol"] == "VOO")
    assert any("recurring" in w for w in voo["warnings"])
    t = next(p for p in y["harvest"] if p["symbol"] == "T")
    assert any("reinvested" in w for w in t["warnings"]) and any("2026-12-20" in w for w in t["warnings"])


def test_year_end_zero_bracket_room_and_lots():
    today = date(2026, 12, 1)
    lots = [{"symbol": "VTI", "account": "Stash", "bought": "2020-01-02", "quantity": 100, "gain": 10000, "long_term": True},
            {"symbol": "NVDA", "account": "Robinhood", "bought": "2026-03-01", "quantity": 5, "gain": 900, "long_term": False}]
    y = taxes.year_end(_ye_tax(lt=2000, lots=lots), [], today, taxable_income=40000, filing="single", zero_limit=49450)
    z = y["zero_bracket"]
    assert z["room"] == 7450.0
    assert z["lots"] == [{"symbol": "VTI", "account": "Stash", "bought": "2020-01-02", "quantity": 74.5, "gain": 7450.0,
                          "future_tax_avoided": 1117.5}]
    assert taxes.year_end(_ye_tax(), [], today, taxable_income=60000)["zero_bracket"]["room"] == 0
    assert taxes.year_end(_ye_tax(), [], today)["zero_bracket"] is None
    # The default limit follows the filing status
    assert taxes.year_end(_ye_tax(), [], today, taxable_income=90000, filing="married")["zero_bracket"]["room"] == 8900.0


def test_year_end_net_loss_does_not_add_zero_bracket_room():
    today = date(2026, 12, 1)
    y = taxes.year_end(_ye_tax(st=-2000), [], today, taxable_income=40000, zero_limit=49450)
    assert y["zero_bracket"]["room"] == 9450.0 and y["zero_bracket"]["net_loss"] == 2000.0
    assert "give up its deduction" in y["zero_bracket"]["note"]
