"""Whole-install backups: the campaign database plus the media the app owns, with verification and restore.

    ..\\.venv\\Scripts\\python -m app.backup create [label]     (from backend\\; safe while the server runs)
    ..\\.venv\\Scripts\\python -m app.backup verify <folder>
    ..\\.venv\\Scripts\\python -m app.backup restore <folder> <target data dir>

A backup folder (data/backups/<timestamp>-<label>/) holds a consistent copy of campaign.db made with SQLite's online
backup API (no torn copy of a WAL database), copies of art/, portraits/, assets/ and visual_reference/, settings.json,
and manifest.json with per-file SHA-256 and per-table row counts. `verify` re-checks hashes, runs PRAGMA
integrity_check and compares row counts. `restore` only writes into an empty or new folder: it never overwrites live
data, so switching to a restored copy is a deliberate step (docs/OPERATIONS.md).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from .config import Settings

MEDIA = ("art", "portraits", "assets", "visual_reference")
SMALL_FILES = ("settings.json",)  # app preferences; the GM token and access passcode are deliberately not copied


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _counts(db: Path) -> dict[str, int]:
    c = sqlite3.connect(db)
    try:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        c.close()


def create(settings: Settings, label: str = "manual", media: bool = True) -> Path:
    """A consistent copy of the database; with media (the default) also the art, portraits and assets the app owns."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = settings.data_dir / "backups" / f"{stamp}-{''.join(ch if ch.isalnum() or ch in '-_' else '-' for ch in label)[:40]}"
    dest.mkdir(parents=True)
    src = sqlite3.connect(settings.campaign_db)
    out = sqlite3.connect(dest / "campaign.db")
    try:
        src.backup(out)  # consistent snapshot even with the server writing (WAL)
    finally:
        out.close()
        src.close()
    for name in MEDIA if media else ():
        if (settings.data_dir / name).is_dir():
            shutil.copytree(settings.data_dir / name, dest / name)
    for name in SMALL_FILES:
        if (settings.data_dir / name).is_file():
            shutil.copy2(settings.data_dir / name, dest / name)
    files = {str(p.relative_to(dest)).replace("\\", "/"): _sha(p) for p in sorted(dest.rglob("*")) if p.is_file()}
    manifest = {"created": stamp, "label": label, "media": media, "source": str(settings.data_dir), "files": files, "row_counts": _counts(dest / "campaign.db")}
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return dest


def verify(folder: Path) -> dict:
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    problems = [f"missing {f}" for f in m["files"] if not (folder / f).is_file()]
    problems += [f"changed {f}" for f, h in m["files"].items() if (folder / f).is_file() and _sha(folder / f) != h]
    c = sqlite3.connect(folder / "campaign.db")
    try:
        integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        c.close()
    if integrity != "ok":
        problems.append(f"integrity_check: {integrity}")
    counts = _counts(folder / "campaign.db")
    problems += [f"row count {t}: {counts.get(t)} != {n}" for t, n in m["row_counts"].items() if counts.get(t) != n]
    return {"ok": not problems, "problems": problems, "files": len(m["files"]), "tables": len(counts)}


def restore(folder: Path, target: Path) -> Path:
    """Copy a verified backup into `target` (new or empty). Never touches the live data folder."""
    v = verify(folder)
    if not v["ok"]:
        raise RuntimeError("backup failed verification: " + "; ".join(v["problems"][:5]))
    if target.exists() and any(target.iterdir()):
        raise RuntimeError(f"{target} is not empty; restore only into a new or empty folder")
    target.mkdir(parents=True, exist_ok=True)
    for p in folder.iterdir():
        if p.name == "manifest.json":
            continue
        (shutil.copytree if p.is_dir() else shutil.copy2)(p, target / p.name)
    return target


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "create":
        d = create(Settings(), sys.argv[2] if len(sys.argv) > 2 else "manual")
        print(d)
        print(json.dumps(verify(d)))
    elif cmd == "verify":
        print(json.dumps(verify(Path(sys.argv[2])), indent=1))
    elif cmd == "restore":
        print(restore(Path(sys.argv[2]), Path(sys.argv[3])))
    else:
        print(__doc__)
