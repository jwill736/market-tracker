"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader so the app works without python-dotenv."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Settings:
    sec_user_agent: str = field(
        default_factory=lambda: os.environ.get("SEC_USER_AGENT", "market-tracker contact@example.com")
    )
    finnhub_api_key: str | None = field(default_factory=lambda: os.environ.get("FINNHUB_API_KEY") or None)
    research_model: str = field(default_factory=lambda: os.environ.get("MT_RESEARCH_MODEL", "claude-opus-5"))
    db_path: str = field(default_factory=lambda: os.environ.get("MT_DB_PATH", "market_tracker.db"))
    http_timeout: float = 20.0


_load_dotenv()
settings = Settings()
