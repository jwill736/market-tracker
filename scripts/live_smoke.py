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

from market_tracker import http, service
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
