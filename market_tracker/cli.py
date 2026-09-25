"""Command-line interface: `mt <command>`."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

from . import db, http, journal, research, service
from .investors import INVESTORS, by_key
from .providers import market, news, sec


def _pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x:+.{digits}f}%"


def _money(x: float | None) -> str:
    return "—" if x is None else f"${x:,.2f}"


def cmd_quote(args) -> int:
    for sym in args.symbols:
        try:
            q = market.get_quote(sym)
            print(f"{q.symbol:<10} {_money(q.price):>14} {_pct(q.change_pct):>9}  ({q.source}, {q.as_of})")
        except http.DataUnavailable as exc:
            print(f"{sym:<10} unavailable: {exc}", file=sys.stderr)
    return 0


def cmd_analyze(args) -> int:
    a = service.analyze(args.symbol, with_smart_money=not args.fast, with_insiders=not args.fast)
    q, ind, sig = a["quote"], a["indicators"], a["signal"]
    print(f"\n{a['symbol']} ({a['asset_class']})  {_money(q and q['price'])}  {_pct(q and q['change_pct'])}")
    if ind:
        def f(key: str, scale: float = 1.0, suffix: str = "") -> str:
            return "—" if ind.get(key) is None else f"{ind[key] * scale:.0f}{suffix}"
        print(f"  RSI {f('rsi14')}   vol {f('vol_annual', 100, '%')}   1y max DD {f('max_drawdown_1y', 100, '%')}"
              f"   12-1 mom {_pct(ind['momentum_12_1'] * 100 if ind.get('momentum_12_1') is not None else None)}")
    if sig:
        print(f"\n  Signal: {sig['label']} ({sig['score']:+.0f}/100, coverage {sig['coverage'] * 100:.0f}%)")
        for name, comp in sig["components"].items():
            if comp:
                print(f"    {name:<12} {comp['score']:+6.0f}  {comp['why']}")
        if sig["suggested_max_weight"]:
            print(f"  Suggested max position: {sig['suggested_max_weight'] * 100:.1f}% of portfolio")
        for flag in sig["risk_flags"]:
            print(f"  ! {flag}")
    if a["forecast"]:
        print("\n  Range (lognormal, 90% band):")
        for row in a["forecast"]["lognormal"]:
            print(f"    {row['horizon_days']:>3}d  {_money(row['p5'])} – {_money(row['p95'])}"
                  f"   median {_money(row['p50'])}   P(up) {row['prob_up'] * 100:.0f}%")
    if a["backtest"]:
        print("\n  Backtest (in-sample, 10bps costs):")
        for name, r in a["backtest"]["results"].items():
            if r:
                print(f"    {name:<14} CAGR {_pct(r['cagr'] and r['cagr'] * 100)}  Sharpe {r['sharpe'] or 0:.2f}"
                      f"  maxDD {r['max_drawdown'] * 100:.0f}%")
    for e in a["errors"]:
        print(f"  (warning) {e}", file=sys.stderr)
    return 0


def cmd_investors(args) -> int:
    if args.verify:
        bad = 0
        for inv in INVESTORS:
            try:
                filer, ok = sec.verify_investor(inv)
            except http.DataUnavailable as exc:
                filer, ok = f"unreachable: {exc}", False
            bad += not ok
            print(f"  {'OK  ' if ok else 'FAIL'} {inv.key:<14} {inv.cik}  expected '{inv.expected_name}'  got '{filer}'")
        return 1 if bad else 0
    if not args.key:
        for i in INVESTORS:
            print(f"{i.key:<14} {i.person:<24} {i.fund:<34} {i.style}")
        return 0
    inv = by_key(args.key)
    if not inv:
        print(f"Unknown investor {args.key}", file=sys.stderr)
        return 1
    rep = sec.investor_report(inv)
    print(f"{inv.person} — {rep['filer_name']}  13F period {rep['period']} (filed {rep['filed']}, "
          f"{rep['staleness_days']} days old)")
    if not rep["cik_verified"]:
        print(f"  ! Filer name doesn't match expected '{inv.expected_name}' — check the CIK")
    print(f"  Reported value {_money(rep['total_value_usd'])} across {rep['positions']} positions\n")
    for p in rep["top_holdings"][:15]:
        print(f"  {p['ticker'] or '?':<7} {p['issuer'][:32]:<32} {p['weight_now']:5.1f}%  {p['action']}")
    for action in ("new", "added", "reduced", "exited"):
        moves = rep["moves"][action][:10]
        if moves:
            print(f"\n  {action.upper()}: " + ", ".join(m["ticker"] or m["issuer"][:20] for m in moves))
    return 0


def cmd_consensus(args) -> int:
    reports, errors = service.smart_money_reports()
    c = sec.consensus(reports, limit=args.limit)
    print("Most bought by tracked investors:")
    for r in c["most_bought"]:
        print(f"  {r['ticker'] or r['issuer'][:20]:<22} score {r['score']:+.2f}  "
              + ", ".join(f"{b['investor']} ({b['action']})" for b in r["buyers"]))
    print("\nMost sold:")
    for r in c["most_sold"]:
        print(f"  {r['ticker'] or r['issuer'][:20]:<22} score {r['score']:+.2f}  "
              + ", ".join(f"{s['investor']} ({s['action']})" for s in r["sellers"]))
    for e in errors:
        print(f"  (warning) {e}", file=sys.stderr)
    return 0


def cmd_news(args) -> int:
    data = news.get_news(args.symbol) if args.symbol else news.market_news()
    print(f"{data['count']} headlines, avg sentiment {data['avg_sentiment']:+.2f}  "
          f"({data['positive']} positive / {data['negative']} negative)")
    print("Trending terms: " + ", ".join(f"{t}({n})" for t, n in data["top_terms"]))
    for a in data["articles"][: args.limit]:
        print(f"  {a['sentiment']:+.2f}  {a['published'][:10]}  {a['title']}  — {a['source']}")
    return 0


def cmd_portfolio(args) -> int:
    with db.connect() as conn:
        if args.action == "add":
            sym = market.normalize_symbol(args.symbol)
            db.add_transaction(conn, sym, args.side, args.quantity, args.price,
                               args.date or date.today().isoformat(), args.fees)
            print(f"Recorded {args.side} {args.quantity} {sym} @ {args.price}")
            return 0
        txs = db.list_transactions(conn)
    s = service.portfolio_summary(txs)
    print(f"Value {_money(s['total_value'])}   cost {_money(s['total_cost'])}   "
          f"unrealized {_money(s['unrealized_pnl'])} ({_pct(s['unrealized_pct'])})   "
          f"realized {_money(s['realized_pnl'])}")
    for p in s["positions"]:
        print(f"  {p['symbol']:<10} {p['quantity']:>12,.4f} @ {_money(p['avg_cost']):>12}  now {_money(p['price']):>12}"
              f"  {_pct(p['unrealized_pct']):>9}  weight {p['weight'] or 0:5.1f}%")
    risk = s.get("risk") or {}
    if risk.get("annual_vol") is not None:
        print(f"\n  Portfolio vol {risk['annual_vol'] * 100:.0f}%  Sharpe {risk['sharpe'] or 0:.2f}  "
              f"max DD {risk['max_drawdown'] * 100:.0f}%  1-day 95% VaR {risk['var_95_1d'] * 100:.1f}%")
    for w in risk.get("warnings", []):
        print(f"  ! {w}")
    return 0


def cmd_research(args) -> int:
    print(f"Collecting data for {args.symbol}…", file=sys.stderr)
    analysis = service.analyze(args.symbol)
    memo = ""
    for event in research.stream_memo(analysis, args.question):
        if event["type"] == "text":
            print(event["text"], end="", flush=True)
        elif event["type"] == "status":
            print(f"\n[{event['text']}]", file=sys.stderr)
        elif event["type"] == "error":
            print(f"\n{event['text']}", file=sys.stderr)
            return 1
        elif event["type"] == "done":
            memo = event["memo"]
    verdict = research.extract_verdict(analysis["symbol"], memo) if memo else None
    if verdict:
        print(f"\n\n=== Verdict: {verdict.rating} ({verdict.conviction} conviction, {verdict.horizon}) ===")
        print(f"Max position: {verdict.max_position_pct:.1f}%\nInvalidation: {verdict.invalidation}")
    return 0


def cmd_journal(args) -> int:
    if args.action == "sync":
        n = journal.sync_from_remote()
        print(f"Merged {n} entries from {journal.REMOTE_URL} into {journal.journal_path()}")
        return 0
    if args.action == "record":
        symbols = args.symbols
        if not symbols and not args.default_universe:
            with db.connect() as conn:
                symbols = db.watchlist(conn)
        symbols = symbols or journal.DEFAULT_UNIVERSE
        entries, failed = [], []
        for sym in symbols:
            a = service.analyze(sym, with_smart_money=args.sec, with_insiders=args.sec)
            entry = journal.entry_from_analysis(a)
            if entry:
                entries.append(entry)
                print(f"  {entry['symbol']:<10} {entry['score']:+6.1f}  {entry['label']}")
            else:
                failed.append(sym)
                print(f"  {sym:<10} skipped: {'; '.join(a['errors']) or 'no signal'}", file=sys.stderr)
        journal.record(entries)
        print(f"Recorded {len(entries)} entries to {journal.journal_path()}")
        return 1 if failed and not entries else 0
    rows = journal.load()
    if not rows:
        print(f"No journal entries in {journal.journal_path()}. Run `mt journal record` daily "
              "(or `mt journal sync` to pull the GitHub Actions journal).")
        return 0
    report = journal.evaluate(rows, journal.history_closes)
    print(journal.report_markdown(report))
    if args.summary_file:
        with open(args.summary_file, "a", encoding="utf-8") as fh:
            fh.write(journal.report_markdown(report) + "\n")
    for e in report["errors"]:
        print(f"  (warning) {e}", file=sys.stderr)
    return 0


def cmd_score_backtest(args) -> int:
    import json
    from datetime import date as _date

    from . import history, score_backtest

    symbols = args.symbols or score_backtest.DEFAULT_UNIVERSE
    end = args.end or _date.today().isoformat()
    history.log(f"Loading history for {len(symbols)} symbols from {args.start}…")
    data = history.load_all(symbols, args.start, args.investors)
    calendar = [d for d, _ in history.load_prices(["SPY"]).get("SPY", [])]
    dates = score_backtest.month_ends(calendar, args.start, end)
    history.log(f"Scoring {len(dates)} month-ends…")
    rows = score_backtest.score_history(data, dates)
    result = score_backtest.evaluate(rows)
    md = score_backtest.report_markdown(result)
    print(md)
    if args.summary_file:
        with open(args.summary_file, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"result": result, "rows": rows}, fh)
    return 0


def cmd_alerts(args) -> int:
    import json
    import os
    from datetime import date as _date, timedelta

    from . import alerts, dilution, scorecard

    data_dir = args.data_dir
    os.makedirs(data_dir, exist_ok=True)
    buys_path = os.path.join(data_dir, "insider_buys.csv")
    alerted_path = os.path.join(data_dir, "alerted.csv")
    state_path = os.path.join(data_dir, "alerts_state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    today = _date.today()
    if args.days:
        start = today - timedelta(days=args.days)
    elif state.get("last_scanned"):
        start = _date.fromisoformat(state["last_scanned"]) + timedelta(days=1)
    else:
        start = today - timedelta(days=3)
    buys = alerts.load_buys(buys_path)
    last_scanned = state.get("last_scanned")
    day = start
    while day < today:  # today's index isn't published until after the close
        if day.weekday() < 5:
            found = alerts.scan_day(day, log=lambda m: print(m, file=sys.stderr, flush=True))
            if found is not None:
                buys.extend(found)
                last_scanned = day.isoformat()
        day += timedelta(days=1)
    clusters = alerts.find_clusters(buys, today)
    alerted = alerts.load_alerted(alerted_path)
    reasons = {c.issuer_cik: alerts.skip_reason(c, alerted, today) for c in clusters}
    fresh = [c for c in clusters if reasons[c.issuer_cik] is None]
    print(f"{len(clusters)} active clusters, {len(fresh)} new alerts. Not alerted: SEEN = alerted in the last "
          f"{alerts.REALERT_AFTER_DAYS} days, 1DAY = all buys on one day, OLD = newest filing over "
          f"{alerts.ALERT_MAX_AGE_DAYS} days old, NOTK = no ticker, FUND = closed-end fund or BDC")
    for c in clusters:
        roles = sorted({b.role for b in c.buys})
        tag = f"{reasons[c.issuer_cik] or 'NEW':<4}"
        print(f"  {tag} {c.symbol or '-':<6} {c.issuer_name[:40]:<40} "
              f"{len(c.insiders)} insiders  {alerts._money(c.total_value):>9}  {c.first_trade} → {c.last_trade} "
              f"({c.trade_days} trading day{'s' if c.trade_days != 1 else ''}; roles: {'; '.join(roles)[:120]})")
    checks = {c.issuer_cik: dilution.check(c.issuer_cik, today) for c in fresh}
    if args.issues_dir:
        for i, c in enumerate(fresh):
            _write_issue(args.issues_dir, i, "insider-alert", alerts.issue_title(c),
                         alerts.issue_body(c, dilution.issue_section(checks[c.issuer_cik])))
    if args.notify and not args.dry_run:
        for c in fresh:
            _notify_cluster(c, checks[c.issuer_cik])
    if not args.dry_run:
        log_path = os.path.join(data_dir, "alert_log.csv")
        log = scorecard.ensure_log(log_path, alerted, buys)
        for c in fresh:
            alerted[c.issuer_cik] = today.isoformat()
        scorecard.save_log(log + [scorecard.from_cluster(c, today) for c in fresh], log_path)
        alerts.save_buys(buys, buys_path, today)
        alerts.save_alerted(alerted, alerted_path)
        with open(state_path, "w") as fh:
            json.dump({"last_scanned": last_scanned}, fh)
    return 0


def _notify_cluster(c, dil=None) -> None:
    from . import alerts, dilution, notify

    latest = max(c.buys, key=lambda b: b.filed)
    warn = f" {dilution.short_label(dil)}." if dil is not None and dil.flagged else ""
    notify.send(notify.Message(
        title=f"Insider cluster: {c.symbol or c.issuer_name} - {len(c.insiders)} insiders, {alerts._money(c.total_value)}",
        body=f"{c.issuer_name}: {len(c.insiders)} officers/directors bought on the open market, "
             f"{c.first_trade} to {c.last_trade}.{warn} An alert issue has been opened.",
        url=f"{alerts.ARCHIVES}edgar/data/{int(latest.issuer_cik)}/{latest.accession.replace('-', '')}/",
        priority=4, tags=("chart_with_upwards_trend",)))


def _write_issue(issues_dir: str, n: int, label: str, title: str, body: str) -> None:
    """The file name carries the issue label (insider-alert or stake-alert)."""
    import os

    os.makedirs(issues_dir, exist_ok=True)
    stem = os.path.join(issues_dir, f"{n:03d}.{label}")
    with open(stem + ".title", "w") as fh:
        fh.write(title)
    with open(stem + ".md", "w") as fh:
        fh.write(body)


def cmd_watch(args) -> int:
    import os
    import time
    from datetime import date as _date, datetime, timedelta, timezone

    from . import alerts, dilution, notify, realtime, scorecard

    os.makedirs(args.data_dir, exist_ok=True)
    buys_path = os.path.join(args.data_dir, "insider_buys.csv")
    alerted_path = os.path.join(args.data_dir, "alerted.csv")
    stakes_path = os.path.join(args.data_dir, "stakes.csv")
    log_path = os.path.join(args.data_dir, "alert_log.csv")
    if args.notify and not notify.configured():
        print("NTFY_TOPIC is not set: alerts will be written but not pushed to a phone", file=sys.stderr)
    issue_no = 0
    while True:
        started = time.monotonic()
        today = _date.today()
        state = realtime.WatchState.load(args.state)
        buys = alerts.load_buys(buys_path)
        alerted = alerts.load_alerted(alerted_path)
        stakes = realtime.load_stakes(stakes_path)
        log = scorecard.ensure_log(log_path, alerted, buys) if not args.dry_run else scorecard.load_log(log_path)
        since = (today - timedelta(days=90)).isoformat()
        watch_ciks = {r.issuer_cik for r in log if r.alerted >= since}
        res = realtime.poll(state, buys, alerted, now=datetime.now(timezone.utc), watch_ciks=watch_ciks,
                            close_fn=realtime.market_close, log=lambda m: print(m, file=sys.stderr, flush=True))
        print("\n".join(realtime.summary_lines(res)), flush=True)
        checks = {c.issuer_cik: dilution.check(c.issuer_cik, today) for c in res.clusters}
        if args.issues_dir:
            for c in res.clusters:
                _write_issue(args.issues_dir, issue_no, "insider-alert", alerts.issue_title(c),
                             alerts.issue_body(c, dilution.issue_section(checks[c.issuer_cik])))
                issue_no += 1
            for s in res.tracked_stakes:
                _write_issue(args.issues_dir, issue_no, "stake-alert", realtime.stake_title(s), realtime.stake_body(s))
                issue_no += 1
        if args.notify:
            for c in res.clusters:
                _notify_cluster(c, checks[c.issuer_cik])
            for b in res.big:
                title, body, url = realtime.big_buy_message(b)
                d = dilution.check(b.issuer_cik, today)
                if d.flagged:
                    body += f" {dilution.short_label(d)}: {d.notes[0]}."
                notify.send(notify.Message(title=title, body=body, url=url, priority=3, tags=("moneybag",)))
            for s in res.tracked_stakes:
                notify.send(notify.Message(title=realtime.stake_title(s), body=f"{s.filer_name} filed {s.form} on "
                                           f"{s.subject_name}.", url=s.url, priority=4, tags=("rotating_light",)))
            for e in res.dilution:
                notify.send(notify.Message(
                    title=f"Offering filing: {e.name} ({e.form})",
                    body=f"{e.name}, which alerted in the last 90 days, filed a {e.form}: it may be selling shares.",
                    url=e.link, priority=3, tags=("warning",)))
        if not args.dry_run:
            keep = [s for s in res.stakes if s.tracked or realtime.is_initial_13d(s.form)]
            log += [scorecard.from_cluster(c, today) for c in res.clusters]
            log += [scorecard.from_big(b, today) for b in res.big]
            log += [scorecard.from_stake(s, today, scorecard.ticker_for_cik(s.subject_cik)) for s in res.tracked_stakes]
            alerts.save_buys(buys, buys_path, today)
            alerts.save_alerted(alerted, alerted_path)
            realtime.save_stakes(stakes + keep, stakes_path, today)
            scorecard.save_log(log, log_path)
            state.save(args.state)
        if not args.loop:
            return 0
        realtime.sleep_until_next(args.loop, started)


def cmd_scorecard(args) -> int:
    import os

    from . import scorecard

    records = scorecard.load_log(os.path.join(args.data_dir, "alert_log.csv"))
    if not records:
        print("No alerts logged yet.")
        return 0
    result = scorecard.evaluate(records, scorecard.history_closes)
    if result.get("error"):
        print(result["error"], file=sys.stderr)
        return 1
    for kind, k in result["by_kind"].items():
        print(f"{k['label']} ({k['alerts']} alerts): {k['verdict']}")
        for h in k["horizons"]:
            if h["n"]:
                print(f"  {h['horizon_days']:>3}d: n={h['n']:<3} mean vs SPY {h['mean_excess']:+.1%}  "
                      f"median {h['median_excess']:+.1%}  beat SPY {h['hit_rate']:.0%}")
    print("\nLatest alerts (return since entry, vs SPY):")
    for r in result["alerts"][:25]:
        if r.get("to_date") is None:
            print(f"  {r['alerted']}  {r['kind']:<7} {r['symbol']:<6} {r['status']}")
        else:
            ex = r["to_date_excess"]
            print(f"  {r['alerted']}  {r['kind']:<7} {r['symbol']:<6} {r['to_date']:+7.1%}  "
                  f"vs SPY {'' if ex is None else f'{ex:+.1%}'}  ({r['days_held']} trading days)")
    for e in result["errors"]:
        print(f"  no price: {e}", file=sys.stderr)
    return 0


def cmd_dilution(args) -> int:
    from datetime import date as _date

    from . import dilution
    from .providers import sec

    cik = sec.ticker_map().cik_for(args.symbol)
    if not cik:
        print(f"{args.symbol}: not in the SEC ticker list", file=sys.stderr)
        return 1
    d = dilution.check(cik, _date.today())
    if d.error:
        print(f"{args.symbol}: {d.error}", file=sys.stderr)
        return 1
    print(f"{args.symbol.upper()}: {dilution.short_label(d) or 'no dilution filings on record'}")
    for n in d.notes:
        print(f"  - {n}")
    for r in d.latest[:5]:
        print(f"    {r['filed']}  {r['form']:<7} {r['url']}")
    return 0


def cmd_site(args) -> int:
    from . import site

    data = site.build(args.out, args.journal, args.alerts_dir)
    quoted = sum(1 for r in data["universe"] if "price" in r)
    scored = sum(1 for r in data["universe"] if r.get("score") is not None)
    print(f"Built {args.out}: {quoted}/{len(data['universe'])} quotes, {scored} scores, "
          f"{len(data['clusters'])} insider clusters")
    for r in data["universe"]:
        if "quote_error" in r:
            print(f"  quote failed: {r['symbol']}: {r['quote_error']}", file=sys.stderr)
    return 0


def cmd_serve(args) -> int:
    import threading
    import uvicorn
    import webbrowser
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"Plumbline is running at {url}  (Ctrl+C to stop)")
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("market_tracker.api:app", host=args.host, port=args.port, reload=False, log_level="warning")
    return 0


SERVICE_LABEL = "com.plumbline.app"


def cmd_service(args) -> int:
    """Keep the dashboard running whenever you're logged in (macOS, Linux or Windows)."""
    import platform
    import shutil
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mt = shutil.which("mt") or os.path.join(os.path.dirname(sys.executable), "mt")
    system = platform.system()
    if system == "Darwin":
        plist = os.path.expanduser(f"~/Library/LaunchAgents/{SERVICE_LABEL}.plist")
        if args.action == "install":
            os.makedirs(os.path.dirname(plist), exist_ok=True)
            with open(plist, "w") as fh:
                fh.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{SERVICE_LABEL}</string>
  <key>ProgramArguments</key><array><string>{mt}</string><string>serve</string><string>--port</string><string>{args.port}</string></array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{root}/plumbline.log</string><key>StandardErrorPath</key><string>{root}/plumbline.log</string>
