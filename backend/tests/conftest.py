from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.carousel.refdata import open_campaign  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.gm.orchestrator import TurnService  # noqa: E402
from app.gm.providers import MockProvider  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.fixtures.demo_seed import seed_demo  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def mini_reference(tmp_path_factory) -> Path:
    """reference.db built once from the synthetic mini source packages (tests/carousel/mini_data.py)."""
    from app.carousel.importer import run_import
    from tests.carousel.conftest import write_mini_source
    root = tmp_path_factory.mktemp("mini_ref")
    s = Settings(data_dir=root / "data", source_dir=write_mini_source(root / "src"), gm_provider="mock")
    s.ensure_dirs()
    assert run_import(s).ok
    return s.reference_db


@pytest.fixture
def settings(tmp_path, mini_reference) -> Settings:
    s = Settings(data_dir=tmp_path / "data", gm_provider="mock", portrait_engine="comfy")
    s.ensure_dirs()
    shutil.copy(mini_reference, s.reference_db)
    return s


@pytest.fixture
def conn(settings):
    c = open_campaign(settings)
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
