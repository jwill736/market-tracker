"""Ask Plumbline: a conversation with Claude about your own portfolio, answered from the app's data.

Claude gets the same read-only tools as the Claude Desktop connector (mcp_server.TOOLS): holdings,
the hold plan, this week's decisions, taxes, the screens, the backtests, news. It calls them to
answer, and says which it used. It speaks as an advisor, not a cheerleader: the uncomfortable
answer first, every claim tagged with how sure it is, and plain disagreement when a plan looks
wrong. It can't trade, change settings or record anything: every tool is a GET. To act on
something it suggests, use the Decisions page.

Uses your Anthropic API key (ANTHROPIC_API_KEY). A question that needs a few tools costs a few
cents; the system prompt and tool list are cached between turns, so follow-ups cost less. With
server-side fallbacks on, a request Claude Opus 5 declines is retried on another model instead of
failing.
"""

from __future__ import annotations

import json
import re
import time
import uuid

import anthropic

from . import mcp_server
from .config import settings

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_ROUNDS = 8
MAX_RESULT_CHARS = 40_000
KEEP_CONVERSATIONS = 50
CONVERSATION_SECONDS = 6 * 3600

SYSTEM = """You are Plumbline, the user's investing advisor, built into their portfolio app. The user is a \
buy-and-hold investor with accounts at Robinhood, Coinbase and Stash. You answer from their own data through \
the tools; call the tools you need before answering, and name the ones you used at the end in one short line.

How you talk:
- Your first sentence challenges an assumption, names what the user is missing, or gives the uncomfortable \
answer. No warm-up, no praise, no "great question".
- Tag claims: [Certain] when a tool result shows it, [Likely] for a strong inference, [Guessing] when filling \
gaps. If most of an answer is guessing, say so first.
- When the user's plan looks wrong, say "I disagree because ...", then what you would do instead, then the \
specific risk in their approach. Hold your position unless they give you new information.
- Use dollars from their portfolio, not percentages alone. Be brief: a direct answer, then the few facts behind it.

What you know about the evidence (from the app's own backtests):
- The weekly screen's buy lists have not beaten SPY by more than luck would explain, and its bottom 50 trailed \
by under a point a quarter (also luck), so a low grade is not a sell reason. Opportunistic insider buying showed no \
edge. Spin-offs bought 20 days in beat SPY on average, but the median was small and it rests on a few big winners. \
Signals, sleepers and chatter are unproven; say so when they come up. VOO is the default, not a pick.
- The decisions tool is the app's ranked answer to "what should I do": prefer it when asked what to do, and \
explain its evidence level (rule / mixed / unproven).

Limits: you cannot trade, place orders, change settings or see anything the tools don't return. If data is \
stale, estimated or missing, the tools say so; pass that on. This is not tax or legal advice; say when a \
question needs an accountant."""


def tool_defs() -> list[dict]:
    out = []
    for name, (path, desc) in mcp_server.TOOLS.items():
        props, req = {}, []
        if "{symbol}" in path:
            props["symbol"] = {"type": "string", "description": "Ticker, e.g. AAPL, BRK-B or BTC-USD"}
            req.append("symbol")
        if "{period}" in path:
            props["period"] = {"type": "string", "enum": ["all", "ytd"], "description": "all or ytd"}
        out.append({"name": name, "description": desc,
                    "input_schema": {"type": "object", "properties": props, "required": req, "additionalProperties": False}})
    return out


def validate(name: str, args: dict) -> str | None:
    """An error message if a tool call's input is unusable, else None."""
    if name not in mcp_server.TOOLS:
        return f"unknown tool {name}"
    if "{symbol}" in mcp_server.TOOLS[name][0]:
        sym = (args or {}).get("symbol")
        if not isinstance(sym, str) or not mcp_server.SYMBOL.match(sym):
            return "symbol: a ticker like AAPL, BRK-B or BTC-USD"
    return None


