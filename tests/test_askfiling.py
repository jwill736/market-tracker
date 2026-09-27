from types import SimpleNamespace as NS

from market_tracker import askfiling, filings


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, msg):
        self.calls = []
        self.beta = NS(messages=NS(stream=self._stream))
        self.msg = msg

    def _stream(self, **kw):
        self.calls.append(kw)
        return FakeStream(self.msg)


def cite(i, start, text):
    return NS(document_index=i, start_char_index=start, cited_text=text, document_title="NIKE, Inc. 10-K filed 2026-07-20")


def test_ask_sends_cited_cached_documents_and_parses_sources(monkeypatch):
    docs = [{"form": "10-K", "filed": "2026-07-20", "period": "2026-05-31", "url": "https://sec/k.htm", "company": "NIKE, Inc."}]
    monkeypatch.setattr(filings, "filings_for", lambda sym, forms, n, get=None: docs if "10-K" in forms else [])
    msg = NS(stop_reason="end_turn", model="claude-opus-5", usage=NS(input_tokens=900, cache_read_input_tokens=0,
                                                                         cache_creation_input_tokens=120_000, output_tokens=300),
             content=[NS(type="thinking", thinking=""),
                      NS(type="text", text="Greater China was 15% of revenue", citations=[cite(0, 100, "Greater China revenues were $7.5 billion")]),
                      NS(type="text", text=" and tariffs could raise costs.", citations=[cite(0, 900, "New tariffs could raise our costs"),
                                                                                         cite(0, 100, "Greater China revenues were $7.5 billion")]),
                      NS(type="text", text=" No citation here.", citations=None)])
    client = FakeClient(msg)
    out = askfiling.ask("NKE", "How big is China?", ["10-K", "10-Q"], client=client, text_fn=lambda u: "FILING TEXT " * 100)
    req = client.calls[0]
    doc = req["messages"][0]["content"][0]
    assert doc["type"] == "document" and doc["citations"] == {"enabled": True} and doc["source"]["media_type"] == "text/plain"
    assert doc["cache_control"] == {"type": "ephemeral"} and req["messages"][0]["content"][-1] == {"type": "text", "text": "How big is China?"}
    assert req["fallbacks"] == "default" and req["thinking"] == {"type": "adaptive"} and "output_config" not in req
    assert [s["cites"] for s in out["segments"]] == [[1], [2, 1], []]
    assert out["sources"][1]["quote"] == "New tariffs could raise our costs" and len(out["sources"]) == 2
    assert out["filings"] == [{"form": "10-K", "filed": "2026-07-20", "period": "2026-05-31", "url": "https://sec/k.htm"}]
    assert out["usage"]["cache_write"] == 120_000


def test_ask_refusal_and_missing_and_too_big(monkeypatch):
    monkeypatch.setattr(filings, "filings_for", lambda sym, forms, n, get=None: [])
    assert "No 10-K on file" in askfiling.ask("VOO", "q?", ["10-K"], client=FakeClient(None))["error"]
    docs = [{"form": "10-K", "filed": "2026-07-20", "period": "x", "url": "u", "company": "C"}]
    monkeypatch.setattr(filings, "filings_for", lambda sym, forms, n, get=None: docs)
    big = askfiling.ask("X", "q?", ["10-K"], client=FakeClient(None), text_fn=lambda u: "x" * (askfiling.MAX_CHARS + 1))
    assert "too long" in big["error"]
    refused = askfiling.ask("X", "q?", ["10-K"], client=FakeClient(NS(stop_reason="refusal", content=[], usage=None, model="m")),
                            text_fn=lambda u: "text")
    assert refused["refused"]


def test_endpoint_rejects_crypto():
    from fastapi.testclient import TestClient
    from market_tracker import api
    r = TestClient(api.app).post("/api/ask-filing", json={"symbol": "BTC-USD", "question": "what?"})
    assert r.status_code == 400
