"""Sleepers and water-cooler chatter: the speculative end, with guard rails.

Sleepers: small and mid-sized companies ($300M to $10B) that the crowd isn't watching yet, where
several independent pieces of evidence line up:
- a good grade on the weekly quality, value and momentum screen, with no fatal flaw;
- insiders buying with their own money outside their usual habit (opportunistic buying);
- contracted backlog growing faster than revenue;
- little attention: among the quietest third of this week's candidates by headline count (a quiet name
  counts as one more piece of evidence; absolute counts mean little, since news aggregators write
  something about almost every listed company every day);
- not already a rocket: 12-month return below the top 10% and less than 30% above the 200-day average.
One piece of evidence is a lead; three is a sleeper. Smaller companies are where these signals
have historically been strongest, and also where they fail hardest.

Chatter: what Reddit and StockTwits are talking about most, and how fast that's rising. This is
shown so you know what everyone else is hearing, not as a buy list. Stocks that grab attention
tend to be bought at the top by individual investors and then lag (Barber & Odean 2008); the
screen's warnings are shown next to each name.

Both lists are logged to the idea log and scored against VOO, so after six months you'll see
whether either one is worth money. Keep what you put into them small: the app suggests a
speculative bucket of at most 10% of the portfolio, and shows how much of it is used.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from . import http

SPECULATIVE_CAP = 0.10
QUIET_SHARE = 1 / 3
FUND_WORDS = ("ETF", " FUND", "TRUST", "PROSHARES", "ISHARES", "DIREXION", "SPDR", "INVESCO QQQ", "TREASURY", "ULTRA", "2X", "3X")
APEWISDOM = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1"


def sleepers(screen_data: dict | None, insider_cands: dict[str, dict], lookup_fn, news_fn, insider_fn=None, limit: int = 15) -> list[dict]:
    """screen_data: weekly screen; insider_cands: {symbol: {cik, company, value, reasons, insider?, trade_date?}}."""
    backlog = {b["symbol"]: b for b in (screen_data or {}).get("backlog", [])}
    pool = {r["symbol"]: r for r in (screen_data or {}).get("small_mid", [])}
    for s in list(insider_cands) + list(backlog):
        row = lookup_fn(s)
        if row and row.get("cap") and 3e8 <= row["cap"] < 1e10 and s not in pool:
            pool[s] = dict(symbol=s, score=row.get("score"), cap=row["cap"], sector=row.get("sector"), return_12m_pct=row.get("return_12m_pct"),
                           above_200d=row.get("above_200d"), name=(insider_cands.get(s) or {}).get("company", ""), flaws=[])

    def evidence(s):
        r = pool[s]
        ev, why = 0, []
        if (r.get("score") or 0) >= 70 and not r.get("flaws"):
            ev += 1
            why.append(f"screen grade {r['score']:.0f}/100 (quality, value, momentum, no dilution)")
        ins = insider_cands.get(s)
        if ins:
            kind = insider_fn(ins) if insider_fn else None
            if kind is None or kind.get("kind") != "routine":
                ev += 1
                why.append(ins["reasons"][0] + (" (not a yearly habit)" if kind and kind.get("kind") == "opportunistic" else ""))
        if s in backlog:
            ev += 1
            why.append(backlog[s]["why"])
        hot = (r.get("return_12m_pct") or 0) >= 90 or (r.get("above_200d") or 0) >= 0.3
        return (None if hot or ev == 0 else (ev, why))

    def headlines(s):
        try:
            return news_fn(s, pool[s].get("name") or None)
        except Exception:  # noqa: BLE001 - attention unknown
            return None

    cands = sorted(pool, key=lambda s: (-(s in insider_cands) - (s in backlog), -(pool[s].get("score") or 0)))[:60]
    with ThreadPoolExecutor(max_workers=6) as ex:
        ev = dict(zip(cands, ex.map(evidence, cands)))
        live = [s for s in cands if ev[s]]
        counts = dict(zip(live, ex.map(headlines, live)))
    known = sorted(v for v in counts.values() if v is not None)
    quiet_line = known[max(0, int(len(known) * QUIET_SHARE) - 1)] if len(known) >= 6 else None
    found = []
    for s in live:
        n, why = ev[s]
        why = list(why)
        c = counts.get(s)
        if c is not None and quiet_line is not None and c <= quiet_line:
            n += 1
            why.append(f"quiet: {c} headlines this week, among the quietest third of these companies")
        elif c is not None:
            why.append(f"{c} headlines this week")
        r = pool[s]
        found.append(dict(symbol=s, name=r.get("name", ""), sector=r.get("sector"), cap=r.get("cap"), score=r.get("score"), evidence=n,
                          level="sleeper" if n >= 3 else "strong lead" if n == 2 else "lead", why=why))
    return sorted(found, key=lambda x: (-x["evidence"], -(x["score"] or 0)))[:limit]


def chatter(ape: dict, trending: list[dict], lookup_fn, limit: int = 20) -> list[dict]:
    """ape: ApeWisdom's all-stocks page; trending: StockTwits trending [{symbol, title?, summary?}]."""
    rows: dict[str, dict] = {}
    for r in ape.get("results", []):
        t = (r.get("ticker") or "").upper()
        name = (r.get("name") or "").upper()
        if not t or t in ("SPY", "QQQ", "VOO", "IWM", "DIA") or any(w in f" {name}" for w in FUND_WORDS):
            continue
        now, before = int(r.get("mentions") or 0), int(r.get("mentions_24h_ago") or 0)
        rows[t] = {"symbol": t, "name": r.get("name", ""), "reddit": now, "reddit_24h_ago": before,
                   "rising": round(now / before, 1) if before else None, "stocktwits": False, "reason": ""}
    for tr in trending:
        t = tr["symbol"].upper()
        e = rows.setdefault(t, {"symbol": t, "name": tr.get("title", ""), "reddit": 0, "reddit_24h_ago": 0, "rising": None, "reason": ""})
        e["stocktwits"] = True
        e["reason"] = tr.get("summary") or e["reason"]
    out = []
    for t, e in rows.items():
        row = lookup_fn(t) or {}
        warn = []
        if row.get("return_12m_pct") is not None and row["return_12m_pct"] >= 90:
            warn.append("already in the top 10% of 12-month returns")
        if row.get("above_200d") is not None and row["above_200d"] >= 0.3:
            warn.append(f"{row['above_200d']:.0%} above its 200-day average")
        if row.get("cap") is not None and row["cap"] < 1e9:
            warn.append("under $1B: easy to push around")
        if not row:
            warn.append("not on the screen (too small, unprofitable or no SEC numbers)")
        if row.get("score") is not None and row["score"] < 40:
            warn.append(f"weak screen grade ({row['score']:.0f}/100)")
        heat = e["reddit"] * (1 + min(e["rising"] or 1, 5)) + (50 if e["stocktwits"] else 0)
        out.append(dict(e, score=row.get("score"), cap=row.get("cap"), warnings=warn, heat=round(heat),
                        caution="high" if len(warn) >= 2 else "medium" if warn else "low"))
    return sorted(out, key=lambda x: -x["heat"])[:limit]


def load_ape(get=None) -> dict:
    return (get or (lambda: http.get(APEWISDOM, ttl=900)))()


def bucket(positions: list[dict], speculative: set[str]) -> dict:
    """How much of the portfolio sits in names that came from the sleepers or chatter lists."""
    total = sum(p.get("market_value") or 0 for p in positions)
    used = sum(p.get("market_value") or 0 for p in positions if p["symbol"] in speculative)
    cap = total * SPECULATIVE_CAP
    return {"total": round(total, 2), "used": round(used, 2), "cap": round(cap, 2), "room": round(max(cap - used, 0), 2),
            "share": round(used / total * 100, 1) if total else 0.0, "over": used > cap,
            "text": (f"Speculative picks: ${used:,.0f} of a ${cap:,.0f} limit (10% of your portfolio)." if total else
                     "Speculative picks: keep them under 10% of your portfolio.")}
