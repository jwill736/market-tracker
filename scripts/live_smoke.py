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

from market_tracker import charts, early, http, people, pulse, radar, reading, service
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
    return ", ".join(f"{f} {len(rows)} holdings ({day})" for f, (day, rows) in got.items())


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


@check("Full analysis MSFT (with SEC)")
def _():
    a = service.analyze("MSFT")
    sig = a["signal"]
    assert sig and a["forecast"] and a["backtest"], a["errors"]
    covered = [k for k, v in sig["components"].items() if v]
    return f"score {sig['score']:+.1f} {sig['label']}, components: {', '.join(covered)}; errors: {len(a['errors'])}"


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
