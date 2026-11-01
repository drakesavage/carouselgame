"""Builds data/reference.db from the Carousel source packages: build beside the live DB, validate, then swap.

1. Read every package (missing ones are reported together).
2. Build reference.db.new with every importer.
3. Re-hash the sources: if anything changed while reading (e.g. the map studio wrote a file), abort.
4. Every Carousel ID saved campaigns depend on must resolve in the new DB (aliases count), else refuse.
5. Atomically replace reference.db. Any failure leaves the old reference.db untouched and removes the .new file.
Each run writes data/import_reports/<timestamp>.json. Run it with `python -m app.carousel.importer` (import-data.cmd).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from . import refdb
from .import_art import import_art
from .import_npcs import import_npcs
from .import_omens import import_omens
from .import_places import import_places
from .import_roles import import_roles
from .import_venues import import_venues
from .refs import referenced_ids, unresolved
from .sources import load_sources, verify_unchanged

IMPORTERS = [import_places, import_omens, import_roles, import_npcs, import_art, import_venues]
COUNTED = ("places", "place_aliases", "graph_nodes", "graph_edges", "omens", "omen_beats", "roles", "tickets", "start_profiles",
           "tropes", "npc_templates", "place_images",
           "venues", "venue_zones", "venue_links", "venue_omens", "import_report")


@dataclass
class ImportResult:
    ok: bool
    counts: dict[str, int] = field(default_factory=dict)
    missing: dict[str, list[str]] = field(default_factory=dict)
    error: str | None = None
    report_path: Path | None = None


def run_import(settings: Settings) -> ImportResult:
    started = dt.datetime.now(dt.timezone.utc)
    new = settings.reference_db.with_suffix(".db.new")
    result = ImportResult(ok=False)
    sources: dict = {}
    try:
        files = load_sources(settings.source_dir)
        sources = {k: {"path": str(f.path), "sha256": f.sha256} for k, f in files.items()}
        new.unlink(missing_ok=True)
        ref = refdb.connect(new)
        try:
            refdb.init(ref)
            with ref:
                for fn in IMPORTERS:
                    fn(ref, files)
                ref.executemany("INSERT INTO manifest (key, path, sha256, imported_at) VALUES (?,?,?,?)",
                                [(k, str(f.path), f.sha256, started.isoformat(timespec="seconds")) for k, f in files.items()])
            result.counts = {t: refdb.count(ref, t) for t in COUNTED}
            verify_unchanged(files)
            if settings.campaign_db.exists():
                camp = sqlite3.connect(str(settings.campaign_db))
                try:
                    result.missing = unresolved(ref, referenced_ids(camp))
                finally:
                    camp.close()
        finally:
            ref.close()
        if result.missing:
            result.error = "saved campaigns use IDs this import no longer has: " + "; ".join(
                f"{kind}: {', '.join(ids)}" for kind, ids in result.missing.items())
        else:
            _swap(new, settings.reference_db)
            result.ok = True
    except Exception as exc:  # noqa: BLE001 - any failure is reported and leaves the old reference.db in place
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        new.unlink(missing_ok=True)
    result.report_path = _write_report(settings, started, result, sources)
    return result


SWAP_TRIES, SWAP_WAIT_S = 10, 0.3


def _swap(new: Path, live: Path) -> None:
    """Windows refuses to replace a file another process has open (the server reading reference.db): retry briefly."""
    for attempt in range(SWAP_TRIES):
        try:
            os.replace(new, live)
            return
        except PermissionError as exc:
            if attempt == SWAP_TRIES - 1:
                raise RuntimeError(f"reference.db is in use ({exc}): stop the server or wait for the GM turn to finish, "
                                   "then run import-data.cmd again") from None
            time.sleep(SWAP_WAIT_S)


def _write_report(settings: Settings, started: dt.datetime, result: ImportResult, sources: dict) -> Path:
    folder = settings.data_dir / "import_reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{started.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    path.write_text(json.dumps({"ok": result.ok, "started": started.isoformat(timespec="seconds"), "source_dir": str(settings.source_dir),
                                "counts": result.counts, "missing": result.missing, "error": result.error, "sources": sources},
                               indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> int:
    settings = Settings()
    settings.ensure_dirs()
    r = run_import(settings)
    if r.ok:
        print(f"[Carousel] reference data imported from {settings.source_dir}")
        for k, v in r.counts.items():
            print(f"  {k:15} {v}")
    else:
        print(f"[Carousel] import refused; the previous reference data is unchanged.\n  {r.error}")
    print(f"  report: {r.report_path}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
