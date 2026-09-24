"""Claude-powered deep-dive research.

Step 1 streams a research memo: Claude gets our quantitative snapshot (price action,
forecast cone, composite signal, 13F moves, insider trades, headlines) and uses web search
and web fetch to read primary sources — filings, earnings calls, reputable coverage.
Step 2 extracts a structured verdict from the memo with a JSON schema so the UI can show
a rating, catalysts and risks consistently.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import anthropic
from pydantic import BaseModel, Field

from .config import settings

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_PAUSE_RESUMES = 5

SYSTEM = """You are a buy-side research analyst writing for an individual investor who wants \
to make an informed decision, not to be sold an idea.

Standards:
- Separate facts (cite the source and date) from inference. Prefer primary sources: SEC \
filings, earnings releases and call transcripts, regulator and exchange data, project docs \
for crypto. Treat social media and promotional sites as low-reliability.
- Use web search to find what has happened in the last 30-90 days that the headline list \
may not capture: earnings, guidance changes, product or regulatory events, management \
changes, major holder moves, on-chain or ETF flow data for crypto.
- 13F data is 45+ days stale and shows longs only; say so when you rely on it. Insider \
open-market buys matter more than sales.
- Give the bear case real weight. State what would prove the thesis wrong.
- The quantitative snapshot's forecast is a volatility-based range, not a prediction. Do not \
convert it into a price target.
- No certainty language. Be concrete about position sizing relative to volatility.

Structure the memo with these headings: Summary; What changed recently; Business / asset \
quality; Valuation and expectations; Smart money and insiders; Bull case; Bear case; \
Key risks and what would change my mind; Catalysts and dates to watch; Positioning."""


class Verdict(BaseModel):
    rating: str = Field(description="One of: Strong buy, Buy, Hold, Reduce, Avoid")
    conviction: str = Field(description="One of: Low, Medium, High")
    horizon: str = Field(description="Holding horizon the rating applies to, e.g. '6-12 months'")
    thesis: str = Field(description="Two to three sentence thesis")
    catalysts: list[str]
    risks: list[str]
    invalidation: str = Field(description="What observable event would invalidate the thesis")
    max_position_pct: float = Field(description="Suggested maximum portfolio weight in percent")


def _compact_context(analysis: dict) -> str:
    """Trim the analysis payload to what's useful for the model."""
    keep = {k: analysis.get(k) for k in ("symbol", "asset_class", "quote", "indicators", "signal",
                                         "smart_money", "smart_money_staleness_days", "insiders")}
    if analysis.get("forecast"):
        keep["forecast_lognormal"] = analysis["forecast"]["lognormal"]
    if analysis.get("backtest"):
        keep["backtest"] = analysis["backtest"]["results"]
    if analysis.get("insiders"):
        keep["insiders"] = dict(analysis["insiders"], trades=analysis["insiders"]["trades"][:15])
    news = analysis.get("news") or {}
    keep["headlines"] = [{"title": a["title"], "source": a["source"], "published": a["published"]}
                         for a in news.get("articles", [])[:25]]
    return json.dumps(keep, default=str, indent=1)


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def stream_memo(analysis: dict, question: str | None = None,
                client: anthropic.Anthropic | None = None) -> Iterator[dict]:
    """Yield {"type": "text", "text": ...} chunks, {"type": "status", ...} events and a final
    {"type": "done", "memo": ...}. Raises anthropic errors to the caller."""
    client = client or _client()
    prompt = (f"Deep-dive {analysis['symbol']} ({analysis['asset_class']}).\n\n"
              f"Quantitative snapshot from our pipeline (JSON):\n<snapshot>\n{_compact_context(analysis)}\n"
              f"</snapshot>\n\n")
    if question:
        prompt += f"The investor specifically wants to know: {question}\n\n"
    prompt += "Research current developments with web search, then write the memo."

    messages: list = [{"role": "user", "content": prompt}]
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6},
    ]
    memo_parts: list[str] = []
    for _ in range(MAX_PAUSE_RESUMES + 1):
        with client.beta.messages.stream(
            model=settings.research_model,
            max_tokens=64000,
            system=SYSTEM,
            messages=messages,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "server_tool_use":
                        yield {"type": "status", "text": f"Using {block.name}…"}
                elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                    memo_parts.append(event.delta.text)
                    yield {"type": "text", "text": event.delta.text}
            final = stream.get_final_message()

        if final.stop_reason == "refusal":
            yield {"type": "error", "text": "The model declined this request."}
            return
        if final.stop_reason != "pause_turn":
            break
        # Server-side tool loop hit its iteration limit; resend so it continues.
        messages = messages + [{"role": "assistant", "content": final.content}]
    yield {"type": "done", "memo": "".join(memo_parts)}


def extract_verdict(symbol: str, memo: str, client: anthropic.Anthropic | None = None) -> Verdict | None:
    client = client or _client()
    response = client.beta.messages.parse(
        model=settings.research_model,
        max_tokens=4000,
        output_config={"effort": "low"},
        betas=[FALLBACK_BETA],
        fallbacks="default",
        messages=[{"role": "user", "content":
                   f"Extract the verdict on {symbol} from this research memo. Use only what the memo "
                   f"supports.\n\n<memo>\n{memo}\n</memo>"}],
        output_format=Verdict,
    )
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output
