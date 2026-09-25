"""Shared HTTP client with a small in-memory TTL cache and per-host throttling."""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import settings

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
_last_request: dict[str, float] = {}
_throttle_lock = threading.Lock()

# Minimum seconds between requests per host. SEC allows 10 req/s; stay well under.
_MIN_INTERVAL = {"www.sec.gov": 0.15, "data.sec.gov": 0.15}

_client: httpx.Client | None = None


class DataUnavailable(RuntimeError):
    """Raised when an upstream data source can't be reached or returns garbage."""


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=settings.http_timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (market-tracker)"},
        )
    return _client


def _throttle(host: str) -> None:
    interval = _MIN_INTERVAL.get(host)
    if not interval:
        return
    with _throttle_lock:
        wait = _last_request.get(host, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()


def get(url: str, *, params: dict | None = None, headers: dict | None = None,
        ttl: float = 60.0, as_json: bool = True) -> Any:
    """GET a URL, returning parsed JSON (or text). Results are cached for `ttl` seconds."""
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    _throttle(urlparse(url).hostname or "")
    try:
        resp = client().get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json() if as_json else resp.text
    except (httpx.HTTPError, ValueError) as exc:
        raise DataUnavailable(f"{url}: {exc}") from exc
    if ttl > 0:  # ttl <= 0: one-off download (e.g. a 10 MB 13F table) - don't hold it in memory
        with _cache_lock:
            _cache[key] = (now + ttl, data)
    return data


def get_bytes(url: str, *, headers: dict | None = None, timeout: float = 180.0) -> bytes:
    """Uncached binary download (bulk data files), throttled like `get`."""
    _throttle(urlparse(url).hostname or "")
    try:
        resp = client().get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise DataUnavailable(f"{url}: {exc}") from exc
    return resp.content


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
