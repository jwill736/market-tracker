from datetime import datetime, timezone

from market_tracker import newsdesk
from market_tracker.newsdesk import Item

NOW = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)


def rss(items):
    body = "".join(f"<item><title>{t}</title><link>{u}</link><pubDate>{d}</pubDate>{f'<source>{s}</source>' if s else ''}</item>"
                   for t, u, d, s in items)
    return f"<?xml version='1.0'?><rss><channel>{body}</channel></rss>"


def test_outlets_and_event_types():
    assert newsdesk.outlet_of("Reuters") == ("Reuters", "newsroom1")
    assert newsdesk.outlet_of("", "https://www.prnewswire.com/news/x") == ("PR Newswire", "wire")
    assert newsdesk.outlet_of("The Motley Fool") == ("Motley Fool", "blog")
    assert newsdesk.classify("Acme to restate three years of results after accounting review") == ("restatement", "A")
    assert newsdesk.classify("Acme CFO steps down effective immediately") == ("exec_exit", "A")
    assert newsdesk.classify("Acme cuts full-year guidance on weak demand") == ("guidance_cut", "A")
    assert newsdesk.classify("Acme suspends its dividend") == ("dividend_cut", "A")
    assert newsdesk.classify("Analyst upgrades Acme, raises price target") == ("analyst", "B")
    assert newsdesk.classify("5 reasons to love Acme") == ("other", "C")


def test_cluster_counts_independent_outlets_not_copies():
    items = [Item("Acme cuts full-year guidance as demand slows", "Reuters", "newsroom1", "u1", "2026-09-25T13:00:00+00:00", "Google News"),
             Item("Acme cuts its outlook, shares fall", "Wall Street Journal", "newsroom1", "u2", "2026-09-25T14:00:00+00:00", "WSJ"),
             Item("Acme cuts full-year guidance as demand slows", "Reuters", "newsroom1", "u3", "2026-09-25T15:00:00+00:00", "Yahoo Finance"),
             Item("Is Acme stock a buy now?", "Motley Fool", "blog", "u4", "2026-09-25T16:00:00+00:00", "Google News")]
    stories = newsdesk.cluster("ACME", items, {"acme"})
    cut = next(s for s in stories if s.event == "guidance_cut")
    assert cut.tier == "A" and cut.outlets == ["Reuters", "Wall Street Journal"] and len(cut.items) == 3
    assert cut.confidence == round(1 - 0.2 * 0.2, 3) and cut.confirmed and newsdesk.decide(cut) == "review"
    other = next(s for s in stories if s.event == "other")
    assert newsdesk.decide(other) == "ignore"


def test_rumor_and_single_source_only_watch():
    items = [Item("Acme reportedly faces SEC probe, people familiar say", "Bloomberg", "newsroom1", "u", "2026-09-25T13:00:00+00:00", "x"),
             Item("Acme reportedly under SEC investigation", "Benzinga", "blog", "u2", "2026-09-25T15:00:00+00:00", "x")]
    st = newsdesk.cluster("ACME", items, {"acme"})[0]
    assert st.event == "enforcement" and st.rumor and not st.confirmed and newsdesk.decide(st) == "watch"
    # A filing confirms it whatever the wording.
    items.append(Item("8-K: Acme discloses SEC investigation", "SEC EDGAR", "filing", "f", "2026-09-26T12:00:00+00:00", "radar"))
    st = newsdesk.cluster("ACME", items, {"acme"})[0]
    assert st.confirmed and newsdesk.decide(st) == "review"


def test_build_end_to_end_with_stale_and_reviews():
    google = rss([("Acme cuts full-year guidance as demand slows - Reuters", "https://reuters.com/a", "Fri, 25 Sep 2026 13:00:00 GMT", "Reuters"),
                  ("Acme cuts its outlook - The Wall Street Journal", "https://wsj.com/a", "Fri, 25 Sep 2026 14:00:00 GMT", "The Wall Street Journal"),
                  ("Acme wins Navy contract - Defense News", "https://dn.com/a", "Thu, 24 Sep 2026 10:00:00 GMT", "Defense News")])
    cnbc = rss([("Acme (ACME) shares slide after guidance cut", "https://cnbc.com/x", "Fri, 25 Sep 2026 15:00:00 GMT", None),
                ("Unrelated company soars", "https://cnbc.com/y", "Fri, 25 Sep 2026 15:00:00 GMT", None)])

    def get(url, **kw):
        if "news.google.com" in url:
            return google
        if "cnbc.com" in url:
            return cnbc
        if "gdeltproject" in url:
            return {"articles": [{"title": "Acme guidance cut", "domain": "a.com"}, {"title": "Acme outlook", "domain": "b.com"}]}
        raise newsdesk.http.DataUnavailable("down")
    desk = newsdesk.build(["ACME"], {"ACME": "Acme"}, {}, [], NOW, get)
    d = desk["ACME"]
    top = d["stories"][0]
    assert top["event"] == "guidance_cut" and top["action"] == "review" and top["sources"] >= 2 and "CNBC" in top["outlets"]
    assert d["outlets_3d"] == 2
    assert any(s["event"] == "contract" and s["action"] == "note" for s in d["stories"])
    assert newsdesk.FEED_STATUS["CNBC"]["ok"] and not newsdesk.FEED_STATUS["Bloomberg"]["ok"]
    rv = newsdesk.reviews(desk)
    assert rv["ACME"][0]["level"] == 2 and "guidance cut" in rv["ACME"][0]["headline"]
    # The same event seen a week ago: stale, so it doesn't trigger a review again.
    hist = newsdesk.remember([], {"ACME": {"stories": [dict(top, first="2026-09-18T10:00:00+00:00")]}}, NOW)
    again = newsdesk.build(["ACME"], {"ACME": "Acme"}, {}, hist, NOW, get)
    assert again["ACME"]["stories"][0]["stale"] and again["ACME"]["stories"][0]["action"] == "watch"


def test_mentions_needs_name_or_ticker_form():
    it = Item("Apple unveils new iPhone", "CNBC", "newsroom1", "", "2026-09-25T00:00:00+00:00", "CNBC")
    assert newsdesk.mentions(it, "AAPL", "Apple")
    assert not newsdesk.mentions(Item("Pineapple prices jump", "x", "newsroom", "", "", "x"), "AAPL", "Apple")
    assert newsdesk.mentions(Item("Shares of (ON) rally", "x", "newsroom", "", "", "x"), "ON", None)
    assert not newsdesk.mentions(Item("Stocks on the move", "x", "newsroom", "", "", "x"), "ON", None)


def test_confirmed_story_puts_holding_under_review_never_sell():
    from datetime import date
    from market_tracker import holdplan
    desk = {"ACME": {"stories": [{"action": "review", "event": "guidance_cut", "sources": 3, "first": "2026-09-25T13:00:00+00:00",
                                  "title": "Acme cuts guidance", "items": [{"url": "u"}]}]}}
    alerts = newsdesk.reviews(desk)["ACME"]
    pos = {"symbol": "ACME", "quantity": 10, "price": 50.0, "market_value": 500.0, "avg_cost": 40.0}
    row = holdplan.check_holding(pos, 10_000, None, alerts, date(2026, 9, 26), 0.2)
    news = [t for t in row.triggers if t.kind == "news"]
    assert row.verdict == "Review" and news and "News (guidance cut, 3 sources)" in news[0].text
    # Even a level-3 news alert stays a review.
    row = holdplan.check_holding(pos, 10_000, None, [dict(alerts[0], level=3)], date(2026, 9, 26), 0.2)
    assert row.verdict == "Review"