</dict></plist>
""")
            subprocess.run(["launchctl", "unload", plist], capture_output=True)
            subprocess.run(["launchctl", "load", plist], check=True)
        elif args.action == "uninstall":
            subprocess.run(["launchctl", "unload", plist], capture_output=True)
            if os.path.exists(plist):
                os.remove(plist)
        else:
            print(subprocess.run(["launchctl", "list", SERVICE_LABEL], capture_output=True, text=True).stdout or "not installed")
    elif system == "Linux":
        unit = os.path.expanduser("~/.config/systemd/user/plumbline.service")
        if args.action == "install":
            os.makedirs(os.path.dirname(unit), exist_ok=True)
            with open(unit, "w") as fh:
                fh.write(f"[Unit]\nDescription=Plumbline\n\n[Service]\nWorkingDirectory={root}\n"
                         f"ExecStart={mt} serve --port {args.port}\nRestart=always\n\n[Install]\nWantedBy=default.target\n")
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "--user", "enable", "--now", "plumbline"], check=True)
        elif args.action == "uninstall":
            subprocess.run(["systemctl", "--user", "disable", "--now", "plumbline"], capture_output=True)
            if os.path.exists(unit):
                os.remove(unit)
        else:
            print(subprocess.run(["systemctl", "--user", "status", "plumbline", "--no-pager"], capture_output=True, text=True).stdout)
    elif system == "Windows":
        if args.action == "install":
            cmd = f'cmd /c cd /d "{root}" && "{mt}" serve --port {args.port}'
            subprocess.run(["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/TN", "Plumbline", "/TR", cmd], check=True)
            subprocess.run(["schtasks", "/Run", "/TN", "Plumbline"], check=True)
        elif args.action == "uninstall":
            subprocess.run(["schtasks", "/Delete", "/F", "/TN", "Plumbline"], capture_output=True)
        else:
            print(subprocess.run(["schtasks", "/Query", "/TN", "Plumbline"], capture_output=True, text=True).stdout or "not installed")
    else:
        print(f"Not supported on {system}")
        return 1
    if args.action == "install":
        print(f"Plumbline now starts when you log in: http://localhost:{args.port} (logs: plumbline.log).")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mt", description="Market tracker")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("quote", help="Live quotes")
    s.add_argument("symbols", nargs="+")
    s.set_defaults(func=cmd_quote)

    s = sub.add_parser("analyze", help="Indicators, forecast range, signal, backtest")
    s.add_argument("symbol")
    s.add_argument("--fast", action="store_true", help="Skip SEC 13F and insider lookups")
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("investors", help="List tracked investors or show one's latest 13F")
    s.add_argument("key", nargs="?")
    s.add_argument("--verify", action="store_true", help="Check every CIK against EDGAR's filer name")
    s.set_defaults(func=cmd_investors)

    s = sub.add_parser("consensus", help="What tracked investors bought/sold last quarter")
    s.add_argument("--limit", type=int, default=15)
    s.set_defaults(func=cmd_consensus)

    s = sub.add_parser("news", help="Headlines + sentiment (market-wide if no symbol)")
    s.add_argument("symbol", nargs="?")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_news)

    s = sub.add_parser("portfolio", help="Show portfolio or record a transaction")
    s.add_argument("action", nargs="?", choices=["show", "add"], default="show")
    s.add_argument("--symbol")
    s.add_argument("--side", choices=["buy", "sell"], default="buy")
    s.add_argument("--quantity", type=float)
    s.add_argument("--price", type=float)
    s.add_argument("--fees", type=float, default=0.0)
    s.add_argument("--date")
    s.set_defaults(func=cmd_portfolio)

    s = sub.add_parser("research", help="Claude deep-dive research memo")
    s.add_argument("symbol")
    s.add_argument("--question", "-q")
    s.set_defaults(func=cmd_research)

    s = sub.add_parser("journal", help="Log daily scores and measure them against real outcomes")
    s.add_argument("action", choices=["record", "report", "sync"])
    s.add_argument("symbols", nargs="*")
    s.add_argument("--default-universe", action="store_true", help="Record the built-in 15-symbol universe")
    s.add_argument("--no-sec", dest="sec", action="store_false",
                   help="Skip 13F/insider components (faster, but rows won't match the daily job's)")
    s.add_argument("--summary-file", help="Also append the Markdown report to this file")
    s.set_defaults(func=cmd_journal)

    s = sub.add_parser("score-backtest", help="Replay the score on history using only point-in-time data")
    s.add_argument("--start", default="2016-01-01")
    s.add_argument("--end")
    s.add_argument("--symbols", nargs="*")
    s.add_argument("--investors", nargs="*", help="Investor keys to include (default: all tracked)")
    s.add_argument("--json", help="Write full results and per-stock rows to this file")
    s.add_argument("--summary-file", help="Also append the Markdown report to this file")
    s.set_defaults(func=cmd_score_backtest)

    s = sub.add_parser("alerts", help="Scan every company's Form 4 filings for insider cluster buys")
    s.add_argument("--data-dir", default="alerts_data", help="Where the rolling buys and alert history live")
    s.add_argument("--days", type=int, help="Scan the last N calendar days instead of resuming")
    s.add_argument("--issues-dir", help="Write one title/body pair per new cluster here")
    s.add_argument("--dry-run", action="store_true", help="Don't save state (for testing)")
    s.add_argument("--notify", action="store_true", help="Push new alerts to ntfy (needs NTFY_TOPIC)")
    s.set_defaults(func=cmd_alerts)

    s = sub.add_parser("scorecard", help="How alerts did against SPY at 1, 3 and 6 months")
    s.add_argument("--data-dir", default="alerts_data", help="Folder holding alert_log.csv")
    s.set_defaults(func=cmd_scorecard)

    s = sub.add_parser("dilution", help="Shelf registrations and share sales on file for a ticker")
    s.add_argument("symbol")
    s.set_defaults(func=cmd_dilution)

    s = sub.add_parser("watch", help="Poll EDGAR's latest filings for insider buys and 13D/13G stakes")
    s.add_argument("--data-dir", default="alerts_data", help="Shared with `mt alerts`: buys, alert history, stakes")
    s.add_argument("--state", default="alerts_data/watch_state.json", help="Cursor of filings already handled")
    s.add_argument("--issues-dir", help="Write one title/body pair per alert here")
    s.add_argument("--notify", action="store_true", help="Push alerts to ntfy (needs NTFY_TOPIC)")
    s.add_argument("--dry-run", action="store_true", help="Don't save anything")
    s.add_argument("--loop", type=float, default=0, metavar="SECONDS",
                   help="Keep polling every SECONDS (e.g. 60) instead of running once")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("site", help="Build the static public site (GitHub Pages) into a folder")
    s.add_argument("--out", default="_site")
    s.add_argument("--journal", help="signal_journal.csv to publish scores and the track record from")
    s.add_argument("--alerts-dir", help="Folder holding insider_buys.csv and alerted.csv")
    s.set_defaults(func=cmd_site)

    s = sub.add_parser("serve", help="Run the web dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--open", action="store_true", help="Open the dashboard in your browser")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("service", help="Run the dashboard in the background whenever you're logged in")
    s.add_argument("action", choices=["install", "uninstall", "status"])
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_service)

    args = p.parse_args(argv)
    if args.command == "portfolio" and args.action == "add" and not (args.symbol and args.quantity and args.price is not None):
        p.error("portfolio add requires --symbol, --quantity and --price")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
