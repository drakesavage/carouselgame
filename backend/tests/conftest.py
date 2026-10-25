from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.gm.orchestrator import TurnService  # noqa: E402
from app.gm.providers import MockProvider  # noqa: E402
from app.main import create_app  # noqa: E402
from app.rules.dice import DiceRoller  # noqa: E402
from app.seed.demo import seed_demo  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def scripted(values):
    """Deterministic die source for arithmetic tests (production always uses the CSPRNG)."""
    it = itertools.cycle(values)
    return lambda sides: min(sides, next(it))


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings(data_dir=tmp_path / "data", gm_provider="mock", portrait_engine="comfy")
    s.ensure_dirs()
    return s


@pytest.fixture
def conn(settings):
    c = connect(settings.campaign_db)
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def demo_id(conn) -> int:
    return seed_demo(conn)


@pytest.fixture
def client(settings, conn, demo_id):
    app = create_app(settings, bootstrap=False, local_hosts={"testclient"})
    state = app.state.crawler
    p = state.prefs()
    p["gm_provider"] = "mock"
    state.save_prefs(p)
    state.turns = TurnService(settings, MockProvider, run_async=False)
    with TestClient(app) as tc:
        tc.app_state = state
        yield tc


@pytest.fixture
def roller() -> DiceRoller:
    return DiceRoller()
