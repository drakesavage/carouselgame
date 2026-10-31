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


@pytest.fixture(scope="session")
def real_files(real_source):
    from app.carousel.sources import load_sources
    return load_sources(real_source)


@pytest.fixture(scope="session")
def ref_real(real_files, tmp_path_factory):
    """A reference DB built once from the real packages by every importer that exists so far."""
    from app.carousel import refdb
    from app.carousel.importer import IMPORTERS
    conn = refdb.connect(tmp_path_factory.mktemp("ref") / "reference.db")
    refdb.init(conn)
    for fn in IMPORTERS:
        fn(conn, real_files)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def real_ref_conn(ref_real):
    """A bare connection with the real reference DB attached as `ref`, like a campaign connection."""
    import sqlite3
    from app.carousel.refdata import attach_reference
    path = ref_real.execute("PRAGMA database_list").fetchone()[2]
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    attach_reference(c, path)
    yield c
    c.close()
