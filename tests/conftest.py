import math
import random
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()


def synthetic_closes(n: int = 600, drift: float = 0.0005, vol: float = 0.015, seed: int = 1,
                     start: float = 100.0) -> list[float]:
    rng = random.Random(seed)
    out = [start]
    for _ in range(n - 1):
        out.append(out[-1] * math.exp(drift + vol * rng.gauss(0, 1)))
    return out


@pytest.fixture(autouse=True)
def _no_background(monkeypatch):
    monkeypatch.setenv("MT_BACKGROUND", "0")
    monkeypatch.delenv("NTFY_TOPIC", raising=False)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    from market_tracker import config
    monkeypatch.setattr(config, "settings", config.Settings(db_path=str(tmp_path / "test.db")))
    import market_tracker.db as db
    monkeypatch.setattr(db, "settings", config.settings)
    yield
