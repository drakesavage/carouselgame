"""Scaled-down copies of local pictures (portraits, item and scene art, assets) for the places that show them small.

`?w=` on a /media URL asks for a width; it snaps up to one of WIDTHS, only ever shrinks, and is cached on disk under
data/thumbs keyed by the source file's path, size and modification time, so a replaced picture gets a fresh thumbnail.
Originals are never changed. Pictures with transparency stay PNG; the rest become JPEG.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

WIDTHS = (64, 128, 256, 512, 1024)
_LOCK = threading.Lock()


def snap(w: int) -> int:
    return next((x for x in WIDTHS if x >= w), WIDTHS[-1])


def thumbnail(src: Path, w: int, cache_dir: Path) -> tuple[Path, str]:
    """(file, media type) of `src` at most `w` pixels wide (snapped); the original when it is already that small."""
    w = snap(w)
    st = src.stat()
    key = hashlib.sha1(f"{src.resolve()}|{st.st_size}|{st.st_mtime_ns}|{w}".encode()).hexdigest()[:20]
    for ext, mt in ((".jpg", "image/jpeg"), (".png", "image/png")):
        hit = cache_dir / f"{key}{ext}"
        if hit.is_file():
            return hit, mt
    if src.suffix.lower() in (".svg", ".gif"):
        return src, "image/svg+xml" if src.suffix.lower() == ".svg" else "image/gif"
    import pymupdf
    with _LOCK:  # one conversion at a time keeps memory flat when a page asks for many at once
        pix = pymupdf.Pixmap(str(src))
        if pix.width <= w:
            return src, _type(src)
        h = max(1, round(pix.height * w / pix.width))
        if pix.alpha:
            small, ext, mt = pymupdf.Pixmap(pix, w, h, None), ".png", "image/png"
            data = small.tobytes("png")
        else:
            if pix.n > 3:  # CMYK and the like
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            small, ext, mt = pymupdf.Pixmap(pix, w, h, None), ".jpg", "image/jpeg"
            data = small.tobytes("jpeg", jpg_quality=84)
        cache_dir.mkdir(parents=True, exist_ok=True)
        out = cache_dir / f"{key}{ext}"
        tmp = out.with_suffix(ext + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(out)
        return out, mt


def warm(folders: list[Path], cache_dir: Path, widths: tuple[int, ...] = (64, 128)) -> int:
    """Make the small sizes the roster, inventory and story ask for first, so a first visit isn't waiting on them.
    Already-cached ones are skipped; failures are ignored (that picture is then made on demand)."""
    made = 0
    for folder in folders:
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*")):
            if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            for w in widths:
                try:
                    out, _ = thumbnail(f, w, cache_dir)
                    made += out != f
                except Exception:  # noqa: BLE001 - a broken picture must not stop the rest
                    break
    return made


def _type(p: Path) -> str:
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(p.suffix.lower(), "application/octet-stream")
