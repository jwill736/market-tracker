from market_tracker import evidence, ideas


def test_lists_start_as_research_and_earn_sizing_with_a_forward_record():
    st = evidence.status([], [], ideas.SOURCES)
    assert evidence.earned(st) == {"manual"}
    assert "Research, not advice" in st["spinoff"]["why"] and "0 of 12 months" in st["spinoff"]["why"]
    book = {"list": "spinoffs", "months": [{}] * 12, "ahead": 900}
    short = {"list": "screen_2b", "months": [{}] * 5, "ahead": 2000}           # ahead, but only 5 months
    board = [{"source": "chatter", "6m": {"resolved": 25, "enough": True, "avg_edge": 3.0, "beat_voo": 60}},
             {"source": "insider", "6m": {"resolved": 25, "enough": True, "avg_edge": 1.0, "beat_voo": 50}}]
    st = evidence.status([book, short], board, ideas.SOURCES)
    assert evidence.earned(st) == {"manual", "spinoff", "chatter"}
    assert "5 of 12 months, ahead of VOO" in st["qvm"]["why"]