class LocalApp:
    """Calls the app's own GET endpoints in-process, signed in (never over the network)."""

    def __init__(self):
        self._client = None

    def get(self, path: str) -> str:
        if not path.startswith("/api/"):
            raise ValueError("only the app's /api/ endpoints")
        if self._client is None:
            from fastapi.testclient import TestClient

            from . import auth
            from .api import app
            self._client = TestClient(app, base_url="http://plumbline.local")
            if auth.enabled():
                self._client.cookies.set(auth.COOKIE, auth.make_token())
        r = self._client.get(path)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", "")
            except ValueError:
                detail = ""
            return json.dumps({"error": f"Plumbline answered {r.status_code}", "detail": detail})
        text = r.text
        return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + f"\n... [cut at {MAX_RESULT_CHARS:,} characters]"


_conversations: dict[str, tuple[float, list]] = {}


def _messages_for(cid: str | None) -> tuple[str, list]:
    now = time.time()
    for k in [k for k, (t, _) in _conversations.items() if now - t > CONVERSATION_SECONDS]:
        _conversations.pop(k, None)
    if cid and cid in _conversations:
        return cid, _conversations[cid][1]
    cid = uuid.uuid4().hex
    if len(_conversations) >= KEEP_CONVERSATIONS:
        _conversations.pop(min(_conversations, key=lambda k: _conversations[k][0]), None)
    _conversations[cid] = (now, [])
    return cid, _conversations[cid][1]


def ask(question: str, conversation: str | None = None, client: anthropic.Anthropic | None = None, app: LocalApp | None = None) -> dict:
    """One user turn: Claude may call tools several times, then answers. Returns the answer text,
    the tools used, the conversation id for follow-ups, and token usage."""
    cid, messages = _messages_for(conversation)
    messages.append({"role": "user", "content": question})
    client = client or anthropic.Anthropic()
    app = app or LocalApp()
    tools = tool_defs()
    used: list[str] = []
    usage = {"input": 0, "output": 0, "cache_read": 0}
    for _ in range(MAX_TOOL_ROUNDS + 1):
        resp = client.beta.messages.create(
            model=settings.research_model, max_tokens=16000, system=SYSTEM, tools=tools, messages=messages,
            thinking={"type": "adaptive"}, cache_control={"type": "ephemeral"}, betas=[FALLBACK_BETA], fallbacks="default")
        u = resp.usage
        usage["input"] += u.input_tokens
        usage["output"] += u.output_tokens
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "refusal":
            return {"conversation": cid, "answer": "Claude declined to answer that one.", "tools": used, "usage": usage, "refused": True}
        if resp.stop_reason != "tool_use":
            text = "\n\n".join(b.text for b in resp.content if b.type == "text").strip()
            _conversations[cid] = (time.time(), messages)
            return {"conversation": cid, "answer": text, "tools": used, "usage": usage, "model": resp.model,
                    "truncated": resp.stop_reason == "max_tokens"}
        results = []
        for b in resp.content:
            if b.type != "tool_use":
                continue
            args = b.input if isinstance(b.input, dict) else {}
            err = validate(b.name, args)
            if err:
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": err, "is_error": True})
                continue
            used.append(b.name + (f"({args['symbol'].upper()})" if args.get("symbol") else ""))
            try:
                out = app.get(mcp_server.path_for(b.name, args.get("symbol"), args.get("period")))
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
            except Exception as exc:  # noqa: BLE001 - the tool failed; Claude is told and can work around it
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": f"The tool failed: {exc}", "is_error": True})
        messages.append({"role": "user", "content": results})
    _conversations[cid] = (time.time(), messages)
    return {"conversation": cid, "answer": "That needed more lookups than one answer allows; ask a narrower question.", "tools": used,
            "usage": usage, "truncated": True}


LETTER_PROMPT = """Write this week's letter to me: at most 200 words. Start with the one thing that matters most \
for my money this week (use the decisions and weekly_recap tools; add others only if needed). Then what I should \
do, in dollars, and what I should leave alone. End with one line on anything stale or missing in the data."""


def letter(client: anthropic.Anthropic | None = None, app: LocalApp | None = None) -> dict:
    return ask(LETTER_PROMPT, None, client, app)


def plain(text: str) -> str:
    """The answer without Markdown emphasis, for a phone notification."""
    return re.sub(r"[*_`#]+", "", text)
