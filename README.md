# Plumbline

*Measure it true before you trust it.*

Plumbline (repo and Python package `market-tracker`, command `mt`) is a portfolio tracker and research tool for stocks and crypto. It combines live prices, what well-known investors
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
| **Hold plan** | Hold by default; sell/trim/review only on your own tripwires, the company's own quarterly numbers, serious filings, concentration or taxes. Where new money goes, earnings in dollars, cross-account wash-sale guard, a year-end tax planner, and an 8:30 ET morning brief | computed locally + SEC, Nasdaq, Fed |
| **Income** | Dividends received, forward income, yield on cost, upcoming ex/pay dates and the next 12 months | Robinhood CSV / SnapTrade, Yahoo, Nasdaq |
| **Deep dive** | Streaming research memo. Claude takes the quantitative snapshot, then uses web search and fetch to read current primary sources. The memo ends in a structured verdict: rating, catalysts, risks, what would invalidate the thesis, and max position | Claude API (`claude-opus-5`) |

## Always on (alerts while your laptop sleeps)

The app watches filings, the early wire, news and the people you follow only while it runs. Three ways to run it:

| Where | Always on? | Cost | How |
|---|---|---|---|
| A small server | Yes, around the clock | about $4-5 a month on Fly.io | `./deploy_fly.sh` (one command: installs Fly's tool, asks for a password, your email and your ntfy topic, creates the app and a 1 GB disk, deploys) |
| Your computer, in the background | Whenever you're logged in and it's awake | free | `mt service install` (see below) |
| A GitHub Codespace | While the tab is open (stops after 30 idle minutes) | GitHub's free allowance | the button below |

Move your data between them with Portfolio → Backup → Download backup, then Restore a backup on the other one.

**In the background on your computer.** After the first `start.sh` / `start.bat`, from the app's folder:

- Mac: `.venv/bin/mt service install`
- Windows: `.venv\Scripts\mt service install`

It starts at every login with no window to keep open (macOS: a LaunchAgent that also restarts it if it stops; Windows: a
script in your Startup folder, no administrator rights needed; Linux: a systemd user service). The app writes
`plumbline.log` in its folder. Then:

| Command | What it does |
|---|---|
| `mt service status` | Whether it starts at login and whether it's running |
| `mt service update` | Pull the latest version, reinstall, restart |
| `mt service restart` | Restart it (after editing `.env`, for example) |
| `mt service uninstall` | Stop starting it at login (your data stays) |

While it runs, `start.sh` / `start.bat` just open the browser. It only watches while the computer is awake: a laptop
that sleeps misses the 8:30 brief (keep it plugged in with sleep off, or use the server row above).

## Run it in your browser (no install)

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/jwill736/market-tracker?quickstart=1)

A Codespace is this app running on GitHub's machines, opened from your browser. It asks for `SEC_USER_AGENT` (your
email; the SEC requires a contact) and optionally `NTFY_TOPIC`, installs everything, starts the dashboard and opens it
in a new tab. The address is private to your GitHub login. It uses GitHub's free monthly Codespaces allowance
(about 60 hours a month on the smallest machine); it stops after 30 idle minutes and keeps your imported data until
you delete it. Restart it from github.com/codespaces. The background watch (radar, news) runs while it's open.

## Run it on your computer (5 minutes)

Everything runs locally with live data: nothing to deploy, and your portfolio never leaves your machine.

1. Install **Python 3.11+** (python.org/downloads) and **Git**.
2. Get the code: `git clone https://github.com/jwill736/market-tracker.git` (or GitHub → Code → Download ZIP).
3. Start it:
   - **Mac:** open Terminal, `cd` into the folder, run `bash start.sh`
   - **Windows:** double-click `start.bat`

   The first run sets everything up (a minute or two), then asks two questions in that window: your email (the SEC
   asks every user of its filing data for a contact) and whether you want phone alerts (it makes up a private ntfy
   topic and tells you how to subscribe). Your browser then opens at http://localhost:8000. Every later start pulls
   the latest version first. To stop it, press Ctrl+C in that window (or close it). `mt setup` asks the questions again.
4. **Load your accounts** in Portfolio → Import your accounts: Robinhood (account activity CSV), Coinbase
   (transaction history CSV), Stash (type your holdings once: Stash has no export).
5. **Phone alerts (optional):** install the ntfy app, subscribe to a long random topic name, and put the same name in
   `NTFY_TOPIC` in `.env`. Scary filings, loud news, pro mentions of your holdings and hot topics then reach your
   phone while the app runs. With the page open you can also allow desktop alerts (News → Heads-up).

The background watch runs only while the app runs. The GitHub workflows keep running regardless: insider alerts,
the filing watcher and the journal.

Manual setup instead of the scripts:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # set SEC_USER_AGENT (required by SEC) and ANTHROPIC_API_KEY (for deep dives)
mt serve --open             # http://localhost:8000
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

## Phone app

Plumbline installs on a phone like an app (an icon on the home screen that opens full-screen), but phones only install
from a secure `https://` address. Three ways, safest first:

| Way | Where it works | Installable | Setup |
|---|---|---|---|
| **Tailscale** (recommended) | Anywhere, only on your own devices | Yes | Install Tailscale (free) on the computer and the phone, sign in to both with the same account. On the computer: `tailscale serve --bg 8000`. It prints an address like `https://your-mac.tail1234.ts.net`: open it on the phone, then Share → **Add to Home Screen** (iPhone) or ⋮ → **Install app** (Android). Nothing is exposed to the internet |
| **Hosted** (Fly, Render) | Anywhere | Yes | See "Private app" below; it's https already and needs `APP_PASSWORD` |
| **Same Wi-Fi** | At home only | Home-screen shortcut, not a full app | Add `APP_PASSWORD="a long passphrase"` to `.env`, run `mt serve --lan` (or `mt service install --lan`) and open the address it prints on your phone. Plain http: anyone on that Wi-Fi could see the traffic, so it refuses to start without a password |

The app on the phone keeps only its own files (page, script, styles, icons). Your portfolio data is never stored on the
phone: it always comes live from your Plumbline, so if the computer is asleep the app says it can't reach it rather than
showing old numbers. Alerts still come through ntfy.

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
- **Formula changes would blend two different scores.** Every row carries the score version it was computed with
  (`SCORE_VERSION` in `journal.py`), and the report counts only the current version. Bump it whenever the score's
  definition changes.

