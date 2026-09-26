"""What your funds really hold. VOO, VTI and QQQ all own Apple, so owning all three plus Apple
shares can leave far more of your money in one company than it looks.

Every US fund reports its full holdings to the SEC each quarter (form N-PORT, public about two
months after the quarter ends). The app finds each fund you own in the SEC's fund list, reads
its latest report, and adds your share of every holding to the stocks you own directly.
Funds that don't file N-PORT (SPY and DIA are unit trusts) use a fund on the same index.
"""

from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET

from . import config, http

MF_TICKERS = "https://www.sec.gov/files/company_tickers_mf.json"
SEARCH = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{file}"
PROXY = {"SPY": "IVV", "DIA": "IVV", "SPLG": "IVV"}      # unit trusts: a fund on the same (or closest) index
CACHE_DAYS = 7


def _sec_headers() -> dict:
    return {"User-Agent": config.settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def _cache_path(sym: str) -> str:
    d = os.path.join(os.path.dirname(os.path.abspath(config.settings.db_path)) or ".", "logo_cache", "funds")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, re.sub(r"[^A-Z0-9]", "_", sym) + ".json")


def fund_series(get=None) -> dict[str, str]:
    """{ticker: series id} for every US mutual fund and ETF share class."""
    get = get or http.get
    data = get(MF_TICKERS, headers=_sec_headers(), ttl=7 * 86400)
    i_sym, i_ser = data["fields"].index("symbol"), data["fields"].index("seriesId")
    return {r[i_sym].upper(): r[i_ser] for r in data["data"] if r[i_ser]}


def latest_filing(series: str, get=None) -> str | None:
    get = get or http.get
    data = get(SEARCH, params={"q": f'"{series}"', "forms": "NPORT-P"}, headers=_sec_headers(), ttl=86400)
    hits = (data.get("hits") or {}).get("hits") or []
    if not hits:
        return None
    h = max(hits, key=lambda h: (h["_source"].get("period_ending") or "", h["_source"].get("file_date") or ""))
    acc, fname = h["_id"].split(":")
    return ARCHIVE.format(cik=int(h["_source"]["ciks"][0]), acc=acc.replace("-", ""), file=fname)


def parse_nport(xml_text: str) -> tuple[str, list[dict]]:
    """(period, [{name, title, cusip, pct, category}]) from an N-PORT primary_doc.xml."""
    root = ET.fromstring(xml_text)
    strip = lambda tag: tag.rsplit("}", 1)[-1]  # noqa: E731
    period, out = "", []
    for el in root.iter():
        if strip(el.tag) == "repPdDate" and el.text:
            period = el.text.strip()
        if strip(el.tag) != "invstOrSec":
            continue
        rec = {strip(c.tag): (c.text or "").strip() for c in el}
        try:
            pct = float(rec.get("pctVal") or 0) / 100
        except ValueError:
            continue
        if pct <= 0:
            continue
        out.append({"name": rec.get("name", ""), "title": rec.get("title", ""), "cusip": rec.get("cusip", ""),
                    "pct": pct, "category": rec.get("assetCat", "")})
    return period, sorted(out, key=lambda r: -r["pct"])


def holdings(sym: str, get=None, now: float | None = None) -> dict | None:
    """{"period", "source", "holdings": [...]} for a fund, or None for anything that isn't one."""
    now = now or time.time()
    path = _cache_path(sym)
    try:
        with open(path) as fh:
            cached = json.load(fh)
        if now - cached.get("fetched", 0) < CACHE_DAYS * 86400:
            return cached.get("data")
    except (OSError, ValueError):
        pass
    use = PROXY.get(sym, sym)
    series = fund_series(get).get(use)
    data = None
    if series:
        url = latest_filing(series, get)
        if url:
            period, rows = parse_nport((get or http.get)(url, headers=_sec_headers(), ttl=86400, as_json=False))
            data = {"period": period, "source": use, "holdings": rows[:300]}
    with open(path, "w") as fh:
        json.dump({"fetched": now, "data": data}, fh)
    return data


def ticker_for(name: str) -> str | None:
    from .providers import sec
    try:
        return sec.ticker_map().ticker_for_issuer(name)
    except (http.DataUnavailable, KeyError, TypeError, ValueError):
        return None


def exposure(positions: list[dict], fund_data: dict[str, dict], resolve=None) -> dict:
    """Your money in each company, direct plus through funds. positions: [{symbol, market_value}]."""
    resolve = resolve or ticker_for
    total = sum(p.get("market_value") or 0 for p in positions)
    by: dict[str, dict] = {}

    def add(key, label, value, via):
        e = by.setdefault(key, {"symbol": key, "name": label, "value": 0.0, "via": {}})
        e["value"] += value
        e["via"][via] = e["via"].get(via, 0.0) + value

    funds = []
    for p in positions:
        v = p.get("market_value") or 0
        fd = fund_data.get(p["symbol"])
        if fd:
            funds.append({"symbol": p["symbol"], "value": v, "period": fd["period"], "source": fd["source"]})
            for h in fd["holdings"]:
                t = resolve(h["name"]) if h["category"] in ("EC", "") else None
                add(t or h["name"], h["name"], v * h["pct"], p["symbol"])
        else:
            add(p["symbol"], p["symbol"], v, "direct")
    rows = sorted(by.values(), key=lambda e: -e["value"])
    for r in rows:
        r["weight"] = round(r["value"] / total, 4) if total else 0.0
        r["value"] = round(r["value"], 2)
        r["via"] = {k: round(v, 2) for k, v in sorted(r["via"].items(), key=lambda kv: -kv[1])}
    return {"total": round(total, 2), "top": rows[:25], "funds": funds}
