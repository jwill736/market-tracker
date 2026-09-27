import asyncio
import json

import httpx
import pytest

from market_tracker import mcp_server

pytest.importorskip("mcp")


def _app(calls):
    def handler(req):
        calls.append((req.method, req.url.path, req.url.query.decode()))
        if req.url.path == "/login":
            return httpx.Response(303, headers={"set-cookie": "plumbline_session=t; Path=/"})
        if req.url.path == "/api/earnings/ZZZ":
            return httpx.Response(404, json={"detail": "No results release"})
        return httpx.Response(200, json={"ok": req.url.path})
    return mcp_server.Plumbline("http://app", "pw", httpx.Client(transport=httpx.MockTransport(handler)))


def test_every_tool_is_a_read_only_get():
    calls = []
    server = mcp_server.make_server(_app(calls))
    tools = asyncio.run(server.list_tools())
    assert len(tools) == len(mcp_server.TOOLS)
    assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint for t in tools)
    for name, (template, _) in mcp_server.TOOLS.items():
        assert template.startswith("/api/")
        assert not any(w in template for w in ("trade", "transactions", "sync", "ask-filing", "import", "settings", "backup"))


def test_calls_log_in_once_and_fill_arguments():
    calls = []
    server = mcp_server.make_server(_app(calls))

    async def go():
        a = await server.call_tool("performance", {"period": "ytd"})
        b = await server.call_tool("earnings_recap", {"symbol": "brk-b"})
        c = await server.call_tool("earnings_recap", {"symbol": "ZZZ"})
        d = await server.call_tool("quote", {"symbol": "../../api/trade"})
        return a, b, c, d
    a, b, c, d = asyncio.run(go())
    assert [x[1] for x in calls] == ["/login", "/api/performance", "/api/earnings/BRK-B", "/api/earnings/ZZZ"]
    assert calls[1][2] == "period=ytd" and all(m in ("GET", "POST") for m, _, _ in calls) and calls[0][0] == "POST"
    assert json.loads(b.content[0].text) == {"ok": "/api/earnings/BRK-B"}
    assert json.loads(c.content[0].text)["detail"] == "No results release"
    assert "error" in json.loads(d.content[0].text)
