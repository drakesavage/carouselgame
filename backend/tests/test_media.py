"""Thumbnails (?w=) for local pictures: smaller, cached, never enlarged; originals unchanged."""
import pymupdf


def _png(path, w, h, alpha=False):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), alpha)
    pix.clear_with(180)
    path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(path))
    return path.stat().st_size


def test_portrait_thumbnails_are_scaled_cached_and_never_enlarged(client, settings):
    full = _png(settings.portraits_dir / "big.png", 1200, 1600)
    r = client.get("/media/portraits/big.png", params={"w": 100})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and len(r.content) < full / 4
    small = pymupdf.Pixmap(r.content)
    assert (small.width, small.height) == (128, 171)  # snapped up to 128, aspect kept
    cached = list((settings.data_dir / "thumbs").glob("*.jpg"))
    assert len(cached) == 1
    assert client.get("/media/portraits/big.png", params={"w": 120}).content == r.content and len(list((settings.data_dir / "thumbs").glob("*"))) == 1
    assert len(client.get("/media/portraits/big.png").content) == full  # no ?w: the original
    _png(settings.portraits_dir / "tiny.png", 40, 40)
    assert pymupdf.Pixmap(client.get("/media/portraits/tiny.png", params={"w": 256}).content).width == 40  # never enlarged
    _png(settings.portraits_dir / "glass.png", 600, 600, alpha=True)
    r = client.get("/media/portraits/glass.png", params={"w": 64})
    assert r.headers["content-type"] == "image/png" and pymupdf.Pixmap(r.content).alpha  # transparency kept
    assert client.get("/media/portraits/big.png", params={"w": 5}).status_code == 422
    assert client.get("/media/portraits/..%2Fcampaign.db", params={"w": 64}).status_code == 404
