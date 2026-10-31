import pytest

from app.carousel.coords import from_pixel, to_pixel
from app.carousel.sources import SOURCE_FILES, SourceError, load_sources, verify_unchanged
from app.carousel.text import norm_name


def test_load_sources_real(real_source):
    files = load_sources(real_source)
    assert set(files) == set(SOURCE_FILES)
    assert all(len(f.sha256) == 64 for f in files.values())
    assert isinstance(files["gazetteer"].data, list)


def test_missing_files_reported_together(mini_source):
    (mini_source / SOURCE_FILES["omens"]).unlink()
    (mini_source / SOURCE_FILES["npcs"]).unlink()
    with pytest.raises(SourceError) as e:
        load_sources(mini_source)
    assert "omens" in str(e.value) and "npcs" in str(e.value)


def test_changed_file_is_detected(mini_source):
    files = load_sources(mini_source)
    verify_unchanged(files)
    (mini_source / SOURCE_FILES["roles"]).write_text('{"changed": true}', encoding="utf-8")
    with pytest.raises(SourceError, match="roles"):
        verify_unchanged(files)


def test_pixel_transform():
    assert to_pixel(1000, 1000) == (0, 0)
    assert to_pixel(0, 0) == (12000, 12000)
    assert to_pixel(425, 570) == (5160, 6900)          # flea market CAR-035
    assert from_pixel(*to_pixel(485, 490)) == (485, 490)


def test_norm_name():
    assert norm_name("Patcher’s Family Farm") == norm_name("Patcher\ufffds  family farm") == "patcher's family farm"
    assert norm_name(None) == ""
