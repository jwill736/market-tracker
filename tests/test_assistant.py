from types import SimpleNamespace as NS

from market_tracker import assistant


class FakeClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls.append(dict(kw, messages=list(kw["messages"])))
        return self.script.pop(0)


def _resp(content, stop, model="claude-opus-5"):
    return NS(content=content, stop_reason=stop, model=model, usage=NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=50))


class FakeApp:
    def __init__(self):
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        return '{"decisions": [{"title": "Put $2,500 of idle cash into VOO"}]}'


def test_tool_defs_cover_every_tool_and_require_symbols():
    defs = {d["name"]: d for d in assistant.tool_defs()}
    assert set(defs) == set(assistant.mcp_server.TOOLS)
    assert defs["priced_in"]["input_schema"]["required"] == ["symbol"] and defs["decisions"]["input_schema"]["required"] == []
    assert assistant.validate("priced_in", {"symbol": "AAPL"}) is None and assistant.validate("priced_in", {}) is not None
    assert assistant.validate("place_order", {}) == "unknown tool place_order"


def test_ask_runs_tools_then_answers_and_keeps_the_conversation():
    script = [
        _resp([NS(type="text", text="Checking."), NS(type="tool_use", id="t1", name="decisions", input={}),
               NS(type="tool_use", id="t2", name="priced_in", input={"symbol": "nvda"}),
               NS(type="tool_use", id="t3", name="quote", input={"symbol": "rm -rf"})], "tool_use"),
        _resp([NS(type="text", text="[Certain] Your cash is the issue: put $2,500 into VOO.")], "end_turn"),
        _resp([NS(type="text", text="[Likely] Still VOO.")], "end_turn"),
    ]
    client, app = FakeClient(script), FakeApp()
    r = assistant.ask("What should I do?", None, client, app)
    assert r["answer"].startswith("[Certain]") and r["tools"] == ["decisions", "priced_in(NVDA)"]
    assert app.paths == ["/api/decisions", "/api/priced-in/NVDA"]
    results = client.calls[1]["messages"][-1]["content"]
    assert len(results) == 3 and results[2]["is_error"]                     # a bad ticker never reaches the app
    assert client.calls[0]["betas"] == [assistant.FALLBACK_BETA] and client.calls[0]["fallbacks"] == "default"
    assert client.calls[0]["system"] == assistant.SYSTEM and r["usage"]["cache_read"] == 100
    r2 = assistant.ask("And next week?", r["conversation"], client, app)
    assert r2["conversation"] == r["conversation"] and len(client.calls[2]["messages"]) == 5   # q, tools, results, answer, q2


def test_refusal_and_local_app_signed_in():
    r = assistant.ask("x", None, FakeClient([_resp([], "refusal")]), FakeApp())
    assert r["refused"]
    out = assistant.LocalApp().get("/api/session")
    assert '"auth"' in out
