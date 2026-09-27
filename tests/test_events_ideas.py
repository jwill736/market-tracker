from datetime import date

from market_tracker import pead, spinoffs


def _fts(hits):
    return lambda params: {"hits": {"hits": hits}}


def test_pead_candidates_and_qualify():
    hits = [{"_source": {"items": ["2.02", "9.01"], "display_names": ["Acme Corp  (ACME)  (CIK 0000000001)"]}},
            {"_source": {"items": ["7.01"], "display_names": ["Other Inc  (OTHR)  (CIK 0000000002)"]}},
            {"_source": {"items": ["2.02"], "display_names": ["Tiny Co  (TINY)  (CIK 0000000003)"]}}]
    assert pead.candidates(date(2026, 9, 20), _fts(hits)) == ["ACME", "TINY"]
    today = date(2026, 9, 20)
    base = {"symbol": "ACME", "release": {"filed": "2026-09-15", "company": "Acme", "url": "u"},
            "outlook": {"direction": "raised", "lines": ["We now expect revenue of $5.0 billion, up from $4.8 billion."]},
            "reaction": {"day": "2026-09-16", "move_pct": 6.2, "times_usual": 3.1}}
    q = pead.qualify(base, today)
    assert q["symbol"] == "ACME" and "Raised its outlook on 2026-09-15" in q["why"] and "3.1x" in q["why"]
    assert pead.qualify(dict(base, outlook={"direction": "kept", "lines": []}), today) is None
    assert pead.qualify(dict(base, reaction={"move_pct": 1.0, "times_usual": 0.5}), today) is None
    assert pead.qualify(dict(base, reaction={"move_pct": 4.0, "times_usual": 1.1}), today) is None      # an ordinary day for a jumpy stock
    assert pead.qualify(base, date(2026, 10, 30)) is None                                                   # too old
    rows = pead.build(today, lookup_fn=lambda s: {"cap": 5e9, "score": 70} if s == "ACME" else {"cap": 1e8},
                      recap_fn=lambda s: base, get=_fts(hits))
    assert [r["symbol"] for r in rows] == ["ACME"] and rows[0]["score"] == 70


def test_spinoff_registrations_and_status():
    hits = [{"_source": {"file_date": "2026-01-16", "display_names": ["FedEx Freight Holding Company, Inc.  (CIK 0002082247)"]}},
            {"_source": {"file_date": "2026-04-10", "display_names": ["FedEx Freight Holding Company, Inc.  (FDXF)  (CIK 0002082247)"]}},
            {"_source": {"file_date": "2026-09-15", "display_names": ["Archer SpinCo, Inc.  (CIK 0002130999)"]}}]
    regs = spinoffs.registrations(date(2026, 9, 27), _fts(hits))
    assert regs[0]["name"] == "Archer SpinCo, Inc." and regs[0]["ticker"] is None
    fdx = regs[1]
    assert (fdx["ticker"], fdx["first_filed"], fdx["last_filed"], fdx["filings"]) == ("FDXF", "2026-01-16", "2026-04-10", 2)
    hist = {"FDXF": [("2026-06-01", 50.0)] + [(f"2026-07-{d:02d}", 50.0 + d) for d in range(1, 29)]}
    bench = [("2026-06-01", 100.0), ("2026-07-28", 102.0)]
    rows = spinoffs.build(date(2026, 9, 27), get=_fts(hits), ticker_fn=lambda c: None,
                          history_fn=lambda s: hist[s], bench_fn=lambda: bench, lookup_fn=lambda s: {"score": 64, "cap": 9e9})
    t = rows[0]
    assert t["stage"] == "trading" and t["trading_since"] == "2026-06-01" and t["days_trading"] == 29
    assert t["return_pct"] == 56.0 and t["spy_pct"] == 2.0 and t["loggable"] and t["score"] == 64
    assert rows[1]["stage"] == "registered" and not rows[1]["loggable"]