**Position sizing matters more than picking.** `suggested_max_weight` caps a position so that a typical (1-sigma) monthly
move costs no more than 2% of the portfolio. A 25%-volatility stock can be 20% of the portfolio. A 100%-volatility token
should be about 7%.

## Real-time filing alerts

`mt watch` reads the SEC's latest-filings feed, which lists a filing minutes after the SEC accepts it. The daily
index that `mt alerts` uses only appears the next morning. Every run it:
- stores new open-market purchases by officers and directors, so an **insider cluster** alerts as soon as the
  filing that completes it appears (same rules as above);
- flags a **large purchase**: one officer or director buying $1M or more in one filing (a notification only,
  since a single buy is weaker evidence than a cluster);
- records every new **Schedule 13D** (a holder crossing 5% who may push for change) and alerts on any 13D/13G
  filed by one of the tracked investors.

Purchases in an IPO, underwritten offering or private placement are skipped: they are coded like open-market
buys but aren't the signal. The same trade reported by a fund and its board member counts once.

**How fast.** In GitHub Actions the watcher runs every 5 minutes, but GitHub runs schedules on a best-effort
basis, so expect alerts 5–20 minutes after the filing. For about a minute, run `mt watch --loop 60` on any
always-on machine (it shares the same data folder as `mt alerts`).

**Phone notifications** (free, no account):
1. Install the ntfy app (iOS or Android) and subscribe to a topic with a long random name, for example
   `plumbline-` followed by 20 random letters. Anyone who knows the name can read it, so don't use a guessable one.
2. Add it as a repository secret named `NTFY_TOPIC`.
New clusters, large buys and tracked-investor stakes then arrive as push notifications. Without the secret,
alerts still open issues.

## Alert scorecard and dilution check

**Scorecard.** Every alert (insider cluster, large buy, tracked-investor stake) is written to `alert_log.csv` on the
`journal-data` branch. `mt scorecard` and the site's *Alert scorecard* buy each one at the first close after the alert
and compare it with SPY over the same days at 21, 63 and 126 trading days, plus a return to date for alerts still open.
The verdict needs about 30 alerts with 3 months behind them before it claims anything; with daily alerts that takes
roughly four to six months. The 20 alerts from the first backfill are included, starting from their alert date.

**Dilution check.** For every alert, the company's EDGAR filing list is checked for a shelf registration (S-3/F-3; the automatic S-3ASR shelves large companies keep for bonds don't count on their own),
a prospectus supplement in the last 90 days (424B5 and similar: shares being sold, often through an at-the-market
program) or an S-1 in the last year. The result goes into the alert issue and the notification, and shows as a badge on
the site. The watcher also sends a notification when a company that alerted in the last 90 days files one of these.
`mt dilution TICKER` checks any company. A shelf is only a possibility: plenty of companies keep one and never use it.

## Market pulse (the app's first screen)

What is moving, what the press is covering, what insiders are buying quietly, and which of your
holdings deserve a second look. Every row says why it is there; click any ticker for its live chart.

