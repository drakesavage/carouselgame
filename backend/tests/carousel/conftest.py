import json
from pathlib import Path

import pytest

from app.carousel.sources import SOURCE_FILES

REAL_SOURCE = Path(r"E:\Carousel")


@pytest.fixture(scope="session")
def real_source() -> Path:
    if not (REAL_SOURCE / SOURCE_FILES["gazetteer"]).exists():
        pytest.skip("Carousel source packages not present")
    return REAL_SOURCE


def write_mini_source(root: Path, overrides: dict | None = None) -> Path:
    """A tiny synthetic copy of every source package, in the real files' shapes (tests/carousel/mini_data.py)."""
    from tests.carousel.mini_data import MINI
    for key, rel in SOURCE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps((overrides or {}).get(key, MINI[key]), ensure_ascii=False), encoding="utf-8")
    return root


@pytest.fixture
def mini_source(tmp_path) -> Path:
    return write_mini_source(tmp_path / "carousel_src")
