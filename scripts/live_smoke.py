"""Exercise every live data source end to end and report PASS/FAIL per check.

Runs in GitHub Actions (which has normal internet access) on pull requests, on a daily
schedule, and on demand. Writes a Markdown table to $GITHUB_STEP_SUMMARY when set.
Exit code is non-zero if any check fails.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone

from market_tracker import charts, divsafety, macro, moneyflow, screen as scr, hype, earnings, factors, filings, stress, tenkrank, cryptoradar, dividends, early, events, fees, fundamentals, http, logos, lookthrough, newsdesk, people, pickers, pulse, radar, reading, service, snaptrade
from market_tracker.investors import INVESTORS, by_key
from market_tracker.providers import market, news, sec

results: list[tuple[str, bool, str]] = []


def check(name: str):
    def wrap(fn):
        start = time.monotonic()
        try:
            detail = fn() or ""
            results.append((name, True, f"{detail} ({time.monotonic() - start:.1f}s)"))
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            tb = traceback.format_exception_only(type(exc), exc)[-1].strip()
            results.append((name, False, tb[:300]))
        return fn
    return wrap


@check("Coinbase quote BTC-USD")
def _():
    q = market.get_quote("BTC")
    assert q.price > 1000, q
    return f"${q.price:,.0f}, 24h {q.change_pct:+.2f}%"


@check("Coinbase daily history ETH-USD (400d, paged)")
def _():
    h = market.get_history("ETH", 400)
    assert len(h) > 350, len(h)
    return f"{len(h)} bars, last {h[-1].date}"


@check("Yahoo quote AAPL")
def _():
    q = market.get_quote("AAPL")
    assert q.price > 1, q
    return f"${q.price:,.2f} via {q.source}"


@check("Yahoo history SPY (5y)")
def _():
    h = market.get_history("SPY", 1300)
    assert len(h) > 1000, len(h)
    return f"{len(h)} bars, {h[0].date} → {h[-1].date}"


@check("SEC ticker map")
def _():
    cik = sec.ticker_map().cik_for("AAPL")
    assert cik == "0000320193", cik
    return f"{len(sec.ticker_map().by_ticker)} tickers"


@check("SEC investor CIKs match filer names")
def _():
    bad = []
    for inv in INVESTORS:
        filer, ok = sec.verify_investor(inv)
        if not ok:
            bad.append(f"{inv.key} ({inv.cik}) -> '{filer}'")
    assert not bad, "; ".join(bad)
    return f"all {len(INVESTORS)} verified"


@check("SEC 13F parse: Berkshire")
def _():
    rep = sec.investor_report(by_key("buffett"))
    top = rep["top_holdings"][:5]
    assert rep["positions"] > 10 and rep["total_value_usd"] > 1e10, rep["positions"]
    mapped = sum(1 for p in rep["holdings"] if p["ticker"])
    unmapped = [p["issuer"] for p in rep["holdings"] if not p["ticker"]]
    assert mapped / rep["positions"] > 0.85, (
        f"only {mapped}/{rep['positions']} issuers mapped to tickers; unmapped: {', '.join(unmapped)}")
    return (f"period {rep['period']}, {rep['positions']} positions, {mapped} mapped, top: "
            + ", ".join(p["ticker"] or p["issuer"] for p in top)
            + (f"; unmapped: {', '.join(unmapped)}" if unmapped else ""))


@check("SEC Form 4 parse: AAPL insiders (180d)")
def _():
    trades = sec.get_insider_trades("AAPL", days=180)
    s = sec.summarize_insiders(trades, days=180)
    assert trades, "no Form 4 transactions parsed"
    return f"{len(trades)} transactions, {s['open_market_sells']} open-market sells, {s['open_market_buys']} buys"


@check("Google News RSS")
def _():
    n = news.get_news("NVDA")
    assert n["count"] > 5, n["errors"]
    return f"{n['count']} headlines, mood {n['avg_sentiment']:+.2f}"


@check("Market-wide news")
def _():
    n = news.market_news()
    assert n["count"] > 10, n["errors"]
    return f"{n['count']} headlines"


@check("EDGAR latest-filings feed (filing watcher)")
def _():
    from market_tracker import realtime
    entries = realtime.parse_feed(realtime.fetch_feed("4", 0))
    forms = {e.form for e in entries}
    # The feed can be nearly empty overnight or at weekends; the point is that it parses.
    assert all(e.accession and e.cik and e.role for e in entries), entries[:3]
    stakes = realtime.parse_feed(realtime.fetch_feed("SCHEDULE 13D", 0))
    return (f"{len(entries)} entries ({sum(e.form == '4' for e in entries)} Form 4, forms: {', '.join(sorted(forms)[:6])}); "
            f"{len(stakes)} Schedule 13D entries")


@check("Dilution check (EDGAR filing list)")
def _():
    from datetime import date

    from market_tracker import dilution
    d = dilution.check("320193", date.today())
    assert not d.error, d.error
    # Apple keeps an automatic shelf for bond issues; it must not read as a dilution risk.
    assert not d.flagged, (d.level, d.notes)
    return "AAPL: not flagged (automatic debt shelf ignored)"


@check("SEC fund filter (insider alerts)")
def _():
    from market_tracker import alerts
    from market_tracker.providers import sec
    if not alerts.issuer_is_fund("40417"):
        d = sec._sec_get(sec.SUBMISSIONS.format(cik="0000040417"), ttl=0)
        forms = sorted(set(d.get("filings", {}).get("recent", {}).get("form", [])))
        raise AssertionError(f"General American Investors should read as a fund (sic={d.get('sic')!r}, "
                             f"entityType={d.get('entityType')!r}, forms={forms[:25]})")
    assert not alerts.issuer_is_fund("320193"), "Apple should not read as a fund"
    return "General American Investors = fund, Apple = operating company"


@check("Yahoo market-news fallback feeds")
def _():
    counts = {sym: len(news._yahoo(sym)) for sym in news.MARKET_FALLBACK_SYMBOLS}
    assert all(counts.values()), counts
    return ", ".join(f"{k}: {v} headlines" for k, v in counts.items())


@check("Yahoo movers screens (market pulse)")
def _():
    got = {name: pulse.fetch_screen(name) for name in pulse.SCREENS}
    assert all(got.values()), {k: len(v) for k, v in got.items()}
    top = got["gainers"][0]
    assert top.price and top.change_pct is not None, top
    return ", ".join(f"{k}: {len(v)}" for k, v in got.items()) + f"; top gainer {top.symbol} {top.change_pct:+.1f}%"


@check("Yahoo live quote with extended hours (AAPL)")
def _():
    q = market.get_live_quote("AAPL")
    assert q.price > 0 and q.session in ("pre", "regular", "post", "closed"), q
    chg = f"{q.change_pct:+.2f}%" if q.change_pct is not None else "no change"
    return f"${q.price:,.2f} {chg} · session {q.session} · as of {q.as_of}"


@check("Watcher purchases for sleepers (journal-data branch)")
def _():
    buys = pulse.load_watcher_buys()
    assert buys, "no rows"
    return f"{len(buys)} purchases, latest filed {max(b.filed for b in buys)}"


@check("Filing radar: EDGAR feeds classify (8-K items, delistings, late filings)")
def _():
    entries = radar.fetch_feed("8-K")
    assert entries and any(e.items for e in entries), "8-K entries carry no item numbers"
    alerts, errors = radar.scan_market()
    assert not errors, errors
    levels = {n: sum(1 for a in alerts if a.level == n) for n in (3, 2, 1)}
    return f"{len(entries)} 8-Ks in the feed; radar filings now: {levels[3]} act-today, {levels[2]} serious, {levels[1]} read-it"


@check("Filing radar: going-concern full-text search")
def _():
    found = radar.going_concern_ciks(date.today(), days=60, pages=1)
    assert found, "no hits"
    return f"{len(found)} companies with going-concern language in 60 days (first page)"


@check("Filing radar: a company's history (Apple)")
def _():
    subs = sec._sec_get(sec.SUBMISSIONS.format(cik="0000320193"), ttl=0)
    recent = subs["filings"]["recent"]
    assert "items" in recent and any(recent["items"]), "no 8-K item numbers"
    got = radar.company_alerts("0000320193", subs, date.today() - timedelta(days=365), "AAPL")
    assert not [a for a in got if a.level == 3], [a.headline for a in got if a.level == 3]
    f25 = [a for a, f in zip(recent["accessionNumber"], recent["form"]) if f in radar.DELISTING_FORMS][:3]
    scopes = []
    for acc in f25:
        url = f"https://www.sec.gov/Archives/edgar/data/320193/{acc.replace('-', '')}/{acc}.txt"
        scopes.append(radar.delisting_scope(http.get(url, headers=radar.sec_headers(), ttl=0, as_json=False)))
    return (f"{len(got)} radar filings in a year: " + (", ".join(sorted({a.headline for a in got})) or "none")
            + f"; Apple's Form 25s read as {scopes or 'none'}")


@check("Reading room: professional feeds")
def _():
    feeds, errors = reading.fetch_all()
    ok = {k: len(v) for k, v in feeds.items() if v}
    assert len(ok) >= 12, f"only {len(ok)} of {len(reading.SOURCES)} feeds: {errors}"
    return f"{len(ok)}/{len(reading.SOURCES)} feeds" + (f"; failed: {'; '.join(errors)}" if errors else "")


@check("Reading room: links picked by Abnormal Returns / Ritholtz")
def _():
    feeds, _ = reading.fetch_all([s for s in reading.SOURCES if s.kind == "curated"])
    picks = reading.curated_picks(feeds, datetime.now(timezone.utc))
    assert len(picks) >= 10, f"{len(picks)} picks"
    both = sum(1 for p in picks if len(p.picked_by) > 1)
    return f"{len(picks)} picks, {both} picked by both; e.g. {picks[0].title[:60]} ({picks[0].domain})"


@check("Candles with volume (AAPL 1D, BTC 1W)")
def _():
    a = charts.candles("AAPL", "1d")
    b = charts.candles("BTC-USD", "1w")
    assert len(a["candles"]) >= charts.MIN_1D_CANDLES and a["candles"][-1]["v"] >= 0 and len(b["candles"]) > 50, (len(a["candles"]), len(b["candles"]))
    return f"AAPL {len(a['candles'])} 5-min candles (prev close {a['reference']}); BTC {len(b['candles'])} hourly"


@check("Early wire: social, wires, filings, crypto")
def _():
    sigs, errors, pairs = early.gather()
    kinds = {}
    for s in sigs:
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    assert kinds.get("social") and pairs, (kinds, errors)
    return f"{len(sigs)} signals {kinds}; {len(pairs)} Coinbase USD pairs" + (f"; down: {'; '.join(errors)}" if errors else "")


@check("People: ARK daily holdings (all funds)")
def _():
    got = people.fetch_ark()
    assert "ARKK" in got and len(got["ARKK"][1]) > 20, list(got)
    stale = people.stale_funds(got)
    return ", ".join(f"{f} {len(rows)} holdings ({day})" for f, (day, rows) in got.items()) + \
        (f"; stale, skipped in the app: {', '.join(stale)}" if stale else "")


@check("People: House trade reports (index + PDF text)")
def _():
    moves, errors = people.house_moves(date.today(), days=21, limit=6)
    parsed = [m for m in moves if m.symbol]
    assert moves, errors
    return f"{len(moves)} rows from {len({m.url for m in moves})} reports; {len(parsed)} trades parsed" + \
        (f"; e.g. {parsed[0].who} {parsed[0].action} {parsed[0].symbol}" if parsed else "") + (f"; notes: {len(errors)}" if errors else "")


@check("People: Senate trade reports (eFD)")
def _():
    moves, errors = people.senate_moves(date.today(), days=21)
    assert moves or not errors, errors
    return f"{len(moves)} rows" + (f"; e.g. {moves[0].who} {moves[0].action} {moves[0].symbol}" if moves else " (none filed)")


@check("Earnings date and options-implied move (Nasdaq)")
def _():
    row = events.earnings_for("NVDA", 10_000.0, date.today(), within_days=200)
    assert row, "no upcoming NVDA earnings date"
    assert row["move_pct"] and 1 < row["move_pct"] < 40, row
    return f"NVDA reports {row['date']}{' (estimated)' if row['estimated'] else ''}: options price ±{row['move_pct']}% to {row['expiry']}, ±${row['move_dollars']:,.0f} on $10,000"


@check("Fed and economic calendar")
def _():
    fomc = events.parse_fomc(http.get(events.FOMC, headers={"User-Agent": events.BROWSER_UA}, ttl=0, as_json=False))
    assert any(d.startswith(str(date.today().year)) for d in fomc), fomc
    rows, errors = events.macro(date.today(), days=14)
    fed = [r for r in rows if r["kind"] == "Fed decision"]
    assert len(fed) <= 3, f"too many Fed decisions: {[r['date'] for r in fed]}"
    upcoming = [d for d in fomc if d >= date.today().isoformat()]
    return (f"Fed page: {len(fomc)} decision days, next {upcoming[0] if upcoming else 'none listed'}; "
            f"{len(rows)} events: " + ", ".join(f"{r['date']} {r['kind']}" for r in rows[:6]))


@check("Crypto radar: hacks, Coinbase status, supply")
def _():
    r = cryptoradar.build(["BTC-USD", "ETH-USD", "SOL-USD", "SUI-USD"])
    assert not r["errors"], r["errors"]
    assert "SUI" in r["supply"], r["supply"]
    return f"{len(r['alerts'])} alerts ({', '.join(sorted({a['kind'] for a in r['alerts']})) or 'none'}); SUI {r['supply']['SUI']['circulating_pct']}% circulating"


@check("StockTwits picker stream (tagged calls with prices)")
def _():
    profile, calls = pickers.fetch_calls("alphatrends", pages=2)
    assert profile.get("username"), profile
    # Many pros never tag posts, so check the parser on a busy ticker stream, where people do.
    tagged = pickers.calls_from_stream(http.get("https://api.stocktwits.com/api/2/streams/symbol/TSLA.json",
                                                headers=pickers.HEADERS, ttl=0))
    assert tagged, "no Bullish/Bearish-tagged posts parsed from the TSLA stream"
    sugg = pickers.suggested()
    return (f"{profile['username']}: {len(calls)} tagged calls in 2 pages; TSLA stream: {len(tagged)} tagged calls, e.g. "
            f"{tagged[0]['user']} {tagged[0]['side']} at {tagged[0]['price']}; {len(sugg)} suggested accounts")


@check("Full analysis MSFT (with SEC)")
def _():
    a = service.analyze("MSFT")
    sig = a["signal"]
    assert sig and a["forecast"] and a["backtest"], a["errors"]
    covered = [k for k, v in sig["components"].items() if v]
    return f"score {sig['score']:+.1f} {sig['label']}, components: {', '.join(covered)}; errors: {len(a['errors'])}"


@check("SEC company financials: AAPL quarters, growth, free cash flow")
def _():
    fundamentals.clear_cache()
    qs = fundamentals.for_symbol("AAPL")
    assert len(qs) >= 5, f"only {len(qs)} quarters"
    q = qs[0]
    assert q.revenue and q.revenue > 5e10, q
    assert q.revenue_yoy is not None and -50 < q.revenue_yoy < 100, q.revenue_yoy
    assert q.operating_margin and 10 < q.operating_margin < 60, q.operating_margin
    assert q.fcf_ttm and q.fcf_ttm > 0, q.fcf_ttm
    assert q.shares_yoy is not None and abs(q.shares_yoy) < 20, q.shares_yoy
    # Quarters must be ~3 months apart: derived Q4s and cash-flow quarters line up.
    gaps = [(date.fromisoformat(a.end) - date.fromisoformat(b.end)).days for a, b in zip(qs, qs[1:])]
    assert all(80 <= g <= 100 for g in gaps), gaps
    return fundamentals.summary_line(qs) + f"; checks: {fundamentals.check(qs) or 'none'}"


@check("Dividends: KO history (Yahoo), AAPL declared (Nasdaq)")
def _():
    ko = dividends.history("KO")
    c = dividends.cadence(ko, date.today())
    assert c and c["per_year"] == 4 and 0.2 < c["amount"] < 2, c
    aapl = dividends.declared("AAPL")
    assert aapl and aapl["ex_date"] and aapl["amount"], aapl
    return (f"KO {len(ko)} payments, {c['per_year']}/yr at ${c['amount']}, last ex {c['last_ex']}; "
            f"AAPL declared ${aapl['amount']} ex {aapl['ex_date']} paid {aapl['pay_date']}")


@check("SnapTrade API reachable (rejects unsigned requests)")
def _():
    import httpx
    up = httpx.get(snaptrade.HOST + "/", timeout=20)
    assert up.status_code == 200 and up.json().get("online"), up.text[:200]
    denied = httpx.get(snaptrade.HOST + "/accounts", timeout=20)
    assert denied.status_code in (401, 403), denied.status_code
    return f"online (API version {up.json().get('version')}); /accounts without a key: {denied.status_code}"


@check("Logos and names: AAPL, VOO, SUI; unknown ticker falls back")
def _():
    got = {s: logos.fetch_logo(s) for s in ("AAPL", "VOO", "SUI-USD", "ZZZZQ")}
    for s in ("AAPL", "VOO", "SUI-USD"):
        assert got[s] and got[s][1].startswith("image/") and len(got[s][0]) > 200, (s, got[s] and got[s][1])
    assert got["ZZZZQ"] is None, "an unknown ticker should have no logo (the app draws a letter instead)"
    assert logos.lookup_name("SUI-USD") == "Sui"
    assert "Apple" in logos.lookup_name("AAPL")
    return ", ".join(f"{s} {len(v[0]):,} B {v[1]}" for s, v in got.items() if v) + f"; names: AAPL = {logos.lookup_name('AAPL')}, VOO = {logos.lookup_name('VOO') or '(none)'}"


@check("What funds hold: VOO and SPY (via IVV) from SEC N-PORT")
def _():
    voo = lookthrough.holdings("VOO", now=time.time() + 10 ** 9)          # far-future 'now' skips any cache
    # The app keeps a fund's largest 300 holdings; a full S&P 500 filing fills that cap.
    assert voo and len(voo["holdings"]) == 300, voo and len(voo["holdings"])
    top = voo["holdings"][0]
    assert 0.02 < top["pct"] < 0.2, top
    names = " ".join(h["name"] for h in voo["holdings"][:10]).lower()
    assert "apple" in names or "nvidia" in names or "microsoft" in names, names
    spy = lookthrough.holdings("SPY", now=time.time() + 10 ** 9)
    assert spy and spy["source"] == "IVV", spy and spy["source"]
    return f"VOO {len(voo['holdings'])} holdings as of {voo['period']}, top {top['name']} {top['pct']:.1%}; SPY via {spy['source']} ({spy['period']})"


@check("Fund fees: VOO and SPY expense ratios")
def _():
    voo, spy = fees.expense_ratio("VOO", now=time.time() + 10 ** 9), fees.expense_ratio("SPY", now=time.time() + 10 ** 9)
    assert voo is not None and 0 < voo < 0.002, voo
    assert spy is not None and voo < spy < 0.005, spy
    return f"VOO {voo:.2%}, SPY {spy:.4%}"


@check("Robinhood Crypto API and Coinbase orders endpoint reachable (refuse unsigned requests)")
def _():
    import httpx
    rh = httpx.get("https://trading.robinhood.com/api/v1/crypto/trading/accounts/", timeout=20)
    assert rh.status_code in (400, 401, 403) and "x-api-key" in rh.text.lower(), (rh.status_code, rh.text[:120])
    cb = httpx.post("https://api.coinbase.com/api/v3/brokerage/orders/preview", json={}, timeout=20)
    assert cb.status_code in (401, 403), cb.status_code
    return f"Robinhood {rh.status_code} (asks for x-api-key/x-signature/x-timestamp); Coinbase preview {cb.status_code} without a key"


@check("News desk: sources answer and Apple's stories cluster")
def _():
    newsdesk.FEED_STATUS.clear()
    desk = newsdesk.build(["AAPL", "BTC-USD"], {"AAPL": "Apple"}, {}, [], crypto_names={"BTC-USD": "Bitcoin"})
    feeds = newsdesk.FEED_STATUS
    ok = sorted(k for k, v in feeds.items() if v["ok"] and v["items"])
    down = sorted(k for k, v in feeds.items() if not v["ok"])
    assert len(ok) >= 12, (ok, down)
    assert desk["AAPL"]["items"] >= 20 and desk["AAPL"]["stories"], desk["AAPL"]["items"]
    top = desk["AAPL"]["stories"][0]
    return (f"{len(ok)}/{len(feeds)} sources with items" + (f" (down: {', '.join(down)})" if down else "")
            + f"; AAPL {desk['AAPL']['items']} headlines → {len(desk['AAPL']['stories'])} stories, top: {top['tier']}/{top['event']} "
              f"from {top['sources']} outlets")


@check("Stock brokers reachable: Alpaca and Public.com refuse requests without a key")
def _():
    import httpx
    alp = httpx.get("https://paper-api.alpaca.markets/v2/account", timeout=20)
    assert alp.status_code in (401, 403), alp.status_code
    pub = httpx.get("https://api.public.com/userapigateway/trading/account", timeout=20)
    assert pub.status_code in (401, 403), pub.status_code
    return f"Alpaca {alp.status_code}, Public.com {pub.status_code}"


@check("Annual reports: AAPL's last two 10-Ks, Risk Factors cut out and compared")
def _():
    r = filings.tenk_changes("AAPL")
    risk = r["sections"]["risk"]
    assert risk["words"] > 3000 and risk["words_before"] > 3000, (risk["words"], risk["words_before"])
    assert risk["similarity"] is not None and 0.5 < risk["similarity"] <= 1.0, risk["similarity"]
    # Apple rewrites little of its Risk Factors in a year: a big "new" share with near-identical
    # wording means edits are being counted as new text.
    assert risk["new_share"] < 0.4 or risk["similarity"] < 0.95, (risk["new_share"], risk["similarity"])
    return (f"{r['current']['form']} {r['current']['filed']} vs {r['previous']['filed']}: Risk Factors {risk['words']:,} words, "
            f"{risk['new_share']:.0%} new, similarity {risk['similarity']:.2f}; level {r['level']}")


@check("Crisis replay: SPY and QQQ daily history back to 2007")
def _():
    days = (date.today() - date(2007, 9, 1)).days
    spy = [(b.date, b.close) for b in market.get_history("SPY", days)]
    assert spy[0][0] <= "2007-10-09", spy[0][0]
    fall = stress.window_return(spy, "2007-10-09", "2009-03-09")
    assert fall is not None and -0.6 < fall < -0.4, fall
    covid = stress.window_return(spy, "2020-02-19", "2020-03-23")
    assert -0.4 < covid < -0.25, covid
    return f"{len(spy):,} days from {spy[0][0]}; 2008 {fall:.0%}, 2020 {covid:.0%}"


@check("Style bets: Kenneth French's daily factor files, VOO's loadings")
def _():
    ff = factors.load()
    last = max(ff)
    assert (date.today() - date.fromisoformat(last)).days < 150, f"factor file ends {last}"
    voo = [(b.date, b.close) for b in market.get_history("VOO", 800)]
    r = factors.exposure(factors._returns(voo), ff)
    mkt = r["loadings"][0]["beta"]
    assert 0.9 < mkt < 1.1 and r["r2"] > 0.9, r
    return f"{len(ff):,} days to {last}; VOO market {mkt:.2f}, R² {r['r2']:.2f} over {r['days']} days"


@check("Earnings recap: AAPL's latest results release (8-K ex. 99.1)")
def _():
    r = earnings.recap("AAPL")
    assert r and r["release"]["url"].endswith((".htm", ".html")), r
    assert len(r["highlights"]) >= 2 and any("revenue" in h.lower() or "net sales" in h.lower() or "per share" in h.lower() for h in r["highlights"]), r["highlights"]
    re_ = r.get("reaction") or {}
    return (f"filed {r['release']['filed']}; {len(r['highlights'])} headline sentences, outlook {r['outlook']['direction']}; "
            f"stock {re_.get('move_pct')}% next day; e.g. {r['highlights'][0][:90]}")


@check("Dividend safety: KO and JNJ from SEC cash flows and Yahoo dividend history")
def _():
    out = []
    for sym in ("KO", "JNJ"):
        g = divsafety.for_symbol(sym)
        assert g and g["grade"] in "ABCD" and g["fcf_payout"] and 20 < g["fcf_payout"] < 150, (sym, g)
        assert g["history"]["years_growing"] >= 10, (sym, g["history"])
        out.append(f"{sym} {g['grade']} ({g['fcf_payout']:.0f}% of FCF, {g['history']['years_growing']} yrs of raises)")
    return "; ".join(out)


@check("10-K ranking: S&P 500 list from Wikipedia, one company scored")
def _():
    cos = tenkrank.universe()
    assert len(cos) >= 480, len(cos)
    aapl = next(c for c in cos if c["symbol"] == "AAPL")
    assert aapl["cik"] == "0000320193", aapl
    brk = next((c for c in cos if c["symbol"] == "BRK-B"), None)
    e = tenkrank.score_one(next(c for c in cos if c["symbol"] == "MSFT"), None)
    assert e.get("risk_new") is not None and e["risk_words"] > 3000, e
    src = "Wikipedia" if any(c["sector"] for c in cos) else "IVV holdings (Wikipedia unavailable)"
    return f"{len(cos)} companies from {src} (BRK-B {'found' if brk else 'missing'}); MSFT 10-K {e['filed']}: {e['risk_new']:.0%} of Risk Factors new"


@check("Economy panel: FRED series one at a time")
def _():
    d = macro.load()
    assert len(d) >= 7 and d["DGS10"] and 0 < d["DGS10"][-1][1] < 15, {k: v[-1:] for k, v in d.items()}
    b = macro.build()
    return f"{len(d)} series; 10-year {d['DGS10'][-1][1]}% ({d['DGS10'][-1][0]}); pace: {b['pace']['level']}"


@check("Screen inputs: SEC XBRL frames and Nasdaq's stock list")
def _():
    y, q = scr.latest_quarter(date.today())
    assets = scr.frame("Assets", f"CY{y}Q{q}I")
    op = scr.frame("OperatingIncomeLoss", f"CY{y if q == 4 else y - 1}")
    lst = scr.listed()
    big = sum(1 for r in lst.values() if r["cap"] >= scr.MIN_CAP)
    assert len(assets) >= 4000 and len(op) >= 2000 and big >= 2500, (len(assets), len(op), big)
    return f"CY{y}Q{q}: {len(assets):,} balance sheets, {len(op):,} operating incomes; {len(lst):,} listed stocks, {big:,} worth $300M+"


@check("Government contracts (USAspending): Lockheed Martin")
def _():
    r = moneyflow.contracts("Lockheed Martin Corp", 7e10)
    assert r["amount"] and r["amount"] > 1e10, r
    return r["text"]


@check("Theme crowding: new fund filings (SEC full-text search)")
def _():
    rows = hype.themes()
    ai = next(r for r in rows if r["theme"].startswith("AI"))
    assert ai["last_6m"] > 50, ai
    return "; ".join(f"{r['theme']} {r['last_6m']} ({r['level']})" for r in rows[:5])


@check("Short interest (FINRA consolidated, latest settlement)")
def _():
    from market_tracker import shorts
    shorts._cache.clear()
    rows = shorts.latest(["AAPL", "GME", "XOM"])
    assert {"AAPL", "GME"} <= set(rows), rows
    days = (date.today() - date.fromisoformat(rows["AAPL"]["settlement"])).days
    assert days <= 40, rows["AAPL"]
    return "; ".join(f"{s} {r['short']:,.0f} short, {r['days_to_cover']} days to cover ({r['settlement']})" for s, r in rows.items())


@check("Events: results releases mentioning guidance, spin-off registrations (SEC full-text search)")
def _():
    from market_tracker import pead, spinoffs
    today = date.today()
    cands = pead.candidates(today)
    regs = spinoffs.registrations(today)
    assert len(regs) >= 5, regs
    return f"{len(cands)} results releases with guidance in 10 days (e.g. {', '.join(cands[:5])}); {len(regs)} spin-off registrants, latest {regs[0]['name']} ({regs[0]['last_filed']})"


def main() -> int:
    # Never print the User-Agent itself: it holds a contact email and CI logs may be public.
    ua_set = bool(os.environ.get("SEC_USER_AGENT"))
    lines = ["## Live data smoke test", "",
             f"SEC User-Agent: {'set (hidden)' if ua_set else '**not set** — SEC blocks requests without a contact email'}",
             "", "| Check | Result | Detail |", "|---|---|---|"]
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
        lines.append(f"| {name} | {'✅ pass' if ok else '❌ FAIL'} | {detail.replace('|', '/')} |")
    failed = [r for r in results if not r[1]]
    if not ua_set and any("sec.gov" in detail and "403" in detail for _, ok, detail in failed):
        hint = ("SEC returned 403: add a repository secret SEC_USER_AGENT such as "
                "'market-tracker you@example.com' (Settings → Secrets and variables → Actions).")
        print(hint)
        lines += ["", f"> {hint}"]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    http.clear_cache()
    sys.exit(main())
