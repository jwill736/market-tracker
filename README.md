# Market Tracker

Portfolio tracker and research tool for stocks and crypto. It combines live prices, what well-known investors
(Buffett, Ackman, Druckenmiller, Burry, and others) disclosed in their SEC filings, insider trades, news sentiment,
probabilistic price ranges, and Claude-powered deep-dive research.

> **Read this first.** No part of this tool predicts prices, and none of it can promise "highest ROI". The app is
> built to help you make *better-informed* decisions and size positions sensibly. Every signal comes with its
> limitations and a way to check it (backtests, coverage, data age). Not financial advice.

## What it does

| Area | What you get | Source |
|---|---|---|
| **Live quotes** | Streaming watchlist (every 5s over SSE) | Crypto: Coinbase Exchange (real-time spot). Stocks: Yahoo Finance chart API, or Finnhub if `FINNHUB_API_KEY` is set |
| **Smart money** | Latest 13F holdings for 17 tracked investors, quarter-over-quarter moves (new/added/reduced/exited), and a consensus of what they collectively bought and sold | SEC EDGAR 13F-HR |
| **Insiders** | Open-market buys and sells by officers and directors, with cluster-buy detection. 10b5-1 planned sales are flagged | SEC EDGAR Form 4 |
| **News** | Headlines, lexicon sentiment, trending terms. Per symbol and market-wide | Google News RSS, Yahoo Finance RSS, Finnhub (optional) |
| **Analytics** | SMA 20/50/200, RSI, MACD, momentum, EWMA volatility, drawdown | computed locally |
| **Forecast ranges** | 5/21/63-day (crypto: 7/30/90-day) price ranges from a lognormal model and a block bootstrap, plus P(up) | computed locally |
| **Backtest** | Buy & hold vs. trend (200-day) vs. 12-1 momentum on the same history, after trading costs | computed locally |
| **Composite signal** | A score from -100 to +100 built from trend, momentum, smart money, insiders, and news, with a per-component explanation and a volatility-based maximum position size | computed locally |
| **Portfolio** | Trade ledger, average-cost P&L (realized and unrealized), allocation, volatility, Sharpe, max drawdown, VaR, concentration and correlation warnings, risk-balanced target weights | SQLite |
| **Track record** | Logs each day's scores to a CSV, then measures them against what prices did 1, 3 and 6 months later. Reports the IC (rank correlation) with its error band, average return by label, and a per-component IC. A scheduled GitHub Actions job records 15 symbols every weekday | computed locally + GitHub Actions |
| **Deep dive** | Streaming research memo. Claude takes the quantitative snapshot, then uses web search and fetch to read current primary sources. The memo ends in a structured verdict: rating, catalysts, risks, what would invalidate the thesis, and max position | Claude API (`claude-opus-5`) |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # set SEC_USER_AGENT (required by SEC) and ANTHROPIC_API_KEY (for deep dives)
mt serve                    # http://127.0.0.1:8000
```

CLI equivalents:

```bash
mt quote AAPL BTC ETH
mt analyze NVDA             # indicators, signal, ranges, backtest (+13F/insiders; --fast skips SEC)
mt investors                # list tracked investors
mt investors buffett        # Berkshire's latest 13F and quarter-over-quarter moves
mt consensus                # what tracked investors bought/sold last quarter
mt news TSLA                # or `mt news` for market-wide
mt portfolio add --symbol BTC --quantity 0.25 --price 64000 --date 2026-09-01
mt portfolio
mt research NVDA -q "Is the data-center growth already priced in?"
mt journal record           # log today's scores for your watchlist
mt journal sync             # pull the journal that GitHub Actions records daily
mt journal report           # has the score predicted anything yet?
mt investors --verify       # check every investor CIK against EDGAR
```

## How to read the signals (and their limits)

**13F "billionaire trades" are old news.**
- Funds file 13F reports up to 45 days after the quarter ends, so a position can be up to ~135 days old when you see it.
- 13Fs show US-listed long positions only. Shorts, cash, bonds, and most non-US holdings are missing, so a "new position" might be a hedge.
- The app shows the data's age on every view.
- Concentrated, low-turnover investors count at full weight (1.0). Quant and multi-strategy books (Renaissance, Citadel) count at 0.35 because their 13Fs are mostly noise.
- ARK publishes its trades daily, which is fresher than its 13F.

**Insider buying is the freshest "smart money" signal.**
- Form 4 filings are due within 2 business days of the trade.
- Research on insider trading consistently finds that open-market *purchases*, especially by several insiders at once, carry information. Sales carry much less.
- For that reason, the model counts a cluster buy as fully bullish, while selling alone only counts as mildly bearish.

**Forecasts are ranges, not targets.**
- Volatility can be estimated reasonably well. Direction mostly can't.
- The price-drift estimate is shrunk 75% toward zero because historical average returns are mostly noise.
- Real price tails are fatter than either model assumes.

**The composite score is a ranking aid.**
- Its weights (25/25/20/15/15) are judgment calls, not fitted values.
- Missing components are dropped, and `coverage` shows how much of the model actually contributed.
- Check the backtest before trusting any component for a given asset. If a rule doesn't beat buy-and-hold on that asset's own history, it isn't adding value there.

**Trust the track record, not the score.** The Track record tab is the only place that measures whether the score
works. Two traps it guards against:
- **A rising market makes every label look good.** When prices go up overall, even "Strong bearish" shows positive average
  returns. Compare labels against each other, or use the IC; never judge a label by its own average.
- **Daily entries overlap.** 700 rows of 21-day outcomes can be only ~40 independent observations. The verdict only claims
  an edge when the IC is at least twice its error band, and says "too early" below ~50 independent observations.

**Position sizing matters more than picking.** `suggested_max_weight` caps a position so that a typical (1-sigma) monthly
move costs no more than 2% of the portfolio. A 25%-volatility stock can be 20% of the portfolio. A 100%-volatility token
should be about 7%.

## Configuration

| Variable | Purpose |
|---|---|
| `SEC_USER_AGENT` | **Required by SEC**: `"your-app your@email.com"`. Requests are throttled below SEC's 10 req/s limit |
| `ANTHROPIC_API_KEY` | Deep-dive research (or any credential source the Anthropic SDK resolves) |
| `MT_RESEARCH_MODEL` | Default `claude-opus-5`. Requests opt into server-side refusal fallbacks (`fallbacks="default"`) |
| `FINNHUB_API_KEY` | Optional: real-time US stock quotes and company news |
| `MT_DB_PATH` | SQLite file (default `market_tracker.db`) |

## Architecture

```
market_tracker/
  providers/market.py   quotes + daily history (Coinbase, Yahoo, Finnhub)
  providers/sec.py      13F parsing and diffs, consensus, Form 4 parsing, insider summary
  providers/news.py     RSS/Finnhub headlines, lexicon sentiment, trending terms
  investors.py          tracked investors (CIKs are checked against EDGAR's filer name at runtime)
  analytics/            indicators, forecast, backtest, signals, portfolio risk
  service.py            assembles a full per-symbol analysis; portfolio valuation
  research.py           Claude deep dive (streaming, web search/fetch, pause_turn resume, structured verdict)
  api.py                FastAPI + SSE streams; serves the dashboard
  cli.py                `mt` command
  static/               dashboard (plain HTML/JS/SVG, no build step)
```

## GitHub Actions

| Workflow | When | What |
|---|---|---|
| `tests.yml` | every PR and push to `main` | `pytest` on Python 3.11 and 3.12 (required to merge) |
| `live-smoke.yml` | every PR, daily, on demand | calls every real data source and reports PASS/FAIL per source in the job summary. Not required to merge, so an upstream outage can't block you |
| `journal.yml` | weekdays after the US close, on demand | records the 15-symbol universe and commits `signal_journal.csv` to the `journal-data` branch; the job summary shows the track record |

Set a repository variable `SEC_USER_AGENT` (Settings → Secrets and variables → Actions → Variables) to
`"market-tracker your@email.com"`. The SEC asks for a contact email and may throttle or block requests without one.

Run the tests with `pytest`. They use recorded fixtures for SEC XML, RSS, and Yahoo JSON, plus a mocked Claude client,
so they need no network access.

## Known gaps / next steps

- **Congressional trades** (STOCK Act disclosures) need a paid feed such as Quiver or FMP. They aren't included yet.
- **Mapping 13F CUSIPs to tickers** matches on company name against SEC's ticker list. A few issuers go unmatched and show only their name.
- **Crypto "smart money"**: 13F doesn't cover crypto directly, so the deep dive covers ETF flows and on-chain data through web research.
- Yahoo's chart endpoint is unofficial and can change. Finnhub is the supported fallback.
