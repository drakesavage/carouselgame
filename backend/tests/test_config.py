from pathlib import Path

from app.config import ROOT_DIR, Settings


def test_settings_isolated_from_crawler_console(monkeypatch):
    for k in ("CAROUSEL_DATA_DIR", "CRAWLER_DATA_DIR", "CAROUSEL_SOURCE_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CRAWLER_DATA_DIR", r"E:\dccrpg\data")   # Crawler Console's variable must be ignored
    s = Settings()
    assert s.data_dir == ROOT_DIR / "data"
    assert "dccrpg" not in str(s.data_dir).lower()
    assert s.port == 8797
    assert s.source_dir == Path(r"E:\Carousel")
    assert s.reference_db == s.data_dir / "reference.db"
    assert s.codex_jobs_dir.is_relative_to(s.data_dir)
