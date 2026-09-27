"""Plumbline as a read-only tool for Claude (a Model Context Protocol server).

Add it to Claude Desktop or Claude Code and ask about your own portfolio in plain words: "why am I
down this month?", "which of my holdings' dividends look shaky?", "what changed in VOO's top
holdings' annual reports?". Claude calls these tools and answers from your own ledger.

It can only read. Every tool is a GET to a fixed list of the app's own endpoints; there is no
tool that records a trade, places an order, changes a setting or asks the paid filing Q&A. It
talks to the running app (your computer or your hosted copy), so it sees exactly what the app
shows and uses the app's caches:

    PLUMBLINE_URL       where the app runs (default http://127.0.0.1:8000)
    PLUMBLINE_PASSWORD  the app's password, if it has one (APP_PASSWORD also works)

Run with `mt mcp` (needs `pip install -e ".[mcp]"`). Claude Desktop's config:

    {"mcpServers": {"plumbline": {"command": "mt", "args": ["mcp"],
                                  "env": {"PLUMBLINE_URL": "http://127.0.0.1:8000"}}}}
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import quote

import httpx

MAX_CHARS = 60_000
SYMBOL = re.compile(r"^[A-Za-z0-9.\-^=]{1,20}$")
INSTRUCTIONS = """Read-only access to the user's own Plumbline portfolio app (a buy-and-hold investor's \
holdings across Robinhood, Coinbase and Stash). Answer from these tools' data; say when data is \
stale, estimated or missing (each result says so). Nothing here can trade or change anything. Don't \
present signals or scores as predictions: the app itself labels which ones are unproven."""

# name: (path template, description). {symbol} and {period} are filled from the arguments.
TOOLS: dict[str, tuple[str, str]] = {
    "portfolio": ("/api/portfolio", "Holdings with quantity, cost, value, gain and weight, plus totals."),
    "accounts": ("/api/accounts", "Per-account holdings, where each account's data comes from and how fresh it is."),
    "weekly_recap": ("/api/weekly", "This week's recap: the week in dollars by holding, decisions needed, confirmed news, next week's events, data checks."),
    "morning_brief": ("/api/brief", "Today's brief: holdings needing a decision, new filings, earnings and releases, moves."),
    "moved_today": ("/api/moved", "Today's change in dollars by holding, with the news desk's likely reason for each big move."),
    "hold_plan": ("/api/holdplan", "The buy-and-hold plan: Hold / Review / Trim / Sell? per holding with the triggers behind it."),
    "news": ("/api/newsdesk", "News grouped into stories per holding, with how many independent outlets confirm each."),
    "performance": ("/api/performance?period={period}", "Money-weighted vs time-weighted return (the cost of your timing) and each holding's contribution. period: all or ytd."),
    "vs_index": ("/api/benchmark", "Your gain against the same money put into VOO on the same days."),
    "crisis_replay": ("/api/crises", "Today's holdings run through the 2008, 2020 and 2022 falls, in dollars."),
    "overlap": ("/api/overlap", "Correlations between holdings over the last year; pairs that move almost as one."),
    "factor_exposure": ("/api/factors", "Hidden style bets: loadings on market, size, value, profitability, investment and momentum."),
    "annual_report_changes": ("/api/tenk", "How much of each holding's latest 10-K Risk Factors is new vs last year, ranked against the S&P 500."),
    "most_changed_reports": ("/api/tenk/most-changed", "S&P 500 companies whose latest 10-K changed most (a look-before-you-buy list)."),
    "earnings_recap": ("/api/earnings/{symbol}", "The company's latest results press release: headline numbers, outlook, and the stock's reaction."),
    "dividend_safety": ("/api/income/safety", "Dividend safety grades (A-F) for dividend-paying holdings, with the reasons."),
    "income": ("/api/income", "Dividends received, forward income, yield on cost, and upcoming ex-dividend dates."),
    "taxes": ("/api/taxes", "Realized and unrealized gains, tax lots, wash-sale windows, harvesting ideas."),
    "events": ("/api/events", "Upcoming earnings (with options-implied moves) and macro releases for your holdings."),
    "data_confidence": ("/api/confidence", "How much of the portfolio was checked against a broker or statement lately, and what to fix."),
    "advice_record": ("/api/advice/record", "The app's own advice track record against simply buying VOO."),
    "what_to_buy": ("/api/screen", "The weekly quality/value/momentum screen of every listed company (large, all $2B+, small and mid) and growing order backlogs."),
    "idea_scorecard": ("/api/ideas", "Every idea the screens logged, scored against VOO at 3/6/12 months, with a leaderboard by kind of idea, and ideas you hold whose reason is gone."),
    "screen_backtest": ("/api/screen/backtest", "The screen replayed quarterly since 2012 on what was public then: its picks against SPY at 3/6/12 months, with survivorship caveats."),
    "earnings_events": ("/api/idea-events", "Companies that raised guidance in the last ten days to a big positive reaction, and spin-offs registered or newly trading."),
    "sleepers": ("/api/sleepers", "Small and mid caps where screen grade, insider buying, backlog growth and quiet coverage line up; and the speculative-bucket limit."),
    "chatter": ("/api/chatter", "What Reddit and StockTwits are talking about, with priced-in warnings per name (a caution list, not a buy list)."),
    "theme_crowding": ("/api/themes", "How many new funds are being filed per theme (AI, nuclear, quantum, drones...): crowded themes tend to be expensive."),
    "money_flow": ("/api/moneyflow", "Capital spending by cloud, utility and chip companies, their suppliers with how far each has run, and lagging small suppliers."),
    "economy": ("/api/macro", "Rates, credit spreads, recession gauges, oil and orders from FRED, and how fast to put new money in (never whether)."),
    "priced_in": ("/api/priced-in/{symbol}", "Is a stock already priced in: P/E against its own 5 years, run-up, dilution, asset growth, crowded theme."),
    "government_contracts": ("/api/contracts/{symbol}", "Federal contract money obligated to a company in the last year, against its revenue."),
    "quote": ("/api/quote/{symbol}", "Latest price and day change for a symbol."),
    "fundamentals": ("/api/fundamentals/{symbol}", "A company's quarterly revenue, margins, EPS, free cash flow and share count from its SEC filings."),
    "analyze": ("/api/analyze/{symbol}", "The app's full signal for a symbol: trend, momentum, news, 13F and insider data (unproven as a predictor)."),
}


class Plumbline:
    """A logged-in, GET-only client for the running app."""

    def __init__(self, base: str | None = None, password: str | None = None, client: httpx.Client | None = None):
        self.base = (base or os.environ.get("PLUMBLINE_URL") or "http://127.0.0.1:8000").rstrip("/")
        self.password = password if password is not None else (os.environ.get("PLUMBLINE_PASSWORD") or os.environ.get("APP_PASSWORD") or "")
        self.client = client or httpx.Client(timeout=180, follow_redirects=False)
        self._logged_in = False

    def _login(self) -> None:
        if self.password and not self._logged_in:
            r = self.client.post(self.base + "/login", data={"password": self.password})
            if r.status_code not in (200, 303):
                raise RuntimeError("Plumbline refused the password (PLUMBLINE_PASSWORD).")
            self._logged_in = True

    def get(self, path: str) -> str:
        if not path.startswith("/api/"):
            raise ValueError("only the app's /api/ endpoints")
        self._login()
        r = self.client.get(self.base + path)
        if r.status_code in (401, 303, 307):
            self._logged_in = False
            self._login()
            r = self.client.get(self.base + path)
        if r.status_code >= 400:
            detail = ""
            try:
                detail = r.json().get("detail", "")
            except ValueError:
                pass
            return json.dumps({"error": f"Plumbline answered {r.status_code}", "detail": detail})
        text = r.text
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f"\n... [cut at {MAX_CHARS:,} characters of {len(r.text):,}]"
        return text


def path_for(name: str, symbol: str | None = None, period: str | None = None) -> str:
    template = TOOLS[name][0]
    if "{symbol}" in template:
        if not symbol or not SYMBOL.match(symbol):
            raise ValueError("symbol: a ticker like AAPL, BRK-B or BTC-USD")
        template = template.replace("{symbol}", quote(symbol.upper(), safe=""))
    if "{period}" in template:
        template = template.replace("{period}", "ytd" if period == "ytd" else "all")
    return template


def make_server(app: Plumbline | None = None):
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations
    app = app or Plumbline()
    server = MCPServer(name="plumbline", instructions=INSTRUCTIONS)
    ro = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

    def register(name: str, desc: str, template: str):
        if "{symbol}" in template:
            def fn(symbol: str) -> str:
                try:
                    return app.get(path_for(name, symbol=symbol))
                except ValueError as exc:
                    return json.dumps({"error": str(exc)})
        elif "{period}" in template:
            def fn(period: str = "all") -> str:
                return app.get(path_for(name, period=period))
        else:
            def fn() -> str:
                return app.get(path_for(name))
        fn.__name__ = name
        fn.__doc__ = desc
        server.tool(name=name, description=desc, annotations=ro, structured_output=False)(fn)

    for name, (template, desc) in TOOLS.items():
        register(name, desc, template)
    return server


def main() -> None:
    make_server().run("stdio")
