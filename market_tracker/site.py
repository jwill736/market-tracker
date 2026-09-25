"""Build the public Plumbline site: a static page plus one data.json snapshot.

GitHub Actions runs `mt site` on a schedule and publishes the result to GitHub Pages. The
page itself adds live crypto prices in the browser (Coinbase's public WebSocket), so the
snapshot only has to carry what a browser can't fetch on its own: stock quotes, the latest
scores, the track record, and insider clusters.

Nothing private goes in here: no portfolio, no API keys, no SEC contact address.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from . import alerts, http, journal, realtime
from .providers import market

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_static")


def latest_scores(rows: list[dict]) -> dict[str, dict]:
    """The newest current-version journal row per symbol."""
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("version", "1") != journal.SCORE_VERSION:
            continue
        if r["symbol"] not in out or r["date"] > out[r["symbol"]]["date"]:
            out[r["symbol"]] = r
    return out


def universe_rows(symbols: list[str], scores: dict[str, dict],
                  quote_fn: Callable[[str], market.Quote]) -> list[dict]:
    rows = []
    for sym in symbols:
        s = scores.get(sym)
        row = {"symbol": sym, "asset_class": market.asset_class(sym)}
        try:
            q = quote_fn(sym)
            row.update(price=q.price, change_pct=q.change_pct, quote_as_of=q.as_of, quote_source=q.source)
        except http.DataUnavailable as exc:
            row["quote_error"] = str(exc)
        if s:
            row.update(score_date=s["date"], score=s["score"], label=s["label"], coverage=s["coverage"],
                       components={c: s[c] for c in journal.COMPONENTS})
        rows.append(row)
    return rows


def track_record(rows: list[dict], history_fn) -> dict:
    report = journal.evaluate(rows, history_fn)
    return {
        "entries": report["entries"],
        "first_date": report["first_date"],
        "last_date": report["last_date"],
        "verdict": report["verdict"],
        "horizons": [{k: h[k] for k in ("horizon_days", "n", "effective_n", "ic", "ic_se", "component_ic")}
                     for h in report["horizons"]],
    }


def cluster_rows(buys: list[alerts.Buy], alerted: dict[str, str], today: date,
                 is_fund: Callable[[str], bool]) -> list[dict]:
    """Every active cluster except funds and untradable issuers, with why it did or didn't alert."""
    out = []
    for c in alerts.find_clusters(buys, today):
        if c.symbol.strip().upper() in alerts.NO_TICKER or is_fund(c.issuer_cik):
            continue
        reason = alerts.skip_reason(c, alerted, today, lambda cik: False)
        status = {None: "new", "SEEN": "alerted", "1DAY": "one-day", "OLD": "older"}[reason]
        # An alerted cluster stays featured for two weeks after its newest filing, then drops
        # to the compact list like any other older buying.
        if status == "alerted" and c.last_filed < (today - timedelta(days=2 * alerts.ALERT_MAX_AGE_DAYS)).isoformat():
            status = "older"
        out.append({
            "symbol": c.symbol, "company": c.issuer_name, "status": status,
            "insiders": len(c.insiders), "total_value": c.total_value,
            "first_trade": c.first_trade, "last_trade": c.last_trade, "last_filed": c.last_filed,
            "trade_days": c.trade_days,
            "buys": [{"date": b.trade_date, "insider": b.insider, "role": b.role, "value": b.value,
                      "url": f"{alerts.ARCHIVES}edgar/data/{int(b.issuer_cik)}/{b.accession.replace('-', '')}/"}
                     for b in c.buys],
        })
    order = {"new": 0, "alerted": 1, "one-day": 2, "older": 3}
    out.sort(key=lambda r: (order[r["status"]], -r["total_value"]))
    return out


def big_buy_rows(buys: list[alerts.Buy], today: date, is_fund: Callable[[str], bool],
                 days: int = 14, limit: int = 10) -> list[dict]:
    """Single insiders buying $1M+ in one filing over the last `days`, largest first."""
    since = (today - timedelta(days=days)).isoformat()
    out = []
    for b in realtime.big_buys([b for b in buys if b.filed >= since]):
        if b.symbol.strip().upper() in alerts.NO_TICKER or is_fund(b.issuer_cik):
            continue
        out.append({"symbol": b.symbol, "company": b.issuer_name, "insider": b.insider, "role": b.role,
                    "value": b.value, "trade_dates": b.trade_dates,
                    "url": realtime.filing_url(b.issuer_cik, b.accession)})
        if len(out) >= limit:
            break
    return out


def stake_rows(stakes: list[realtime.Stake], today: date, days: int = 30, limit: int = 30) -> list[dict]:
    """New 13D stakes by anyone, and any 13D/13G by a tracked investor, newest first."""
    since = (today - timedelta(days=days)).isoformat()
    keep = [s for s in stakes if s.filed >= since and (s.tracked or realtime.is_initial_13d(s.form))]
    keep.sort(key=lambda s: (s.filed, s.accession), reverse=True)
    return [{"filed": s.filed, "form": s.form, "company": s.subject_name, "filer": s.filer_name,
             "tracked": s.tracked, "url": s.url or realtime.filing_url(s.subject_cik, s.accession)}
            for s in keep[:limit]]


def build_data(journal_rows: list[dict], buys: list[alerts.Buy], alerted: dict[str, str], *,
               today: date, quote_fn=market.get_quote, history_fn=journal.history_closes,
               is_fund=alerts.issuer_is_fund, backtest: dict | None = None,
               now: datetime | None = None, stakes: list[realtime.Stake] | None = None) -> dict:
    return {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "universe": universe_rows(journal.DEFAULT_UNIVERSE, latest_scores(journal_rows), quote_fn),
        "track_record": track_record(journal_rows, history_fn) if journal_rows else None,
        "clusters": cluster_rows(buys, alerted, today, is_fund),
        "big_buys": big_buy_rows(buys, today, is_fund),
        "stakes": stake_rows(stakes or [], today),
        "alert_rules": {
            "min_insiders": alerts.CLUSTER_MIN_INSIDERS, "min_total": alerts.CLUSTER_MIN_TOTAL,
            "min_buy": alerts.MIN_BUY_VALUE, "window_days": alerts.CLUSTER_WINDOW_DAYS,
            "max_age_days": alerts.ALERT_MAX_AGE_DAYS,
        },
        "backtest": backtest,
    }


def build(out_dir: str, journal_file: str | None, alerts_dir: str | None, today: date | None = None,
          **kw) -> dict:
    """Copy the static page into out_dir and write out_dir/data.json."""
    today = today or date.today()
    rows = journal.load(journal_file) if journal_file and os.path.exists(journal_file) else []
    buys, alerted, stakes = [], {}, []
    if alerts_dir:
        buys = alerts.load_buys(os.path.join(alerts_dir, "insider_buys.csv"))
        alerted = alerts.load_alerted(os.path.join(alerts_dir, "alerted.csv"))
        stakes = realtime.load_stakes(os.path.join(alerts_dir, "stakes.csv"))
    backtest_file = os.path.join(STATIC_DIR, "backtest.json")
    backtest = json.load(open(backtest_file, encoding="utf-8")) if os.path.exists(backtest_file) else None
    data = build_data(rows, buys, alerted, today=today, backtest=backtest, stakes=stakes, **kw)
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    shutil.copytree(STATIC_DIR, out_dir)
    with open(os.path.join(out_dir, "data.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    return data