- **Movers**: Yahoo's day gainers, losers and most-active screens (the whole US market), plus the
  ten largest coins. Prices tick live. Tags: *In the news* (5+ headlines in 48 h), *Deep coverage*
  (Reuters, Bloomberg, WSJ, FT, Barron's and similar), *Unusual volume* (2x the 3-month average),
  *Moving without news* (5%+ move, no headlines). If the screens fail, a fixed list of large caps
  is ranked instead and the page says so.
- **In the news / In-depth coverage**: the movers with the most headlines, and the ones covered
  by outlets that do their own reporting, with links.
- **Sleepers**: an insider cluster or a $1M+ officer/director purchase in the last 30 days (from
  the filing watcher's data), yet 3 or fewer headlines this week and less than 15% up over the
  month. Funds and tickers with no US quote are dropped; companies selling stock sort last.
  Unproven: the alert scorecard decides whether these beat SPY.
- **Sell watch** (only when you have holdings): each position checked against rules: below its
  200-day average, negative 12-month momentum, composite signal ≤ -15, $1M+ discretionary insider
  selling with no buying, up 40%+ in 3 months with RSI > 75, a position larger than its volatility
  supports (or over 25%), an active share offering, and a possible tax-loss sale. "Review" means
  the flags add up to 3+; a holding whose data couldn't be fetched says "Couldn't check", never
  "No red flags". These are rules to start a decision, not the decision.

`GET /api/pulse` (cached 3 minutes, `?refresh=true` rescans) and `GET /api/sellwatch` (cached 10
minutes per portfolio) return the same data as JSON.

## Home, trading screens and accounts

- **Home** (Robinhood-style): your portfolio's value with a live 1D line and 1W/1M/3M/1Y/ALL history, green when up and
  orange when down; hover to read any moment. The gain counts purchases as money put in, not as profit. Buying power
  (the cash you set), your stocks, crypto and watchlist with sparklines and live price pills, the early wire and your news.
- **Symbol page**: Today and After-hours (or Pre-market) changes on separate lines, ranges 1D to 5Y, a candlestick
  chart with volume and OHLC on hover, and **Trade**: buy or sell in shares or dollars, market or limit, a live estimate,
  Copy order, Open in Robinhood/Coinbase, and "It filled: record it". Orders are placed in your broker: neither Robinhood
  nor Stash has a trading API for individuals.
- **Accounts**: Robinhood (CSV), Coinbase (CSV or **read-only API sync**: a View-only key in `.env` imports your fills
  with cost basis and reports balances the ledger can't explain), Stash and anything else (type holdings once), or
  **Automatic** sync through SnapTrade (below).

### How each account reaches the app (free, near real time)

Connect them in **Portfolio → Accounts**; no files to edit. Prices are live everywhere; your holdings follow within
minutes of each trade:

| Account | How | How fast |
|---|---|---|
| Robinhood stocks | **Trade-confirmation emails**: the app reads only emails from robinhood.com, stash.com and coinbase.com in your inbox (IMAP with an app password, read-only) and adds each executed order | ~2 minutes after the email |
| Robinhood crypto | **Robinhood Crypto API** (official, free). The app makes the key pair; you paste the public half into robinhood.com/account/crypto → API trading | every 5 minutes |
| Coinbase | **Coinbase API key** (free; View permission, plus Trade if you want to place orders here) | every 5 minutes |
| Stash | Trade emails, plus an **auto-invest schedule** ("$20 into VOO every Monday") the app records at each day's close, plus a **statement check** that compares a monthly PDF with the ledger | ~2 minutes / daily / monthly |

Why these routes: Robinhood (stocks) and Stash offer individuals no API, and tools that log in with your password and
2FA break their terms and keep your broker password on disk. Emails the reader doesn't understand are listed on the
Connections card (subject and date only) rather than guessed; the patterns cover Robinhood's "Your order to buy 10
shares of AAPL … was executed at an average price of $150.25" and similar wording, and may need extending for your
brokers' exact emails. A trade already in the ledger (CSV, another sync, or a schedule) isn't added twice.

Home → **Accounts** shows each account's value, where its data comes from and how fresh it is; the morning brief says
when an account's data has gone stale. Every symbol page says which accounts hold it ("Robinhood 40 · Stash 2").

### Moves between your own accounts (Portfolio → Accounts → Transfers)

Sending coins from Coinbase to Robinhood, or shares from one broker to another, isn't a sale: they keep what you paid
and the date you bought them, so a later sale is taxed on the original cost (and counts as long-term from the first
purchase). Coinbase's CSV sends and receives, and the balance checks after each sync, show up here to pair; a move you
made by hand can be recorded in one line. A network fee (up to 3%) is folded into the cost of what arrived. Coins that
arrived from outside the accounts here ask for their original cost and date; coins that left ask whether they went to a
wallet of yours or were spent.

### Your money (Accounts page)

- **Cash waiting**: each account's uninvested cash (Coinbase fills in from its sync, Robinhood crypto's buying power from
  its API), how long it has sat, and what it would earn at today's 13-week T-bill yield. Over $100 for two weeks is flagged
  in the morning brief with a link to the new-money planner.
- **Off-site backup**: every night after 2am, the whole database, encrypted (AES-256-GCM, key from your passphrase by
  scrypt), to a folder (a synced Google Drive / iCloud / Dropbox folder works; the last 14 kept) and/or a private GitHub
  repository (refused if public). Only the passphrase opens a backup; the app keeps a key derived from it, not the
  passphrase, so it can run while you sleep. Restore replaces the data and keeps the old copy beside it.
- **Connection health**: a push when a sync has failed for 3 hours, when an auto-invest buy has no confirmation email
  after 5 days, or when the backup fails or hasn't run for 3 days.
- **Live prices**: crypto is tick by tick from Coinbase; stocks poll Yahoo every few seconds until you paste a free Finnhub
  key here, which switches them to Finnhub's live trade stream without a restart.

### Trading from the app

The app has its own order engine: a preview, your limits, a one-time sealed confirm and a trade log, for every venue.

| Where | What | Money |
|---|---|---|
| **Paper account** | Stocks and crypto, filled at the live price. Starts at $10,000, resettable | Pretend |
| **Coinbase**, **Robinhood crypto** | Crypto, with your API keys | Real |
| **Alpaca** | Stocks and ETFs, fractional and by dollar amount, commission-free. Its paper keys are pretend money | Real or pretend |
| **Public.com** | Stocks and ETFs, fractional by dollar amount (its free Individual Trader API) | Real |

Robinhood and Stash have no stock API for individuals (Robinhood's 2026 agentic trading works only in a separate account
through an AI agent), so shares held there still open in their app, and the confirmation email brings the trade in. To
trade stocks from here, hold money at Alpaca or Public (new money by bank transfer; moving existing shares costs about
$75-100 per account and fractional shares get sold). Their adapters follow each broker's official SDK; Public's hasn't
run against a live account yet. Filled Alpaca (live) and Public orders come into the ledger as accounts "Alpaca" and
"Public" within a couple of minutes.

Crypto orders go straight to Coinbase or Robinhood from any coin's **Trade** window: pick where to send it, **Preview**
(the broker's own price and fees, plus wash-sale, tax and concentration warnings), then **Confirm**. Safety:

- Off until you switch it on (Portfolio → Trading), with a per-order limit ($250) and a daily limit ($500) you set.
- Confirming sends exactly the previewed order: the preview carries a one-time code that expires after 90 seconds.
- Sells can't exceed what that account holds; every attempt, sent or refused, is in the trade log.
- Stocks and funds open in Robinhood or Stash with the order ready (neither offers a stock API for individuals); the
  confirmation email then brings the trade in.

### Automatic account sync (SnapTrade)

SnapTrade connects to brokers for you, so trades, reinvested dividends, dividends and interest arrive without CSVs.
It's a paid service; a **personal API key** covers your own accounts.

1. Create a personal key at snaptrade.com and add it to `.env` in the app folder:
   `SNAPTRADE_CLIENT_ID="..."` and `SNAPTRADE_CONSUMER_KEY="..."`. Restart the app.
2. Portfolio → Import your accounts → **Automatic** → **Connect a broker**. SnapTrade's page opens; sign in to Robinhood,
   Coinbase and the rest. The connection is **read-only**: it can't place orders or move money.
3. **Sync now.** Trades you already imported from a CSV aren't added twice (same account, symbol, side, quantity and day).
   Positions the ledger can't explain (shares transferred in, splits, trades older than the broker's history) are listed
   for you to add with Stash / other.

Check SnapTrade's current broker list for Stash before relying on it; if it isn't there, keep typing Stash holdings once.
The sync's request signing is tested against SnapTrade's own SDK, but it hasn't run against a real account yet: the
first sync with your key is the real test, and any error it shows is SnapTrade's own message.

### Logos and names

Every ticker shows its company logo and name. The app fetches each logo once (Financial Modeling
Prep's public images or Parqet for stocks and funds, the cryptocurrency-icons set or CoinGecko for coins) and keeps it in
`logo_cache/` next to the database; your browser only ever talks to this app, so no logo service sees what you look at.
A ticker with no logo gets its first letter on a colored circle.

## Early wire (before the mainstream)

The **Early** tab lists tickers starting to move where the press hasn't caught up: StockTwits trending (with its one-line
reason), Reddit mention spikes (via ApeWisdom: Reddit blocks servers), companies' own press releases (GlobeNewswire; Business
Wire, PR Newswire and Accesswire via Google News) sorted into catalysts (deal, FDA, contract, guidance, buyback, offering,
reverse split), 8-Ks that sign an agreement, complete a deal or change control, CoinGecko trending coins, Binance new
listings, new Coinbase USD pairs, and stablecoins more than 0.5% off $1. A ticker is **early** while mainstream outlets have
at most two articles about it in 24 hours. Each day's first sighting is logged with its price and scored live on the
same tab. Social spikes are also where pump-and-dumps start.

## People (follow traders with a record)

The **People** tab: congressional stock trades (House reports parsed from the Clerk's PDFs, Senate reports from eFD, each
with how late it was disclosed), ARK Invest's daily trades (its six ETFs diffed day over day), big insider purchases and
activist stakes. Follow anyone to get their new moves as heads-ups. **If you'd copied** buys the same dollars at the close
on each disclosure date (the first day you could have acted), sells on disclosed sales, and compares with SPY.

## Filing radar (scary SEC filings)

The **Radar** tab and the heads-up bell watch for filings that usually mean trouble, for every company you own or watch
(last 90 days) and market-wide (last 3 days):

| Level | Filings |
|---|---|
| Act today | Bankruptcy (8-K 1.03), past financials can't be relied on / restatement (4.02), delisting notice (3.01), debt default or acceleration (2.04), exchange delisting (Form 25-NSE, 25) |
| Serious | Auditor change (4.01), cybersecurity incident (1.05), write-down (2.06), restructuring or layoffs (2.05), late annual or quarterly report (NT 10-K / NT 10-Q), deregistration (Form 15), going-concern doubt in a 10-K/10-Q |
| Read it | Director or officer change (5.02: appointments too, so read it), change to shareholder rights / reverse split (3.03), material agreement ended (1.02) |

EDGAR's live feed lists each 8-K's item numbers, so filings are classified without downloading them. The app
checks it every 2 minutes while it runs. The going-concern check uses EDGAR full-text search. Crypto has no SEC
filings.

## News for your holdings, and what the pros are reading

- **News tab:** every stock and coin you own (Robinhood, Coinbase, Stash) or watch: headlines from the last 7 days, today's
  count against its usual daily pace (**loud** at 3× and 5+), headline mood, in-depth coverage, and one merged
  headline feed you can filter by symbol. The **heads-up** list at the top collects everything the background
  watch found.
- **News desk** (top of the News tab): each holding's news from aggregators (Google News, Yahoo, Nasdaq, Finnhub),
  newsrooms read directly (CNBC, WSJ, MarketWatch, Bloomberg), press wires (PR Newswire, Business Wire, GlobeNewswire),
  regulators (SEC, FDA, FTC), the SEC filing radar and crypto outlets (CoinDesk, Cointelegraph, Decrypt, The Block),
  grouped into stories. Each story counts independent outlets rather than copies (a filing or regulator weighs 1.0, a top
  newsroom 0.8, other newsrooms 0.6, a company press release 0.5, blogs 0.4), gets an event type, and is marked rumor or
  stale (a repeat within 30 days). A serious event (restatement, auditor leaving, SEC/DOJ action, going concern, dividend
  or guidance cut, sudden CEO/CFO exit, bankruptcy, FDA rejection, exchange hack) confirmed by a filing or two major
  newsrooms puts the holding under Review in the hold plan and pauses new buys of it until you look. News never says sell
  on its own and headline mood never moves anything: research finds large-company news is mostly priced by the time
  it's a headline (Ke, Kelly & Xiu 2019; Lopez-Lira & Tang 2023), recycled news reverses (Tetlock 2011), and specific
  events, not article counts, carry the information (Boudoukh et al. 2019).
- **Reading tab:**
  - *What the pros are reading*: the links Abnormal Returns (a daily list for investment professionals) and Barry
    Ritholtz picked in the last few days. Picked by both ranks first.
  - *From the desks*: FT (incl. Alphaville), Bloomberg, WSJ, CNBC, MarketWatch, Seeking Alpha, the Economist, Yahoo
    Finance, Calculated Risk, the Fed, the SEC, CoinDesk, Decrypt, The Block.
  - Every item is tagged with your holdings it names (ticker, company or coin name) and your **topics**. A topic
    **heats up** at twice its usual daily pace (5+ stories). Five default topics are set up (rates, AI, crypto
    policy, tariffs, recession); add your own with the words to match.

## Hold plan (when a buy-and-hold investor should sell)

The **Hold plan** tab is for people who buy and hold. Every holding is **Hold** unless something real fires. Price
wiggles, "extended" charts and 200-day averages are not on the list: for long-term investors, trading on them mostly
costs money. A holding is raised for a decision only by:

1. **Your own tripwire.** Write down why you own it and what would prove you wrong, and set the lines the app can check:
   sell below a price, re-think after a loss from your cost, take some off at a price, a target share of the portfolio,
   a review date. Crossing a line puts it on the list with your own words.
2. **The business itself**, from the company's quarterly filings with the SEC: revenue, growth on a year earlier, gross
   and operating margin, EPS, free cash flow and share count. Automatically, a stock goes to Review when revenue is below
   a year earlier two quarters running, the share count grows more than 10% in a year, or free cash flow over the last
   four quarters turns negative. Your own rules replace those: "revenue growth under 10% for 2 quarters", "operating
   margin under 20%", "dilution over 3%", "free cash flow must stay positive". Funds and crypto have no such filings.
3. **A serious SEC filing** in the last 90 days: delisting or bankruptcy (Sell?), going-concern doubt, restatement,
   auditor change or late report (Review).
4. **Concentration.** One position past your cap (20%, settable; loosened to 1.5x an equal share so a 3-stock
   portfolio can hold 50% each) or past the target you set. Trimming back is the sale buy-and-hold research supports.
5. **Taxes.** Sales that would turn long-term within 90 days say when, and what waiting saves; losses worth harvesting
   come with a replacement to hold for 31 days so you stay invested.

Money a trim frees up (plus buying power) goes to a **reinvest queue**: holdings below the target you set, watchlist
names with no serious filings, then a broad market fund (VTI). **Coming up** lists your earnings with the options-implied
move in dollars ("NVDA reports Nov 18: options price ±11%, ±$1,016 on yours"), Fed decisions, CPI and jobs reports.

**Putting new money in?** Enter an amount and the plan splits it toward the targets you set, biggest shortfall first,
without selling anything. Holdings without a target keep their share of the rest (so with no targets it keeps your
current mix). Holdings flagged Sell?/Trim/Review and anything in a wash-sale window are left out; once every target is
met the rest goes to a broad market fund.

**Taxes across your accounts**: realized gains this year, and wash sales that no single broker can see: selling at a loss
in Robinhood and buying the same stock, or a fund on the same index, in Stash within 30 days either side. A don't-buy-until
list after loss sales, the short- to long-term clock per lot, and harvest candidates with the recent buys that would wash
them. Rates are settable (24% short-term and 15% long-term by default). Not tax advice: IRA and spouse accounts count too
and aren't visible here; crypto has not been under the wash-sale rule, which the app notes rather than assumes.

**Before December 31**: tax on this year's realized gains now, which losses to take by the last trading day to offset
them (each one only while it still lowers this year's tax: gains first, then up to $3,000 of other income, the rest
carried forward), what that saves, and losses a recent buy would wash. It warns when dividend reinvesting or a
recurring buy would wash a harvested loss. Enter your filing status and taxable income (before investment gains) and it
sizes long-term gains you could take at a **0% federal rate**: sell and buy straight back to raise your cost basis
(the wash-sale rule covers losses only). The 0% limits default to the 2026 IRS figures (single $49,450, married
jointly $98,900, head of household $66,200); they change yearly, so the field is editable. Your state may still tax
the gain.

## Plan tools

- **Five sections**: Home, Plan (Hold plan, Income, Strategy), Discover (Pulse, Early wire, People, Filing radar, Smart
  money, Analyze, Deep dive, Dashboard, Track record), News (Your news, Reading room), Portfolio.
- **vs the market** (Home): every dollar you put in goes, on the same day, into VOO in a shadow portfolio; every sale takes
  the same dollars out. Your gain against that one, overall and per account.
- **Buy-the-dip list** (Hold plan): a price you'd pay for a stock or coin you're watching. Your phone gets a push when it
  gets there, and it goes to the top of where freed or new money goes.
- **Price lines reach your phone**: your sell-below and take-some-off lines, and dip prices, are checked every 2 minutes
  (one push per line per day).
- **Goal** (Hold plan): "$X by year Y": bad, typical and good outcomes from thousands of simulated markets, the chance of
  getting there, and the monthly amount that gives even odds.
- **What your funds really hold** (Portfolio): your money in each company, directly and through your funds, from each
  fund's latest SEC holdings report (N-PORT). SPY and DIA, unit trusts that don't file one, use a fund on the same index.
- **What your funds cost** (Portfolio): each fund's expense ratio in dollars a year and over 30 years, and a cheaper fund
  on the same index where one exists (switching means selling, so check the tax first).
- **Auto-invest schedules and statement check** (Portfolio): see the accounts table above.

## Setup and "checked against your brokers"

Portfolio → Accounts opens with **Setup**: the backup, phone alerts (ntfy, with a test push), each account's feed, a
statement check for Robinhood stocks and Stash, live prices, and a paper trade. Each step is done only when its **Test
now** passes. Home shows **Checked against your brokers**: the share of your money (by value) that matched a broker's
own balance in the last day (Coinbase, Robinhood crypto, SnapTrade) or a statement in the last 35 days, per account,
with the list of things to fix (balances that differ, transfers waiting, unread broker emails, failing syncs).

## Review (Plan → Review)

- **What your timing cost:** your dollars' return (money-weighted, counting dividends paid to you) against the holdings'
  own return (time-weighted, what a fund reports). The gap is your timing. Also what you'd have if you'd never sold, and
  each holding's contribution in dollars, since your first trade or this year.
- **If a past crash happened today:** today's holdings through 2008, 2020 and 2022 (holdings too young use their beta
  times the S&P 500, or Bitcoin's 2022 fall for coins before 2014; labelled as stand-ins).
- **Holdings that move together:** a year of daily correlations; pairs above 0.8 flagged.
- **What changed in their annual reports:** each company's latest 10-K against last year's, Risk Factors and Legal
  Proceedings: share of new text, similarity, and the new sentences (after "Lazy Prices", Cohen, Malloy & Nguyen 2020).
  Each is ranked against the S&P 500 ("more new text than 92% of S&P 500 companies' latest reports"): a weekly GitHub job
  (`10-K ranking`, `mt tenk-rank`) reads every S&P 500 company's last two 10-Ks, re-reading only companies with a new one,
  and stores `tenk_rank.json` on the journal-data branch. The S&P 500 reports that changed most are listed under the card:
  a look-before-you-buy list, not a sell list.
- **Hidden style bets:** your stocks and funds, weighted as held today, regressed on the Fama-French five factors plus
  momentum (Kenneth French's free daily files): market sensitivity, and tilts to small or large, value or growth,
  profitable or not, conservative or aggressive investors, recent winners or losers, next to VOO's own loadings. A tilt
  counts only at 0.15+ and t of 2+. Coins are left out and their share is shown.

**Home → What moved today:** today's change in dollars by holding with the news desk's likely reason; a move over twice
the holding's usual daily move (and 3%+) pushes to your phone once a day, saying whether news explains it.

**Ask the company's filings** (any stock's page): a question answered from its latest 10-K, 10-Q and/or results press
release with the passages quoted (Claude with citations; uses your Anthropic key: roughly $0.50-0.75 for the first
question on a report, cents for follow-ups while it's cached).

**Latest results** (any stock's page): the company's results press release (the 8-K item 2.02 exhibit 99.1, free on EDGAR
the moment it's filed): its headline sentences quoted in order, the outlook and whether it was raised, lowered or kept,
the stock's move on the first trading day after (against a normal day), and, once the 10-Q is out, the audited revenue and
EPS against a year earlier. Releases lead with the company's own adjusted figures; the 10-Q numbers are the check.

## Ask Claude about your portfolio (read-only connector)

`mt mcp` runs Plumbline as a Model Context Protocol server, so Claude Desktop or Claude Code can answer "why am I down this
month?" or "which of my dividends look shaky?" from your own data. It has 33 tools and every one is a GET to the running
app (portfolio, accounts, weekly recap, brief, what moved, hold plan, news, performance, crises, overlap, style bets,
10-K changes, earnings recap, dividend safety, income, taxes, events, data confidence, advice record, the screen, the
idea scorecard, sleepers, chatter, theme crowding, money flow, the economy, priced-in checks, government contracts,
quote, fundamentals, analysis). There is no tool that records a trade, places an order, changes a setting or runs the paid
filing Q&A. Install with `pip install -e ".[mcp]"`, then in Claude Desktop's config:

```json
{"mcpServers": {"plumbline": {"command": "mt", "args": ["mcp"],
  "env": {"PLUMBLINE_URL": "http://127.0.0.1:8000", "PLUMBLINE_PASSWORD": "only if the app has one"}}}}
```

`PLUMBLINE_URL` can point at your hosted copy; the connector logs in with the app's password like the browser does.

## Ideas: what to buy, sleepers, chatter, money flow, economy

The honest version first: nothing, free or paid, reliably picks the stock that will boom. Most professional stock pickers
trail the index over 10+ years, published stock-picking edges lose about half their strength once known (McLean &
Pontiff 2016), and themed funds have trailed the market by about 6% a year after launch because the theme was already
priced in (Ben-David et al. 2023). So every idea here is written to the **idea scorecard** the day it appears, can't be
edited, and is scored against simply buying VOO at 3, 6 and 12 months. A kind of idea earns your money only after its
record does (20 results at 6 months before the app calls it anything but "too early").

- **What to buy** (weekly job `Stock screen`, `mt screen`): every US-listed company worth $300M+ (about 2,700 with SEC
  numbers) graded within its sector on quality (operating profit on assets), value (earnings and book value against the
  price), momentum (12 months, skipping the last) and not diluting shareholders; one grade in the bottom 10% caps the
  total at 50. Lists for large companies, all $2B+, and small and mid caps. Data: SEC XBRL frames (one request per number
  for every filer), Nasdaq's list of listed stocks, a year of daily prices. About 35 seconds on a GitHub runner.
- **Growing order backlogs**: contracted revenue not yet delivered ("remaining performance obligations") growing 10+
  points faster than revenue, a meaningful backlog a year ago, and the stock not among the most expensive.
- **Priced in?** (any stock's page): P/E against its own last five years of quarter-ends, a 12-month return in the top
  5%, 30%+ above the 200-day average, assets or share count growing fast, a crowded theme; plus federal contract money
  over the last year against revenue (USAspending.gov).
- **Sleepers**: $300M-$10B companies where independent evidence lines up: a good screen grade, insiders buying outside
  their usual habit (Cohen, Malloy & Pomorski 2012: routine traders predict nothing, opportunistic ones do), a backlog
  growing faster than revenue, and quiet coverage; never names that already ran. One is a lead, three is a sleeper.
  Sleepers and chatter picks you mark "bought" count against a speculative limit of 10% of the portfolio.
- **Chatter**: the most-discussed tickers on Reddit and StockTwits and how fast that's rising, with warnings per name.
  A caution list, not a buy list; the top five are scored so you'll see how chatter does. **Theme crowding**: new fund
  registrations per theme (SEC full-text search), last six months against the six before.
- **Money flow**: capital spending over the last year by the cloud and AI data-center companies, utilities and chip
  makers (from their cash-flow statements), next to hand-picked suppliers by category with how far each has run; and
  small suppliers whose 10-K names a big customer that moved 10%+ in a month when they didn't.
- **Economy**: the 10-year yield and yield curve, high-yield credit spreads, the Sahm recession gauge, the Chicago Fed's
  financial conditions, jobless claims, oil and new factory orders (FRED). It only changes how fast to put new money in
  (all at once, 3 monthly pieces, or 6), never whether to be invested.

## Taxes (Portfolio → Taxes)

- **Which shares to sell**: a sale under each cost-basis method (first in first out, highest cost, last in first out,
  least tax now), the lots each takes and the tax in dollars, with the cheapest marked. Set each account's method to what
  the broker uses so the app's gains match your 1099; the trade preview also says when another method would save $5+.
- **Tax export**: the year's sales from every account in one CSV laid out like Form 8949 (short/long-term, wash-sale code
  W across accounts), and an income CSV (dividends, interest, tax withheld, crypto rewards and staking). A worksheet to
  check each broker's 1099-B / 1099-DA / 1099-DIV against, not a filing.

## Does the advice work? (Discover → Track record)

Every day the hold plan's sells, trims and buys are written down with that day's price and later scored against what
you'd have done anyway: put the money in VOO. A sell helped if the stock then lagged VOO; a buy helped if it beat it. The
page says "too early to tell" until 20 have a 3-month result.

## Income (dividends)

The **Income** tab: dividends received in the last 12 months and this year, what your holdings pay a year at today's
rate and per month, yield on what you paid and on today's value, the next ex-dividend dates (own the shares at the close
the day before) and a month-by-month view of the next 12 months. Robinhood's CSV dividend, tax-withheld and interest rows
are imported; for an account with no dividend rows (Stash) payments are estimated from each ex-date and the shares that
account held, and marked so. Dates come from Nasdaq where the company has declared them (Nasdaq-listed names);
otherwise they're projected from the usual spacing and marked estimated. A one-off special dividend isn't treated as if
it would repeat.

**Dividend safety** grades each dividend-paying stock A to F from its own filings: dividends against free cash flow
(the strongest warning sign) and profit over the last four quarters, net debt against operating profit before
depreciation, years of raises, and any cut in the last three years, with the reasons written out. Rules of thumb, not a
fitted model; real estate trusts get a caveat (they're judged on funds from operations, which filings don't tag).

## Weekly recap (Sunday 5pm ET) and the morning brief

**The weekly recap** is the default push: one notification on Sunday evening, also on **Home → This week**. Your
holdings' change over the week in dollars (biggest first), what needs a decision, news confirmed by several outlets,
big moves, new annual reports (with the S&P 500 rank), next week's earnings and releases, and how much of your money was
checked against a broker, with what to fix. Checking less often means fewer fear-driven sales (Benartzi & Thaler's
myopic loss aversion). Urgent things still push the moment they happen. Choose on Home: weekly recap, daily brief only,
or both.

**The morning brief** is always on Home each weekday (and pushes at 8:30 ET if you choose daily or both): holdings that need a decision
and why, new serious filings on your companies, your earnings today or tomorrow (with the move in dollars), CPI/jobs/Fed
releases, pre-market moves of 2% or more and what the whole portfolio is doing, early-wire hits on your holdings, crypto
radar items and tax dates this week. On a quiet day it says so. `POST /api/brief/send` pushes it now to test your phone.

## Receipts (a public, tamper-evident record)

The filing watcher records the day's strongest early-wire tickers with the price when first seen (`early_calls.csv` on the
`journal-data` branch). When a day ends its calls, together with that day's insider alerts, are hashed with SHA-256 and
chained to the previous day (`receipts.jsonl`), so changing, adding or dropping a past call breaks every later seal. The
public site's **Receipts** section shows every call older than 24 hours, scored 5 trading days on against SPY, misses
included. `mt receipts verify --data-dir <folder>` recomputes the chain from the raw files.

## Stock pickers and crypto radar

**People → Stock pickers, graded**: follow any StockTwits account. Posts tagged Bullish or Bearish are calls, with the
price StockTwits recorded at posting, scored 5 trading days later against SPY (bearish calls are right when the stock
lagged). Calls are stored when first seen, so deleted posts still count.

**Radar → Crypto radar** for the coins you hold or watch: exchange hacks of $10M+ and hacks on your coins' chains
(DefiLlama), Coinbase incidents naming your coins, supply not yet circulating (CoinGecko), and stablecoins off their
peg. Exact unlock dates and ETF flows need paid data, so they're left out rather than guessed. Serious items also arrive
as heads-ups.

## Strategy plan (what to sell, trim, add and buy)

The **Plan** tab (for active trading; buy-and-hold investors should start with the Hold plan) turns your holdings into orders. For each position: Sell, Trim, Hold or Add, and new Buys from
your watchlist and the latest sleepers, each with a share count, the value at the live price, the reasons, and
the tax effect (short- vs long-term gain, the date shares turn long-term, harvestable losses and the wash-sale
rule). Enter the cash you have to invest; adds and buys are paid only from that cash and the plan's own sales.

- Size limit per position: the most a normal (1-sigma) month can cost is 2% of the portfolio, capped at 20%
  (10% when volatility is unknown).
- **Sell**: sell-watch flags add up to 4+ and the composite signal is -15 or worse.
- **Trim** to the limit when a position is over 1.25x it; **trim by half** when flags add up to 3+.
- **Add** (at most 4 points of weight per plan): no serious flag, signal +15 or better, above the 200-day average,
  under 60% of its limit.
- **Buy** a 4% starter position: signal +25 or better, above the 200-day average, no serious flag.

Each order has a ticket: quantity, a limit price 0.2% through the live price, the estimated total, time in force
(extended hours outside the regular session), **Copy order**, **Open in Robinhood**, and **It filled: record the
trade**, which adds it to your ledger. Placing the order is yours to do: Robinhood has no public trading API.

These rules are not validated (the backtest found no reliable edge in the composite score). So the plan keeps
score on itself: each day's first calls are logged, and the tab shows each call's price then, the price now (live)
and whether it has been right so far. Judge it after months, not days. `GET /api/plan?cash=...` returns the plan
and the log as JSON.

## Private app (your dashboard, online, behind a password)

The full dashboard (`mt serve`) can run on a small always-on server so you can use it from your phone:
- live prices: crypto tick by tick from Coinbase; US stocks tick by tick from Finnhub if `FINNHUB_API_KEY` is set,
  otherwise the latest trade every ~5 seconds, **pre-market (4:00 ET) and after hours (to 8:00 PM ET) included**;
- every number on screen follows the price: portfolio value, today's change, P&L per position and in total,
  weights, order values, and the plan's track record. A strip under the tape shows your portfolio, the US session
  (open / pre-market / after hours / closed, with a countdown) and how long since the last tick. Lists that aren't
  prices (movers, news, the plan) refresh themselves every few minutes. Outside 4 AM–8 PM ET on weekdays, US stocks
  don't trade anywhere, so only crypto moves;
- a ticker tape of your holdings and watchlist; click any ticker for its page, with a live 1D/1W/1M/1Y chart, your
  position, the signal and news, and Buy/Sell buttons that fill in the trade form;
- your portfolio, imported from Robinhood (Portfolio → Import from Robinhood), plus deep dives.

It is one Docker image (`Dockerfile`). Every page and API call needs the password in `APP_PASSWORD`; the hosted
configs also set `REQUIRE_LOGIN=1`, so a server without a password shows nothing. Your trades live on a small
persistent disk.

**Fly.io** (about $3–5 a month for one small always-on machine plus a 1 GB volume):
1. Install `flyctl` and run `fly auth signup` (or `login`).
2. In this repo: `fly launch --copy-config --no-deploy` (accept a unique app name), then
   `fly volumes create plumbline_data --size 1`.
3. Set secrets: `fly secrets set APP_PASSWORD='a long passphrase' SEC_USER_AGENT='plumbline you@example.com'`,
   plus optionally `FINNHUB_API_KEY=...` (free at finnhub.io) and `ANTHROPIC_API_KEY=...` for deep dives.
4. `fly deploy`, then open `https://<app-name>.fly.dev`.

**Render** (Starter plan, about $7 a month plus the disk):
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/jwill736/market-tracker)
or New → Blueprint → choose this repo. `render.yaml` sets
up the service and disk and asks for the secrets. The free plan sleeps after 15 minutes and has no disk, so it
doesn't suit this app.

The `Docker image` workflow builds the image on every pull request that touches it and checks that it starts,
refuses requests without a login, and serves the dashboard after one.

## Public site

`https://jwill736.github.io/market-tracker/` is a read-only Plumbline page that anyone can open. It shows:
- crypto prices streaming live from Coinbase in your browser;
- stock quotes as of the latest snapshot;
- today's scores and what drives them;
- recent insider cluster buys, large single purchases, and new 13D stakes, with dilution flags;
- the alert scorecard;
- the live track record and the backtest.

It never publishes your portfolio, deep dives or keys; those stay in the local app (`mt serve`). One-time setup:
**Settings → Pages → Build and deployment → Source: GitHub Actions**. Build it locally with
`mt site --journal signal_journal.csv --alerts-dir alerts_data` and open `_site/index.html` through any static server.

## Configuration

| Variable | Purpose |
|---|---|
| `SEC_USER_AGENT` | **Required by SEC**: `"your-app your@email.com"`. Requests are throttled below SEC's 10 req/s limit |
| `ANTHROPIC_API_KEY` | Deep-dive research (or any credential source the Anthropic SDK resolves) |
| `MT_RESEARCH_MODEL` | Default `claude-opus-5`. Requests opt into server-side refusal fallbacks (`fallbacks="default"`) |
| `FINNHUB_API_KEY` | Optional: real-time US stock trades (streamed to the private app), quotes and company news |
| `MT_DB_PATH` | SQLite file (default `market_tracker.db`) |
| `APP_PASSWORD` | Password for the private app. When set, every page and API call needs a login |
| `APP_SECRET` | Optional: signs login sessions (defaults to one derived from the password) |
| `REQUIRE_LOGIN` | Set to `1` on a server so the app refuses to serve until `APP_PASSWORD` is set |
| `MAIL_USER`, `MAIL_APP_PASSWORD`, `MAIL_IMAP_HOST` | Set from Portfolio → Accounts: reads broker trade-confirmation emails (read-only IMAP; Gmail needs an app password) |
| `COINBASE_API_KEY_NAME`, `COINBASE_API_PRIVATE_KEY` | Set from Connections: Coinbase sync every 5 minutes; with the Trade permission, orders from the app |
| `ROBINHOOD_CRYPTO_API_KEY`, `ROBINHOOD_CRYPTO_PRIVATE_KEY` | Set from Connections: Robinhood Crypto API (holdings, trades, orders) |
| `OFFSITE_DIR`, `OFFSITE_REPO`, `OFFSITE_GITHUB_TOKEN`, `OFFSITE_KEY`, `OFFSITE_SALT` | Set from Accounts → Off-site backup (the key is derived from your passphrase; the passphrase isn't stored) |
| `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY`, `ALPACA_LIVE` | Set from Accounts → Brokers: stock orders through Alpaca (`ALPACA_LIVE=1` for real money, else its paper mode) |
| `PUBLIC_API_SECRET`, `PUBLIC_ACCOUNT_ID` | Set from Accounts → Brokers: stock orders through Public.com |
| `SNAPTRADE_CLIENT_ID`, `SNAPTRADE_CONSUMER_KEY` | Optional: automatic, read-only account sync through SnapTrade (personal key) |
| `SNAPTRADE_USER_ID`, `SNAPTRADE_USER_SECRET` | Only with a commercial SnapTrade key: the user it registered |

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
| `score-backtest.yml` | on demand | rebuilds the score month by month from 2016 using only data public at each date, and reports how well each component ranked later returns |
| `insider-alerts.yml` | daily at 10:30 UTC (catch-up), on demand (with an optional backfill) | scans every Form 4 the SEC indexed since the last run and opens an `insider-alert` issue for each new cluster buy (rules below). State lives on the `journal-data` branch |
| `tenk-rank.yml` | Sundays 07:17 UTC, on demand (with an optional company limit) | ranks every S&P 500 company by how much of its latest 10-K's Risk Factors is new; only companies with a new 10-K are re-read. Writes `tenk_rank.json` to the `journal-data` branch |
| `screen.yml` | Sundays 06:43 UTC, on demand | scores every listed company worth $300M+ on quality, value, momentum and dilution within its sector, and finds growing backlogs. Writes `screen.json` to the `journal-data` branch |
| `watch.yml` | every 5 min on weekdays, every 3 h at weekends | reads EDGAR's latest-filings feed for Form 4 purchases and Schedule 13D/13G stakes; opens `insider-alert` / `stake-alert` issues and pushes phone notifications (below) |
| `site.yml` | every 30 min in US market hours, every 6 h otherwise, after journal and alert runs | builds the public site (`mt site`) and deploys it to GitHub Pages |

**When an insider cluster opens an issue.** At least 3 officers or directors (10% holders alone don't count) buy
on the open market, at least $10k each and $100k in total, within 30 days, and all of the following hold:
- the buys span at least 2 trading days, because same-day batches are usually compensation programs;
- the newest filing is at most 7 days old, so a backfill or a late run doesn't raise old news;
- the issuer has a ticker and isn't a closed-end fund or BDC (an investment-company or blank-check industry code, none at all, or fund-only filings such as N-CSR or N-2);
- the company wasn't alerted in the last 30 days.

A trade filed twice counts once. The job summary lists every active cluster with the reason it did or didn't alert
(`NEW`, `SEEN`, `1DAY`, `OLD`, `NOTK`, `FUND`).

Add a repository **secret** `SEC_USER_AGENT` (Settings → Secrets and variables → Actions → New repository secret)
set to `market-tracker your@email.com`. The SEC returns 403 to requests without a contact email. Use a secret, not a
variable: this repo is public, so Actions logs are public, and only secrets are masked in them.

Run the tests with `pytest`. They use recorded fixtures for SEC XML, RSS, and Yahoo JSON, plus a mocked Claude client,
so they need no network access.

## Known gaps / next steps

- **Congressional trades** (STOCK Act disclosures) need a paid feed such as Quiver or FMP. They aren't included yet.
- **Mapping 13F CUSIPs to tickers** matches on company name against SEC's ticker list. A few issuers go unmatched and show only their name.
- **Crypto "smart money"**: 13F doesn't cover crypto directly, so the deep dive covers ETF flows and on-chain data through web research.
- Yahoo's chart endpoint is unofficial and can change. Finnhub is the supported fallback.
