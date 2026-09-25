"""Crypto radar for the coins you hold.

- Hacks (DefiLlama's hack list, last 14 days): exchange hacks of $10M+ matter to everyone
  (they shake confidence and sometimes force selling); hacks on a chain you hold matter to
  that coin.
- Coinbase incidents (its status page): delayed sends or receives, or trading issues, on a
  coin you hold, and site-wide outages.
- Supply not yet circulating (CoinGecko): the share of a coin's total supply still locked or
  unissued. Future unlocks add supply to the market; a large overhang is a known drag.
  Exact unlock dates are behind a paid API, so this reports the overhang, not the calendar.
- Stablecoins off their $1 peg (shared with the early wire).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import early, http
from .providers import market
from .reading import BROWSER_UA

HACKS = "https://api.llama.fi/hacks"
CB_STATUS = "https://status.coinbase.com/api/v2/incidents/unresolved.json"
CG_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"
HEADERS = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
HACK_DAYS = 14
BIG_CEX_HACK = 10_000_000
OVERHANG = 0.30          # flag when more than 30% of supply isn't circulating yet

CHAINS = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP", "TRX": "Tron", "BNB": "BSC",
          "AVAX": "Avalanche", "POL": "Polygon", "MATIC": "Polygon", "ADA": "Cardano", "DOT": "Polkadot",
          "ATOM": "Cosmos", "SUI": "Sui", "APT": "Aptos", "NEAR": "Near", "ARB": "Arbitrum", "OP": "Optimism",
          "ZEC": "Zcash", "DOGE": "Dogecoin", "LTC": "Litecoin", "TON": "TON", "HYPE": "Hyperliquid L1",
          "SEI": "Sei", "INJ": "Injective", "TIA": "Celestia", "NTRN": "Neutron", "EGLD": "MultiversX",
          "HBAR": "Hedera", "ALGO": "Algorand", "XLM": "Stellar", "BCH": "Bitcoin Cash", "ETC": "Ethereum Classic"}
COINGECKO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple", "DOGE": "dogecoin",
                 "ADA": "cardano", "AVAX": "avalanche-2", "DOT": "polkadot", "LINK": "chainlink", "LTC": "litecoin",
                 "BCH": "bitcoin-cash", "XLM": "stellar", "ATOM": "cosmos", "UNI": "uniswap", "AAVE": "aave",
                 "SUI": "sui", "APT": "aptos", "NEAR": "near", "ARB": "arbitrum", "OP": "optimism", "SHIB": "shiba-inu",
                 "PEPE": "pepe", "HBAR": "hedera-hashgraph", "ALGO": "algorand", "TRX": "tron", "TON": "the-open-network",
                 "ONDO": "ondo-finance", "HYPE": "hyperliquid", "SEI": "sei-network", "INJ": "injective-protocol",
                 "TIA": "celestia", "RENDER": "render-token", "FET": "fetch-ai", "POL": "polygon-ecosystem-token",
                 "ZEC": "zcash", "ETC": "ethereum-classic", "EGLD": "elrond-erd-2", "BONK": "bonk", "WIF": "dogwifcoin",
                 "JUP": "jupiter-exchange-solana", "ENA": "ethena", "XTZ": "tezos", "FIL": "filecoin", "ICP": "internet-computer"}


def coin(symbol: str) -> str:
    return market.normalize_symbol(symbol).split("-")[0].upper()


def hack_alerts(hacks: list[dict], coins: set[str], now: datetime, days: int = HACK_DAYS) -> list[dict]:
    cutoff = (now - timedelta(days=days)).timestamp()
    chains = {CHAINS[c]: c for c in coins if c in CHAINS}
    out = []
    for h in sorted(hacks or [], key=lambda h: -(h.get("date") or 0)):
        if (h.get("date") or 0) < cutoff:
            continue
        amount = h.get("amount") or 0
        day = datetime.fromtimestamp(h["date"], timezone.utc).date().isoformat()
        name, how = h.get("name", "?"), h.get("technique") or h.get("classification") or "hack"
        hit = sorted({chains[c] for c in (h.get("chain") or []) if c in chains})
        is_cb = "coinbase" in name.lower()
        if is_cb:
            out.append({"level": 3, "kind": "hack", "coins": sorted(coins), "date": day,
                        "text": f"Coinbase itself was hacked ({how}, ${amount:,.0f})", "url": "https://defillama.com/hacks"})
        elif h.get("targetType") == "CEX" and amount >= BIG_CEX_HACK:
            out.append({"level": 2, "kind": "hack", "coins": hit, "date": day,
                        "text": f"{name} (an exchange) lost ${amount / 1e6:,.0f}M ({how}) on {', '.join(h.get('chain') or [])}",
                        "url": "https://defillama.com/hacks"})
        elif hit and amount >= 1_000_000:
            out.append({"level": 1, "kind": "hack", "coins": hit, "date": day,
                        "text": f"{name} on {', '.join(hit)}'s chain lost ${amount / 1e6:,.1f}M ({how})",
                        "url": "https://defillama.com/hacks"})
    return out


def coinbase_alerts(status: dict, coins: set[str]) -> list[dict]:
    out = []
    for inc in (status or {}).get("incidents") or []:
        name = inc.get("name") or ""
        hit = sorted(c for c in coins if f"({c})" in name.upper() or f" {c} " in f" {name.upper()} ")
        impact = inc.get("impact") or "none"
        day = (inc.get("started_at") or inc.get("created_at") or "")[:10]
        if hit:
            out.append({"level": 2, "kind": "exchange", "coins": hit, "date": day,
                        "text": f"Coinbase: {name} ({inc.get('status')})", "url": inc.get("shortlink") or "https://status.coinbase.com"})
        elif impact in ("major", "critical"):
            out.append({"level": 2, "kind": "exchange", "coins": [], "date": day,
                        "text": f"Coinbase {impact} incident: {name}", "url": inc.get("shortlink") or "https://status.coinbase.com"})
    return out


def supply_alerts(markets: list[dict], coins: set[str], overhang: float = OVERHANG) -> tuple[list[dict], dict[str, dict]]:
    ids = {v: k for k, v in COINGECKO_IDS.items()}
    out, table = [], {}
    for m in markets or []:
        c = ids.get(m.get("id")) or (m.get("symbol") or "").upper()
        if c not in coins:
            continue
        circ = m.get("circulating_supply") or 0
        total = m.get("max_supply") or m.get("total_supply") or 0
        if not circ or not total:
            continue
        locked = max(0.0, 1 - circ / total)
        table[c] = {"circulating_pct": round(circ / total * 100, 1), "fdv": m.get("fully_diluted_valuation"),
                    "market_cap": m.get("market_cap")}
        if locked >= overhang and c != "BTC":
            out.append({"level": 1, "kind": "supply", "coins": [c], "date": "",
                        "text": f"{c}: {locked:.0%} of its supply isn't circulating yet; unlocks and emissions add supply over time",
                        "url": f"https://www.coingecko.com/en/coins/{m.get('id')}"})
    return out, table


def build(symbols: list[str], now: datetime | None = None, get=None, quote_fn=None) -> dict:
    now = now or datetime.now(timezone.utc)
    get = get or http.get
    coins = {coin(s) for s in symbols if market.asset_class(s) == "crypto"}
    alerts: list[dict] = []
    errors: list[str] = []
    try:
        alerts += hack_alerts(get(HACKS, headers=HEADERS, ttl=1800), coins, now)
    except (http.DataUnavailable, KeyError, TypeError) as exc:
        errors.append(f"DefiLlama hacks: {str(exc)[:100]}")
    try:
        alerts += coinbase_alerts(get(CB_STATUS, headers=HEADERS, ttl=300), coins)
    except (http.DataUnavailable, KeyError, TypeError) as exc:
        errors.append(f"Coinbase status: {str(exc)[:100]}")
    supply: dict[str, dict] = {}
    ids = [COINGECKO_IDS[c] for c in sorted(coins) if c in COINGECKO_IDS]
    if ids:
        try:
            got, supply = supply_alerts(get(CG_MARKETS, params={"vs_currency": "usd", "ids": ",".join(ids)},
                                            headers=HEADERS, ttl=6 * 3600), coins)
            alerts += got
        except (http.DataUnavailable, KeyError, TypeError) as exc:
            errors.append(f"CoinGecko supply: {str(exc)[:100]}")
    try:
        for s in early.depeg_signals(quote_fn or market.get_quote):
            alerts.append({"level": 3, "kind": "depeg", "coins": [coin(s.symbol)], "date": now.date().isoformat(),
                           "text": s.headline, "url": s.url})
    except (http.DataUnavailable, KeyError, ValueError) as exc:
        errors.append(f"stablecoins: {str(exc)[:100]}")
    alerts.sort(key=lambda a: a["date"], reverse=True)
    alerts.sort(key=lambda a: -a["level"])          # most serious first, newest first within a level
    return {"generated_at": now.isoformat(timespec="seconds"), "coins": sorted(coins), "alerts": alerts,
            "supply": supply, "errors": errors}
