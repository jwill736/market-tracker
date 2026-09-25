import httpx
import pytest

from market_tracker import http


class FakeClient:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def get(self, url, **kw):
        self.calls += 1
        status = self.statuses.pop(0)
        if status == "timeout":
            raise httpx.ReadTimeout("timed out")
        req = httpx.Request("GET", url)
        return httpx.Response(status, request=req, text='{"ok": true}')


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(http, "_BACKOFF", 0)
    http.clear_cache()

    def install(statuses):
        c = FakeClient(statuses)
        monkeypatch.setattr(http, "client", lambda: c)
        return c
    return install


def test_retries_transient_failures_then_succeeds(fake):
    c = fake([503, "timeout", 200])
    assert http.get("https://example.com/a", ttl=0) == {"ok": True}
    assert c.calls == 3


def test_gives_up_after_retries(fake):
    c = fake([503] * 10)
    with pytest.raises(http.DataUnavailable):
        http.get("https://example.com/b", ttl=0)
    assert c.calls == http.RETRIES + 1


def test_does_not_retry_client_errors(fake):
    c = fake([404, 200])
    with pytest.raises(http.DataUnavailable):
        http.get("https://example.com/c", ttl=0)
    assert c.calls == 1


def test_get_bytes_uses_same_retry_path(fake):
    c = fake([502, 200])
    assert http.get_bytes("https://example.com/d") == b'{"ok": true}'
    assert c.calls == 2
