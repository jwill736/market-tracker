from datetime import date

import pytest

from market_tracker import events

EARN = {"data": {"announcement": "Earnings announcement* for NVDA: Nov 18, 2026",
                 "reportText": "NVIDIA Corporation Common Stock is estimated to report earnings on  11/18/2026."}}
CHAIN = {"data": {"lastTrade": "LAST TRADE: $224.79 (AS OF SEP 25, 2026 1:34 PM ET)", "table": {"rows": [
    {"expirygroup": "November 13, 2026", "strike": None},
    {"expirygroup": "", "strike": "225.00", "c_Bid": "9.00", "c_Ask": "9.20", "c_Last": "9.1", "p_Bid": "9.30", "p_Ask": "9.50", "p_Last": "9.4"},
    {"expirygroup": "November 20, 2026", "strike": None},
    {"expirygroup": "", "strike": "220.00", "c_Bid": "16.20", "c_Ask": "16.30", "c_Last": "16.25", "p_Bid": "9.60", "p_Ask": "9.75", "p_Last": "9.81"},
    {"expirygroup": "", "strike": "225.00", "c_Bid": "13.50", "c_Ask": "13.60", "c_Last": "13.60", "p_Bid": "11.90", "p_Ask": "12.00", "p_Last": "11.95"},
    {"expirygroup": "", "strike": "230.00", "c_Bid": "--", "c_Ask": "--", "c_Last": "--", "p_Bid": "--", "p_Ask": "--", "p_Last": "--"},
]}}}
FOMC_HTML = """<h4><a id="1">2026 FOMC Meetings</a></h4>
<div class="fomc-meeting__month col-xs-5"><strong>September</strong></div><div class="fomc-meeting__date col-xs-4">15-16*</div>
<div class="fomc-meeting__month col-xs-5"><strong>October</strong></div><div class="fomc-meeting__date col-xs-4">27-28</div>
<div class="fomc-meeting__month col-xs-5"><strong>Dec</strong></div><div class="fomc-meeting__date">8-9</div>
<h4><a id="2">2027 FOMC Meetings</a></h4>
<div class="fomc-meeting__month col-xs-5"><strong>Jan/Feb</strong></div><div class="fomc-meeting__date col-xs-4">31-1</div>"""
ECON = {"data": {"rows": [
    {"gmt": "12:30", "country": "United States", "eventName": "CPI (YoY)", "consensus": "2.9%", "previous": "3.0%"},
    {"gmt": "12:30", "country": "United States", "eventName": "Nonfarm Payrolls", "consensus": "&nbsp;", "previous": "140K"},
    {"gmt": "11:30", "country": "United States", "eventName": "Atlanta Fed GDPNow"},
    {"gmt": "14:00", "country": "United States", "eventName": "FOMC Member Waller Speaks"},
    {"gmt": "18:00", "country": "United States", "eventName": "FOMC Meeting Minutes"},
    {"gmt": "01:00", "country": "India", "eventName": "CPI"}]}}


def test_earnings_date_and_implied_move():
    assert events.parse_earnings_date(EARN) == ("2026-11-18", True)
    last, chain = events.parse_chain(CHAIN)
    assert last == 224.79 and sorted(chain) == ["2026-11-13", "2026-11-20"]
    move, expiry = events.implied_move(last, chain, "2026-11-18")
    assert expiry == "2026-11-20" and move == pytest.approx((13.55 + 11.95) / 224.79)   # the 225 straddle

    def get(url, **kw):
        return EARN if "earnings-date" in url else CHAIN
    row = events.earnings_for("NVDA", 9000.0, date(2026, 9, 25), get=get)
    assert row["days"] == 54 and row["move_pct"] == pytest.approx(11.3, abs=0.05)
    assert row["move_dollars"] == pytest.approx(9000 * (13.55 + 11.95) / 224.79, abs=0.01)


def test_fomc_and_econ_calendar():
    assert events.parse_fomc(FOMC_HTML) == ["2026-09-16", "2026-10-28", "2026-12-09", "2027-02-01"]
    rows = events.parse_econ(ECON, "2026-10-14")
    assert [(r["kind"], r["consensus"]) for r in rows] == [("Inflation (CPI)", "2.9%"), ("Jobs report", None), ("Fed minutes", None)]

    def get(url, **kw):
        return FOMC_HTML if "federalreserve" in url else ECON
    mac, errors = events.macro(date(2026, 9, 25), days=2, get=get)
    assert errors == [] and [m["kind"] for m in mac][:1] == ["Inflation (CPI)"] and any(m["date"] == "2026-10-28" for m in mac)
