from datetime import datetime, timezone

from market_tracker import cryptoradar
from market_tracker.providers import market

NOW = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
DAY = 86400
HACKS = [{"date": NOW.timestamp() - DAY, "name": "Bitget", "amount": 387_000_000, "chain": ["Ethereum", "XRP", "Tron"],
          "targetType": "CEX", "technique": "Key Compromise"},
         {"date": NOW.timestamp() - 2 * DAY, "name": "Drop", "amount": 4_400_000, "chain": ["Neutron"], "targetType": "DeFi Protocol",
          "technique": "Governance"},
         {"date": NOW.timestamp() - 3 * DAY, "name": "SolFarm", "amount": 2_000_000, "chain": ["Solana"], "targetType": "DeFi Protocol",
          "classification": "Protocol Logic"},
         {"date": NOW.timestamp() - 40 * DAY, "name": "OldHack", "amount": 90_000_000, "chain": ["Solana"], "targetType": "CEX"}]
STATUS = {"incidents": [{"name": "Delayed Sends and Receives - SOL (Solana)", "impact": "minor", "status": "investigating",
                         "started_at": "2026-09-25T10:00:00Z", "shortlink": "u"},
                        {"name": "Delayed Sends and Receives - EGLD (MultiversX)", "impact": "minor", "status": "investigating",
                         "started_at": "2026-09-25T10:00:00Z"}]}
MARKETS = [{"id": "sui", "symbol": "sui", "circulating_supply": 4.1e9, "total_supply": 1e10, "max_supply": 1e10},
           {"id": "bitcoin", "symbol": "btc", "circulating_supply": 20.09e6, "max_supply": 21e6},
           {"id": "solana", "symbol": "sol", "circulating_supply": 5.9e8, "total_supply": 6.3e8, "max_supply": None}]


def test_crypto_radar_for_held_coins():
    def get(url, **kw):
        return HACKS if "llama" in url else STATUS if "coinbase" in url else MARKETS

    def quote(sym):
        return market.Quote(sym, "crypto", 0.991 if sym == "USDT-USD" else 1.0, 1.0, 0.0, "USD", "t", "t")
    r = cryptoradar.build(["BTC-USD", "SOL-USD", "SUI-USD", "AAPL"], now=NOW, get=get, quote_fn=quote)
    assert r["coins"] == ["BTC", "SOL", "SUI"]
    texts = [a["text"] for a in r["alerts"]]
    assert r["alerts"][0]["kind"] == "depeg" and "USDT" in texts[0]
    assert any("Bitget" in t and "$387M" in t for t in texts)                 # exchange hack: everyone
    assert any("SolFarm" in t for t in texts) and not any("Drop" in t or "OldHack" in t for t in texts)
    assert any("SOL (Solana)" in t for t in texts) and not any("EGLD" in t for t in texts)
    assert any(t.startswith("SUI: 59%") for t in texts) and not any(t.startswith("BTC:") for t in texts)
    assert r["supply"]["SUI"]["circulating_pct"] == 41.0 and r["errors"] == []
